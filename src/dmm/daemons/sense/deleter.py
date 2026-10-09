import logging

from dmm.daemons.base import DaemonBase

from dmm.db.session import databased
from dmm.models.request import Request, RequestStatus

from dmm.core.recovery import retry_cancel
from dmm.core.sense import delete_instance, is_cancel_failed, is_cancel_ready, is_create_compiled

class SENSEDeleterDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
    
    def process(self, **kwargs):
        self.run_once(**kwargs)

    @databased
    def run_once(self, session=None):
        reqs_cancelled = Request.get_by_status(statuses=[RequestStatus.CANCELED], session=session)
        if reqs_cancelled == []:
            return
            
        for req in reqs_cancelled:
            if req.sense_uuid is None:
                logging.info(f"Request {req.rule_id} has no SENSE UUID, marking as DELETED")
                req.set_status(status=RequestStatus.DELETED, session=session)
                continue
                
            try:
                status = req.sense_circuit_status

                if status == "UNKNOWN":
                    logging.info(f"SENSE instance {req.sense_uuid} of {req.rule_id} is already gone, marking as DELETED")
                    req.set_status(status=RequestStatus.DELETED, session=session)
                    continue

                if is_cancel_failed(status):
                    # The cancel was accepted, then failed upstream. Endpoints are
                    # already released, so only the cancel is retried here.
                    retry_cancel(req, status, session)
                    continue

                # The canceller lets a circuit that was never committed through
                # without a cancel call; it will never reach CANCEL - READY.
                if status and not (is_cancel_ready(status) or is_create_compiled(status)):
                    logging.debug(f"Request {req.sense_uuid} not in cancel-ready status (current: {status}), will try to delete again later")
                    continue
                    
                logging.info(f"Deleting SENSE instance {req.sense_uuid} for request {req.rule_id}")
                delete_instance(req.sense_uuid)
                req.set_status(status=RequestStatus.DELETED, session=session)
                logging.info(f"Successfully deleted SENSE instance for request {req.rule_id}")
                
            except Exception as e:
                logging.error(f"Failed to delete link for {req.rule_id}: {e}", exc_info=True)