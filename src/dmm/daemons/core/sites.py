import logging
import json
import ipaddress

from dmm.daemons.base import DaemonBase

from dmm.db.site import Site
from dmm.db.mesh import Mesh
from dmm.db.endpoint import Endpoint
from dmm.db.session import databased

from dmm.utils.config import config_get
from dmm.utils.sense import SENSEUtils

from sense.client.discover_api import DiscoverApi
from sense.client.workflow_combined_api import WorkflowCombinedApi

class RefreshSiteDBDaemon(DaemonBase, SENSEUtils):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        SENSEUtils.__init__(self)
        
    @databased
    def process(self, session=None):
        sites = config_get("sites", "sites", default=None)
        logging.debug(f"Refreshing site database with sites: {sites}")
        site_objs = []
        if sites is None:
            raise IndexError("No sites found in DMM config")
        for site in sites.split(","):
            try:
                site_exists = Site.from_name(name=site, session=session)
                if not site_exists:
                    logging.debug(f"Site {site} not found in database, adding...")
                    
                    # get site uri
                    try:
                        logging.debug(f"Getting site info for {site}")
                        discover_api = DiscoverApi()
                        response = discover_api.discover_lookup_name_get(site, search="metadata", type="/sitename")
                        if not self.good_response(response):
                            raise ValueError(f"Discover query failed for {site}")
                        if not response["results"]:
                            raise ValueError(f"No results for {site}")
                        matched_results = [result for result in response["results"] if site in result["name/tag/value"]]
                        if len(matched_results) == 0:
                            raise ValueError(f"No results matched")
                        full_uri = matched_results[0]["resource"]
                        root_uri = discover_api.discover_lookup_rooturi_get(full_uri)
                        if not self.good_response(root_uri):
                            raise ValueError(f"Discover query failed for {full_uri}")
                        logging.debug(f"Got URI: {root_uri} for {site}")
                    except Exception as e:
                        logging.error(f"get_uri: {str(e)}")
                        raise ValueError(f"Getting URI failed for {site}")    
                    
                    # using uri, get site info
                    try:                    
                        discover_api = DiscoverApi()
                        site_info = discover_api.discover_domain_id_get(root_uri)
                        if not self.good_response(site_info):
                            raise ValueError(f"Site Info Query Failed for {site}")
                    except Exception as e:
                        logging.error(f"Error occurred in refresh_site_db for site {site}: {str(e)}")

                    sense_uri = site_info["domain_uri"]
                    query_url = site_info["domain_url"]
                    site_ = Site(name=site, sense_uri=sense_uri, query_url=query_url)
                    site_.save(session=session)
                    logging.debug(f"Site {site} added to database, adding endpoints...")

                    # create mesh of this site with all previously added sites
                    for site_obj in site_objs:
                        vlan_range = config_get("vlan-ranges", f"{site_obj.name}-{site}", default="any")
                        if vlan_range == "any":
                            # try again to see if there is a vlan range defined for the opposite direction
                            vlan_range = config_get("vlan-ranges", f"{site}-{site_obj.name}", default="any")
                        if vlan_range == "any":
                            logging.debug(f"No vlan range found for {site_obj.name} and {site}, will default to any")
                            vlan_range_start = -1
                            vlan_range_end = -1
                        else:
                            logging.debug(f"using vlan range {vlan_range} for {site_obj.name} and {site}")
                            vlan_range_start = vlan_range.split("-")[0]
                            vlan_range_end = vlan_range.split("-")[1]
                        for peer_point in site_info["peer_points"]:
                            if str(vlan_range_start) in peer_point["peer_vlan_pool"] and str(vlan_range_end) in peer_point["peer_vlan_pool"]:
                                max_bandwidth = int(peer_point["port_capacity"])
                                break
                        else:
                            max_bandwidth = site_info["peer_points"][0]["port_capacity"]
                        mesh = Mesh(site1=site_obj, site2=site_, vlan_range_start=vlan_range_start, vlan_range_end=vlan_range_end, maximum_bandwidth=max_bandwidth)
                        mesh.save(session=session)
                    site_objs.append(site_)
                else:
                    logging.debug(f"Site {site} already exists in database")
                    site_ = site_exists

                logging.debug("Checking for new / adding endpoints...")

                # get endpoints for this site
                try:
                    logging.info(f"Getting list of endpoints for {sense_uri}")
                    workflow_api = WorkflowCombinedApi()
                    manifest_json = {
                        "Metadata": "?metadata?",
                        "sparql-ext": f"SELECT ?metadata WHERE {{ ?site nml:hasService ?md_svc. ?md_svc mrs:hasNetworkAttribute ?dir_xrootd. ?dir_xrootd mrs:type 'metadata:directory'. ?dir_xrootd mrs:tag '/xrootd'. ?dir_xrootd mrs:value ?metadata.  FILTER regex(str(?site), '{sense_uri}') }} LIMIT 1",
                        "required": "true"
                    }
                    response = workflow_api.manifest_create(json.dumps(manifest_json))
                    metadata = response["jsonTemplate"]
                    response = workflow_api.manifest_create(json.dumps(manifest_json))
                    metadata = json.loads(response["jsonTemplate"])
                    logging.debug(f"Got list of endpoints: {metadata} for {sense_uri}")
                    endpoint_list = json.loads(metadata["Metadata"].replace("'", "\""))
                except Exception as e:
                    raise ValueError(f"Getting list of endpoints failed for {sense_uri}, {e}, SENSE response: {response}")
                
                for block, hostname in endpoint_list.items():
                    if Endpoint.from_hostname(hostname=hostname, session=session) is None:
                        new_endpoint = Endpoint(site=site_,
                                                ip_block=ipaddress.IPv6Network(block).compressed,
                                                hostname=hostname)
                        new_endpoint.save(session=session)
            
            except Exception as e:
                logging.error(f"Error occurred in refresh_site_db for site {site}: {str(e)}")