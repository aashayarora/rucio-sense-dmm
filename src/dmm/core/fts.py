import logging
import json
import requests
import urllib

from dmm.core.config import config_get

class FTSClient:
    def __init__(self):
        self.fts_host = config_get("fts", "fts_host")
        self.cert = (config_get("fts", "cert"), config_get("fts", "key"))
        self.capath = "/etc/grid-security/certificates/"
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
    
    def _send_post(self, endpoint, data):
        logging.debug(f"FTS POST {self.fts_host}{endpoint}: {data}")
        try:
            response = requests.post(
                self.fts_host + endpoint,
                headers=self.headers,
                cert=self.cert,
                verify=self.capath,
                data=data,
                timeout=15,
            )
            logging.debug(f"FTS POST {endpoint} response {response.status_code}: {response.text}")
            success = response.status_code in [200, 201]
            if success:
                logging.info(f"FTS config modified successfully for {endpoint}")
            else:
                logging.warning(f"FTS config modification returned status {response.status_code}: {response.text}")
            return success
        except requests.RequestException as e:
            logging.error(f"Error while modifying FTS config for {endpoint}: {e}", exc_info=True)
            return False
    
    def _send_delete(self, endpoint, params=None):
        logging.debug(f"FTS DELETE {self.fts_host}{endpoint} params={params}")
        try:
            response = requests.delete(
                self.fts_host + endpoint,
                params=params,
                headers=self.headers,
                cert=self.cert,
                verify=self.capath,
                timeout=15,
            )
            logging.debug(f"FTS DELETE {endpoint} params={params} response {response.status_code}: {response.text}")
            success = response.status_code in [200, 201, 204]
            if not success:
                logging.warning(f"FTS deletion returned status {response.status_code}: {response.text}")
            return success
        except requests.RequestException as e:
            logging.error(f"Error while deleting FTS config for {endpoint}: {e}", exc_info=True)
            return False


def get_endpoint_urls(src_endpoint, dst_endpoint):
    src_url_no_port = src_endpoint.protocol + "://" + src_endpoint.hostname.split(":")[0]
    dst_url_no_port = dst_endpoint.protocol + "://" + dst_endpoint.hostname.split(":")[0]
    return src_url_no_port, dst_url_no_port


def build_link_config_data(src_url, dst_url, max_active, min_active):
    return json.dumps({
        "symbolicname": "-".join([src_url, dst_url]),
        "source": src_url,
        "destination": dst_url,
        "max_active": max_active,
        "min_active": min_active,
        "nostreams": 0,
        "optimizer_mode": 0,
        "no_delegation": False,
        "tcp_buffer_size": 0
    })


def build_se_config_data(src_url, dst_url, max_inbound, max_outbound):
    return json.dumps({
        src_url: {
            "se_info": {
                "inbound_max_active": None,
                "inbound_max_throughput": None,
                "outbound_max_active": max_outbound,
                "outbound_max_throughput": None,
                "udt": None,
                "ipv6": None,
                "se_metadata": None,
                "site": None,
                "debug_level": None,
                "eviction": None
            }
        },
        dst_url: {
            "se_info": {
                "inbound_max_active": max_inbound,
                "inbound_max_throughput": None,
                "outbound_max_active": None,
                "outbound_max_throughput": None,
                "udt": None,
                "ipv6": None,
                "se_metadata": None,
                "site": None,
                "debug_level": None,
                "eviction": None
            }
        }
    })


def modify_link_config(src_endpoint, dst_endpoint, max_active, min_active):
    client = FTSClient()
    src_url, dst_url = get_endpoint_urls(src_endpoint, dst_endpoint)
    data = build_link_config_data(src_url, dst_url, max_active, min_active)
    return client._send_post("/config/links", data)


def delete_link_config(src_endpoint, dst_endpoint):
    client = FTSClient()
    src_url, dst_url = get_endpoint_urls(src_endpoint, dst_endpoint)
    # Encoded twice: the httpd in front of FTS returns 404 for any %2F in the
    # path, and FTS unquotes the name itself.
    link_name = urllib.parse.quote("-".join([src_url, dst_url]), safe="")
    return client._send_delete(f"/config/links/{urllib.parse.quote(link_name, safe='')}")


def modify_se_config(src_endpoint, dst_endpoint, max_inbound, max_outbound):
    client = FTSClient()
    src_url, dst_url = get_endpoint_urls(src_endpoint, dst_endpoint)
    data = build_se_config_data(src_url, dst_url, max_inbound, max_outbound)
    return client._send_post("/config/se", data)


def delete_se_config(src_endpoint, dst_endpoint):
    client = FTSClient()
    src_url, dst_url = get_endpoint_urls(src_endpoint, dst_endpoint)

    # FTS takes the SE as a query parameter, not a path segment.
    success_src = client._send_delete("/config/se", params={"se": src_url})
    success_dst = client._send_delete("/config/se", params={"se": dst_url})

    return success_src and success_dst


def modify_fts_config(src_endpoint, dst_endpoint, streams):
    link_modified = modify_link_config(src_endpoint, dst_endpoint, max_active=streams, min_active=streams)
    se_modified = modify_se_config(src_endpoint, dst_endpoint, max_inbound=streams, max_outbound=streams)
    return link_modified and se_modified


def delete_fts_config(src_endpoint, dst_endpoint):
    link_deleted = delete_link_config(src_endpoint, dst_endpoint)
    se_deleted = delete_se_config(src_endpoint, dst_endpoint)
    return link_deleted and se_deleted
