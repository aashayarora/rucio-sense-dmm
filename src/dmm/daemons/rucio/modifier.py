import logging

from dmm.daemons.base import DaemonBase
from dmm.models.request import Request
from dmm.db.session import databased

class RucioModifierDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)

    @databased
    def process(self, client=None, session=None):
        reqs = Request.from_status(status=["ALLOCATED", "STAGED", "DECIDED", "PROVISIONED"], session=session)
        if not reqs:
            return
        
        for req in reqs:
            curr_prio_in_rucio = client.get_replication_rule(req.rule_id)["priority"]
            if req.priority != curr_prio_in_rucio:
                self._update_request_priority(req, curr_prio_in_rucio, session)

    def _update_request_priority(self, req, new_priority, session):
        logging.debug(f"{req.rule_id} priority changed from {req.priority} to {new_priority}")
        req.update_priority(priority=new_priority, session=session)
        req.mark_as(status="MODIFIED", session=session)
