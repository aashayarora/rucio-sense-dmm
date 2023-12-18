import json
import re

import logging
from time import sleep

from dmm.utils.config import config_get

from sense.client.workflow_combined_api import WorkflowCombinedApi
from sense.client.discover_api import DiscoverApi
from sense.client.address_api import AddressApi

PROFILE_UUID = ""

def get_profile_uuid():
    global PROFILE_UUID
    if PROFILE_UUID == "":
        PROFILE_UUID = config_get("sense", "profile_uuid")
    logging.debug(f"Using SENSE Profile: {PROFILE_UUID}")
    return PROFILE_UUID

def good_response(response):
    return bool(response and not any("ERROR" in r for r in response))

def get_uri(rse_name, regex=".*?"):
    try:
        logging.debug(f"Getting URI for {rse_name}")
        discover_api = DiscoverApi()
        response = discover_api.discover_lookup_name_get(rse_name, search="NetworkAddress")
        if not good_response(response):
            raise ValueError(f"Discover query failed for {rse_name}")
        response = json.loads(response)
        if not response["results"]:
            raise ValueError(f"No results for {rse_name}")
        matched_results = [result for result in response["results"] if re.search(regex, result["name/tag/value"])]
        if len(matched_results) == 0:
            raise ValueError(f"No results matched {regex}")
        full_uri = matched_results[0]["resource"]
        root_uri = discover_api.discover_lookup_rooturi_get(full_uri)
        if not good_response(root_uri):
            raise ValueError(f"Discover query failed for {full_uri}")
        logging.debug(f"Got URI: {root_uri} for {rse_name}")
        return root_uri
    except Exception as e:
        logging.error(f"Error occurred in get_uri: {str(e)}")
        raise ValueError(f"Getting URI failed for {rse_name}")

def get_site_info(rse_name):
    try:
        logging.debug(f"Getting site info for {rse_name}")
        discover_api = DiscoverApi()
        response = discover_api.discover_domain_id_get(get_uri(rse_name))
        if not good_response(response):
            raise ValueError(f"Site Info Query Failed for {rse_name}")
        return response
    except Exception as e:
        logging.error(f"Error occurred in get_site_info: {str(e)}")
        raise ValueError(f"Getting site info failed for {rse_name}")

def get_allocation(sitename, alloc_name):
    try:
        logging.debug(f"Getting IPv6 allocation for {sitename}")
        addressApi = AddressApi()
        pool_name = "RUCIO_Site_BGP_Subnet_Pool-" + sitename
        alloc_type = "IPv6"
        response = addressApi.allocate_address(pool_name, alloc_type, alloc_name, netmask="/64", batch="subnet")
        return response
    except Exception as e:
        logging.error(f"Error occurred in get_allocation: {str(e)}")
        raise ValueError(f"Getting allocation failed for {sitename} and {alloc_name}")

def free_allocation(sitename, alloc_name):
    try:
        logging.debug(f"Freeing IPv6 allocation {alloc_name}")
        addressApi = AddressApi()
        pool_name = 'RUCIO_Site_BGP_Subnet_Pool-' + sitename
        addressApi.free_address(pool_name, name=alloc_name)
    except Exception as e:
        logging.error(f"Error occurred in free_allocation: {str(e)}")
        raise ValueError(f"Freeing allocation failed for {sitename} and {alloc_name}")
    
def provision_link(instance_uuid, src_uri, dst_uri, src_ipv6, dst_ipv6, bandwidth, alias=""):
    try:
        logging.info(f"provisioning sense link for request {alias} with bandwidth {bandwidth / 1000} G")
        workflow_api = WorkflowCombinedApi()
        workflow_api.si_uuid = instance_uuid
        vlan_tag = config_get("sense", "vlan_tag", default="Any")
        intent = {
            "service_profile_uuid": get_profile_uuid(),
            "queries": [
                {
                    "ask": "edit",
                    "options": [
                        {"data.connections[0].bandwidth.capacity": str(bandwidth)},
                        {"data.connections[0].terminals[0].uri": src_uri},
                        {"data.connections[0].terminals[0].ipv6_prefix_list": src_ipv6},
                        {"data.connections[0].terminals[1].uri": dst_uri},
                        {"data.connections[0].terminals[1].ipv6_prefix_list": dst_ipv6},
                        {"data.connections[0].terminals[0].vlan_tag": vlan_tag}, 
                        {"data.connections[0].terminals[1].vlan_tag": vlan_tag}
                    ]
                }
            ]
        }
        if alias:
            intent["alias"] = alias
        response = workflow_api.workflow_combined_intent_post(intent)
        if not good_response(response):
            raise ValueError(f"SENSE query failed for {instance_uuid}")
        return response
    except Exception as e:
        logging.error(f"Error occurred in provision_link: {str(e)}")
        raise ValueError(f"Provisioning link failed for {instance_uuid}")

def modify_link(instance_uuid, bandwidth, alias=""):
    try:
        logging.info(f"modifying sense link for request {alias} with new bandwidth {bandwidth}")
        workflow_api = WorkflowCombinedApi()
        workflow_api.si_uuid = instance_uuid
        status = workflow_api.instance_get_status(si_uuid=instance_uuid)
        intent = {
            "service_profile_uuid": get_profile_uuid(),
            "queries": [
                {
                    "ask": "edit",
                    "options": [
                        {"data.connections[0].bandwidth.capacity": str(bandwidth)},
                    ]
                }
            ]
        }
        if alias:
            intent["alias"] = alias
        response = workflow_api.instance_modify(json.dumps(intent), sync="true")
        if not good_response(response):
            raise ValueError(f"SENSE query failed for {instance_uuid}")
    except Exception as e:
        logging.error(f"Error occurred in modify_link: {str(e)}")
        raise ValueError(f"Modifying link failed for {instance_uuid}")
    logging.debug(f"Modify got response {response}")

def delete_link(instance_uuid):
    try:
        logging.info(f"deleting sense link with uuid {instance_uuid}")
        workflow_api = WorkflowCombinedApi()
        status = workflow_api.instance_get_status(si_uuid=instance_uuid)
        if "error" in status:
            raise ValueError(status)
        if not any(status.startswith(s) for s in ["CREATE", "REINSTATE", "MODIFY"]):
            raise ValueError(f"Cannot cancel an instance in status '{status}'")
        workflow_api.instance_operate("cancel", si_uuid=instance_uuid, sync="true", force=str("READY" not in status).lower())
        total_time = 0
        while "CANCEL - READY" not in status and total_time < 30:
            sleep(5)
            status = workflow_api.instance_get_status(si_uuid=instance_uuid)
            total_time += 5
        try:
            workflow_api.instance_delete(si_uuid=instance_uuid)
        except:
            raise Exception(f"Cancel operation disrupted; instance not deleted")
    except Exception as e:
        logging.error(f"Error occurred in delete_link: {str(e)}")
        raise ValueError(f"Deleting link failed for {instance_uuid}")