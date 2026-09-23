import logging

from dmm.daemons.base import DaemonBase

from dmm.db.session import databased
from dmm.models.request import Request, RequestStatus

from dmm.core.sense import delete_instance, is_cancel_ready

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
                
                if status and not is_cancel_ready(status):
                    logging.debug(f"Request {req.sense_uuid} not in cancel-ready status (current: {status}), will try to delete again later")
                    continue
                    
                logging.info(f"Deleting SENSE instance {req.sense_uuid} for request {req.rule_id}")
                delete_instance(req.sense_uuid)
                req.set_status(status=RequestStatus.DELETED, session=session)
                logging.info(f"Successfully deleted SENSE instance for request {req.rule_id}")
                
            except Exception as e:
                logging.error(f"Failed to delete link for {req.rule_id}: {e}", exc_info=True)