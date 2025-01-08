import logging
import ipaddress

from dmm.daemons.base import DaemonBase

from dmm.db.request import Request
from dmm.db.endpoint import Endpoint

from dmm.db.session import databased

from sense.client.address_api import AddressApi

class AllocatorDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        
    @databased
    def process(self, session=None):
        reqs_init = Request.from_status(status=["INIT"], session=session)
        if not reqs_init:
            return
        
        for new_request in reqs_init:  
            if self._reuse_finished_request(new_request, session):
                continue
            
            self._allocate_new_endpoints(new_request, session)

    def _reuse_finished_request(self, new_request, session):
        reqs_finished = Request.from_status(status=["FINISHED"], session=session)
        for req_fin in reqs_finished:
            if req_fin.src_site == new_request.src_site and req_fin.dst_site == new_request.dst_site:
                logging.debug(f"Request {new_request.rule_id} found a finished request {req_fin.rule_id} with same endpoints, reusing ipv6 blocks and urls.")
                new_request.update({
                    "src_endpoint": req_fin.src_endpoint,
                    "dst_endpoint": req_fin.dst_endpoint,
                    "transfer_status": "ALLOCATED"
                })
                req_fin.mark_as(status="DELETED", session=session)
                return True
        return False

    def _allocate_new_endpoints(self, new_request, session):
        logging.debug(f"Request {new_request.rule_id} did not find a finished request with same endpoints, allocating new ipv6 blocks and urls.")
        try:
            if not new_request.src_site or not new_request.dst_site:
                raise ValueError("Could not find sites")
            
            free_src_ipv6 = self._get_allocation(new_request.src_site.name, new_request.rule_id)
            free_dst_ipv6 = self._get_allocation(new_request.dst_site.name, new_request.rule_id)
            free_src_ipv6 = ipaddress.IPv6Network(free_src_ipv6).compressed
            free_dst_ipv6 = ipaddress.IPv6Network(free_dst_ipv6).compressed

            src_endpoint = Endpoint.for_rule(site_name=new_request.src_site.name, ip_range=free_src_ipv6, session=session)
            dst_endpoint = Endpoint.for_rule(site_name=new_request.dst_site.name, ip_range=free_dst_ipv6, session=session)

            if not src_endpoint:
                raise ValueError(f"Could not find source endpoint {src_endpoint} given by SENSE-O address pool in the database")    
            if not dst_endpoint:
                raise ValueError(f"Could not find dest endpoint {dst_endpoint} given by SENSE-O address pool in the database")

            logging.debug(f"Got ipv6 ranges {src_endpoint.ip_range} and {dst_endpoint.ip_range} and urls {src_endpoint.hostname} and {dst_endpoint.hostname} for request {new_request.rule_id}")
        
            new_request.update({
                "src_endpoint": src_endpoint,
                "dst_endpoint": dst_endpoint,
                "transfer_status": "ALLOCATED"
            })

            src_endpoint.mark_inuse(in_use=True, session=session)
            dst_endpoint.mark_inuse(in_use=True, session=session)

        except Exception as e:
            self._free_allocation(new_request.src_site.name, new_request.rule_id)
            self._free_allocation(new_request.dst_site.name, new_request.rule_id)
            logging.error(e)

    def _get_allocation(self, sitename, alloc_name):
        addressApi = AddressApi()
        pool_name = f"RUCIO_Site_BGP_Subnet_Pool-{sitename}"
        alloc_type = "IPv6"
        try:
            logging.debug(f"Getting IPv6 allocation for {sitename}")
            response = addressApi.allocate_address(pool_name, alloc_type, alloc_name, netmask="/64", batch="subnet")
            logging.debug(f"Got allocation: {response} for {sitename}")
            return response
        except Exception as e:
            logging.error(f"get_allocation: {str(e)}")
            addressApi.free_address(pool_name, name=alloc_name)
            raise ValueError(f"Getting allocation failed for {sitename} and {alloc_name}")

    def _free_allocation(self, sitename, alloc_name):
        try:
            logging.debug(f"Freeing IPv6 allocation {alloc_name}")
            addressApi = AddressApi()
            pool_name = f'RUCIO_Site_BGP_Subnet_Pool-{sitename}'
            addressApi.free_address(pool_name, name=alloc_name)
            logging.debug(f"Allocation {alloc_name} freed for {sitename}")
            return True
        except Exception as e:
            logging.error(f"free_allocation: {str(e)}")
            raise ValueError(f"Freeing allocation failed for {sitename} and {alloc_name}")