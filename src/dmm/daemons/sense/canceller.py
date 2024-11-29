import logging
from datetime import datetime, timezone
import re

from dmm.daemons.base import DaemonBase

from dmm.db.session import databased
from dmm.db.request import Request

from sense.client.workflow_combined_api import WorkflowCombinedApi

class SENSECancellerDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        
    @databased
    def process(self, session=None):
        reqs_finished = Request.from_status(status=["FINISHED"], session=session)
        if reqs_finished == []:
            return
        for req in reqs_finished:
            if req.sense_uuid is None:
                continue
            if (datetime.now(timezone.utc) - req.updated_at).seconds > 60:
                try:
                    logging.info(f"cancelling sense link with uuid {req.sense_uuid}")
                    workflow_api = WorkflowCombinedApi()
                    status = req.sense_circuit_status
                    if re.match(r"(CANCEL) - READY$", status):
                        logging.debug(f"Request {req.sense_uuid} already in ready status, marking as canceled")
                        req.src_endpoint.mark_inuse(in_use=False, session=session)
                        req.dst_endpoint.mark_inuse(in_use=False, session=session)
                        req.mark_as(status="CANCELED", session=session)
                        continue
                    if not re.match(r"(CREATE|MODIFY|REINSTATE) - READY$", status):
                        raise ValueError(f"Cannot cancel an instance in status '{status}', will try to cancel again")
                    response = workflow_api.instance_operate("cancel", si_uuid=req.sense_uuid, sync="true", force=str("READY" not in status).lower())
                    req.src_endpoint.mark_inuse(in_use=False, session=session)
                    req.dst_endpoint.mark_inuse(in_use=False, session=session)
                    req.mark_as(status="CANCELED", session=session)
                except Exception as e:
                    logging.error(f"Failed to cancel link for {req.rule_id}, {e}, will try again")
