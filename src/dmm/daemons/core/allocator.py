import logging

from dmm.daemons.base import DaemonBase

from dmm.models.request import Request, RequestStatus
from dmm.models.endpoint import Endpoint

from dmm.db.session import databased

from dmm.core.allocation import (
    allocate_address,
    free_address,
    format_ipv6_compressed,
    subnet_pool_name,
)
from dmm.core.sense import is_circuit_active

# Statuses that mean a request is actively using its endpoints.
_ACTIVE_STATUSES = {
    RequestStatus.ALLOCATED,
    RequestStatus.RETRY,
    RequestStatus.STAGED,
    RequestStatus.DECIDED,
    RequestStatus.PROVISIONED,
    RequestStatus.STALE,
    RequestStatus.MODIFIED,
    RequestStatus.FINISHED,
}

class AllocatorDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        
    def process(self, **kwargs):
        self.run_once(**kwargs)

    @staticmethod
    def _is_orphan(endpoint, session) -> bool:
        """Return True if no active request is currently holding this endpoint.

        is_allocated=True with no live request behind it is a stale flag left
        by a prior run whose cleanup path didn't complete (e.g. cancel_link()
        threw before release_endpoints_and_addresses() was reached).  It is
        safe to clear the flag and reuse the endpoint.
        """
        holders = Request.get_by_status(statuses=list(_ACTIVE_STATUSES), session=session)
        for req in holders:
            if req.src_endpoint_ == endpoint.id or req.dst_endpoint_ == endpoint.id:
                return False
        return True

    @databased
    def run_once(self, session=None):
        reqs_init = Request.get_by_status(statuses=[RequestStatus.INIT], session=session)
        if not reqs_init:
            return

        # Track rule_ids of FINISHED requests already claimed this cycle
        # so that two INIT requests for the same site pair don't both try to reuse
        # the same finished request.
        claimed_rule_ids = set()
        
        for new_request in reqs_init:  
            if self._reuse_finished_request(new_request, session, claimed_rule_ids):  # check if we can reuse a finished request
                continue
            
            self._allocate_new_endpoints(new_request, session)  # if not found, allocate new endpoints

    def _reuse_finished_request(self, new_request, session, claimed_rule_ids: set) -> bool:
        # A finished request's circuit stays up at its allocated bandwidth for
        # sense_keep_alive_seconds before the canceller takes it down.
        reqs_finished = Request.get_by_status(statuses=[RequestStatus.FINISHED], session=session)
        for req_fin in reqs_finished:
            if req_fin.rule_id in claimed_rule_ids:
                continue

            if req_fin.sense_circuit_status == "UNKNOWN":
                logging.debug(
                    f"Skipping reuse of circuit {req_fin.sense_uuid} from {req_fin.rule_id}: "
                    "circuit_status is UNKNOWN — likely cleaned up by SENSE-O ConsistencyService"
                )
                continue

            if not is_circuit_active(req_fin.sense_circuit_status):
                logging.debug(
                    f"Skipping reuse of circuit {req_fin.sense_uuid} from {req_fin.rule_id}: "
                    f"circuit_status '{req_fin.sense_circuit_status}' is not an active state"
                )
                continue

            # Match on the logical sites, not just the physical ones. Reusing a
            # circuit means inheriting its endpoints, and the endpoint hostname is
            # what DMM hands back to Rucio - a T2_US_UCSD rule adopting a
            # T2_US_UCSD_Blackhole circuit would send real data to a host that
            # discards it. Compared via the pool-site property so that requests
            # predating this feature (no logical site recorded) still match.
            same_direction = (
                req_fin.src_site == new_request.src_site
                and req_fin.dst_site == new_request.dst_site
                and req_fin.src_pool_site == new_request.src_pool_site
                and req_fin.dst_pool_site == new_request.dst_pool_site
            )
            reverse_direction = (
                req_fin.src_site == new_request.dst_site
                and req_fin.dst_site == new_request.src_site
                and req_fin.src_pool_site == new_request.dst_pool_site
                and req_fin.dst_pool_site == new_request.src_pool_site
            )

            if same_direction or reverse_direction:
                reused_src_endpoint = req_fin.src_endpoint if same_direction else req_fin.dst_endpoint
                reused_dst_endpoint = req_fin.dst_endpoint if same_direction else req_fin.src_endpoint
                reused_src_uri = req_fin.sense_src_uri if same_direction else req_fin.sense_dst_uri
                reused_dst_uri = req_fin.sense_dst_uri if same_direction else req_fin.sense_src_uri
                reused_src_vlan = req_fin.src_vlan if same_direction else req_fin.dst_vlan
                reused_dst_vlan = req_fin.dst_vlan if same_direction else req_fin.src_vlan

                logging.info(
                    f"Request {new_request.rule_id} found a live circuit from FINISHED request "
                    f"{req_fin.rule_id} for the same site pair — reusing circuit {req_fin.sense_uuid}."
                )

                inherited_alloc_rule_id = req_fin.sense_alloc_rule_id or req_fin.rule_id

                new_request.update({
                    "src_endpoint": reused_src_endpoint,
                    "dst_endpoint": reused_dst_endpoint,
                    "sense_uuid": req_fin.sense_uuid,
                    "sense_src_uri": reused_src_uri,
                    "sense_dst_uri": reused_dst_uri,
                    "src_vlan": reused_src_vlan,
                    "dst_vlan": reused_dst_vlan,
                    "allocated_bandwidth_mbps": req_fin.allocated_bandwidth_mbps,
                    "available_bandwidth_mbps": req_fin.available_bandwidth_mbps,
                    "sense_circuit_status": req_fin.sense_circuit_status,
                    "sense_affiliated": req_fin.sense_affiliated,
                    "sense_alloc_rule_id": inherited_alloc_rule_id,
                    "transfer_status": RequestStatus.PROVISIONED
                }, session=session)
                req_fin.set_status(status=RequestStatus.DELETED, session=session)
                session.commit()  # commit reuse atomically before continuing loop — prevents a later session.rollback() from erasing this request's writes
                claimed_rule_ids.add(req_fin.rule_id)
                return True

        return False

    def _allocate_new_endpoints(self, new_request, session) -> None:
        """
        Allocate new endpoints for a new request, get endpoints and ip ranges from SENSE-O
        """
        logging.info(f"Allocating endpoints for request {new_request.rule_id}")
        
        # Validate request has required fields
        if not new_request.src_site or not new_request.dst_site:
            new_request.mark_failed("Missing source or destination site", session=session)
            logging.error(f"Request {new_request.rule_id} is missing source or destination site")
            return
        
        src_allocation = None
        dst_allocation = None
        src_endpoint = None
        dst_endpoint = None
        endpoints_marked = False

        # Subnet pools are per logical site, endpoints belong to the physical one.
        src_pool_site = new_request.src_pool_site
        dst_pool_site = new_request.dst_pool_site

        try:
            src_allocation = allocate_address(src_pool_site, new_request.rule_id)
            dst_allocation = allocate_address(dst_pool_site, new_request.rule_id)

            free_src_ipv6 = format_ipv6_compressed(src_allocation)
            free_dst_ipv6 = format_ipv6_compressed(dst_allocation)

            src_endpoint = Endpoint.get_for_allocation(
                site_name=new_request.src_site.name,
                ip_range=free_src_ipv6,
                session=session
            )
            dst_endpoint = Endpoint.get_for_allocation(
                site_name=new_request.dst_site.name,
                ip_range=free_dst_ipv6,
                session=session
            )

            if not src_endpoint:
                raise ValueError(
                    f"Subnet {free_src_ipv6} from pool {subnet_pool_name(src_pool_site)} is not a registered "
                    f"endpoint of physical site {new_request.src_site.name} — check that site's SENSE metadata"
                )
            if not dst_endpoint:
                raise ValueError(
                    f"Subnet {free_dst_ipv6} from pool {subnet_pool_name(dst_pool_site)} is not a registered "
                    f"endpoint of physical site {new_request.dst_site.name} — check that site's SENSE metadata"
                )

            if src_endpoint.is_allocated:
                if self._is_orphan(src_endpoint, session):
                    logging.warning(
                        f"Source endpoint {free_src_ipv6} has stale is_allocated=True with no "
                        "active request — resetting orphan flag and proceeding with allocation"
                    )
                    src_endpoint.set_allocated(is_allocated=False, session=session)
                else:
                    raise ValueError(f"Source endpoint {free_src_ipv6} is already in use")
            if dst_endpoint.is_allocated:
                if self._is_orphan(dst_endpoint, session):
                    logging.warning(
                        f"Destination endpoint {free_dst_ipv6} has stale is_allocated=True with no "
                        "active request — resetting orphan flag and proceeding with allocation"
                    )
                    dst_endpoint.set_allocated(is_allocated=False, session=session)
                else:
                    raise ValueError(f"Destination endpoint {free_dst_ipv6} is already in use")

            src_endpoint.set_allocated(is_allocated=True, session=session)
            dst_endpoint.set_allocated(is_allocated=True, session=session)
            endpoints_marked = True

            new_request.update({
                "src_endpoint": src_endpoint,
                "dst_endpoint": dst_endpoint,
                "transfer_status": RequestStatus.ALLOCATED
            }, session=session)

            session.commit()
            logging.info(f"Successfully allocated endpoints for request {new_request.rule_id}")

        except Exception as e:
            session.rollback()
            if endpoints_marked:
                try:
                    if src_endpoint:
                        session.refresh(src_endpoint)
                        src_endpoint.set_allocated(is_allocated=False, session=session)
                    if dst_endpoint:
                        session.refresh(dst_endpoint)
                        dst_endpoint.set_allocated(is_allocated=False, session=session)
                    session.commit()
                    logging.info(f"Freed endpoints for {new_request.rule_id}")
                except Exception as endpoint_err:
                    logging.error(f"Failed to free endpoints for {new_request.rule_id}: {endpoint_err}")
                    session.rollback()

            # Clean up SENSE-O allocations
            if src_allocation:
                try:
                    free_address(new_request.src_site.name, new_request.rule_id)
                    logging.info(f"Freed source allocation for {new_request.rule_id}")
                except Exception as free_err:
                    logging.error(f"Failed to free source allocation for {new_request.rule_id}: {free_err}")

            if dst_allocation:
                try:
                    free_address(new_request.dst_site.name, new_request.rule_id)
                    logging.info(f"Freed destination allocation for {new_request.rule_id}")
                except Exception as free_err:
                    logging.error(f"Failed to free destination allocation for {new_request.rule_id}: {free_err}")

            session.refresh(new_request)
            new_request.mark_failed(f"Endpoint allocation failed: {e}", session=session)
            session.commit()
            logging.error(
                f"Failed to allocate endpoints for request {new_request.rule_id}: {str(e)}",
                exc_info=True,
            )