from dmm.utils.db import mark_requests, get_requests, get_request_from_id, update_priority
from dmm.db.models import Request
from dmm.db.session import databased
import logging

@databased
def preparer(client=None, session=None):
    # logging.debug('Creates requests based on the list of rules from client')
    rules = client.list_replication_rules()
    for rule in rules:
        if (rule["meta"] is not None) and ("sense" in rule["meta"]) and (get_request_from_id(rule["id"], session=session) is None):
            new_request = Request(rule_id=rule["id"], 
                                    src_site=rule["source_replica_expression"], 
                                    dst_site=rule["rse_expression"],
                                    priority=rule["priority"],
                                    transfer_status="INIT",
                                    )
            new_request.save(session=session)

@databased
def rucio_modifier(client=None, session=None):
    # logging.debug('Ensures all reqs have their priority up to date')
    #Understanding: Gets all requests with status ALLOCATED, STAGED, DECIDED, and PROVISIONED
    reqs = get_requests(status=["ALLOCATED", "STAGED", "DECIDED", "PROVISIONED"], session=session)
    for req in reqs:
        #Understanding: Gets the priority of the current request
        #Understanding: If the priority doesn't match the client's priority, updates it for the current req
        #Understanding: Then changes its status to MODIFIED
        curr_prio_in_rucio = client.get_replication_rule(req.rule_id)["priority"]
        if req.priority != curr_prio_in_rucio:
            update_priority(req, curr_prio_in_rucio, session=session)
            mark_requests([req], "MODIFIED", session=session)

# updates request status in db, daemon just deregisters request
@databased
def finisher(client=None, session=None):
    # logging.debug('Marks completed requests with FINISHED status')
    reqs = get_requests(status=["ALLOCATED", "STAGED", "DECIDED", "PROVISIONED"], session=session)
    for req in reqs:
        status = client.get_replication_rule(req.rule_id)['state']
        if status in ["OK", "STUCK"]:
            mark_requests([req], "FINISHED", session=session)