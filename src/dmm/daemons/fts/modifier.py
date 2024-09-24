import re
import logging

from dmm.db.session import databased
from dmm.db.request import Request

from dmm.utils.config import config_get_int
from dmm.utils.fts import FTSUtils

from dmm.daemons.base import DaemonBase

class FTSModifierDaemon(DaemonBase, FTSUtils):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        FTSUtils.__init__(self)
    
    @databased
    def process(self, session=None):
        reqs_new = Request.from_status(status=["ALLOCATED"], session=session)
        if reqs_new != []:
            for allocated_req in reqs_new:
                if not allocated_req.fts_modified:
                    num_streams = 20
                    logging.debug(f"Mofifying FTS limits for request {allocated_req.rule_id} to {num_streams} max streams.")
                    link_modified = self.modify_link_config(allocated_req, max_active=num_streams, min_active=num_streams)
                    se_modified = self.modify_se_config(allocated_req, max_inbound=num_streams, max_outbound=num_streams)
                    if link_modified and se_modified:
                        allocated_req.mark_fts_modified(session=session)

        reqs = Request.from_status(status=["PROVISIONED"], session=session)
        if reqs != []:
            for provisioned_req in reqs:
                if not provisioned_req.fts_modified and re.match(r"(CREATE|MODIFY|REINSTATE) - READY$", provisioned_req.sense_circuit_status):
                    num_streams = config_get_int("fts-streams", f"{provisioned_req.src_site}-{provisioned_req.dst_site}", 200)
                    logging.debug(f"request {provisioned_req.rule_id} in ready state, modifying fts limits to {num_streams} max streams.")
                    link_modified = self.modify_link_config(provisioned_req, max_active=num_streams, min_active=num_streams)
                    se_modified = self.modify_se_config(provisioned_req, max_inbound=num_streams, max_outbound=num_streams)
                    if link_modified and se_modified:
                        provisioned_req.mark_fts_modified(session=session)

        reqs_deleted = Request.from_status(status=["DELETED"], session=session)
        if reqs_deleted != []:
            for deleted_req in reqs_deleted:
                logging.debug(f"Deleting FTS limits for request {deleted_req.rule_id}")
                self.delete_config(deleted_req)
