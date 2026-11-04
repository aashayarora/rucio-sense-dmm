import json
import re
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

    def get_path_samples(self, hops, lookback_seconds, at_time=None):
        """
        Raw octet counters for a circuit's VLAN on every switch along its path.

        SiteRM scrapes every switch it manages over SNMP and exports each VLAN
        interface as interface_statistics{Key="ifHCInOctets"|"ifHCOutOctets"}. A
        circuit has a VLAN to itself on each switch, so unlike a host interface or
        a trunk port this counts the circuit's traffic and nothing else - where it
        counts at all: on many switches the VLAN interface only sees routed
        traffic, and reads zero for a circuit bridged through it. Any hop whose
        counter does count measures the whole circuit, since the same traffic
        crosses them all.

        Each hop is {"site", "host", "vlan"}, with host None for any switch at the
        site. One query covers them all; what comes back for a site, host and VLAN
        that aren't on the path together is dropped.

        Returns {(sitename, hostname, ifDescr, Key): [(timestamp, bytes), ...]}
        with samples oldest first - sample timestamps, not the time of this call,
        because SiteRM scrapes on its own schedule - or None when Prometheus
        refuses the query.
        """
        if not hops:
            return {}
        sites = sorted({hop["site"] for hop in hops})
        hosts = sorted({hop["host"] for hop in hops if hop["host"] is not None})
        vlans = sorted({int(hop["vlan"]) for hop in hops})
        matchers = [f'sitename=~"{self._alternation(sites)}"']
        if all(hop["host"] is not None for hop in hops):
            matchers.append(f'hostname=~"{self._alternation(hosts)}"')
        # Switches spell a VLAN interface "Vlan3611", "vlan3611", "Vlan 3611", or
        # as a sub-interface of the port it rides, "Ethernet1/1.3611".
        vlan_descr = f"(?i)(vlan[ .]?|.+\\.)({'|'.join(str(vlan) for vlan in vlans)})"
        matchers.append(f'ifDescr=~"{self._escape_label_value(vlan_descr)}"')
        matchers.append('Key=~"ifHC(In|Out)Octets"')
        params = {"query": f"interface_statistics{{{','.join(matchers)}}}[{max(int(lookback_seconds), 1)}s]"}
        if at_time is not None:
            params["time"] = at_time
        response = self.submit_query(params)
        if response.get("status") != "success":
            logging.warning(f"Counter query for VLANs {vlans} at {sites} failed: {response.get('error', response.get('status'))}")
            return None

        series = {}
        for result in response.get("data", {}).get("result", []):
            labels = result.get("metric", {})
            site, host, descr = labels.get("sitename"), labels.get("hostname"), labels.get("ifDescr")
            if not on_path(hops, site, host, descr_vlan(descr)):
                continue
            samples = []
            for timestamp, value in result.get("values", []):
                try:
                    samples.append((float(timestamp), float(value)))
                except (TypeError, ValueError):
                    continue
            if samples:
                series[(site, host, descr, labels.get("Key"))] = sorted(samples)
        return series

    @classmethod
    def _alternation(cls, values):
        """A regex label value matching exactly one of values."""
        return cls._escape_label_value("|".join(re.escape(str(value)) for value in values))


def descr_vlan(descr):
    """The VLAN tag an interface name ends in, or None."""
    match = re.search(r"(\d+)$", str(descr or ""))
    return int(match.group(1)) if match else None


def on_path(hops, site, host, vlan):
    """Whether a counter on host at site for vlan belongs to one of the hops."""
    return any(
        hop["site"] == site and hop["host"] in (None, host) and int(hop["vlan"]) == vlan
        for hop in hops
    )


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
    """
    A hop for each end of the request's circuit whose VLAN is known, on whichever
    of the site's switches has it.
    """
    ends = []
    for site, vlan in ((req.src_site, req.src_vlan), (req.dst_site, req.dst_vlan)):
        if site is not None and vlan is not None:
            ends.append({"site": site.name, "host": None, "vlan": int(vlan)})
    return ends
