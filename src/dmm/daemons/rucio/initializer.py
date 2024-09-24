from dmm.daemons.base import DaemonBase

from dmm.db.request import Request
from dmm.db.session import databased

import logging

class RucioInitDaemon(DaemonBase):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

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
