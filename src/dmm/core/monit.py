import json
import requests
import logging

from dmm.core.config import config_get

# Helper functions


class PrometheusNotConfigured(RuntimeError):
    """The [prometheus] section is missing or carries no host."""


class PrometheusUtils:
    def __init__(self):
        self.prometheus_host = config_get("prometheus", "host", default="").strip().rstrip("/")
        self.prometheus_user = config_get("prometheus", "user", default="")
        self.prometheus_pass = config_get("prometheus", "password", default="")
        if not self.prometheus_host:
            raise PrometheusNotConfigured("no host set in the [prometheus] section")

    @property
    def _auth(self):
        # Anonymous Prometheus deployments are common, and sending ("", "") turns
        # into an empty Basic header that some proxies reject outright.
        return (self.prometheus_user, self.prometheus_pass) if self.prometheus_user else None

    def submit_query(self, query_dict) -> dict:
        endpoint = "api/v1/query"
        query_addr = f"{self.prometheus_host}/{endpoint}"
        logging.debug(f"Prometheus query {query_addr}: {json.dumps(query_dict)}")
        try:
            response = requests.get(
                query_addr,
                params=query_dict,
                auth=self._auth,
                timeout=15,
            )
            logging.debug(f"Prometheus response {response.status_code}: {response.text}")
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logging.error(f"Prometheus query request failed for {query_addr}: {e}", exc_info=True)
            raise
        except ValueError as e:
            logging.error(f"Prometheus query returned invalid JSON for {query_addr}: {e}", exc_info=True)
            raise

    @staticmethod
    def _escape_label_value(value) -> str:
        return str(value).replace("\\", "\\\\").replace('"', '\\"')

    def get_circuit_samples(self, site, vlan, lookback_seconds, at_time=None):
        """
        Raw octet counters for one circuit VLAN on a site's switches.

        SiteRM scrapes every switch it manages over SNMP and exports each VLAN
        interface as interface_statistics{Key="ifHCInOctets"|"ifHCOutOctets"}. A
        circuit has a VLAN to itself, so unlike a host interface this counts the
        circuit's traffic and nothing else. Every switch at the site that reports
        the VLAN is returned; which direction carries the data depends on which end
        of the circuit the site is.

        Returns {(hostname, ifDescr, Key): [(timestamp, bytes), ...]} with samples
        oldest first - sample timestamps, not the time of this call, because SiteRM
        scrapes on its own schedule - or None when Prometheus refuses the query.
        """
        selector = (
            f'interface_statistics{{sitename="{self._escape_label_value(site)}",'
            f'ifDescr=~"(?i)vlan ?{int(vlan)}",Key=~"ifHC(In|Out)Octets"}}'
        )
        params = {"query": f"{selector}[{max(int(lookback_seconds), 1)}s]"}
        if at_time is not None:
            params["time"] = at_time
        response = self.submit_query(params)
        if response.get("status") != "success":
            logging.warning(f"Counter query for VLAN {vlan} at {site} failed: {response.get('error', response.get('status'))}")
            return None

        series = {}
        for result in response.get("data", {}).get("result", []):
            labels = result.get("metric", {})
            key = (labels.get("hostname"), labels.get("ifDescr"), labels.get("Key"))
            samples = []
            for timestamp, value in result.get("values", []):
                try:
                    samples.append((float(timestamp), float(value)))
                except (TypeError, ValueError):
                    continue
            if samples:
                series[key] = sorted(samples)
        return series


def counter_increase(samples):
    """
    Growth of a counter across consecutive samples.

    A drop means the counter restarted (switch reboot, SiteRM reset), so the value
    after it counts from zero - the same rule PromQL's increase() uses, without
    its extrapolation past the first and last sample.
    """
    total = 0.0
    for (_, previous), (_, current) in zip(samples, samples[1:]):
        total += current - previous if current >= previous else current
    return total


def counter_rate_mbps(samples):
    """Average rate between the first and last sample in decimal Mbps, or None."""
    if len(samples) < 2:
        return None
    elapsed = samples[-1][0] - samples[0][0]
    if elapsed <= 0:
        return None
    # Counters are bytes; bandwidth everywhere else in DMM is decimal Mbps.
    return round(counter_increase(samples) * 8 / 1e6 / elapsed, 2)


def circuit_ends(req):
    """(site name, VLAN) for each end of the request's circuit whose VLAN is known."""
    ends = []
    for site, vlan in ((req.src_site, req.src_vlan), (req.dst_site, req.dst_vlan)):
        if site is not None and vlan is not None:
            ends.append((site.name, int(vlan)))
    return ends
