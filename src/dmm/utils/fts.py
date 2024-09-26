import logging
import json
import requests
import urllib

from dmm.utils.config import config_get

class FTSUtils:
    def __init__(self):
        self.fts_host = config_get("fts", "fts_host")
        self.cert = (config_get("fts", "cert"), config_get("fts", "key"))
        self.capath = "/etc/grid-security/certificates/"
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    def modify_link_config(self, req, max_active, min_active):
        src_url_no_port = "davs://" + req.src_endpoint.hostname.split(":")[0]
        dst_url_no_port = "davs://" + req.dst_endpoint.hostname.split(":")[0]

        data = {
            "symbolicname": "-".join([src_url_no_port, dst_url_no_port]),
            "source": src_url_no_port,
            "destination": dst_url_no_port,
            "max_active": max_active,
            "min_active": min_active,
            "nostreams": 0,
            "optimizer_mode": 0,
            "no_delegation": False,
            "tcp_buffer_size": 0
        }
        
        data = json.dumps(data)
        try:
            response = requests.post(self.fts_host + "/config/links", headers=self.headers, cert=self.cert, verify=self.capath, data=data)
            logging.info(f"FTS link config modified, response: {response.text}")
            return (response.status_code == 200 or response.status_code == 201)
        except:
            logging.exception("Error while modifying FTS link config")
            return None
        
    def modify_se_config(self, req, max_inbound, max_outbound):
        src_url_no_port = "davs://" + req.src_endpoint.hostname.split(":")[0]
        dst_url_no_port = "davs://" + req.dst_endpoint.hostname.split(":")[0]

        data = {
            src_url_no_port: {
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
            dst_url_no_port: {
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
        }
        try:
            data = json.dumps(data)
            response = requests.post(self.fts_host + "/config/se", headers=self.headers, cert=self.cert, verify=self.capath, data=data)
            logging.info(f"FTS storage config modified, response: {response.text}")
            return (response.status_code == 200 or response.status_code == 201)
        except: 
            logging.exception("Error while modifying FTS storage config")
            return None
    
    def delete_config(self, req):
        src_url_no_port = "davs://" + req.src_endpoint.hostname.split(":")[0]
        dst_url_no_port = "davs://" + req.dst_endpoint.hostname.split(":")[0]
        try:
            response_link = requests.delete(self.fts_host + "/config/links/" + urllib.parse.quote("-".join([src_url_no_port, dst_url_no_port]), safe=""), headers=self.headers, cert=self.cert, verify=False)
            return (response_link.status_code == 200 or response_link.status_code == 201 or response_link.status_code == 204)
        except:
            logging.exception("Error while deleting FTS configs")
            return None
        