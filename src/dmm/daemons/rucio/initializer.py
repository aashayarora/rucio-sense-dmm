from dmm.daemons.base import DaemonBase

from dmm.db.request import Request
from dmm.db.site import Site
from dmm.db.session import databased

import logging

class RucioInitDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)

    @databased
    def process(self, client=None, session=None):
        rules = client.list_replication_rules()
        for rule in rules:
            if self._is_rule_in_db(rule, session):
                logging.debug(f"rule {rule['id']} already in db, nothing to do")
                continue

            logging.debug(f"evaluating rule {rule['id']}")
            new_request = self._create_request_from_rule(rule, session)
            new_request.save(session=session)

    def _is_rule_in_db(self, rule, session):
        return Request.from_id(rule["id"], session=session) is not None

    def _create_request_from_rule(self, rule, session):
        src_site = Site.from_name(rule["source_replica_expression"], session=session)
        dst_site = Site.from_name(rule["rse_expression"], session=session)
        priority = rule["priority"]
        fts_limit_desired = 20

        if rule["meta"] is None or "sense" not in rule["meta"]:
            logging.debug(f"rule {rule['id']} is not a sense rule, will add to db but do nothing")
            transfer_status = "NOT_SENSE"
        else:
            logging.debug(f"rule {rule['id']} identified as a sense rule")
            transfer_status = "INIT"

        return Request(
            rule_id=rule["id"],
            src_site=src_site,
            dst_site=dst_site,
            priority=priority,
            transfer_status=transfer_status,
            fts_limit_desired=fts_limit_desired,
        )
