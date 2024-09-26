import logging
import re

from dmm.daemons.base import DaemonBase

from dmm.utils.sense import SENSEUtils

from dmm.db.session import databased
from dmm.db.request import Request

from sense.client.workflow_combined_api import WorkflowCombinedApi

class SENSEDeleterDaemon(DaemonBase, SENSEUtils):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        SENSEUtils.__init__(self)
        
    @databased
    def process(self, session=None):
        reqs_cancelled = Request.from_status(status=["CANCELED"], session=session)
        if reqs_cancelled == []:
            return
        for req in reqs_cancelled:
            try:
                workflow_api = WorkflowCombinedApi()
                status = req.sense_circuit_status
                if not re.match(r"(CANCEL) - READY$", status):
                    logging.debug(f"Request not in ready status, will try to delete again")
                    raise AssertionError(f"Request {req.sense_uuid} not in compiled status, will try to delete again")
                response = workflow_api.instance_delete(si_uuid=req.sense_uuid)
                req.mark_as(status="DELETED", session=session)
            except Exception as e:
                logging.error(f"Failed to delete link for {req.rule_id}, {e}, will try again")