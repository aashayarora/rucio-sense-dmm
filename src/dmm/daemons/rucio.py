from dmm.daemons.base import DaemonBase

from dmm.db.request import Request
from dmm.db.session import databased

import logging

class RucioInitDaemon(DaemonBase):
    @databased
    def process(self, client=None, session=None):
        rules = client.list_replication_rules()
        for rule in rules:
            if Request.from_id(rule["id"], session=session) is not None:
                logging.debug(f"rule {rule['id']} already in db, nothing to do")
                continue
            else:
                logging.debug(f"evaluating rule {rule['id']}")
                if ((rule["meta"] is None) and ("sense" not in rule["meta"])):
                    logging.debug(f"rule {rule['id']} is not a sense rule, will add to db but do nothing")
                    new_request = Request(rule_id=rule["id"],
                                            src_site=rule["source_replica_expression"], 
                                            dst_site=rule["rse_expression"],
                                            priority=rule["priority"],
                                            transfer_status="NOT_SENSE",
                                        )
                else:
                    logging.debug(f"rule {rule['id']} identified as a sense rule")
                    new_request = Request(rule_id=rule["id"],
                                            src_site=rule["source_replica_expression"], 
                                            dst_site=rule["rse_expression"],
                                            priority=rule["priority"],
                                            transfer_status="INIT",
                                        )
                    new_request.save(session=session)

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

class RucioFinisherDaemon(DaemonBase):
    @databased
    def process(self, client=None, session=None):
        reqs = Request.from_status(status=["ALLOCATED", "STAGED", "DECIDED", "PROVISIONED"], session=session)
        if reqs == []:
            logging.debug("finisher: nothing to do")
            return
        for req in reqs:
            status = client.get_replication_rule(req.rule_id)['state']
            if status in ["OK", "STUCK"]:
                logging.debug(f"Request {req.rule_id} finished with status {status}")
                req.mark_as(status="FINISHED", session=session)  # Mark request as finished