import re
import logging

from dmm.db.session import databased
from dmm.db.request import Request

from dmm.utils.fts import FTSUtils

from dmm.daemons.base import DaemonBase

class FTSModifierDaemon(DaemonBase, FTSUtils):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        FTSUtils.__init__(self)
    
    @databased
    def process(self, session=None):
        reqs = Request.from_status(status=["ALLOCATED", "DECIDED", "PROVISIONED"], session=session)
        if reqs != []:
            for req in reqs:
                if req.fts_limit_current != req.fts_limit_desired:
                    logging.debug(f"Modifying FTS limits for request {req.rule_id}, from {req.fts_limit_current} to {req.fts_limit_desired}")
                    link_modified = self.modify_link_config(req, max_active=req.fts_limit_desired, min_active=req.fts_limit_desired)
                    se_modified = self.modify_se_config(req, max_inbound=req.fts_limit_desired, max_outbound=req.fts_limit_desired)
                    if link_modified and se_modified:
                        req.update_fts_limit_current(limit=req.fts_limit_desired, session=session)

        reqs_deleted = Request.from_status(status=["DELETED"], session=session)
        if reqs_deleted != []:
            for deleted_req in reqs_deleted:
                if deleted_req.fts_limit_current != 0:
                    logging.debug(f"Deleting FTS limits for request {deleted_req.rule_id}")
                    self.delete_config(deleted_req)
                    deleted_req.update_fts_limit_current(limit=0, session=session)