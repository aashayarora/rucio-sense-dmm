from dmm.daemons.base import DaemonBase

from dmm.db.request import Request
from dmm.db.session import databased

import logging

class RucioModifierDaemon(DaemonBase):
    @databased
    def process(self, client=None, session=None):
        reqs = Request.from_status(status=["ALLOCATED", "STAGED", "DECIDED", "PROVISIONED"], session=session)
        if reqs == []:
            logging.debug("rucio_modifier: nothing to do")
            return
        req_prio_changed = False
        for req in reqs:
            curr_prio_in_rucio = client.get_replication_rule(req.rule_id)["priority"]
            if req.priority != curr_prio_in_rucio:
                req_prio_changed = True
                logging.debug(f"{req.rule_id} priority changed from {req.priority} to {curr_prio_in_rucio}")
                req.update_priority(priority=curr_prio_in_rucio, session=session)
                req.mark_as(status="MODIFIED", session=session)
        if not req_prio_changed:
            logging.debug("No priority changes detected in rucio")
