import logging

from dmm.daemons.base import DaemonBase
from dmm.db.session import databased

from dmm.models.request import Request, RequestStatus
from dmm.models.mesh import Mesh

from dmm.core.config import config_get
from dmm.core.utils import is_sync_timeout
from dmm.core.sense import (
    provision_link,
    is_create_ready,
    is_create_compiled,
    is_being_provisioned,
    is_create_failed,
)

class SENSEProvisionerDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        self.profile_uuid = config_get("sense", "profile_uuid")
        
    def process(self, **kwargs):
        self.run_once(**kwargs)

    @databased
    def run_once(self, session=None):
        # make sure there are no stale requests before provisioning any new ones
        reqs_stale = Request.get_by_status(statuses=[RequestStatus.STALE], session=session)
        if reqs_stale != []:
            logging.debug("Stale requests exist, skipping provisioning")
            return
            
        reqs_decided = Request.get_by_status(statuses=[RequestStatus.DECIDED], session=session)
        if reqs_decided == []:
            return
            
        # Check which specific requests have provisioning in progress
        # Build a set of rule_ids that are currently being provisioned
        all_reqs = Request.get_by_status(statuses=[RequestStatus.DECIDED, RequestStatus.PROVISIONED], session=session)
        provisioning_rule_ids = set()
        for req_ in all_reqs:
            if is_being_provisioned(req_.sense_circuit_status):
                provisioning_rule_ids.add(req_.rule_id)
        
        # Only skip requests that are already being provisioned, allow others to proceed        
        if provisioning_rule_ids:
            logging.debug(f"Requests currently being provisioned: {provisioning_rule_ids}")
                
        for req in reqs_decided:
            # Skip this specific request if it's already being provisioned
            if req.rule_id in provisioning_rule_ids:
                logging.debug(f"Request {req.rule_id} is already being provisioned, skipping")
                continue
                
            if req.sense_uuid is None:
                logging.warning(f"Request {req.rule_id} has no SENSE UUID, skipping")
                continue
                
            try:
                status = req.sense_circuit_status
                if is_create_ready(status):
                    logging.info(f"Request {req.sense_uuid} already in ready status, marking as provisioned")
                    req.clear_failure_reason(session=session)
                    req.set_status(status=RequestStatus.PROVISIONED, session=session)
                    continue

                if is_create_failed(status):
                    logging.warning(
                        f"Request {req.rule_id} is in {status} for SENSE instance {req.sense_uuid}; "
                        "marking as RETRY"
                    )
                    req.mark_retry(f"SENSE circuit {req.sense_uuid} in {status}", session=session)
                    continue
                    
                if not is_create_compiled(status):
                    logging.debug(f"Request {req.sense_uuid} not in compiled status (current: {status}), will try to provision again")
                    continue
                    
                vlan_range = Mesh.get_vlan_range(site_1=req.src_site, site_2=req.dst_site, session=session)
                if not vlan_range:
                    logging.error(f"No VLAN range found for {req.rule_id}")
                    continue
                    
                provision_link(
                    sense_uuid=req.sense_uuid,
                    profile_uuid=self.profile_uuid,
                    bandwidth_mbps=req.allocated_bandwidth_mbps,
                    src_site=req.src_site,
                    dst_site=req.dst_site,
                    src_ip_range=req.src_endpoint.ip_range,
                    dst_ip_range=req.dst_endpoint.ip_range,
                    vlan_range=vlan_range,
                    rule_id=req.rule_id
                )
                req.clear_failure_reason(session=session)
                req.set_status(status=RequestStatus.PROVISIONED, session=session)
                logging.info(f"Successfully provisioned request {req.rule_id}")
            except Exception as e:
                if is_sync_timeout(e):
                    logging.warning(
                        f"Provision for {req.rule_id} timed out (504); treating as in-flight, "
                        "marking PROVISIONED and deferring to handler status polling"
                    )
                    req.set_status(status=RequestStatus.PROVISIONED, session=session)
                else:
                    logging.error(f"Failed to provision link for {req.rule_id}: {e}", exc_info=True)
                    req.mark_retry(f"Provisioning failed: {e}", session=session)