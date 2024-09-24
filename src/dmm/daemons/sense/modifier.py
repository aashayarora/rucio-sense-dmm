import logging
import re
import json

from dmm.daemons.base import DaemonBase

from dmm.db.session import databased
from dmm.db.request import Request
from dmm.db.site import Site
from dmm.db.mesh import Mesh

from dmm.utils.sense import SENSEUtils

from sense.client.workflow_combined_api import WorkflowCombinedApi

class SENSEModifierDaemon(DaemonBase, SENSEUtils):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        SENSEUtils.__init__(self)
        
    @databased
    def process(self, debug_mode=False, session=None):
        reqs_stale = Request.from_status(status=["STALE"], session=session)
        if reqs_stale == []:
            return
        for req in reqs_stale:
            try:
                vlan_range = Mesh.vlan_range(site_1=req.src_site, site_2=req.dst_site, session=session)
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
                                {"data.connections[0].terminals[0].uri": Site.from_name(name=req.src_site, attr="sense_uri", session=session)},
                                {"data.connections[0].terminals[0].ipv6_prefix_list": req.src_ipv6_block},
                                {"data.connections[0].terminals[1].uri": Site.from_name(name=req.dst_site, attr="sense_uri", session=session)},
                                {"data.connections[0].terminals[1].ipv6_prefix_list": req.dst_ipv6_block},
                                {"data.connections[0].terminals[0].vlan_tag": vlan_range},
                                {"data.connections[0].terminals[1].vlan_tag": vlan_range}
                            ]
                        }
                    ],
                    "alias": req.rule_id
                }
                response = workflow_api.instance_modify(json.dumps(intent), sync="true")
                req.mark_as(status="PROVISIONED", session=session)
            except Exception as e:
                logging.error(f"Failed to modify link for {req.rule_id}, {e}, will try again")
            