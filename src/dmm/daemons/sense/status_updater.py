import logging
from datetime import datetime

from dmm.daemons.base import DaemonBase
from dmm.db.request import Request
from dmm.db.session import databased

from sense.client.workflow_combined_api import WorkflowCombinedApi

import re

class SENSEStatusUpdaterDaemon(DaemonBase):
    @databased
    def process(self, debug_mode=False, session=None):
        if not debug_mode:
            reqs_provisioned = Request.from_status(status=["STAGED", "PROVISIONED", "CANCELED", "STALE", "DECIDED", "FINISHED"], session=session)
            if reqs_provisioned == []:
                return
            for req in reqs_provisioned:
                workflow_api = WorkflowCombinedApi()
                status = workflow_api.instance_get_status(si_uuid=req.sense_uuid) or "UNKNOWN"
                req.update_sense_circuit_status(status=status, session=session)
                if req.sense_provisioned_at is None and re.match(r"(CREATE|MODIFY|REINSTATE) - READY$", status):
                    req.update({"sense_provisioned_at": datetime.utcnow()})
        else:
            logging.debug("status_updater: skipping status update in debug mode")
