import logging
from datetime import datetime, timezone

from dmm.daemons.base import DaemonBase
from dmm.models.base import utcnow
from dmm.models.request import Request, RequestStatus
from dmm.db.session import databased
from dmm.core.config import config_get_float, config_get_int
from dmm.core.monit import (
    PrometheusUtils, PrometheusNotConfigured,
    circuit_ends, counter_increase, counter_rate_mbps,
)

# Values of Request.health. None - the column default - means "not measured", and
# is what the frontend renders as UNKNOWN.
HEALTH_OK = "1"
HEALTH_BAD = "0"

# Statuses whose health is kept up to date. Health on anything else is cleared, so
# a verdict from an earlier PROVISIONED spell doesn't outlive the transfer.
_MONITORED_STATUSES = [RequestStatus.PROVISIONED]

# Slack on every lookback, for Prometheus ingest lag and clock skew against DMM.
_LOOKBACK_MARGIN_SECONDS = 120
# SENSE-O circuits are provisioned for at most a day, so nothing older is ours.
_MAX_LOOKBACK_SECONDS = 86400


class _Series:
    """
    One counter since the circuit came up: a running total of what it carried,
    plus the samples inside the rate window.
    """

    def __init__(self):
        self.total = 0.0
        self.samples = []

    def add(self, samples, window_seconds):
        for sample in samples:
            if self.samples:
                self.total += counter_increase([self.samples[-1], sample])
            self.samples.append(sample)
        if not self.samples:
            return
        cutoff = self.samples[-1][0] - window_seconds
        # Keep at least two, so a sparsely scraped counter still gives a rate.
        while len(self.samples) > 2 and self.samples[0][0] < cutoff:
            self.samples.pop(0)


class MonitDaemon(DaemonBase):
    """
    Reads the switch counters for each provisioned circuit's VLAN and turns them
    into a throughput figure and a health verdict.

    Health means one thing only: is traffic actually flowing on this circuit.
    Anything that can't be measured - Prometheus unreachable, no VLAN recorded, no
    counters for it yet, still inside the post-provisioning grace period - is left
    unknown rather than reported as unhealthy.

    Rates come from the timestamps SiteRM put on its samples, not from when this
    daemon polls, so neither side's schedule skews them. A poll that finds nothing
    newer than the last one leaves everything as it was.
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
        # Throughput is averaged over this much of the latest counter history rather
        # than taken from two consecutive samples - one SNMP interval swings wildly
        # with FTS queue depth and scrape jitter, which is noise a health verdict
        # should not react to.
        self.window_seconds = config_get_int("monit", "throughput_window_seconds", default=300, constraint="pos")

        # rule_id -> {(site, vlan, hostname, ifDescr, Key): _Series}
        self._series = {}
        # rule_id -> timestamp of the newest sample health was last judged on
        self._judged = {}
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

    def _forget(self, rule_id):
        self._series.pop(rule_id, None)
        self._judged.pop(rule_id, None)
        self._strikes.pop(rule_id, None)

    def _forget_unmonitored(self, reqs):
        """Drop in-memory state for requests that are no longer being monitored."""
        live = {req.rule_id for req in reqs}
        for rule_id in set(self._series) | set(self._judged) | set(self._strikes):
            if rule_id not in live:
                self._forget(rule_id)

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
        ends = circuit_ends(req)
        if not ends:
            logging.warning(f"Request {req.rule_id} has no circuit VLAN recorded, leaving health unknown")
            self._forget(req.rule_id)
            req.set_health(None, session=session)
            return

        series = self._fetch(req, ends, now)
        if series is None:
            # Nothing measurable: say nothing rather than something wrong, and drop
            # what was collected so the next good read starts clean.
            logging.warning(f"No usable Prometheus data for {req.rule_id}, leaving health unknown")
            self._forget(req.rule_id)
            req.set_health(None, session=session)
            return
        if not series:
            logging.info(f"No switch counters yet for {req.rule_id}'s circuit VLANs {ends}")
            req.set_health(None, session=session)
            return

        newest = max(s.samples[-1][0] for s in series.values())
        if newest <= self._judged.get(req.rule_id, float("-inf")):
            logging.debug(f"No new counter samples for {req.rule_id} since the last poll")
            return
        self._judged[req.rule_id] = newest

        # Data leaves the source switch and enters the destination's; the counter
        # that carried the most is that direction, the other only sees ACKs.
        busiest = max(series.values(), key=lambda s: s.total)
        throughput_mbps = counter_rate_mbps(busiest.samples)
        if throughput_mbps is None:
            # A single reading says nothing about rate.
            req.set_prometheus_metrics(bytes_transferred=busiest.total, session=session)
            return

        req.set_prometheus_metrics(throughput=throughput_mbps, bytes_transferred=busiest.total, session=session)
        req.set_health(self._health(req, throughput_mbps), session=session)

    def _fetch(self, req, ends, now):
        """
        The circuit's counters since it came up, with whatever is new merged in.

        The first read reaches back to provisioning; later ones only as far as the
        newest sample already held. Returns the series that have samples, or None
        when Prometheus refused a query.
        """
        series = self._series.setdefault(req.rule_id, {})
        # A modify can move the circuit to another VLAN; the old one's counters
        # stop describing it.
        for key in [key for key in series if key[:2] not in ends]:
            del series[key]

        provisioned = None
        if req.sense_provisioned_at is not None:
            provisioned = req.sense_provisioned_at.replace(tzinfo=timezone.utc).timestamp()
        newest = max((s.samples[-1][0] for s in series.values() if s.samples), default=None)
        since = newest if newest is not None else provisioned
        lookback = max(now - since, self.window_seconds) if since is not None else self.window_seconds
        lookback = min(lookback + _LOOKBACK_MARGIN_SECONDS, _MAX_LOOKBACK_SECONDS)

        for site, vlan in ends:
            fetched = self.prometheus.get_circuit_samples(site, vlan, lookback)
            if fetched is None:
                return None
            for (hostname, descr, counter), samples in fetched.items():
                held = series.setdefault((site, vlan, hostname, descr, counter), _Series())
                if held.samples:
                    fresh = [s for s in samples if s[0] > held.samples[-1][0]]
                else:
                    # VLAN tags are reused, so a counter's history before this
                    # circuit came up belongs to whichever circuit had the tag then.
                    fresh = [s for s in samples if provisioned is None or s[0] >= provisioned]
                held.add(fresh, self.window_seconds)

        return {key: s for key, s in series.items() if s.samples}

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
