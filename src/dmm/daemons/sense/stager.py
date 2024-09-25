import logging
import json

from dmm.daemons.base import DaemonBase

from dmm.db.session import databased
from dmm.db.request import Request

from dmm.db.site import Site
from dmm.db.mesh import Mesh

from dmm.utils.sense import SENSEUtils

from sense.client.workflow_combined_api import WorkflowCombinedApi

class SENSEStagerDaemon(DaemonBase, SENSEUtils):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        SENSEUtils.__init__(self)
        
    @databased
    def process(self, debug_mode=False, session=None):
        reqs_init = Request.from_status(status=["ALLOCATED"], session=session)
        if reqs_init == []:
            return
        for req in reqs_init:
            try:
                vlan_range = Mesh.vlan_range(site_1=req.src_site, site_2=req.dst_site, session=session)
                workflow_api = WorkflowCombinedApi()
                workflow_api.instance_new()
                intent = {
                    "service_profile_uuid": self.profile_uuid,
                    "queries": [
                        {
                            "ask": "edit",
                            "options": [
                                {"data.connections[0].terminals[0].uri": Site.from_name(name=req.src_site, attr="sense_uri", session=session)},
                                {"data.connections[0].terminals[0].ipv6_prefix_list": req.src_ipv6_block},
                                {"data.connections[0].terminals[1].uri": Site.from_name(name=req.dst_site, attr="sense_uri", session=session)},
                                {"data.connections[0].terminals[1].ipv6_prefix_list": req.dst_ipv6_block},
                                {"data.connections[0].terminals[0].vlan_tag": vlan_range},
                                {"data.connections[0].terminals[1].vlan_tag": vlan_range}
                            ]
                        },
                        {"ask": "maximum-bandwidth", "options": [{"name": "Connection 1"}]}
                    ],
                    "alias": req.rule_id
                }
                response = workflow_api.instance_create(json.dumps(intent))
                if not self.good_response(response):
                    raise ValueError(f"SENSE req staging failed for {req.rule_id}")
                logging.debug(f"Staging returned response {response}")
                for query in response["queries"]:
                    if query["asked"] == "maximum-bandwidth":
                        result = query["results"][0]
                        if "bandwidth" not in result:
                            raise ValueError(f"SENSE query failed for {req.rule_id}")
                        sense_uuid, max_bandwidth = response["service_uuid"], float(result["bandwidth"])
                req.update({"sense_uuid": sense_uuid, "max_bandwidth": max_bandwidth})
                req.mark_as(status="STAGED", session=session)
            except Exception as e:
                logging.error(f"Failed to stage link for {req.rule_id}, {e}, will try again")