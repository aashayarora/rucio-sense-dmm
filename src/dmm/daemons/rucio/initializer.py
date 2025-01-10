import logging

from dmm.daemons.base import DaemonBase
from dmm.db.request import Request
from dmm.db.site import Site
from dmm.db.session import databased

class RucioInitDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)

    @databased
    def process(self, client=None, session=None):
        rules = client.list_replication_rules()
        for rule in rules:
            if self._is_rule_in_db(rule, session):
                logging.debug(f"Rule {rule['id']} already exists in the database.")
                continue
            
            if rule.get("state") in ["OK", "STUCK"]:
                logging.debug(f"Rule {rule['id']} is already finished; skipping.")
                continue

            logging.debug(f"Processing rule {rule['id']}.")
            try:
                new_request = self._create_request_from_rule(rule, client, session)
                new_request.save(session=session)
                logging.info(f"Created new request for rule {rule['id']}.")
            except Exception as e:
                logging.error(f"Failed to create request for rule {rule['id']}: {e}")
                continue

    def _is_rule_in_db(self, rule, session):
        return Request.from_id(rule["id"], session=session) is not None

    def _get_rule_size(self, rule, client):
        try:
            return sum([i.get("bytes") for i in client.list_files(scope=rule["scope"], name=rule["name"])])
        except Exception as e:
            logging.error(f"Failed to get rule size for rule {rule['id']}: {e}")
            return None

    def _create_request_from_rule(self, rule, client, session):
        src_site_name = rule.get("source_replica_expression")
        dst_site_name = rule.get("rse_expression")
        src_site = Site.from_name(src_site_name, session=session)
        dst_site = Site.from_name(dst_site_name, session=session)
        
        if not src_site or not dst_site:
            raise ValueError(f"Source or destination site not found for rule {rule['id']}.")

        priority = rule.get("priority")
        fts_limit_desired = 20

        meta = rule.get("meta", {})
        if not meta or "sense" not in meta:
            logging.debug(f"Rule {rule['id']} is not a SENSE rule; setting status to 'NOT_SENSE'.")
            transfer_status = "NOT_SENSE"
        else:
            logging.debug(f"Rule {rule['id']} identified as a SENSE rule.")
            transfer_status = "INIT"

        rule_size = self._get_rule_size(rule, client)

        return Request(
            rule_id=rule["id"],
            src_site=src_site,
            dst_site=dst_site,
            priority=priority,
            rule_size=rule_size,
            transfer_status=transfer_status,
            fts_limit_desired=fts_limit_desired,
        )    