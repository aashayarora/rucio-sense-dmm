"""
Core allocation and site management operations.
This module contains all allocation-related logic and SENSE discovery API calls,
extracted from the allocator and sites daemons for better separation of concerns.
"""
import logging
import json
import ipaddress

from sense.client.address_api import AddressApi
from sense.client.discover_api import DiscoverApi
from sense.client.workflow_combined_api import WorkflowCombinedApi

from dmm.models.site import Site
from dmm.models.mesh import Mesh
from dmm.models.endpoint import Endpoint
from dmm.core.rucio import list_rses, get_rse_protocol
from dmm.core.config import config_get
from dmm.core.sitemap import physical_site_candidates

def _good_response(response):
    if not response:
        return False
    if isinstance(response, dict):
        if response.get("error"):
            return False
    return not any("error" in str(r).lower() for r in response)

def subnet_pool_name(pool_site):
    """
    Name of the SENSE-O global address pool for a site.

    Pools are per *logical* site: T2_US_UCSD and T2_US_UCSD_Blackhole are one
    physical site with one pool each.

    Args:
        pool_site: Logical site name owning the pool

    Returns:
        SENSE-O address pool name
    """
    return f"RUCIO_Site_BGP_Subnet_Pool-{pool_site}"

def allocate_address(pool_site, alloc_name):
    """
    Allocate an IPv6 address from SENSE-O address pool.

    Args:
        pool_site: Logical site name whose address pool to allocate from
        alloc_name: Alias for the allocation used in SENSE-O (typically rule_id)

    Returns:
        Allocated IPv6 address string

    Raises:
        Exception: If allocation fails
    """
    address_api = AddressApi()
    pool_name = subnet_pool_name(pool_site)
    alloc_type = "IPv6"
    try:
        logging.debug(f"Getting IPv6 allocation from pool {pool_name}")
        response = address_api.allocate_address(pool_name, alloc_type, alloc_name, netmask="/64", batch="subnet")
        logging.debug(f"Got allocation: {response} from pool {pool_name}")
        return response
    except Exception as e:
        logging.error(f"allocate_address: {str(e)}")
        try:
            address_api.free_address(pool_name, name=alloc_name)
        except Exception as cleanup_err:
            logging.error(
                f"allocate_address cleanup failed for pool={pool_name}, alloc_name={alloc_name}: {cleanup_err}",
                exc_info=True,
            )
        raise

def free_address(pool_site, alloc_name):
    """
    Free an IPv6 address allocation from SENSE-O.

    Must be called with the logical site whose pool the address was allocated
    from, not the physical site - see Request.src_pool_site.

    Args:
        pool_site: Logical site name whose address pool holds the allocation
        alloc_name: Alias for the allocation to free

    Returns:
        True if successful

    Raises:
        ValueError: If freeing fails
    """
    try:
        logging.debug(f"Freeing IPv6 allocation {alloc_name}")
        address_api = AddressApi()
        pool_name = subnet_pool_name(pool_site)
        address_api.free_address(pool_name, name=alloc_name)
        logging.debug(f"Allocation {alloc_name} freed from pool {pool_name}")
        return True
    except Exception as e:
        logging.error(f"free_address: {str(e)}")
        raise ValueError(f"Freeing allocation failed for pool {subnet_pool_name(pool_site)} and {alloc_name}")

def affiliate_endpoints(sense_uuid, src_pool_site, dst_pool_site, rule_id,
                        sense_src_uri, sense_dst_uri):
    """
    Affiliate endpoints with a SENSE instance.

    Args:
        sense_uuid: SENSE instance UUID
        src_pool_site: Logical site name whose pool holds the source allocation
        dst_pool_site: Logical site name whose pool holds the destination allocation
        rule_id: Rule ID used as alias for the affiliation in SENSE-O
        sense_src_uri: SENSE source URI
        sense_dst_uri: SENSE destination URI

    Raises:
        Exception: If affiliation fails
    """
    address_api = AddressApi()

    try:
        src_pool_name = subnet_pool_name(src_pool_site)
        logging.debug(f"Affiliating allocation {rule_id} with SENSE instance {sense_uuid} in address pool {src_pool_name}")
        address_api.affiliate_address(pool=src_pool_name, uri=sense_src_uri, name=rule_id)
        address_api.expire_address(pool=src_pool_name, expire=-1, name=rule_id)

        dst_pool_name = subnet_pool_name(dst_pool_site)
        logging.debug(f"Affiliating allocation {rule_id} with SENSE instance {sense_uuid} in address pool {dst_pool_name}")
        address_api.affiliate_address(pool=dst_pool_name, uri=sense_dst_uri, name=rule_id)
        address_api.expire_address(pool=dst_pool_name, expire=-1, name=rule_id)
        
        logging.info(f"Successfully affiliated endpoints for SENSE instance {sense_uuid}")
    except Exception as e:
        logging.error(f"Failed to affiliate endpoints for SENSE instance {sense_uuid}: {e}", exc_info=True)
        raise

def format_ipv6_compressed(ip_range):
    """
    Format an IPv6 range to compressed notation.
    
    Args:
        ip_range: IPv6 range string
        
    Returns:
        Compressed IPv6 network string
    """
    return ipaddress.IPv6Network(ip_range).compressed

def get_site_uris(site_name):
    """
    Get the full URI and root URI for a given site from SENSE.
    
    Args:
        site_name: Name of the site
        
    Returns:
        Tuple of (full_uri, root_uri)
        
    Raises:
        ValueError: If discovery fails
    """
    try:
        discover_api = DiscoverApi()
        response = discover_api.discover_lookup_name_get(site_name, search="metadata", type="/sitename")
        if not _good_response(response) or not response["results"]:
            raise ValueError(f"Discover query failed for {site_name}")
        matched_results = [result for result in response["results"] if site_name in result["name/tag/value"]]
        if not matched_results:
            raise ValueError(f"No results matched for {site_name}")
        full_uri = matched_results[0]["resource"]
        root_uri = discover_api.discover_lookup_rooturi_get(full_uri)
        if not _good_response(root_uri):
            raise ValueError(f"Discover query failed for {full_uri}")
        logging.debug(f"Got URI: {root_uri} for {site_name}")
        return full_uri, root_uri
    except Exception as e:
        logging.error(f"get_site_uris: {str(e)}")
        raise ValueError(f"Getting URI failed for {site_name}")

def get_site_info(root_uri):
    """
    Get site info for a given root URI from SENSE.
    
    Args:
        root_uri: Root URI of the site
        
    Returns:
        Site info dict containing domain_uri, domain_url, peer_points, etc.
        
    Raises:
        ValueError: If query fails
    """
    try:
        discover_api = DiscoverApi()
        site_info = discover_api.discover_domain_id_get(root_uri)
        if not _good_response(site_info):
            raise ValueError(f"Site Info Query Failed for {root_uri}")
        return site_info
    except Exception as e:
        logging.error(f"Error occurred while getting site info for {root_uri}: {str(e)}")
        raise

def get_link_capacity(site_info, vlan_range):
    """
    Get the link capacity for a given site and VLAN range.
    
    Args:
        site_info: Site info dict from get_site_info
        vlan_range: VLAN range string (e.g., "100-200", "100,101", or "any")
        
    Returns:
        Link capacity in Mbps
    """
    vlan_range_start = None
    vlan_range_end = None
    
    if "-" in vlan_range:
        vlan_range_start, vlan_range_end = map(int, vlan_range.split("-"))
        logging.debug(f"Using vlan range {vlan_range_start}-{vlan_range_end} for link capacity")
    elif "," in vlan_range:
        vlan_range_start = min(map(int, vlan_range.split(",")))
        vlan_range_end = max(map(int, vlan_range.split(",")))
        logging.debug(f"Using vlan range {vlan_range_start}-{vlan_range_end} for link capacity")
    
    if vlan_range == "any":
        port_capacity = int(site_info["peer_points"][0]["port_capacity"])
        logging.debug(f"Using port capacity {port_capacity} for vlan range 'any'")
        return port_capacity
    else:
        for peer_point in site_info["peer_points"]:
            if str(vlan_range_start) in peer_point["peer_vlan_pool"] and str(vlan_range_end) in peer_point["peer_vlan_pool"]:
                port_capacity = int(peer_point["port_capacity"])
                logging.debug(f"Using port capacity {port_capacity} for vlan range {vlan_range_start}-{vlan_range_end}")
                return port_capacity
        port_capacity = int(site_info["peer_points"][0]["port_capacity"])
        logging.debug(f"Using default port capacity {port_capacity} for vlan range {vlan_range}")
        return port_capacity

def get_endpoints_for_site(sense_uri, site_name):
    """
    Get the endpoints (IP ranges and hostnames) for a given site from SENSE.
    
    Args:
        sense_uri: SENSE URI of the site
        site_name: Name of the site (used for metadata tag determination)
        
    Returns:
        Dict mapping IP ranges to hostnames
        
    Raises:
        ValueError: If query fails or no endpoints found
    """
    if not sense_uri:
        logging.warning(f"Site {site_name} has no SENSE URI, skipping endpoint discovery")
        return {}
    
    try:
        logging.info(f"Getting list of endpoints for {sense_uri}")
        workflow_api = WorkflowCombinedApi()
        
        # Determine the correct metadata tag based on site name
        # TODO: Make this configurable instead of hardcoded
        metadata_tag = "/xrootd6" if "FNAL" in site_name else "/xrootd"
        
        manifest_json = {
            "Metadata": "?metadata?",
            "sparql-ext": f"SELECT ?metadata WHERE {{ ?site nml:hasService ?md_svc. ?md_svc mrs:hasNetworkAttribute ?dir_xrootd. ?dir_xrootd mrs:type 'metadata:directory'. ?dir_xrootd mrs:tag '{metadata_tag}'. ?dir_xrootd mrs:value ?metadata.  FILTER regex(str(?site), '{sense_uri}') }} LIMIT 1",
            "required": "true"
        }
        
        response = workflow_api.manifest_create(json.dumps(manifest_json))
        if not response or "jsonTemplate" not in response:
            raise ValueError(f"Invalid response from SENSE manifest creation: {response}")
            
        metadata = json.loads(response["jsonTemplate"])
        logging.debug(f"Got metadata response for {sense_uri}")
        
        if "Metadata" not in metadata:
            logging.warning(f"No Metadata field in response for {sense_uri}")
            return {}
            
        endpoint_list = json.loads(metadata["Metadata"].replace("'", "\""))
        if not endpoint_list:
            logging.warning(f"Empty endpoint list for {sense_uri}")
            return {}
        
        return endpoint_list
        
    except Exception as e:
        error_msg = f"Getting list of endpoints failed for {sense_uri}: {e}"
        logging.error(error_msg, exc_info=True)
        raise ValueError(error_msg)

def refresh_all_sites(rucio_client, session):
    """
    Refresh all sites from Rucio and SENSE.
    This is a high-level function that can be called from the frontend API.
    
    Args:
        rucio_client: Rucio client instance
        session: Database session
        
    Returns:
        List of Rucio site names that were processed
    """

    logging.debug("Getting list of sites registered in Rucio")
    rse_names = [i['rse'] for i in list_rses(rucio_client)]
    logging.debug(f"Got list of sites: {rse_names}, adding to database")

    site_objs = []
    refreshed = {}  # physical site name -> Rucio site name that refreshed it
    for rse_name in rse_names:
        try:
            physical_name = _resolve_physical_site_name(rse_name, session)
            if physical_name != rse_name:
                logging.info(f"Rucio site {rse_name} maps to physical site {physical_name}")

            if physical_name in refreshed:
                logging.debug(
                    f"Physical site {physical_name} already refreshed this cycle "
                    f"(via {refreshed[physical_name]}), skipping {rse_name}"
                )
                continue

            site_ = _get_or_create_site(physical_name, site_objs, session, config_get)
            site_objs.append(site_)
            refreshed[physical_name] = rse_name
            _add_endpoints_for_site(site_, rucio_client, session, rse_name=rse_name)
        except Exception as e:
            logging.error(f"Error occurred in refresh_sites for site {rse_name}: {str(e)}")

    return rse_names

def _resolve_physical_site_name(rse_name, session):
    """
    Resolve a Rucio (logical) site name to its physical site name.

    Known sites win over SENSE lookups so an existing site is never folded into
    a shorter name; SENSE discovery is only consulted for names DMM has not seen
    before.

    Args:
        rse_name: Rucio site name
        session: Database session

    Returns:
        Physical site name

    Raises:
        ValueError: If no candidate is a known site
    """
    candidates = physical_site_candidates(rse_name)

    for candidate in candidates:
        if Site.get_by_name(name=candidate, session=session, use_lock=False):
            return candidate

    for candidate in candidates:
        try:
            get_site_uris(candidate)
            return candidate
        except Exception:
            logging.debug(f"SENSE does not know a site named {candidate}")

    raise ValueError(
        f"Cannot resolve Rucio site '{rse_name}' to a known physical site "
        f"(tried: {', '.join(candidates)}). Add a [site-aliases] entry to dmm.cfg "
        "if its physical site cannot be derived from the name."
    )

def _get_or_create_site(site_name, site_objs, session, config_get_func):
    """
    Get existing site or create new one with SENSE URIs.
    
    Args:
        site_name: Name of the site
        site_objs: List of already processed site objects
        session: Database session
        config_get_func: Config get function for VLAN ranges
        
    Returns:
        Site object
    """
    
    site_exists = Site.get_by_name(name=site_name, session=session, use_lock=False)
    if site_exists:
        logging.debug(f"Site {site_name} already exists in database")
        return site_exists
    
    logging.debug(f"Site {site_name} not found in database, adding...")
    try:
        full_uri, root_uri = get_site_uris(site_name)
        site_info = get_site_info(root_uri)
        sense_uri = site_info["domain_uri"]
        query_url = site_info["domain_url"]
        site_ = Site(name=site_name, sense_uri=sense_uri, query_url=query_url)
        site_.save(session=session)

        # Create mesh links between this site and existing sites
        for site_obj in site_objs:
            if site_obj == site_:
                continue
            vlan_range = _get_vlan_range_for_pair(site_obj, site_, config_get_func)
            link_capacity = get_link_capacity(site_info, vlan_range)
            mesh = Mesh(site1=site_obj, site2=site_, vlan_range=vlan_range, link_capacity_mbps=link_capacity)
            mesh.save(session=session)

        logging.debug(f"Site {site_name} added to database")
        return site_
    except Exception as e:
        logging.error(f"Error occurred while adding site {site_name}: {str(e)}")
        raise

def _get_vlan_range_for_pair(site_obj, site_, config_get_func):
    """Get VLAN range for a site pair."""
    vlan_range = config_get_func("vlan-ranges", f"{site_obj.name}-{site_.name}", default="any")
    if vlan_range == "any":
        vlan_range = config_get_func("vlan-ranges", f"{site_.name}-{site_obj.name}", default="any")
    logging.debug(f"Using vlan range {vlan_range} for {site_obj.name} and {site_.name}")
    return vlan_range

def _add_endpoints_for_site(site_, rucio_client, session, rse_name=None):
    """
    Add endpoints for a site from SENSE metadata.

    Endpoints belong to the physical site, but the protocol has to be read from
    a Rucio RSE - the physical site name is not necessarily one itself.

    Args:
        site_: Site object (physical site)
        rucio_client: Rucio client instance
        session: Database session
        rse_name: Rucio site name to read the protocol from (defaults to the site name)
    """
    rse_name = rse_name or site_.name
    try:
        if not site_.sense_uri:
            logging.warning(f"Site {site_.name} has no SENSE URI, skipping endpoint creation")
            return

        endpoint_list = get_endpoints_for_site(site_.sense_uri, site_.name)
        if not endpoint_list:
            logging.warning(f"No endpoints found for {site_.name}")
            return

        logging.info(f"Getting protocol for the registered endpoints for {rse_name}")
        protocol = get_rse_protocol(rucio_client, rse_name)
        if not protocol:
            raise ValueError(f"No protocol found for RSE {rse_name}")

        endpoints_added = 0
        for iprange, hostname in endpoint_list.items():
            try:
                iprange_compressed = format_ipv6_compressed(iprange)
                
                if Endpoint.get_by_ip_range(ip_range=iprange_compressed, session=session, use_lock=False) is None:
                    new_endpoint = Endpoint(
                        site=site_,
                        protocol=protocol,
                        ip_range=iprange_compressed,
                        hostname=hostname,
                        is_allocated=False
                    )
                    new_endpoint.save(session=session)
                    endpoints_added += 1
                else:
                    logging.debug(f"Endpoint {iprange_compressed} already exists, skipping")
            except Exception as endpoint_err:
                logging.error(f"Failed to add endpoint {iprange} for site {site_.name}: {endpoint_err}")
                continue
                
        logging.info(f"Added {endpoints_added} new endpoints for {site_.name}")
        
    except Exception as e:
        error_msg = f"Getting list of endpoints failed for {site_.sense_uri}: {e}"
        logging.error(error_msg, exc_info=True)
        raise ValueError(error_msg)
