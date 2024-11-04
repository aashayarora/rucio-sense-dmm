import logging
import json
import ipaddress

from dmm.daemons.base import DaemonBase

from dmm.db.site import Site
from dmm.db.mesh import Mesh
from dmm.db.endpoint import Endpoint
from dmm.db.session import databased

from dmm.utils.config import config_get

from sense.client.discover_api import DiscoverApi
from sense.client.workflow_combined_api import WorkflowCombinedApi

class RefreshSiteDBDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        self.sites = config_get("sites", "sites", default=None)
        
    @databased
    def process(self, session=None):
        logging.debug(f"Refreshing site database with sites: {self.sites}")
        if self.sites is None:
            raise IndexError("No sites found in DMM config")
        
        site_objs = []
        for site in self.sites.split(","):
            try:
                site_ = self._get_or_create_site(site, site_objs, session)
                site_objs.append(site_)
                self._add_endpoints_for_site(site_, session)
            except Exception as e:
                logging.error(f"Error occurred in refresh_site_db for site {site}: {str(e)}")

    def _get_or_create_site(self, site, site_objs, session):
        site_exists = Site.from_name(name=site, session=session)
        if site_exists:
            logging.debug(f"Site {site} already exists in database")
            return site_exists
        
        logging.debug(f"Site {site} not found in database, adding...")
        try:
            full_uri, root_uri = self._get_site_uris(site)
            site_info = self._get_site_info(root_uri)
            sense_uri = site_info["domain_uri"]
            query_url = site_info["domain_url"]
            site_ = Site(name=site, sense_uri=sense_uri, query_url=query_url)
            site_.save(session=session)

            for site_obj in site_objs:
                if site_obj == site_:
                    continue
                vlan_range_start, vlan_range_end = self._get_vlan_range(site_obj, site_)
                max_bandwidth = self._get_max_bandwidth(site_, site_info, vlan_range_start, vlan_range_end)
                mesh = Mesh(site1=site_obj, site2=site_, vlan_range_start=vlan_range_start, vlan_range_end=vlan_range_end, maximum_bandwidth=max_bandwidth)
                mesh.save(session=session)

            logging.debug(f"Site {site} added to database")
            return site_
        except Exception as e:
            logging.error(f"Error occurred while adding site {site}: {str(e)}")
            raise

    def _get_vlan_range(self, site_obj, site_):
        vlan_range = config_get("vlan-ranges", f"{site_obj.name}-{site_.name}", default="any")
        if vlan_range == "any":
            vlan_range = config_get("vlan-ranges", f"{site_.name}-{site_obj.name}", default="any")
        if vlan_range == "any":
            logging.debug(f"No vlan range found for {site_obj.name} and {site_.name}, will default to any")
            return -1, -1
        logging.debug(f"Using vlan range {vlan_range} for {site_obj.name} and {site_.name}")
        return vlan_range.split("-")[0], vlan_range.split("-")[1]

    def _get_max_bandwidth(self, site_, site_info, vlan_range_start, vlan_range_end):
        for peer_point in site_info["peer_points"]:
            if str(vlan_range_start) in peer_point["peer_vlan_pool"] and str(vlan_range_end) in peer_point["peer_vlan_pool"]:
                return int(peer_point["port_capacity"])
        return site_info["peer_points"][0]["port_capacity"]

    def _get_site_uris(self, site):
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

    def _get_site_info(self, root_uri):
        try:
            discover_api = DiscoverApi()
            site_info = discover_api.discover_domain_id_get(root_uri)
            if not self._good_response(site_info):
                raise ValueError(f"Site Info Query Failed for {root_uri}")
            return site_info
        except Exception as e:
            logging.error(f"Error occurred while getting site info for {root_uri}: {str(e)}")
            raise

    def _add_endpoints_for_site(self, site_, session):
        try:
            logging.info(f"Getting list of endpoints for {site_.sense_uri}")
            workflow_api = WorkflowCombinedApi()
            manifest_json = {
                "Metadata": "?metadata?",
                "sparql-ext": f"SELECT ?metadata WHERE {{ ?site nml:hasService ?md_svc. ?md_svc mrs:hasNetworkAttribute ?dir_xrootd. ?dir_xrootd mrs:type 'metadata:directory'. ?dir_xrootd mrs:tag '/xrootd'. ?dir_xrootd mrs:value ?metadata.  FILTER regex(str(?site), '{site_.sense_uri}') }} LIMIT 1",
                "required": "true"
            }
            response = workflow_api.manifest_create(json.dumps(manifest_json))
            metadata = json.loads(response["jsonTemplate"])
            logging.debug(f"Got list of endpoints: {metadata} for {site_.sense_uri}")
            endpoint_list = json.loads(metadata["Metadata"].replace("'", "\""))
            for block, hostname in endpoint_list.items():
                if Endpoint.from_hostname(hostname=hostname, session=session) is None:
                    new_endpoint = Endpoint(site=site_,
                                            ip_block=ipaddress.IPv6Network(block).compressed,
                                            hostname=hostname,
                                            in_use=False)
                    new_endpoint.save(session=session)
        except Exception as e:
            raise ValueError(f"Getting list of endpoints failed for {site_.sense_uri}, {e}, SENSE response: {response}")

    @staticmethod
    def _good_response(response):
        return bool(response and not any("ERROR" in r for r in response))