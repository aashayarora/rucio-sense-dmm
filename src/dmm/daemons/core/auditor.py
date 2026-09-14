import logging
from collections import Counter
from datetime import timedelta, timezone

from dmm.daemons.base import DaemonBase
from dmm.models.base import utcnow
from dmm.models.request import Request, RequestStatus, TransferVerdict
from dmm.db.session import databased
from dmm.core.config import config_get_float, config_get_int
from dmm.core.monit import (
    FTSMonitUtils, FTSNotConfigured,
    PrometheusUtils, PrometheusNotConfigured,
)

# A rule is worth auditing once Rucio has finished with it. FAILED and NOT_SENSE
# rules never ran a transfer over a circuit, so there is nothing to check.
_AUDITABLE_STATUSES = [
    RequestStatus.FINISHED_R, RequestStatus.FINISHED,
    RequestStatus.CANCELED, RequestStatus.DELETED,
]

# FTS reports a per-file outcome; these are the ones that mean the file arrived.
_FTS_SUCCESS_STATES = {"FINISHED", "SUCCESS", "OK"}


class TransferAuditDaemon(DaemonBase):
    """
    Cross-checks a completed rule against the circuit that was provisioned for it.

    Two independent sources: FTS knows which files succeeded and which failed, and
    node_exporter knows how many bytes actually crossed the source endpoint while
    the circuit was up. Agreement means the transfer went as planned; the
    interesting cases are the disagreements.

    One caveat shapes the whole comparison: node_network_transmit_bytes_total counts
    an entire interface, and an interface can carry more than one request's traffic.
    The wire figure is therefore an upper bound on what this rule moved, never an
    exact attribution. Only one volume conclusion survives that: if the upper bound
    is below what FTS says was transferred, the data cannot have taken this circuit.
    Wire above FTS says nothing at all, so it is never treated as a fault.
    """

    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        try:
            self.fts = FTSMonitUtils()
        except FTSNotConfigured as e:
            logging.warning(f"Transfer auditing disabled: {e}")
            self.fts = None
        try:
            self.prometheus = PrometheusUtils()
        except PrometheusNotConfigured as e:
            logging.warning(f"Transfer audits will have no wire measurement: {e}")
            self.prometheus = None

        # FTS records land in CERN monit minutes after the transfer; auditing sooner
        # reads an empty index and would score a healthy rule as UNKNOWN.
        self.audit_delay_seconds = config_get_int("monit", "audit_delay_seconds", default=900, constraint="nonneg")
        # Also bounds how long a rule that never gets records is retried.
        self.audit_max_age_seconds = config_get_int("monit", "audit_max_age_seconds", default=604800, constraint="pos")
        self.volume_tolerance = config_get_float("monit", "audit_volume_tolerance", default=0.1, constraint="nonneg")
        self.fail_threshold = config_get_float("monit", "audit_fail_threshold", default=0.5, constraint="nonneg")
        # Auditing is all external HTTP - a big FTS query plus two Prometheus reads
        # per interface - and it runs holding the daemon lock every other daemon
        # shares. Capping the batch keeps one cycle from stalling the rest of DMM;
        # the backlog just drains over the following cycles.
        self.batch_size = config_get_int("monit", "audit_batch_size", default=5, constraint="pos")

    def process(self, **kwargs):
        self.run_once(**kwargs)

    @databased
    def run_once(self, session=None):
        if self.fts is None:
            return

        now = utcnow()
        reqs = Request.get_pending_audit(
            _AUDITABLE_STATUSES,
            finished_before=now - timedelta(seconds=self.audit_delay_seconds),
            finished_after=now - timedelta(seconds=self.audit_max_age_seconds),
            limit=self.batch_size,
            session=session,
        )
        if reqs:
            logging.debug(f"Auditing {len(reqs)} finished requests")

        for req in reqs:
            try:
                verdict, audit = self._audit(req)
                req.set_transfer_audit(verdict, audit, session=session)
                log = logging.info if verdict == TransferVerdict.OK else logging.warning
                log(f"Transfer audit for {req.rule_id}: {verdict} - {audit.get('summary')}")
            except Exception as e:
                # Left unaudited on purpose: a transient FTS or Prometheus failure
                # should be retried, and audit_max_age_seconds bounds how long for.
                logging.error(f"Could not audit request {req.rule_id}: {e}", exc_info=True)
                continue

    def _audit(self, req):
        audit = {"audited_at": utcnow().isoformat()}

        records = self.fts.submit_job_query(req.rule_id)
        if not records:
            audit["summary"] = "no FTS records found for this rule"
            return TransferVerdict.UNKNOWN, audit

        window = self._circuit_window(req)
        if window:
            audit["circuit_window_seconds"] = round(window[1] - window[0], 1)
        else:
            audit["note"] = "no circuit window - rule was never provisioned or never finished"

        fts = self._summarize_fts(records, window)
        audit.update(fts)

        wire_bytes = self._wire_bytes(req, window)
        if wire_bytes is None:
            audit["wire_bytes"] = None
            audit.setdefault("note", "no wire measurement available")
        else:
            audit["wire_bytes"] = round(wire_bytes)

        return self._verdict(audit, fts, wire_bytes)

    @staticmethod
    def _circuit_window(req):
        """The circuit's lifetime as (start, end) epoch seconds, or None."""
        if not req.sense_provisioned_at or not req.rucio_finished_at:
            return None
        start = req.sense_provisioned_at.replace(tzinfo=timezone.utc).timestamp()
        end = req.rucio_finished_at.replace(tzinfo=timezone.utc).timestamp()
        return (start, end) if end > start else None

    @staticmethod
    def _summarize_fts(records, window):
        """
        Roll the per-file FTS records up into counts and volumes.

        Successful transfers are split by whether they completed while the circuit
        was up: bytes that moved outside that window definitively did not use it.
        """
        succeeded = failed = 0
        bytes_in_window = bytes_outside_window = 0.0
        errors = Counter()

        for record in records:
            state = str(record.get("t_final_transfer_state") or "").upper()
            try:
                size = float(record.get("file_size") or 0)
            except (TypeError, ValueError):
                size = 0.0

            if state not in _FTS_SUCCESS_STATES:
                failed += 1
                errors[str(record.get("tr_error_category") or "unknown")] += 1
                continue

            succeeded += 1
            completed = FTSMonitUtils.normalize_timestamp(record.get("tr_timestamp_complete"))
            if window and completed and not (window[0] <= completed <= window[1]):
                bytes_outside_window += size
            else:
                bytes_in_window += size

        return {
            "fts_files_total": len(records),
            "fts_files_succeeded": succeeded,
            "fts_files_failed": failed,
            "fts_bytes_in_window": round(bytes_in_window),
            "fts_bytes_outside_window": round(bytes_outside_window),
            "fts_error_categories": dict(errors.most_common(10)),
        }

    def _wire_bytes(self, req, window):
        """
        Bytes transmitted on the source endpoint's interfaces over the circuit window.

        None whenever the figure would be a guess: no Prometheus, no interfaces at
        the time, a missing sample at either edge, or a counter reset mid-window.
        """
        if self.prometheus is None or window is None:
            return None
        if not req.src_endpoint or not req.src_endpoint.ip_range:
            return None

        start, end = window
        interfaces = self.prometheus.get_interfaces(req.src_endpoint.ip_range, at_time=start)
        if not interfaces:
            logging.warning(f"No interfaces for {req.src_endpoint.ip_range} at the time {req.rule_id} ran")
            return None

        total = 0.0
        for selector in interfaces:
            at_start = self.prometheus.get_transmit_bytes(selector, start)
            at_end = self.prometheus.get_transmit_bytes(selector, end)
            if at_start is None or at_end is None:
                logging.warning(f"Prometheus has no counter at a window edge for {req.rule_id}")
                return None
            if at_end < at_start:
                logging.warning(f"Counter reset inside {req.rule_id}'s window, wire volume unusable")
                return None
            total += at_end - at_start
        return total

    def _verdict(self, audit, fts, wire_bytes):
        total = fts["fts_files_total"]
        failed = fts["fts_files_failed"]
        moved = fts["fts_bytes_in_window"]
        outside = fts["fts_bytes_outside_window"]

        if total and failed / total >= self.fail_threshold:
            audit["summary"] = (
                f"{failed}/{total} FTS transfers failed"
                f" ({self._top_errors(fts)})"
            )
            return TransferVerdict.FAILED, audit

        # The wire figure caps what this rule could have sent, so falling below the
        # claimed volume means the bytes went somewhere other than this circuit.
        if wire_bytes is not None and moved > 0 and wire_bytes < moved * (1 - self.volume_tolerance):
            audit["summary"] = (
                f"FTS moved {self._gb(moved)} GB but the circuit carried at most "
                f"{self._gb(wire_bytes)} GB - the data did not use this circuit"
            )
            return TransferVerdict.BYPASSED, audit

        if outside > moved:
            audit["summary"] = (
                f"{self._gb(outside)} GB of {self._gb(outside + moved)} GB completed outside "
                "the circuit's lifetime"
            )
            return TransferVerdict.BYPASSED, audit

        if failed:
            audit["summary"] = (
                f"{failed}/{total} FTS transfers failed ({self._top_errors(fts)}), "
                f"{self._gb(moved)} GB delivered"
            )
            return TransferVerdict.DEGRADED, audit

        wire_note = "" if wire_bytes is None else f", circuit carried {self._gb(wire_bytes)} GB"
        audit["summary"] = f"all {total} FTS transfers finished, {self._gb(moved)} GB delivered{wire_note}"
        return TransferVerdict.OK, audit

    @staticmethod
    def _top_errors(fts):
        errors = fts.get("fts_error_categories") or {}
        if not errors:
            return "no error category reported"
        return ", ".join(f"{category}: {count}" for category, count in errors.items())

    @staticmethod
    def _gb(num_bytes):
        return round((num_bytes or 0) / 1e9, 2)
