import logging
from datetime import datetime

from dmm.daemons.base import DaemonBase

from dmm.db.session import databased
from dmm.models.request import Request, RequestStatus

from dmm.core.config import config_get_int
from dmm.core.sense import (
    cancel_link,
    get_instance_status,
    is_being_cancelled,
    is_cancel_failed,
    is_cancel_ready,
    is_create_compiled,
    is_ready_for_cancel
)
from dmm.core.utils import release_endpoints_and_addresses

class SENSECancellerDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
    
    def process(self, **kwargs):
        self.run_once(**kwargs)

    @databased
    def run_once(self, session=None):
        reqs_finished = Request.get_by_status(statuses=[RequestStatus.FINISHED], session=session)
        if reqs_finished == []:
            return

        live_reqs = Request.get_by_status(statuses=[RequestStatus.PROVISIONED, RequestStatus.STALE, RequestStatus.DECIDED, RequestStatus.STAGED], session=session)
        live_uuids = {r.sense_uuid for r in live_reqs if r.sense_uuid}
            
        for req in reqs_finished:
            try:
                # Safety check: don't cancel a circuit that's been taken over by another request
                if req.sense_uuid and req.sense_uuid in live_uuids:
                    logging.warning(
                        f"Request {req.rule_id} is FINISHED but its circuit {req.sense_uuid} is still "
                        f"in use by another live request — skipping cancellation, marking as DELETED"
                    )
                    req.set_status(status=RequestStatus.DELETED, session=session)
                    continue

                if req.sense_uuid is None:
                    logging.info(f"Request {req.rule_id} has no SENSE UUID, releasing endpoints and marking as DELETED")
                    release_endpoints_and_addresses(req, session)
                    req.set_status(status=RequestStatus.DELETED, session=session)
                    continue
                    
                keep_alive_secs = config_get_int(
                    "sense", "sense_keep_alive_seconds", default=60, constraint="nonneg"
                )
                if req.rucio_finished_at is None:
                    logging.warning(
                        f"Request {req.rule_id} has no rucio_finished_at timestamp; "
                        "proceeding with cancellation immediately"
                    )
                elif (datetime.now() - req.rucio_finished_at).total_seconds() < keep_alive_secs:
                    logging.debug(
                        f"Request {req.rule_id} finished {(datetime.now() - req.rucio_finished_at).total_seconds():.0f}s ago, "
                        f"waiting for keep-alive window ({keep_alive_secs}s) before cancellation"
                    )
                    continue

                logging.info(f"Cancelling SENSE link with uuid {req.sense_uuid} for request {req.rule_id}")

                # Always fetch the live status from SENSE before deciding how to cancel.
                # The DB column (sense_circuit_status) may be stale if a previous cancel
                # call timed out (504) and SENSE has already transitioned internally.
                try:
                    live_status = get_instance_status(req.sense_uuid)
                except Exception as status_err:
                    logging.warning(
                        f"Could not fetch live circuit status for {req.sense_uuid}: {status_err}. "
                        "Will retry next cycle."
                    )
                    continue

                # Persist the refreshed status so other daemons see the correct state.
                req.set_sense_circuit_status(live_status, session=session)

                if is_cancel_ready(live_status):
                    logging.info(f"Circuit {req.sense_uuid} in CANCEL-READY, releasing and marking CANCELED")
                    release_endpoints_and_addresses(req, session)
                    req.set_status(status=RequestStatus.CANCELED, session=session)
                    continue

                if is_being_cancelled(live_status):
                    # SENSE has accepted the cancel (CANCEL-COMMITTING / CANCEL-COMMITTED).
                    # Do NOT re-issue cancel_link() — that causes a 500 BadRequest storm.
                    # Wait for SENSE to reach CANCEL-READY on the next poll cycle.
                    logging.debug(
                        f"Circuit {req.sense_uuid} is already being cancelled on SENSE side "
                        f"(status={live_status}). Waiting for CANCEL-READY."
                    )
                    continue

                if is_cancel_failed(live_status):
                    # SENSE-O failed to complete the cancel.
                    # Because "READY" is not in the status string, cancel_link() will automatically
                    # set force=true — which is the correct SENSE-O API call to break the
                    # circuit out of this state.  If the retry itself fails (e.g. the 503
                    # is still ongoing) we log the error and leave the request in FINISHED
                    # so the next daemon cycle tries again.
                    logging.warning(
                        f"Circuit {req.sense_uuid} is in CANCEL - FAILED for request "
                        f"{req.rule_id} (possible transient upstream error). "
                        "Retrying with force cancel."
                    )
                    try:
                        cancel_link(req.sense_uuid, live_status)  # force=true, "READY" not in status
                        release_endpoints_and_addresses(req, session)
                        req.set_status(status=RequestStatus.CANCELED, session=session)
                        logging.info(
                            f"Force-cancel succeeded for CANCEL-FAILED circuit "
                            f"{req.sense_uuid} ({req.rule_id})"
                        )
                    except Exception as force_err:
                        logging.error(
                            f"Force-cancel of CANCEL-FAILED circuit {req.sense_uuid} "
                            f"for {req.rule_id} failed: {force_err} — will retry next cycle",
                            exc_info=True,
                        )
                    continue

                if is_create_compiled(live_status):
                    logging.info(f"Circuit {req.sense_uuid} in CREATE-COMPILED, safe to mark CANCELED without cancel call")
                    release_endpoints_and_addresses(req, session)
                    req.set_status(status=RequestStatus.CANCELED, session=session)
                    continue

                if not is_ready_for_cancel(live_status):
                    logging.debug(f"Cannot cancel instance {req.sense_uuid} in status '{live_status}', will try again later")
                    continue

                cancel_link(req.sense_uuid, live_status)

                release_endpoints_and_addresses(req, session)
                req.set_status(status=RequestStatus.CANCELED, session=session)
                logging.info(f"Successfully cancelled SENSE link for request {req.rule_id}")

            except Exception as e:
                logging.error(f"Failed to cancel link for {req.rule_id}: {e}", exc_info=True)
