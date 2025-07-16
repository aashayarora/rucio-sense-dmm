from dmm.daemons.base import DaemonBase

from dmm.models.request import Request
from dmm.db.session import databased

import logging

class RucioFinisherDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)

    def process(self, **kwargs):
        self.run_once(**kwargs)
    
    @databased
    def run_once(self, client=None, session=None):
        reqs = Request.from_status(status=["ALLOCATED", "STAGED", "DECIDED", "PROVISIONED"], session=session)
        if not reqs:
            return
        
        for req in reqs:
            self._process_request(req, client, session)

    def _process_request(self, req, client, session):
        status = client.get_replication_rule(req.rule_id)['state']
        if status == "OK":
            logging.debug(f"Request {req.rule_id} finished with status {status}")
            req.update_transfer_status(status="FINISHED", session=session)  # Mark request as finished
            req.update_fts_limit_current(limit=0, session=session)  # Remove FTS limits
        elif status == "STUCK":
            logging.debug(f"Request {req.rule_id} is stuck, marking as FINISHED in DMM so circuit can be taken down")
            req.update_transfer_status(status="FINISHED", session=session)