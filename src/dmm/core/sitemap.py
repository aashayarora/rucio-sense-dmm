"""
Logical to physical site name resolution.

Rucio may know several "logical" site names that all map to a single physical
site, following the pattern PhysicalSiteName_ExtName, e.g. both

    T2_US_UCSD              (physical name, also a logical name)
    T2_US_UCSD_Blackhole    (logical name only)

are Rucio RSEs that SENSE knows as the single site T2_US_UCSD.

DMM keeps the logical name on the request and uses it for exactly one thing:
picking the SENSE-O subnet pool the endpoints are allocated from
(RUCIO_Site_BGP_Subnet_Pool-<logical name>).  Everything else - SENSE
discovery, mesh/VLAN lookups, endpoints, bandwidth decisions, FTS stream caps -
uses the physical name.

Resolution tries the name itself before stripping any suffix, so a site SENSE
already knows about is never folded into a shorter name.  An explicit
[site-aliases] entry in dmm.cfg always wins and is never suffix-stripped, which
also makes it a way to pin a name to itself.
"""
import logging

from dmm.core.config import config_get
from dmm.models.site import Site

# Never suffix-strip a name below this many underscore-separated components:
# "T2_US" is not a site we ever want to resolve to.
MIN_NAME_COMPONENTS = 3

def physical_site_candidates(name: str) -> list:
    """
    Ordered list of candidate physical site names for a logical site name.

    An explicit [site-aliases] mapping short-circuits the list.  Otherwise the
    name itself comes first, followed by progressively shorter names produced by
    dropping trailing _ExtName components.

    Args:
        name: Logical site name (a Rucio RSE name)

    Returns:
        List of candidate physical site names, most specific first
    """
    if not name:
        return []

    # Empty (not None) default: config_get re-raises when a section or option is
    # missing and no default is given, and [site-aliases] is optional.
    alias = config_get("site-aliases", name, default="").strip()
    if alias:
        logging.debug(f"Site {name} is mapped to {alias} by [site-aliases]")
        return [alias]

    candidates = [name]
    parts = name.split("_")
    while len(parts) > MIN_NAME_COMPONENTS:
        parts = parts[:-1]
        candidates.append("_".join(parts))
    return candidates

def resolve_physical_site(name: str, session=None):
    """
    Resolve a logical site name to the Site row of its physical site.

    This only looks at sites DMM already knows about, so it is cheap enough to
    call per rule.  Sites are discovered by the site refresh daemon.

    Args:
        name: Logical site name (a Rucio RSE name)
        session: Database session

    Returns:
        Site object, or None if no candidate is a known site
    """
    for candidate in physical_site_candidates(name):
        site_ = Site.get_by_name(name=candidate, session=session, use_lock=False)
        if site_:
            if candidate != name:
                logging.debug(f"Resolved logical site {name} to physical site {candidate}")
            return site_
    return None
