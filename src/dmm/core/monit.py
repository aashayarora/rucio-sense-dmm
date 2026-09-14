import ipaddress
import json
import requests
import logging

from dmm.core.config import config_get

# Helper functions

# node_exporter labels that together pin a series down to a single interface
# Not every deployment carries all of them, so only the ones actually present on
# a series are used - a label pinned to the string "None" matches nothing.
_INTERFACE_LABELS = ("device", "instance", "job", "sitename")

# How many leading hextets the PromQL prefilter is willing to anchor on.
_MAX_ANCHORED_HEXTETS = 4


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
        try:
            response = requests.get(
                query_addr,
                params=query_dict,
                auth=self._auth,
                timeout=15,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logging.error(f"Prometheus query request failed for {query_addr}: {e}", exc_info=True)
            raise
        except ValueError as e:
            logging.error(f"Prometheus query returned invalid JSON for {query_addr}: {e}", exc_info=True)
            raise

    @staticmethod
    def get_val_from_response(response):
        try:
            return response["data"]["result"][0]["value"][1]
        except (KeyError, IndexError, TypeError) as e:
            raise ValueError(f"Invalid Prometheus response format: {response}") from e

    @staticmethod
    def _escape_label_value(value) -> str:
        return str(value).replace("\\", "\\\\").replace('"', '\\"')

    @classmethod
    def selector_to_promql(cls, selector) -> str:
        return ",".join(f'{label}="{cls._escape_label_value(value)}"' for label, value in selector)

    @staticmethod
    def _prefilter_regex(network) -> str:
        """
        Cheap server-side filter for node_network_address_info.

        Only the leading non-zero hextets are safe to anchor on: RFC 5952 lets a run
        of zero hextets collapse to "::", so anything at or after the first zero
        hextet may simply not appear in the text form. Exact membership is decided
        in get_interfaces() with ipaddress - this only keeps the response small.
        """
        hextets = network.network_address.exploded.split(":")
        anchored = []
        for hextet in hextets[: min(network.prefixlen // 16, _MAX_ANCHORED_HEXTETS)]:
            if int(hextet, 16) == 0:
                break
            anchored.append(f"{int(hextet, 16):x}")
        if not anchored:
            return ".*"
        return "(?i)" + ":".join(anchored) + ":.*"

    def get_interfaces(self, ipv6, at_time=None) -> list:
        """
        Every node_exporter interface whose address falls inside the ipv6 network.

        Returns one label selector per interface - a tuple of (label, value) pairs
        built only from the labels the series actually carries.

        at_time resolves the interfaces as they were at a past instant, which is what
        an audit of a completed transfer needs: endpoints are freed and handed to the
        next request, so asking "now" would answer for whoever holds the block today.
        """
        try:
            network = ipaddress.IPv6Network(ipv6, strict=False)
        except ValueError:
            logging.error(f"Cannot monitor {ipv6!r}: not a valid IPv6 network")
            return []

        query = f'node_network_address_info{{address=~"{self._prefilter_regex(network)}"}}'
        params = {"query": query}
        if at_time is not None:
            params["time"] = at_time
        response = self.submit_query(params)
        if response.get("status") != "success":
            logging.warning(f"Interface lookup for {ipv6} failed: {response.get('error', response.get('status'))}")
            return []

        interfaces = []
        seen = set()
        for series in response.get("data", {}).get("result", []):
            labels = series.get("metric", {}) if isinstance(series, dict) else {}
            address = labels.get("address")
            if not address:
                continue
            try:
                if ipaddress.ip_address(address) not in network:
                    continue
            except ValueError:
                continue
            selector = tuple((label, labels[label]) for label in _INTERFACE_LABELS if labels.get(label))
            if not selector or selector in seen:
                continue
            seen.add(selector)
            interfaces.append(selector)

        if not interfaces:
            logging.warning(f"No node_exporter interface has an address inside {ipv6}")
        return interfaces

    def get_transmit_bytes(self, selector, at_time):
        """
        node_network_transmit_bytes_total for one interface at at_time, or None when
        Prometheus holds no sample for it.
        """
        metric = f"node_network_transmit_bytes_total{{{self.selector_to_promql(selector)}}}"
        response = self.submit_query({"query": metric, "time": at_time})
        if response.get("status") != "success" or not response.get("data", {}).get("result"):
            logging.warning(f"Query {metric} returned no data")
            return None
        try:
            return float(self.get_val_from_response(response))
        except (ValueError, TypeError) as e:
            logging.error(f"Could not read a counter value out of {metric}: {e}")
            return None


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
        try:
            response_obj = requests.get(query_addr, data=data_string, headers=self.headers, timeout=30)
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