import logging
import json
import ipaddress

from dmm.daemons.base import DaemonBase

from dmm.models.site import Site
from dmm.models.mesh import Mesh
from dmm.models.endpoint import Endpoint
from dmm.db.session import databased

from dmm.core.config import config_get

from sense.client.discover_api import DiscoverApi
from sense.client.workflow_combined_api import WorkflowCombinedApi

class RefreshSiteDBDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
    
    def process(self, **kwargs):
        self.run_once(**kwargs)

    @databased
    def run_once(self, client=None, session=None) -> None:
        logging.debug(f"Getting list of sites registered in Rucio")
        sites = [i['rse'] for i in client.list_rses()] # list of sites in Rucio
        logging.debug(f"Got list of sites: {sites}, adding to database")

        site_objs = [] # list of site objects (see dmm.db.site) to be added to the database
        for site in sites:
            try:
                site_ = self._get_or_create_site(site, site_objs, session)
                site_objs.append(site_)
                self._add_endpoints_for_site(site_, client, session)
            except Exception as e:
                logging.error(f"Error occurred in refresh_site_db for site {site}: {str(e)}")

    def _get_or_create_site(self, site, site_objs, session) -> Site:
        """
        Depending on whether the daemon has run before, the site might exist in the db already, if it exists then skip the SENSE queries for the uris.
        """
        site_exists = Site.from_name(name=site, session=session)
        if site_exists:
            logging.debug(f"Site {site} already exists in database")
            return site_exists
        
        logging.debug(f"Site {site} not found in database, adding...")
        # if site doesn't exist, get the uris from SENSE and add it to the database
        try:
            full_uri, root_uri = self._get_site_uris(site)
            site_info = self._get_site_info(root_uri)
            sense_uri = site_info["domain_uri"]
            query_url = site_info["domain_url"]
            site_ = Site(name=site, sense_uri=sense_uri, query_url=query_url)
            site_.save(session=session)

            # for each new site, calculate the mesh links between existing sites (for a fully connected graph)
            for site_obj in site_objs:
                if site_obj == site_:
                    continue
                vlan_range = self._get_vlan_range(site_obj, site_) # vlan ranges for site pairs can be different and need to be configured
                link_capacity = self._get_link_capacity(site_info, vlan_range) # get the link capacity from SENSE
                mesh = Mesh(site1=site_obj, site2=site_, vlan_range=vlan_range, link_capacity=link_capacity)
                mesh.save(session=session)

            logging.debug(f"Site {site} added to database")
            return site_
        except Exception as e:
            logging.error(f"Error occurred while adding site {site}: {str(e)}")
            raise

    def _get_vlan_range(self, site_obj, site_) -> tuple:
        """
        Get the vlan range for a given site pair, if not found, default to any
        """
        try:
            vlan_range = config_get("vlan-ranges", f"{site_obj.name}-{site_.name}", default="any") # try to get A-B
            if (vlan_range == "any"):
                vlan_range = config_get("vlan-ranges", f"{site_.name}-{site_obj.name}", default="any") # try to get B-A
            logging.debug(f"Using vlan range {vlan_range} for {site_obj.name} and {site_.name}")
        except Exception as e:
            logging.error(f"Error occurred while getting vlan range for {site_obj.name} and {site_.name}: {str(e)}")
            vlan_range = "any"
        return vlan_range

    def _get_link_capacity(self, site_info, vlan_range):
        if ("-" in vlan_range):
            vlan_range_start, vlan_range_end = map(int, vlan_range.split("-"))
            logging.debug(f"Using vlan range {vlan_range_start}-{vlan_range_end} for link capacity")
        elif ("," in vlan_range):
            vlan_range_start, vlan_range_end = min(map(int, vlan_range.split(","))), max(map(int, vlan_range.split(",")))
            logging.debug(f"Using vlan range {vlan_range_start}-{vlan_range_end} for link capacity")
        # for peer_point in site_info["peer_points"]:
            # if str(vlan_range_start) in peer_point["peer_vlan_pool"] and str(vlan_range_end) in peer_point["peer_vlan_pool"]:
                # return int(peer_point["port_capacity"]) # return the port capacity for the vlan range chosen, if not found, return the first one
        # return int(site_info["peer_points"][0]["port_capacity"])
        return 100000.

    def _get_site_uris(self, site) -> tuple:
        """
        Get the full uri and root uri for a given site
        """
        try:
            discover_api = DiscoverApi()
            response = discover_api.discover_lookup_name_get(site, search="metadata", type="/sitename")
            if not self._good_response(response) or not response["results"]:
                raise ValueError(f"Discover query failed for {site}")
            matched_results = [result for result in response["results"] if site in result["name/tag/value"]]
            if not matched_results:
                raise ValueError(f"No results matched for {site}")
            full_uri = matched_results[0]["resource"]
            root_uri = discover_api.discover_lookup_rooturi_get(full_uri)
            if not self._good_response(root_uri):
                raise ValueError(f"Discover query failed for {full_uri}")
            logging.debug(f"Got URI: {root_uri} for {site}")
            return full_uri, root_uri
        except Exception as e:
            logging.error(f"get_uri: {str(e)}")
            raise ValueError(f"Getting URI failed for {site}")

    def _get_site_info(self, root_uri) -> dict:
        """
        Get the site info for a given root uri namely used for getting the peer points and port capacity
        """
        try:
            discover_api = DiscoverApi()
            site_info = discover_api.discover_domain_id_get(root_uri)
            if not self._good_response(site_info):
                raise ValueError(f"Site Info Query Failed for {root_uri}")
            return site_info
        except Exception as e:
            logging.error(f"Error occurred while getting site info for {root_uri}: {str(e)}")
            raise

    def _add_endpoints_for_site(self, site_, client, session) -> None:
        """
        Get the endpoints for a given site
        """
        try:
            logging.info(f"Getting list of endpoints for {site_.sense_uri}")
            workflow_api = WorkflowCombinedApi()
            manifest_json = {
                "Metadata": "?metadata?",
                "sparql-ext": f"SELECT ?metadata WHERE {{ ?site nml:hasService ?md_svc. ?md_svc mrs:hasNetworkAttribute ?dir_xrootd. ?dir_xrootd mrs:type 'metadata:directory'. ?dir_xrootd mrs:tag '/xrootd'. ?dir_xrootd mrs:value ?metadata.  FILTER regex(str(?site), '{site_.sense_uri}') }} LIMIT 1",
                "required": "true"
            } # manifest json to query SENSE for the endpoints for a given uri

            ## HACK for FNAL testing
            if "FNAL" in site_.name:
                manifest_json["sparql-ext"] = f"SELECT ?metadata WHERE {{ ?site nml:hasService ?md_svc. ?md_svc mrs:hasNetworkAttribute ?dir_xrootd. ?dir_xrootd mrs:type 'metadata:directory'. ?dir_xrootd mrs:tag '/xrootd6'. ?dir_xrootd mrs:value ?metadata.  FILTER regex(str(?site), '{site_.sense_uri}') }} LIMIT 1"
            
            response = workflow_api.manifest_create(json.dumps(manifest_json))
            metadata = json.loads(response["jsonTemplate"])
            logging.debug(f"Got list of endpoints: {metadata} for {site_.sense_uri}")
            endpoint_list = json.loads(metadata["Metadata"].replace("'", "\""))

            logging.info(f"Getting protocol for the registered endpoints for {site_.name}")
            rse = client.get_rse(site_.name)
            if not rse:
                raise ValueError(f"RSE {site_.name} not found in Rucio")
            
            protocol = rse.get('protocols', [{}])[0].get('scheme', None)
            if not protocol:
                raise ValueError(f"No protocol found for RSE {site_.name}")

            for iprange, hostname in endpoint_list.items():
                iprange = ipaddress.IPv6Network(iprange).compressed
                if Endpoint.from_iprange(iprange=iprange, session=session) is None:
                    new_endpoint = Endpoint(site=site_,
                                            protocol=protocol,
                                            ip_range=iprange,
                                            hostname=hostname,
                                            in_use=False)
                    new_endpoint.save(session=session)
        except Exception as e:
            raise ValueError(f"Getting list of endpoints failed for {site_.sense_uri}, {e}, SENSE response: {response}")

    @staticmethod
    def _good_response(response):
        return bool(response and not any("ERROR" in r for r in response))