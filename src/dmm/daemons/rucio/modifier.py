import logging

from dmm.daemons.base import DaemonBase
from dmm.models.request import Request, RequestStatus
from dmm.db.session import databased

from dmm.core.rucio import get_rule_priority

from rucio.common.exception import RuleNotFound

class RucioModifierDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)

    def process(self, **kwargs):
        self.run_once(**kwargs)

    @databased
    def run_once(self, client=None, session=None):
        reqs = Request.get_by_status(statuses=[RequestStatus.ALLOCATED, RequestStatus.STAGED, RequestStatus.DECIDED, RequestStatus.PROVISIONED], session=session)
        if not reqs:
            return
        
        for req in reqs:
            try:
                curr_prio_in_rucio = get_rule_priority(client, req.rule_id)
            except RuleNotFound:
                continue  # will be taken care of by finisher
            if req.priority != curr_prio_in_rucio:
                self._update_request_priority(req, curr_prio_in_rucio, session)

    def _update_request_priority(self, req, new_priority, session):
        logging.info(f"{req.rule_id} priority changed from {req.priority} to {new_priority}")
        req.set_priority(priority=new_priority, session=session)
        if req.transfer_status == RequestStatus.PROVISIONED:
            req.set_status(status=RequestStatus.MODIFIED, session=session)
