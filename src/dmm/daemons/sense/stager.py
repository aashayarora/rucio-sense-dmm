import logging
import json

from dmm.daemons.base import DaemonBase

from dmm.db.session import databased
from dmm.models.request import Request

from dmm.core.config import config_get

from dmm.models.site import Site
from dmm.models.mesh import Mesh

from sense.client.workflow_combined_api import WorkflowCombinedApi

class SENSEStagerDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        self.profile_uuid = config_get("sense", "profile_uuid")
        
    @databased
    def process(self, session=None):
        reqs_allocated = Request.from_status(status=["ALLOCATED"], session=session)
        if reqs_allocated == []:
            return
        for req in reqs_allocated:
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
                                {"data.connections[0].terminals[0].uri": Site.from_name(name=req.src_site.name, attr="sense_uri", session=session)},
                                {"data.connections[0].terminals[0].ipv6_prefix_list": req.src_endpoint.ip_range},
                                {"data.connections[0].terminals[1].uri": Site.from_name(name=req.dst_site.name, attr="sense_uri", session=session)},
                                {"data.connections[0].terminals[1].ipv6_prefix_list": req.dst_endpoint.ip_range},
                                {"data.connections[0].terminals[0].vlan_tag": vlan_range},
                                {"data.connections[0].terminals[1].vlan_tag": vlan_range}
                            ]
                        }, {
                        "ask": "total-block-maximum-bandwidth",
                        "options": [
                            {
                                "name": "Connection 1",
                                "start": "now",
                                "end-before": "+24h"
                            }
                        ]
                        }
                    ],
                    "alias": req.rule_id
                }
                response = workflow_api.instance_create(json.dumps(intent))
                if not self._good_response(response):
                    raise ValueError(f"SENSE req staging failed for {req.rule_id}")
                logging.debug(f"Staging returned response {response}")
                sense_uuid = response["service_uuid"]
                req.update_sense_uuid(sense_uuid, session=session)
                available_bandwidth = int(response.get("queries")[1].get("results")[0].get("bandwidth")) / 1000 ** 2
                req.update_available_bandwidth(available_bandwidth, session=session)
                req.mark_as(status="STAGED", session=session)
            except Exception as e:
                logging.error(f"Failed to stage link for {req.rule_id}, {e}, will try again")
    
    @staticmethod
    def _good_response(response):
        return bool(response and not any("ERROR" in r for r in response))