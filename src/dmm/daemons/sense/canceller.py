import logging
from datetime import datetime
import re

from dmm.daemons.base import DaemonBase

from dmm.db.session import databased
from dmm.db.request import Request
from dmm.db.endpoint import Endpoint

from dmm.utils.sense import SENSEUtils

from sense.client.workflow_combined_api import WorkflowCombinedApi

class SENSECancellerDaemon(DaemonBase, SENSEUtils):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        SENSEUtils.__init__(self)
        
    @databased
    def process(self, session=None):
        reqs_finished = Request.from_status(status=["FINISHED"], session=session)
        if reqs_finished == []:
            return
        for req in reqs_finished:
            if (datetime.utcnow() - req.updated_at).seconds > 60:
                try:
                    logging.info(f"cancelling sense link with uuid {req.sense_uuid}")
                    workflow_api = WorkflowCombinedApi()
                    status = req.sense_circuit_status
                    if re.match(r"(CANCEL) - READY$", status):
                        logging.debug(f"Request {req.sense_uuid} already in ready status, marking as canceled")
                        req.mark_as(status="CANCELED", session=session)
                        continue
                    if not re.match(r"(CREATE|MODIFY|REINSTATE) - READY$", status):
                        raise ValueError(f"Cannot cancel an instance in status '{status}', will try to cancel again")
                    response = workflow_api.instance_operate("cancel", si_uuid=req.sense_uuid, sync="true", force=str("READY" not in status).lower())
                    self.free_allocation(req.src_site.name, req.rule_id)
                    self.free_allocation(req.dst_site.name, req.rule_id)
                    req.src_endpoint.mark_inuse(in_use=False, session=session)
                    req.dst_endpoint.mark_inuse(in_use=False, session=session)
                    req.mark_as(status="CANCELED", session=session)
                except Exception as e:
                    logging.error(f"Failed to cancel link for {req.rule_id}, {e}, will try again")