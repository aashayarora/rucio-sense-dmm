import logging
import re
import json

from dmm.daemons.base import DaemonBase

from dmm.db.session import databased
from dmm.models.request import Request
from dmm.models.site import Site
from dmm.models.mesh import Mesh

from dmm.core.config import config_get

from sense.client.workflow_combined_api import WorkflowCombinedApi

class SENSEModifierDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        self.profile_uuid = config_get("sense", "profile_uuid")
        
    def process(self, **kwargs):
        self.run_once(**kwargs)

    @databased
    def run_once(self, session=None):
        reqs_stale = Request.from_status(status=["STALE"], session=session)
        if reqs_stale == []:
            return
        for req in reqs_stale:
            if req.sense_uuid is None:
                continue
            try:
                vlan_range = Mesh.get_vlan_range(site_1=req.src_site, site_2=req.dst_site, session=session)
                workflow_api = WorkflowCombinedApi()
                workflow_api.si_uuid = req.sense_uuid
                status = req.sense_circuit_status
                if not re.match(r"(CREATE|MODIFY|REINSTATE) - READY$", status):
                    raise ValueError(f"Cannot cancel an instance in status '{status}', will try to cancel again")
                intent = {
                    "service_profile_uuid": self.profile_uuid,
                    "queries": [
                        {
                            "ask": "edit",
                            "options": [
                                {"data.connections[0].bandwidth.capacity": str(int(req.bandwidth))},
                                {"data.connections[0].terminals[0].uri": Site.from_name(name=req.src_site.name, session=session).sense_uri},
                                {"data.connections[0].terminals[0].ipv6_prefix_list": req.src_endpoint.ip_range},
                                {"data.connections[0].terminals[1].uri": Site.from_name(name=req.dst_site.name, session=session).sense_uri},
                                {"data.connections[0].terminals[1].ipv6_prefix_list": req.dst_endpoint.ip_range},
                                {"data.connections[0].terminals[0].vlan_tag": vlan_range},
                                {"data.connections[0].terminals[1].vlan_tag": vlan_range}
                            ]
                        }
                    ],
                    "alias": req.rule_id
                }
                response = workflow_api.instance_modify(json.dumps(intent), sync="true")
                req.update_transfer_status(status="PROVISIONED", session=session)
            except Exception as e:
                logging.error(f"Failed to modify link for {req.rule_id}, {e}, will try again")
            