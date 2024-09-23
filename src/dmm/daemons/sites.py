import logging
import ipaddress

from dmm.db.site import Site
from dmm.db.mesh import Mesh
from dmm.db.endpoint import Endpoint
from dmm.db.session import databased

from dmm.daemons.base import DaemonBase

from dmm.utils.config import config_get
from dmm.utils.sense import get_site_info, get_list_of_endpoints
from dmm.utils.db import get_site, get_endpoint

class RefreshSiteDBDaemon(DaemonBase):
    @databased
    def process(self, session=None):
        # list of sites is defined in config
        sites = config_get("sites", "sites", default=None)
        logging.debug(f"Refreshing site database with sites: {sites}")
        # make a list of site_objects so we can make a mesh later
        site_objs = []
        if sites is None:
            raise IndexError("No sites found in DMM config")
        for site in sites.split(","):
            try:
                # check if site already exists in the database
                site_exists = get_site(site, session=session)
                if not site_exists:
                    logging.debug(f"Site {site} not found in database, adding...")
                    # if not, get the site info from sense and add it to the database
                    site_info = get_site_info(site)
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
                        mesh = Mesh(site1=site_obj, site2=site_, vlan_range_start=vlan_range_start, vlan_range_end=vlan_range_end, max_bandwidth=max_bandwidth)
                        mesh.save(session=session)
                    site_objs.append(site_)
                else:
                    logging.debug(f"Site {site} already exists in database")
                    site_ = site_exists
                logging.debug("Checking for new / adding endpoints...")
                endpoint_list = get_list_of_endpoints(site_.sense_uri)
                for block, hostname in endpoint_list.items():
                    if get_endpoint(hostname, session=session) is None:
                        new_endpoint = Endpoint(site=site_,
                                                ip_block=ipaddress.IPv6Network(block).compressed,
                                                hostname=hostname,
                                                )
                        new_endpoint.save(session=session)
            except Exception as e:
                logging.error(f"Error occurred in refresh_site_db for site {site}: {str(e)}")