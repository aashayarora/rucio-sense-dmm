import logging
import ipaddress

from dmm.daemons.base import DaemonBase

from dmm.models.request import Request
from dmm.models.endpoint import Endpoint

from dmm.db.session import databased

from sense.client.address_api import AddressApi

class AllocatorDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        
    def process(self, **kwargs):
        self.run_once(**kwargs)

    @databased
    def run_once(self, session=None):
        reqs_init = Request.from_status(status=["INIT"], session=session)
        if not reqs_init:
            return
        
        for new_request in reqs_init:  
            if self._reuse_finished_request(new_request, session): # check if we can reuse a finished request
                continue
            
            self._allocate_new_endpoints(new_request, session) # if not found, allocate new endpoints

    def _reuse_finished_request(self, new_request, session) -> bool:
        """
        Check if there is a finished request with the same src and dst site. if found: reuse its endpoints and return True
        """
        reqs_finished = Request.from_status(status=["FINISHED"], session=session)
        for req_fin in reqs_finished:
            if req_fin.src_site == new_request.src_site and req_fin.dst_site == new_request.dst_site:
                logging.debug(f"Request {new_request.rule_id} found a finished request {req_fin.rule_id} with same endpoints, reusing ipv6 blocks and urls.")
                new_request.update({
                    "src_endpoint": req_fin.src_endpoint,
                    "dst_endpoint": req_fin.dst_endpoint,
                    "transfer_status": "ALLOCATED"
                })
                req_fin.update_transfer_status(status="DELETED", session=session)
                return True
        return False

    def _allocate_new_endpoints(self, new_request, session) -> None:
        """
        Allocate new endpoints for a new request, get endpoints and ip ranges from SENSE-O
        """
        logging.info(f"Allocating endpoints for request {new_request.rule_id}")
        
        # Validate request has required fields
        if not new_request.src_site or not new_request.dst_site:
            new_request.update_transfer_status("FAILED", session=session)
            logging.error(f"Request {new_request.rule_id} is missing source or destination site")
            return
        
        src_allocation = None
        dst_allocation = None
        
        try:
            # Get allocations
            src_allocation = self._get_allocation(new_request.src_site.name, new_request.rule_id)
            dst_allocation = self._get_allocation(new_request.dst_site.name, new_request.rule_id)
            
            # Format IP addresses consistently
            free_src_ipv6 = ipaddress.IPv6Network(src_allocation).compressed
            free_dst_ipv6 = ipaddress.IPv6Network(dst_allocation).compressed
            
            # Find matching endpoints
            src_endpoint = Endpoint.for_rule(site_name=new_request.src_site.name, ip_range=free_src_ipv6, session=session)
            dst_endpoint = Endpoint.for_rule(site_name=new_request.dst_site.name, ip_range=free_dst_ipv6, session=session)
            
            # Validate endpoints
            if not src_endpoint:
                raise ValueError(f"Could not find source endpoint with IP range {free_src_ipv6}")
            if not dst_endpoint:
                raise ValueError(f"Could not find destination endpoint with IP range {free_dst_ipv6}")
                
            # Update request with allocated endpoints
            new_request.update({
                "src_endpoint": src_endpoint,
                "dst_endpoint": dst_endpoint,
                "transfer_status": "ALLOCATED"
            })
            
            # Mark endpoints as in use
            src_endpoint.mark_inuse(in_use=True, session=session)
            dst_endpoint.mark_inuse(in_use=True, session=session)
            
            logging.info(f"Successfully allocated endpoints for request {new_request.rule_id}")
            
        except Exception as e:
            # Clean up any allocations we made
            if src_allocation:
                self._free_allocation(new_request.src_site.name, new_request.rule_id)
            if dst_allocation:
                self._free_allocation(new_request.dst_site.name, new_request.rule_id)
                
            # Mark request as failed
            new_request.update_transfer_status("FAILED", session=session)
            logging.error(f"Failed to allocate endpoints for request {new_request.rule_id}: {str(e)}")

    def _get_allocation(self, sitename, alloc_name) -> str:
        """
        Get an allocation from SENSE-O using the addressApi
        @param sitename: name of the site (used for the address pool)
        @param alloc_name: alias for the allocation used in SENSE-O, this is just the rule_id
        """
        addressApi = AddressApi()
        pool_name = f"RUCIO_Site_BGP_Subnet_Pool-{sitename}"
        alloc_type = "IPv6"
        try:
            logging.debug(f"Getting IPv6 allocation for {sitename}")
            response = addressApi.allocate_address(pool_name, alloc_type, alloc_name, netmask="/64", batch="subnet")
            #TODO: there is a possibility that the address pool does not exist, in this case return an error and mark the request as failed
            logging.debug(f"Got allocation: {response} for {sitename}")
            return response
        except Exception as e:
            logging.error(f"get_allocation: {str(e)}")
            addressApi.free_address(pool_name, name=alloc_name)
            raise e

    def _free_allocation(self, sitename, alloc_name) -> bool:
        """
        Free an allocation from SENSE-O (in case of failure of allocation)
        """
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