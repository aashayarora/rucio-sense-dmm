import logging
import ipaddress

from dmm.daemons.base import DaemonBase

from dmm.db.request import Request
from dmm.db.endpoint import Endpoint
from dmm.db.site import Site
from dmm.db.session import databased

from dmm.utils.sense import SENSEUtils

class AllocatorDaemon(DaemonBase, SENSEUtils):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        SENSEUtils.__init__(self)
        
    @databased
    def process(self, session=None):
        reqs_init = Request.from_status(status=["INIT"], session=session)
        if len(reqs_init) == 0:
            return
        for new_request in reqs_init:  
            reqs_finished = Request.from_status(status=["FINISHED"], session=session)
            for req_fin in reqs_finished:
                if (req_fin.src_site == new_request.src_site and req_fin.dst_site == new_request.dst_site):
                    logging.debug(f"Request {new_request.rule_id} found a finished request {req_fin.rule_id} with same endpoints, reusing ipv6 blocks and urls.")
                    new_request.update({
                        "src_endpoint": req_fin.src_endpoint,
                        "dst_endpoint": req_fin.dst_endpoint,
                        "transfer_status": "ALLOCATED"
                    })
                    req_fin.mark_as(status="DELETED", session=session)
                    break
            else:
                logging.debug(f"Request {new_request.rule_id} did not find a finished request with same endpoints, allocating new ipv6 blocks and urls.")
                try:
                    if new_request.src_site is None or new_request.dst_site is None:
                        raise Exception("Could not find sites")
                    
                    free_src_ipv6 = self.get_allocation(new_request.src_site.name, new_request.rule_id)
                    free_dst_ipv6 = self.get_allocation(new_request.dst_site.name, new_request.rule_id)
                    free_src_ipv6 = ipaddress.IPv6Network(free_src_ipv6).compressed
                    free_dst_ipv6 = ipaddress.IPv6Network(free_dst_ipv6).compressed

                    src_endpoint = Endpoint.for_rule(site_name=new_request.src_site.name, ip_block=free_src_ipv6, session=session)
                    dst_endpoint = Endpoint.for_rule(site_name=new_request.dst_site.name, ip_block=free_dst_ipv6, session=session)

                    if src_endpoint is None or dst_endpoint is None:
                        raise Exception("Could not find endpoints given by SENSE-O address pool in the database")    
                    
                    logging.debug(f"Got ipv6 blocks {src_endpoint.ip_block} and {dst_endpoint.ip_block} and urls {src_endpoint.hostname} and {dst_endpoint.hostname} for request {new_request.rule_id}")
                
                    new_request.update({
                        "src_endpoint": src_endpoint,
                        "dst_endpoint": dst_endpoint,
                        "transfer_status": "ALLOCATED"
                    })

                    src_endpoint.mark_inuse(in_use=True, session=session)
                    dst_endpoint.mark_inuse(in_use=True, session=session)

                except Exception as e:
                    self.free_allocation(new_request.src_site.name, new_request.rule_id)
                    self.free_allocation(new_request.dst_site.name, new_request.rule_id)
                    logging.error(e)