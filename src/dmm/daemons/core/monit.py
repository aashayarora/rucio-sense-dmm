import logging
from collections import deque
from datetime import datetime, timezone

from dmm.daemons.base import DaemonBase
from dmm.models.base import utcnow
from dmm.models.request import Request, RequestStatus
from dmm.db.session import databased
from dmm.core.config import config_get_float, config_get_int
from dmm.core.monit import PrometheusUtils, PrometheusNotConfigured

# Values of Request.health. None - the column default - means "not measured", and
# is what the frontend renders as UNKNOWN.
HEALTH_OK = "1"
HEALTH_BAD = "0"

# Statuses whose health is kept up to date. Health on anything else is cleared, so
# a verdict from an earlier PROVISIONED spell doesn't outlive the transfer.
_MONITORED_STATUSES = [RequestStatus.PROVISIONED]


class MonitDaemon(DaemonBase):
    """
    Samples node_exporter transmit counters on each request's source endpoint and
    turns the delta into a throughput figure and a health verdict.

    Health means one thing only: is traffic actually flowing on this circuit.
    Anything that can't be measured - Prometheus unreachable, no interfaces found,
    no bandwidth decided yet, still inside the post-provisioning grace period - is
    left unknown rather than reported as unhealthy.
    """

    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        try:
            self.prometheus = PrometheusUtils()
        except PrometheusNotConfigured as e:
            logging.warning(f"Throughput monitoring disabled: {e}")
            self.prometheus = None

        self.grace_seconds = config_get_int("monit", "health_grace_seconds", default=300, constraint="nonneg")
        # Below this, nothing is moving on the circuit and that is a real fault.
        self.min_throughput_mbps = config_get_float("monit", "health_min_throughput_mbps", default=1.0, constraint="nonneg")
        # Fraction of the allocated bandwidth below which a circuit also counts as
        # unhealthy. Off by default: a transfer that doesn't saturate its circuit is
        # a tuning signal, not a fault, and scoring it as one marked essentially
        # every request unhealthy. Set to e.g. 0.8 to opt back in.
        self.min_utilization = config_get_float("monit", "health_min_utilization", default=0.0, constraint="nonneg")
        self.strikes_before_unhealthy = config_get_int("monit", "health_strikes", default=2, constraint="pos")
        # Throughput is averaged over a rolling window rather than a single poll -
        # one 60s delta swings wildly with FTS queue depth and node_exporter scrape
        # jitter, which is noise a health verdict should not react to.
        self.window_samples = config_get_int("monit", "throughput_window_samples", default=5, constraint="pos")
        if self.window_samples < 2:
            logging.warning("throughput_window_samples must be at least 2 to give a rate, using 2")
            self.window_samples = 2

        # rule_id -> deque of (unix timestamp, {interface selector: counter value})
        self._samples = {}
        # rule_id -> consecutive bad samples, so a single blip doesn't flip the badge
        self._strikes = {}

    def process(self, **kwargs):
        self.run_once(**kwargs)

    @databased
    def run_once(self, session=None):
        reqs = Request.get_by_status(statuses=_MONITORED_STATUSES, session=session)
        self._forget_unmonitored(reqs)
        self._clear_stale_health(session)

        if self.prometheus is None:
            return

        now = datetime.now(timezone.utc).timestamp()

        for req in reqs:
            try:
                self._monitor(req, now, session=session)
            except Exception as e:
                logging.error(f"Error monitoring request {req.rule_id}: {e}", exc_info=True)
                continue

    def _forget_unmonitored(self, reqs):
        """Drop in-memory samples for requests that are no longer being monitored."""
        live = {req.rule_id for req in reqs}
        for rule_id in list(self._samples):
            if rule_id not in live:
                del self._samples[rule_id]
                self._strikes.pop(rule_id, None)

    @staticmethod
    def _clear_stale_health(session):
        """
        A request that has left PROVISIONED is never sampled again, so whatever
        verdict it was last given would stick to it forever on the dashboard.
        """
        for req in Request.get_stale_health(_MONITORED_STATUSES, session=session):
            logging.debug(f"Clearing stale health on {req.rule_id} (status {req.transfer_status})")
            req.set_health(None, session=session)

    def _monitor(self, req, now, session=None):
        if not req.src_endpoint or not req.src_endpoint.ip_range:
            logging.warning(f"Request {req.rule_id} has no source endpoint, skipping monitoring")
            return

        counters = self._sample_interfaces(req.src_endpoint.ip_range, now)
        if counters is None:
            # Nothing measurable: say nothing rather than something wrong, and drop
            # the baseline so the next good sample isn't compared against stale data.
            logging.warning(f"No usable Prometheus data for {req.rule_id}, leaving health unknown")
            self._samples.pop(req.rule_id, None)
            self._strikes.pop(req.rule_id, None)
            req.set_health(None, session=session)
            return

        total_bytes = sum(counters.values())
        window = self._record_sample(req.rule_id, (now, counters))

        if len(window) < 2:
            # A single counter reading says nothing about rate.
            req.set_prometheus_metrics(bytes_transferred=total_bytes, session=session)
            return

        throughput_mbps = self._throughput_mbps(window[0], window[-1])
        if throughput_mbps is None:
            req.set_prometheus_metrics(bytes_transferred=total_bytes, session=session)
            return

        req.set_prometheus_metrics(throughput=throughput_mbps, bytes_transferred=total_bytes, session=session)
        req.set_health(self._health(req, throughput_mbps), session=session)

    def _record_sample(self, rule_id, sample):
        """
        Append to the rule's rolling window and return it.

        The window is only meaningful as a continuous series, so a sample that
        can't be compared against the one before it - node_exporter restarted, the
        interface set changed - starts the window over instead of being appended to
        a series it doesn't belong to.
        """
        window = self._samples.setdefault(rule_id, deque(maxlen=self.window_samples))
        if window and self._throughput_mbps(window[-1], sample) is None:
            logging.debug(f"Restarting the throughput window for {rule_id}")
            window.clear()
        window.append(sample)
        return window

    def _sample_interfaces(self, ip_range, at_time):
        """
        Counter value per interface for ip_range, or None if the set is incomplete.

        A partial read is worse than no read: the values get summed, so one missing
        interface looks exactly like a drop in traffic.
        """
        interfaces = self.prometheus.get_interfaces(ip_range)
        if not interfaces:
            return None

        counters = {}
        for selector in interfaces:
            value = self.prometheus.get_transmit_bytes(selector, at_time)
            if value is None:
                logging.warning(f"Incomplete counter set for {ip_range}, discarding this sample")
                return None
            counters[selector] = value
        return counters

    @staticmethod
    def _throughput_mbps(previous, current):
        """
        Mbps between two samples, or None when the pair cannot be compared.

        Only interfaces present in both samples count: one appearing or vanishing
        between polls would otherwise register as a burst or a stall. A counter that
        went backwards means node_exporter restarted, so the pair is dropped and the
        next poll starts from a fresh baseline.
        """
        prev_time, prev_counters = previous
        now_time, now_counters = current

        elapsed = now_time - prev_time
        if elapsed <= 0:
            logging.warning(f"Non-positive interval between samples ({elapsed}s), re-baselining")
            return None

        shared = prev_counters.keys() & now_counters.keys()
        if not shared:
            logging.warning("Interface set changed completely between samples, re-baselining")
            return None

        delta = 0.0
        for selector in shared:
            per_interface = now_counters[selector] - prev_counters[selector]
            if per_interface < 0:
                logging.info("Transmit counter went backwards (node_exporter restart?), re-baselining")
                return None
            delta += per_interface

        # Counters are bytes; bandwidth everywhere else in DMM is decimal Mbps.
        return round(delta * 8 / 1e6 / elapsed, 2)

    def _health(self, req, throughput_mbps):
        """
        HEALTH_OK, HEALTH_BAD, or None when there is no basis for a verdict.
        """
        if req.sense_provisioned_at is None:
            return None

        age_seconds = (utcnow() - req.sense_provisioned_at).total_seconds()
        if age_seconds < self.grace_seconds:
            logging.debug(
                f"{req.rule_id} was provisioned {age_seconds:.0f}s ago, inside the "
                f"{self.grace_seconds}s grace period - health stays unknown"
            )
            return None

        floor_mbps = self.min_throughput_mbps
        if self.min_utilization > 0 and req.allocated_bandwidth_mbps:
            floor_mbps = max(floor_mbps, self.min_utilization * req.allocated_bandwidth_mbps)

        if throughput_mbps >= floor_mbps:
            self._strikes.pop(req.rule_id, None)
            return HEALTH_OK

        strikes = self._strikes.get(req.rule_id, 0) + 1
        self._strikes[req.rule_id] = strikes
        if strikes < self.strikes_before_unhealthy:
            logging.debug(
                f"{req.rule_id} at {throughput_mbps} Mbps is below {floor_mbps} Mbps "
                f"({strikes}/{self.strikes_before_unhealthy} strikes), holding previous health"
            )
            return req.health

        logging.warning(
            f"{req.rule_id} is unhealthy: {throughput_mbps} Mbps is below {floor_mbps} Mbps "
            f"over {strikes} consecutive polls"
        )
        return HEALTH_BAD
