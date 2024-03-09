import re

from dmm.db.session import databased
from dmm.utils.db import get_requests, mark_fts_modified
from dmm.utils.fts import modify_link_config, modify_se_config

import logging

num_active = {
    "T2_US_SDSC": {
        "T2_US_Caltech_Test" : 200
    },
    "T1_US_FNAL": {
        "T2_US_Caltech_Test" : 1600
    }
}

@databased
def fts_modifier(session=None):
    logging.debug('Prepares ALLOCATED and PROVISIONED requests')
    #Understanding: Retrieves all Allocated reqs
    reqs_new = [req for req in get_requests(status=["ALLOCATED"], session=session)]
    for allocated_req in reqs_new:
        #Understanding: If none of the reqs are modified, modifies them appropriately
        if not allocated_req.fts_modified:
            num_streams = num_active[allocated_req.src_site][allocated_req.dst_site]
            link_modified = modify_link_config(allocated_req, max_active=num_streams, min_active=num_streams)
            se_modified = modify_se_config(allocated_req, max_inbound=num_streams, max_outbound=num_streams)
            if link_modified and se_modified:
                mark_fts_modified(allocated_req, session=session)

    #Understanding: Retrieves all Provisioned requests        
    reqs = [req for req in get_requests(status=["PROVISIONED"], session=session)]
    for provisioned_req in reqs:
        #Understanding: If none of the reqs have appropriate modifications, modify them
        if not provisioned_req.fts_modified and re.match(r"(CREATE|MODIFY|REINSTATE) - READY$", provisioned_req.sense_circuit_status):
            num_streams = num_active[provisioned_req.src_site][provisioned_req.dst_site]
            link_modified = modify_link_config(provisioned_req, max_active=num_streams, min_active=num_streams)
            se_modified = modify_se_config(provisioned_req, max_inbound=num_streams, max_outbound=num_streams)
            if link_modified and se_modified:
                mark_fts_modified(provisioned_req, session=session)