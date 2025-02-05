import re
import logging
import json
import requests
import urllib

from dmm.utils.config import config_get

from dmm.db.session import databased
from dmm.db.request import Request

from dmm.daemons.base import DaemonBase

class FTSModifierDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        self.fts_host = config_get("fts", "fts_host")
        self.cert = (config_get("fts", "cert"), config_get("fts", "key"))
        self.capath = "/etc/grid-security/certificates/"
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

    @databased
    def process(self, session=None):
        self._process_requests(session, ["ALLOCATED", "DECIDED", "PROVISIONED"], self._modify_request)
        self._process_requests(session, ["DELETED"], self._delete_request)

    def _process_requests(self, session, statuses, action):
        reqs = Request.from_status(status=statuses, session=session)
        if reqs:
            for req in reqs:
                action(req, session)

    def _modify_request(self, req, session):
        if req.fts_limit_current != req.fts_limit_desired:
            logging.debug(f"Modifying FTS limits for request {req.rule_id}, from {req.fts_limit_current} to {req.fts_limit_desired}")
            link_modified = self._modify_link_config(req, max_active=req.fts_limit_desired, min_active=req.fts_limit_desired)
            se_modified = self._modify_se_config(req, max_inbound=req.fts_limit_desired, max_outbound=req.fts_limit_desired)
            if link_modified and se_modified:
                req.update_fts_limit_current(limit=req.fts_limit_desired, session=session)

    def _delete_request(self, req, session):
        if req.fts_limit_current != 0:
            logging.debug(f"Deleting FTS limits for request {req.rule_id}")
            self._delete_config(req)
            req.update_fts_limit_current(limit=0, session=session)

    def _modify_link_config(self, req, max_active, min_active):
        data = self._prepare_link_data(req, max_active, min_active)
        return self._send_request("/config/links", data)

    def _modify_se_config(self, req, max_inbound, max_outbound):
        data = self._prepare_se_data(req, max_inbound, max_outbound)
        return self._send_request("/config/se", data)

    def _delete_config(self, req):
        src_url_no_port, dst_url_no_port = self._get_endpoints(req)
        try:
            response_link = requests.delete(
                self.fts_host + "/config/links/" + urllib.parse.quote("-".join([src_url_no_port, dst_url_no_port]), safe=""),
                headers=self.headers, cert=self.cert, verify=False
            )
            return response_link.status_code in [200, 201, 204]
        except:
            logging.exception("Error while deleting FTS configs")
            return None

    def _prepare_link_data(self, req, max_active, min_active):
        src_url_no_port, dst_url_no_port = self._get_endpoints(req)
        return json.dumps({
            "symbolicname": "-".join([src_url_no_port, dst_url_no_port]),
            "source": src_url_no_port,
            "destination": dst_url_no_port,
            "max_active": max_active,
            "min_active": min_active,
            "nostreams": 0,
            "optimizer_mode": 0,
            "no_delegation": False,
            "tcp_buffer_size": 0
        })

    def _prepare_se_data(self, req, max_inbound, max_outbound):
        src_url_no_port, dst_url_no_port = self._get_endpoints(req)
        return json.dumps({
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
        })

    def _send_request(self, endpoint, data):
        try:
            response = requests.post(
                self.fts_host + endpoint, headers=self.headers, cert=self.cert, verify=self.capath, data=data
            )
            logging.info(f"FTS config modified, response: {response.text}")
            return response.status_code in [200, 201]
        except:
            logging.exception("Error while modifying FTS config")
            return None

    def _get_endpoints(self, req):
        src_url_no_port = "davs://" + req.src_endpoint.hostname.split(":")[0]
        dst_url_no_port = "davs://" + req.dst_endpoint.hostname.split(":")[0]
        return src_url_no_port, dst_url_no_port
