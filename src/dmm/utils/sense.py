import logging

from dmm.utils.config import config_get
from sense.client.address_api import AddressApi

class SENSEUtils:
    def __init__(self):
        self.profile_uuid = config_get("sense", "profile_uuid")

    @staticmethod
    def good_response(response):
        return bool(response and not any("ERROR" in r for r in response))
    
    def get_allocation(self, sitename, alloc_name):
        addressApi = AddressApi()
        pool_name = "RUCIO_Site_BGP_Subnet_Pool-" + sitename
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
    
    def free_allocation(self, sitename, alloc_name):
        try:
            logging.debug(f"Freeing IPv6 allocation {alloc_name}")
            addressApi = AddressApi()
            pool_name = 'RUCIO_Site_BGP_Subnet_Pool-' + sitename
            addressApi.free_address(pool_name, name=alloc_name)
            logging.debug(f"Allocation {alloc_name} freed for {sitename}")
            return True
        except Exception as e:
            logging.error(f"free_allocation: {str(e)}")
            raise ValueError(f"Freeing allocation failed for {sitename} and {alloc_name}")

# def get_one_host_ip_interface(site_uri):
#     manifest_json = {
#         "HOST": "?hostname?",
#         "NIC": "?nicname?",
#         "sparql": "SELECT ?bp WHERE { ?bp a nml:BidirectionalPort } LIMIT 1",
#         "sparql-ext": "SELECT ?hostname ?nicname WHERE { ?site nml:hasNode ?host. ?host nml:hostname ?hostname. ?host nml:hasBidirectionalPort ?nic. ?nic nml:isAlias ?orther_port. ?nic mrs:hasNetworkAddress ?nic_na_name. ?nic_na_name mrs:type 'sense-rtmon:name'. ?nic_na_name mrs:value ?nicname.  FILTER regex(str(?site), '%s') FILTER NOT EXISTS {?host nml:hasService ?sw_svc. ?sw_svc a nml:SwitchingService.}  } LIMIT 1".format(site_uri),
#         "required": "true"
#     }
#     workflowApi = WorkflowCombinedApi()
#     placeholder_uuid = config_get("sense", "dummy_uuid")
#     response = workflowApi.manifest_create(json.dumps(manifest_json), si_uuid=placeholder_uuid)