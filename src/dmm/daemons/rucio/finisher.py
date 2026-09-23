import logging

from dmm.daemons.base import DaemonBase

from dmm.models.base import utcnow
from dmm.models.request import Request, RequestStatus
from dmm.db.session import databased

from dmm.core.rucio import get_rule_status, is_rule_ok, is_rule_stuck
from dmm.core.sense import modify_link

from rucio.common.exception import RuleNotFound


class RucioFinisherDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)

    def process(self, **kwargs):
        self.run_once(**kwargs)
    
    @databased
    def run_once(self, client=None, session=None):
        reqs = Request.get_by_status(
            statuses=[
                RequestStatus.ALLOCATED, RequestStatus.STAGED, RequestStatus.DECIDED,
                RequestStatus.PROVISIONED, RequestStatus.STALE, RequestStatus.MODIFIED,
            ],
            session=session,
        )
        if not reqs:
            return
        
        for req in reqs:
            self._process_request(req, client, session)

    def _process_request(self, req, client, session):
        try:
            status = get_rule_status(client, req.rule_id)
        except RuleNotFound as e:
            logging.error(f"Request {req.rule_id} not found in Rucio (probably because of the reaper), marking as FINISHED_R in DMM")
            req.set_status(status=RequestStatus.FINISHED_R, session=session)
            req.update({"rucio_finished_at": utcnow()}, session=session)
            return

        if is_rule_ok(status):
            logging.info(f"Request {req.rule_id} finished with status {status}")
            req.set_status(status=RequestStatus.FINISHED_R, session=session)  # Mark request as finished
            req.update({"rucio_finished_at": utcnow()}, session=session)
        elif is_rule_stuck(status):
            logging.info(f"Request {req.rule_id} is stuck, marking as FINISHED in DMM so circuit can be taken down")
            req.set_status(status=RequestStatus.FINISHED_R, session=session)
            req.update({"rucio_finished_at": utcnow()}, session=session)