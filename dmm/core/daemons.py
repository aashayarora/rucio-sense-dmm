from time import sleep
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import logging

from dmm.utils.db import get_request_by_status, mark_requests, get_site, update_bandwidth
from dmm.utils.fts import modify_link_config, modify_se_config
import dmm.utils.sense as sense
from dmm.db.session import databased

def stage_sense_link(req, session):
    logging.info(f"Staging SENSE link for request {req.request_id}")
    if req.src_ipv6_block != "best_effort":
        sense_link_id, _ = sense.stage_link(
            get_site(req.src_site, session=session).sense_uri,
            get_site(req.dst_site, session=session).sense_uri,
            req.src_ipv6_block,
            req.dst_ipv6_block,
            instance_uuid="",
            alias=req.request_id
        )
        req.update({"sense_link_id": sense_link_id})
        modify_link_config(req, max_active=50, min_active=50)
        modify_se_config(req, max_inbound=50, max_outbound=50)
    mark_requests([req], "STAGED", session)
    logging.info(f"SENSE instance with UUID {sense_link_id} staged for request {req.request_id}")

def provision_sense_link(req, session):
    logging.info(f"Provisioning SENSE link UUID {req.sense_link_id} for request {req.request_id} with bandwidth {req.bandwidth}")
    if req.src_ipv6_block != "best_effort":
        sense.provision_link(
            req.sense_link_id,
            get_site(req.src_site, session=session).sense_uri,
            get_site(req.dst_site, session=session).sense_uri,
            req.src_ipv6_block,
            req.dst_ipv6_block,
            int(req.bandwidth),
            alias=req.request_id
        )
        modify_link_config(req, max_active=500, min_active=500)
        modify_se_config(req, max_inbound=500, max_outbound=500)
    mark_requests([req], "PROVISIONED", session)
    logging.info(f"SENSE link UUID {req.sense_link_id} for request {req.request_id} with bandwidth {req.bandwidth} Provisioned")

def modify_sense_link(req, session):
    logging.info(f"Modifying SENSE link UUID {req.sense_link_id} for request {req.request_id} with bandwidth {req.bandwidth}")
    if req.src_ipv6_block != "best_effort":
        sense.modify_link(
            req.sense_link_id,
            int(req.bandwidth),
            alias=req.request_id
        )
    logging.info(f"SENSE link UUID {req.sense_link_id} for request {req.request_id} with bandwidth {req.bandwidth} Modified")

@databased
def stager_daemon(session=None):
    reqs_init = [req for req in get_request_by_status(status=["ALLOCATED"], session=session)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        for req in reqs_init:
            executor.submit(stage_sense_link, req, session)

@databased
def decision_daemon(network_graph=None, session=None):
    network_graph.update()
    reqs_staged = [req for req in get_request_by_status(status=["STAGED"], session=session)]
    for req in reqs_staged:
        if req.src_ipv6_block != "best_effort":
            allocated_bandwidth = network_graph.get_bandwidth_for_request_id(req.request_id)
            update_bandwidth(req, allocated_bandwidth, session=session)
        mark_requests([req], "DECIDED", session)
    reqs_provisioned = [req for req in get_request_by_status(status=["PROVISIONED"], session=session)]
    for req in reqs_provisioned:
        if req.src_ipv6_block != "best_effort":
            allocated_bandwidth = network_graph.get_bandwidth_for_request_id(req.request_id)
            if allocated_bandwidth != req.bandwidth:
                update_bandwidth(req, allocated_bandwidth, session=session)
                mark_requests([req], "STALE", session)
    
@databased
def provision_daemon(session=None):
    reqs_decided = [req for req in get_request_by_status(status=["DECIDED"], session=session)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        for req in reqs_decided:
            executor.submit(provision_sense_link, req, session)

@databased
def modifier_daemon(session=None):
    reqs_stale = [req for req in get_request_by_status(status=["STALE"], session=session)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        for req in reqs_stale:
            executor.submit(modify_sense_link, req, session)
            mark_requests([req], "PROVISIONED", session)

@databased
def reaper_daemon(session=None):
    reqs_finished = [req for req in get_request_by_status(status=["FINISHED"], session=session)]
    for req in reqs_finished:
        if (datetime.utcnow() - req.updated_at).seconds > 600:
            sense.delete_link(req.sense_link_id)
            sense.free_allocation(req.src_site, req.request_id+"_src")
            sense.free_allocation(req.dst_site, req.request_id+"_dst")
            req.delete(session)
            mark_requests([req], "DELETED", session)

def run_daemon(daemon, lock, frequency, **kwargs):
    while True:
        logging.info(f"Running {daemon.__name__}")
        with lock:
            try:
                daemon(**kwargs)
            except Exception as e:
                logging.error(f"{daemon.__name__} {e}")
        sleep(frequency)
        logging.info(f"{daemon.__name__} sleeping for {frequency} seconds")