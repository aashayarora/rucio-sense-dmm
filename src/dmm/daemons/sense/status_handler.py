import logging
from datetime import datetime

from dmm.utils.config import config_get_int

from dmm.daemons.base import DaemonBase
from dmm.db.request import Request
from dmm.db.session import databased

from sense.client.workflow_combined_api import WorkflowCombinedApi

import re

class SENSEStatusHandlerDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        
    @databased
    def process(self, session=None):
        reqs_provisioned = Request.from_status(status=["STAGED", "PROVISIONED", "CANCELED", "STALE", "DECIDED", "FINISHED"], session=session)
        if reqs_provisioned == []:
            return
        for req in reqs_provisioned:
            workflow_api = WorkflowCombinedApi()
            status = workflow_api.instance_get_status(si_uuid=req.sense_uuid) or "UNKNOWN"
            req.update_sense_circuit_status(status=status, session=session)

            # update sense_provisioned_at if the status is READY for monit
            if req.sense_provisioned_at is None and re.match(r"(CREATE|MODIFY|REINSTATE) - READY$", status):
                req.update({"sense_provisioned_at": datetime.utcnow()})
                fts_limit = config_get_int("fts-streams", f"{req.src_site.name}-{req.dst_site.name}", 200)
                req.update_fts_limit_desired(limit=fts_limit, session=session)

            # TODO: if sense creation fails, should retry
            # at staging step, i.e. before create - committed: 
                # reasons could be failed vlan tag regex, in that case, maybe retry with default vlan tag - mark as allocated and let stager run again
            # at provisioned step, i.e. after create - committed:
                # should put in allocated state, so vlan allocation can be retried
            # at finished step, i.e. after cancel - committed: should force retry

            if re.match(r"(CREATE|MODIFY|REINSTATE) - FAILED", status):
                if req.transfer_status == "PROVISIONED":
                    req.mark_as(status="ALLOCATED", session=session)