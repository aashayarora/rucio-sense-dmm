import logging
import json
import re

from dmm.daemons.base import DaemonBase
from dmm.db.session import databased

from dmm.models.request import Request
from dmm.models.site import Site
from dmm.models.mesh import Mesh

from dmm.core.config import config_get

from sense.client.workflow_combined_api import WorkflowCombinedApi

class SENSEProvisionerDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)

        self.profile_uuid = config_get("sense", "profile_uuid")
        
    @databased
    def process(self, session=None):
        # make sure there are no stale requests before provisioning any new ones
        reqs_stale = Request.from_status(status=["STALE"], session=session)
        if reqs_stale != []:
            return
        reqs_decided = Request.from_status(status=["DECIDED"], session=session)
        if reqs_decided == []:
            return
        for req in reqs_decided:
            if req.sense_uuid is None:
                continue
            try:
                vlan_range = Mesh.vlan_range(site_1=req.src_site, site_2=req.dst_site, session=session)
                workflow_api = WorkflowCombinedApi()
                workflow_api.si_uuid = req.sense_uuid
                status = req.sense_circuit_status
                if re.match(r"(CREATE) - READY$", status):
                    logging.debug(f"Request {req.sense_uuid} already in ready status, marking as provisioned")
                    req.mark_as(status="PROVISIONED", session=session)
                    continue
                if not re.match(r"(CREATE) - COMPILED$", status):
                    logging.debug(f"Request {req.sense_uuid} not in compiled status, will try to provision again")
                    raise AssertionError(f"Request {req.sense_uuid} not in compiled status, will try to provision again")
                intent = {
                    "service_profile_uuid": self.profile_uuid,
                    "queries": [
                        {
                            "ask": "edit",
                            "options": [
                                {"data.connections[0].bandwidth.capacity": str(int(req.bandwidth))},
                                {"data.connections[0].terminals[0].uri": Site.from_name(name=req.src_site.name, attr="sense_uri", session=session)},
                                {"data.connections[0].terminals[0].ipv6_prefix_list": req.src_endpoint.ip_range},
                                {"data.connections[0].terminals[1].uri": Site.from_name(name=req.dst_site.name, attr="sense_uri", session=session)},
                                {"data.connections[0].terminals[1].ipv6_prefix_list": req.dst_endpoint.ip_range},
                                {"data.connections[0].terminals[0].vlan_tag": vlan_range},
                                {"data.connections[0].terminals[1].vlan_tag": vlan_range}
                            ]
                        }
                    ],
                    "alias": req.rule_id
                }
                response = workflow_api.instance_create(json.dumps(intent))
                if not self._good_response(response):
                    raise ValueError(f"SENSE query failed for {req.rule_id}")
                workflow_api.instance_operate("provision", sync="true")
                req.mark_as(status="PROVISIONED", session=session)
            except Exception as e:
                logging.error(f"Failed to provision link for {req.rule_id}, {e}, will try again")
    
    @staticmethod
    def _good_response(response):
        return bool(response and not any("error" in r for r in response))