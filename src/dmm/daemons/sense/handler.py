import logging
from datetime import datetime, timezone
import json
import ipaddress
import re

from dmm.core.config import config_get_int
from dmm.daemons.base import DaemonBase
from dmm.models.request import Request
from dmm.db.session import databased

from sense.client.workflow_combined_api import WorkflowCombinedApi
from sense.client.address_api import AddressApi

class SENSEHandlerDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        
    def process(self, **kwargs):
        self.run_once(**kwargs)

    @databased
    def run_once(self, session=None):
        reqs = Request.from_status(status=["STAGED", "PROVISIONED", "CANCELED", "STALE", "DECIDED", "FINISHED"], session=session)
        if not reqs:
            return
        
        for req in reqs:
            if req.sense_uuid is None:
                continue

            workflow_api = WorkflowCombinedApi()

            status = workflow_api.instance_get_status(si_uuid=req.sense_uuid) or "UNKNOWN"
            req.update_sense_circuit_status(status=status, session=session)

            # update sense_provisioned_at if the status is READY for monit
            if req.sense_provisioned_at is None and re.match(r"(CREATE|MODIFY|REINSTATE) - READY$", status):
                req.update({"sense_provisioned_at": datetime.now()})

                self._affiliate_endpoints(req, workflow_api)

                fts_limit = config_get_int("fts-streams", f"{req.src_site.name}-{req.dst_site.name}", 200)
                req.update_fts_limit_desired(limit=fts_limit, session=session)

            # TODO: if sense creation fails, should retry
            # at staging step, i.e. before create - committed: 
                # reasons could be failed vlan tag regex, in that case, maybe retry with default vlan tag - mark as allocated and let stager run again
            # at provisioned step, i.e. after create - committed:
                # should put in allocated state, so vlan allocation can be retried
            # at finished step, i.e. after cancel - committed: should force retry

    def _affiliate_endpoints(self, req, workflow_api):
        # Affiliate endpoints with this instance
        manifest_json = {
            "AnyNewVlanXC": "?subnet?",
            "sparql": "SELECT ?subnet WHERE { ?svc mrs:providesSubnet ?subnet } LIMIT 1",
            "required": "true"
        }

        uri_response = workflow_api.manifest_create(json.dumps(manifest_json), si_uuid=req.sense_uuid)
        if not self._good_response(uri_response):
            logging.error(f"Failed to affiliate endpoints with SENSE instance {req.sense_uuid}") 

        addapi_uri = json.loads(uri_response["jsonTemplate"])["AnyNewVlanXC"]

        address_api = AddressApi()

        src_pool_name = f'RUCIO_Site_BGP_Subnet_Pool-{req.src_site.name}'
        exploded_source_ip = self.format_ipv6(ipaddress.IPv6Network(req.src_endpoint.ip_range))
        logging.debug(f"Affiliating source endpoint {exploded_source_ip} with SENSE instance {req.sense_uuid} in address pool {src_pool_name}")
        address_api.affiliate_address(pool=src_pool_name, uri=addapi_uri, address=exploded_source_ip)

        dst_pool_name = f'RUCIO_Site_BGP_Subnet_Pool-{req.dst_site.name}'
        exploded_dest_ip = self.format_ipv6(ipaddress.IPv6Network(req.dst_endpoint.ip_range))
        logging.debug(f"Affiliating destination endpoint {exploded_dest_ip} with SENSE instance {req.sense_uuid} in address pool {dst_pool_name}")
        address_api.affiliate_address(pool=dst_pool_name, uri=addapi_uri, address=exploded_dest_ip)
    
    @staticmethod
    def _good_response(response):
        return bool(response and not any("error" in r for r in response))

    @staticmethod
    def format_ipv6(ip):
        exploded_ip = ip.network_address.exploded
        segments = exploded_ip.split(":")
        simplified_segments = []
        for segment in segments:
            if segment == "0000":
                simplified_segments.append("0")
            else:
                simplified_segments.append(segment.lstrip("0") or "0")
        return f"{':'.join(simplified_segments)}/{ip.prefixlen}"
