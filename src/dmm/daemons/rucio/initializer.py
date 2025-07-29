import logging

from dmm.daemons.base import DaemonBase
from dmm.models.request import Request
from dmm.models.site import Site
from dmm.db.session import databased

class RucioInitDaemon(DaemonBase):
    """
    Daemon to initialize Rucio rules and create requests in the database.
    """
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)

    def process(self, **kwargs):
        self.run_once(**kwargs)

    @databased
    def run_once(self, client=None, session=None) -> None:
        """
        Process Rucio rules and create requests in the database.
        """
        rules = client.list_replication_rules()
        for rule in rules:
            if self._is_rule_in_db(rule, session):
                logging.debug(f"Rule {rule['id']} already exists in the database.")
                continue
            
            if rule.get("state") == "OK":
                logging.debug(f"Rule {rule['id']} is already finished; skipping.")
                continue
            elif rule.get("state") == "STUCK":
                logging.debug(f"Rule {rule['id']} is stuck; skipping.")
                continue

            logging.debug(f"Processing rule {rule['id']}.")
            try:
                new_request = self._create_request_from_rule(rule, client, session)
                new_request.save(session=session)
                logging.info(f"Created new request for rule {rule['id']}.")
            except Exception as e:
                logging.error(f"Failed to create request for rule {rule['id']}: {e}")
                continue

    def _is_rule_in_db(self, rule, session) -> bool:
        """
        Check if the rule already exists in the database.
        """
        return Request.from_id(rule["id"], session=session) is not None

    def _get_rule_size(self, rule, client) -> int:
        """
        Get the total size of the files in the rule (in bytes).
        """
        try:
            return sum([i.get("bytes") for i in client.list_files(scope=rule["scope"], name=rule["name"])])
        except Exception as e:
            logging.error(f"Failed to get rule size for rule {rule['id']}: {e}")
            return None

    def _create_request_from_rule(self, rule, client, session) -> Request:
        """
        Create a new request from the given rule.
        """
        src_site_name = rule.get("source_replica_expression")
        dst_site_name = rule.get("rse_expression")
        src_site = Site.from_name(src_site_name, session=session)
        dst_site = Site.from_name(dst_site_name, session=session)
        
        if not src_site or not dst_site:
            raise ValueError(f"Source or destination site not found for rule {rule['id']}.")

        priority = rule.get("priority")
        fts_limit_desired = 20 # Default value for fts limits when the rule is added (before SENSE circuit is provisioned)

        activity = rule.get("activity", None) # activity for SENSE rules contains SENSE
        if not activity or "sense" not in activity.lower():
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