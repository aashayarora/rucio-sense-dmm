import logging
from datetime import datetime

from dmm.daemons.base import DaemonBase

from dmm.db.session import databased
from dmm.models.request import Request, RequestStatus, SenseCircuitStatus
from dmm.models.mesh import Mesh

from dmm.core.config import config_get, config_get_int
from dmm.core.utils import is_sync_timeout
from dmm.core.sense import (
    modify_link,
    cancel_link,
    delete_instance,
    get_instance_status,
    is_ready_for_modify,
    is_being_modified,
    is_modify_failed,
)

class SENSEModifierDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        self.profile_uuid = config_get("sense", "profile_uuid")
        
    def process(self, **kwargs):
        self.run_once(**kwargs)

    @databased
    def run_once(self, session=None):
        reqs_finished_r = Request.get_by_status(statuses=[RequestStatus.FINISHED_R], session=session)
        reqs_stale = Request.get_by_status(statuses=[RequestStatus.STALE], session=session)
        if reqs_stale == [] and reqs_finished_r == []:
            return
        
        # Sort requests by previous bandwidth to prioritize modifications which reduce bandwidth
        # This ensures that we make bandwidth available for other requests
        reqs_stale = sorted(reqs_stale, key=lambda x: (x.allocated_bandwidth_mbps or 0) - (x.previous_bandwidth_mbps or 0))

        # Track which requests are currently being modified (not just if ANY modification is in progress)
        # Build a set of rule_ids that are currently in a modifying state
        all_reqs = Request.get_by_status(statuses=[RequestStatus.FINISHED_R, RequestStatus.STALE, RequestStatus.PROVISIONED], session=session)
        modifying_rule_ids = set()
        for req_ in all_reqs:
            if is_being_modified(req_.sense_circuit_status):
                modifying_rule_ids.add(req_.rule_id)
        
        # Build a set of sense_uuids that have been taken over by a PROVISIONED request.
        # A FINISHED_R circuit that was reused will have its sense_uuid now also referenced
        # by the new PROVISIONED request — we must not throttle/tear it down.
        reqs_provisioned = Request.get_by_status(statuses=[RequestStatus.PROVISIONED], session=session)
        reused_uuids = {req_.sense_uuid for req_ in reqs_provisioned if req_.sense_uuid}

        # Only skip requests that are already being modified, allow others to proceed
        if modifying_rule_ids:
            logging.debug(f"Requests currently being modified: {modifying_rule_ids}")
            
        for req in reqs_finished_r:
            # Skip this specific request if it's already being modified
            if req.rule_id in modifying_rule_ids:
                logging.debug(f"Request {req.rule_id} is already being modified, skipping")
                continue

            if req.sense_uuid is None:
                logging.info(f"Request {req.rule_id} finished before SENSE circuit was created, marking as FINISHED directly")
                req.set_status(status=RequestStatus.FINISHED, session=session)
                continue

            # Skip if the circuit has been taken over by a new provisioned request
            if req.sense_uuid in reused_uuids:
                logging.debug(
                    f"Request {req.rule_id} circuit {req.sense_uuid} has been reused by another "
                    f"request — skipping throttle/teardown, marking as DELETED"
                )
                req.set_status(status=RequestStatus.DELETED, session=session)
                continue

            # Hold in FINISHED_R for a configurable grace period to allow circuit reuse
            finished_r_hold_secs = config_get_int("sense", "finished_r_hold_seconds", default=120, constraint="nonneg")
            if req.rucio_finished_at is not None:
                elapsed = (datetime.now() - req.rucio_finished_at).total_seconds()
                if elapsed < finished_r_hold_secs:
                    logging.debug(
                        f"Request {req.rule_id} entered FINISHED_R {elapsed:.0f}s ago, "
                        f"holding for {finished_r_hold_secs}s before throttle/teardown"
                    )
                    continue

            try:
                vlan_range = Mesh.get_vlan_range(site_1=req.src_site, site_2=req.dst_site, session=session)
                if not vlan_range:
                    logging.error(f"No VLAN range found for {req.rule_id}")
                    continue
                
                modify_link(
                    sense_uuid=req.sense_uuid,
                    profile_uuid=self.profile_uuid,
                    bandwidth_mbps=1000, # set finished links to 1 Gbps to free up bandwidth for other requests
                    src_site=req.src_site,
                    dst_site=req.dst_site,
                    src_ip_range=req.src_endpoint.ip_range,
                    dst_ip_range=req.dst_endpoint.ip_range,
                    vlan_range=vlan_range,
                    rule_id=req.rule_id
                )
                req.set_sense_circuit_status(
                    status=SenseCircuitStatus.MODIFY_COMMITTING.value, session=session
                )
                logging.info(
                    f"Submitted throttle for {req.rule_id}, waiting for SENSE MODIFY-READY before marking FINISHED"
                )
            except Exception as e:
                if is_sync_timeout(e):
                    logging.warning(
                        f"Throttle of finished request {req.rule_id} timed out (504); "
                        "marking MODIFY_COMMITTING, waiting for SENSE MODIFY-READY before FINISHED"
                    )
                    req.set_sense_circuit_status(
                        status=SenseCircuitStatus.MODIFY_COMMITTING.value, session=session
                    )
                else:
                    logging.error(
                        f"Failed to throttle {req.rule_id}: {e} — "
                        "skipping throttle, advancing to FINISHED for cancellation",
                        exc_info=True,
                    )
                    req.set_status(status=RequestStatus.FINISHED, session=session)

        for req in reqs_stale:
            # Skip this specific request if it's already being modified
            if req.rule_id in modifying_rule_ids:
                logging.debug(f"Request {req.rule_id} is already being modified, skipping")
                continue
                
            if req.sense_uuid is None:
                logging.warning(f"Request {req.rule_id} has no SENSE UUID, skipping modification")
                continue
                
            try:
                status = req.sense_circuit_status

                if status == "UNKNOWN":
                    # The circuit UUID is no longer known to SENSE-O — most likely cleaned up
                    # by its ConsistencyService (zombie instance).  We cannot modify or recover
                    # a zombie; reset the request to ALLOCATED so the stager creates a fresh
                    # SENSE instance using the same already-allocated endpoints.
                    logging.warning(
                        f"Request {req.rule_id} is STALE but circuit {req.sense_uuid} has "
                        "UNKNOWN status — treating as zombie, resetting to ALLOCATED for re-staging"
                    )
                    req.update({
                        "sense_uuid": None,
                        "sense_src_uri": None,
                        "sense_dst_uri": None,
                        "sense_circuit_status": None,
                        "sense_affiliated": False,
                        "sense_provisioned_at": None,
                        "sense_retries": 0,
                        "transfer_status": RequestStatus.ALLOCATED,
                    }, session=session)
                    continue

                if is_modify_failed(status):
                    logging.warning(
                        f"Request {req.rule_id} circuit {req.sense_uuid} is in MODIFY - FAILED. "
                        "Cancelling and deleting the failed instance to rebuild from scratch."
                    )
                    failed_uuid = req.sense_uuid
                    # Before attempting cancel/delete, check if SENSE has already cleaned up
                    # this circuit (e.g., auto-cancelled after the modify rejection).
                    # If so, skip the cancel/delete and go straight to the ALLOCATED reset.
                    current_sense_status = get_instance_status(failed_uuid)
                    if current_sense_status == "UNKNOWN":
                        logging.warning(
                            f"Circuit {failed_uuid} is already gone from SENSE (status=UNKNOWN) "
                            f"— skipping cancel/delete, resetting {req.rule_id} to ALLOCATED"
                        )
                    else:
                        try:
                            cancel_link(failed_uuid, status)
                            delete_instance(failed_uuid)
                            logging.info(
                                f"Successfully cancelled and deleted MODIFY-FAILED instance "
                                f"{failed_uuid} for {req.rule_id}"
                            )
                        except Exception as e:
                            logging.error(
                                f"Failed to cancel/delete MODIFY-FAILED instance {failed_uuid} "
                                f"for {req.rule_id}: {e} — will retry next cycle",
                                exc_info=True,
                            )
                            continue
                    req.update({
                        "sense_uuid": None,
                        "sense_src_uri": None,
                        "sense_dst_uri": None,
                        "sense_circuit_status": None,
                        "sense_affiliated": False,
                        "sense_provisioned_at": None,
                        "sense_retries": 0,
                        "transfer_status": RequestStatus.ALLOCATED,
                    }, session=session)
                    continue

                if not is_ready_for_modify(status):
                    logging.debug(f"Cannot modify request {req.rule_id} in status '{status}', will try again later")
                    continue
                    
                vlan_range = Mesh.get_vlan_range(site_1=req.src_site, site_2=req.dst_site, session=session)
                if not vlan_range:
                    logging.error(f"No VLAN range found for {req.rule_id}")
                    continue
                    
                modify_link(
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

                req.set_sense_circuit_status(
                    status=SenseCircuitStatus.MODIFY_COMMITTING.value, session=session
                )
                logging.info(f"Submitted bandwidth modification for {req.rule_id}, waiting for SENSE confirmation")

            except Exception as e:
                if is_sync_timeout(e):
                    # 504 — the modify is almost certainly still committing upstream and
                    # will succeed. Mark MODIFY_COMMITTING (exactly as on a successful
                    # submit) so the handler confirms MODIFY-READY → PROVISIONED, rather
                    # than re-issuing the modify against an in-flight commit.
                    logging.warning(
                        f"Modify of {req.rule_id} timed out (504); treating as in-flight, "
                        "marking MODIFY-COMMITTING for handler confirmation"
                    )
                    req.set_sense_circuit_status(
                        status=SenseCircuitStatus.MODIFY_COMMITTING.value, session=session
                    )
                else:
                    logging.error(f"Failed to modify link for {req.rule_id}: {e}", exc_info=True)
            