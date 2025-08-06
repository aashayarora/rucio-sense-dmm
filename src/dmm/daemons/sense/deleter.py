import logging
import re

from dmm.daemons.base import DaemonBase

from dmm.db.session import databased
from dmm.models.request import Request

from sense.client.workflow_combined_api import WorkflowCombinedApi

class SENSEDeleterDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
    
    def process(self, **kwargs):
        self.run_once(**kwargs)

    @databased
    def run_once(self, session=None):
        reqs_cancelled = Request.from_status(status=["CANCELED"], session=session)
        if reqs_cancelled == []:
            return
        for req in reqs_cancelled:
            if req.sense_uuid is None:
                continue
            try:
                workflow_api = WorkflowCombinedApi()
                status = req.sense_circuit_status
                if not re.match(r"(CANCEL) - READY$", status):
                    raise AssertionError(f"Request {req.sense_uuid} not in cancel - ready status, will try to delete again")
                response = workflow_api.instance_delete(si_uuid=req.sense_uuid)
                req.update_transfer_status(status="DELETED", session=session)
            except Exception as e:
                logging.error(f"Failed to delete link for {req.rule_id}, {e}, will try again")