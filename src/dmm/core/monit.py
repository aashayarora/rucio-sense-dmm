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


# Per-file fields pulled from the FTS monit index for the transfer audit.
_FTS_AUDIT_FIELDS = [
    "data.t_final_transfer_state",
    "data.tr_error_category",
    "data.tr_error_message",
    "data.file_size",
    "data.tr_timestamp_start",
    "data.tr_timestamp_complete",
]

# Elasticsearch refuses a plain search beyond this many hits.
_FTS_MAX_HITS = 10000


class FTSNotConfigured(RuntimeError):
    """The [fts] section has no monit host or token."""


class FTSMonitUtils:
    def __init__(self):
        self.fts_host = config_get("fts", "monit_host", default="").strip().rstrip("/")
        self.fts_token = config_get("fts", "monit_auth_token", default="").strip()
        if not self.fts_host or not self.fts_token:
            raise FTSNotConfigured("monit_host and monit_auth_token must both be set in [fts]")
        self.headers = {"Authorization": f"Bearer {self.fts_token}", "Content-Type": "application/json"}

    @staticmethod
    def get_val_from_response(response):
        return response["hits"]["hits"][0]["_source"]["data"]

    def submit_job_query(self, rule_id, query_params=None, size=_FTS_MAX_HITS) -> list:
        """
        Every FTS per-file record carrying this rule_id, as a list of data dicts.

        A rule can cover thousands of files, so this asks for the index's maximum in
        one shot and says so in the log when the rule is larger than that rather
        than silently auditing a slice of it.
        """
        if query_params is None:
            query_params = _FTS_AUDIT_FIELDS
        endpoint = "api/datasources/proxy/9233/monit_prod_fts_enr_complete*/_search"
        query_addr = f"{self.fts_host}/{endpoint}"
        data = {
            "size": min(size, _FTS_MAX_HITS),
            "track_total_hits": True,
            "query":{
                "bool":{
                    "filter":[{
                        "query_string": {
                            "analyze_wildcard": "true",
                            "query": f"data.file_metadata.rule_id:{rule_id}"
                        }
                    }]
                }
            },
            "_source": query_params
        }
        data_string = json.dumps(data)
        logging.debug(f"FTS Monit query for {rule_id} {query_addr}: {data_string}")
        try:
            response_obj = requests.get(query_addr, data=data_string, headers=self.headers, timeout=15)
            logging.debug(f"FTS Monit response for {rule_id} {response_obj.status_code}: {response_obj.text}")
            response_obj.raise_for_status()
            response = response_obj.json()
        except requests.RequestException as e:
            logging.error(f"FTS Monit request failed for rule {rule_id}: {e}", exc_info=True)
            raise
        except ValueError as e:
            logging.error(f"FTS Monit returned invalid JSON for rule {rule_id}: {e}", exc_info=True)
            raise

        hits = response.get("hits", {})
        records = [hit.get("_source", {}).get("data") for hit in hits.get("hits", []) if hit.get("_source", {}).get("data")]

        total = hits.get("total")
        if isinstance(total, dict):
            total = total.get("value")
        if isinstance(total, int) and total > len(records):
            logging.warning(
                f"FTS reports {total} records for rule {rule_id} but only {len(records)} "
                "were returned - the audit covers a sample, not the whole rule"
            )
        return records

    @staticmethod
    def normalize_timestamp(value):
        """
        FTS monit timestamps are epoch milliseconds; return seconds.

        Values are sanity-checked rather than trusted, because a field that is
        already in seconds would otherwise land 50000 years in the past and drag
        the audit window with it.
        """
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        # Anything past ~2001 in seconds is past ~1970 in milliseconds; the split is
        # unambiguous for any timestamp this system will ever see.
        return value / 1000 if value > 1e11 else value