import logging
from datetime import datetime

import dmm.utils.sense as sense

from dmm.daemons.base import DaemonBase

from dmm.db.session import databased
from dmm.db.request import Request

import re

@databased
def status_updater(debug_mode=False, session=None):
    if not debug_mode:
        reqs_provisioned = [req for req in get_requests(status=["STAGED", "PROVISIONED", "CANCELED", "STALE", "DECIDED", "FINISHED"], session=session)]
        if reqs_provisioned == []:
            logging.debug("status_updater: nothing to do")
            return
        for req in reqs_provisioned:
            status = sense.get_sense_circuit_status(req.sense_uuid)
            update_sense_circuit_status(req, status, session=session)
            if req.sense_provisioned_at is None and re.match(r"(CREATE|MODIFY|REINSTATE) - READY$", status):
                req.update({"sense_provisioned_at": datetime.utcnow()})
    else:
        logging.debug("status_updater: skipping status update in debug mode")

@databased
def stager(debug_mode=False, session=None):
    def stage_sense_link(req, session):
        if not debug_mode:
            try:
                sense_uuid, max_bandwidth = sense.stage_link(
                    src_uri=get_site(req.src_site, attr="sense_uri", session=session),
                    dst_uri=get_site(req.dst_site, attr="sense_uri", session=session),
                    src_ipv6=req.src_ipv6_block,
                    dst_ipv6=req.dst_ipv6_block,
                    vlan_range=get_vlan_range(req.src_site, req.dst_site, session=session),
                    instance_uuid="",
                    alias=req.rule_id
                )
                req.update({"sense_uuid": sense_uuid, "max_bandwidth": max_bandwidth})
                mark_requests([req], "STAGED", session)
            except Exception as e:
                logging.error(f"Failed to stage link for {req.rule_id}, {e}, will try again")
        else:
            try:
                req.update({"sense_uuid": "debug", "max_bandwidth": 10000})
                mark_requests([req], "STAGED", session)
            except Exception as e:
                logging.error(f"Failed to stage link for {req.rule_id}, {e}, will try again")
    reqs_init = [req for req in get_requests(status=["ALLOCATED"], session=session)]
    if reqs_init == []:
        logging.debug("stager: nothing to do")
        return
    with ThreadPoolExecutor(max_workers=4) as executor:
        for req in reqs_init:
            executor.submit(stage_sense_link, req, session)
    
@databased
def provision(debug_mode=False, session=None):
    def provision_sense_link(req, session):
        if not debug_mode:
            try:
                sense.provision_link(
                    instance_uuid=req.sense_uuid,
                    src_uri=get_site(req.src_site, attr="sense_uri", session=session),
                    dst_uri=get_site(req.dst_site, attr="sense_uri", session=session),
                    src_ipv6=req.src_ipv6_block,
                    dst_ipv6=req.dst_ipv6_block,
                    bandwidth=int(req.bandwidth),
                    vlan_range=get_vlan_range(req.src_site, req.dst_site, session=session),
                    alias=req.rule_id
                )
                mark_requests([req], "PROVISIONED", session)
            except Exception as e:
                logging.error(f"Failed to provision link for {req.rule_id}, {e}, will try again")
        else:
            try:
                mark_requests([req], "PROVISIONED", session)
            except Exception as e:
                logging.error(f"Failed to provision link for {req.rule_id}, {e}, will try again")
    reqs_decided = [req for req in get_requests(status=["DECIDED"], session=session)]
    if reqs_decided == []:
        logging.debug("provisioner: nothing to do")
        return
    with ThreadPoolExecutor(max_workers=4) as executor:
        for req in reqs_decided:
            executor.submit(provision_sense_link, req, session)

@databased
def sense_modifier(debug_mode=False, session=None):
    def modify_sense_link(req):
        if not debug_mode:
            try:
                sense.modify_link(
                    instance_uuid=req.sense_uuid,
                    src_uri=get_site(req.src_site, attr="sense_uri", session=session),
                    dst_uri=get_site(req.dst_site, attr="sense_uri", session=session),
                    src_ipv6=req.src_ipv6_block,
                    dst_ipv6=req.dst_ipv6_block,
                    bandwidth=int(req.bandwidth),
                    vlan_range=get_vlan_range(req.src_site, req.dst_site, session=session),
                    alias=req.rule_id
                )
            except Exception as e:
                logging.error(f"Failed to modify link for {req.rule_id}, {e}, will try again")
            finally:
                mark_requests([req], "PROVISIONED", session)
        else:
            try:
                mark_requests([req], "PROVISIONED", session)
            except Exception as e:
                logging.error(f"Failed to modify link for {req.rule_id}, {e}, will try again")
    reqs_stale = [req for req in get_requests(status=["STALE"], session=session)]
    if reqs_stale == []:
        logging.debug("sense_modifier: nothing to do")
        return
    with ThreadPoolExecutor(max_workers=4) as executor:
        for req in reqs_stale:
            executor.submit(modify_sense_link, req)

@databased
def canceller(debug_mode=False, session=None):
    reqs_finished = [req for req in get_requests(status=["FINISHED"], session=session)]
    if reqs_finished == []:
        logging.debug("canceller: nothing to do")
        return
    for req in reqs_finished:
        if not debug_mode:
            if (datetime.utcnow() - req.updated_at).seconds > 600:
                try:
                    sense.cancel_link(instance_uuid=req.sense_uuid)
                    sense.free_allocation(req.src_site, req.rule_id)
                    sense.free_allocation(req.dst_site, req.rule_id)
                    mark_requests([req], "CANCELED", session=session)
                except Exception as e:
                    logging.error(f"Failed to cancel link for {req.rule_id}, {e}, will try again")
        
@databased
def deleter(debug_mode=False, session=None):
    reqs_cancelled = [req for req in get_requests(status=["CANCELED"], session=session)]
    if reqs_cancelled == []:
        logging.debug("deleter: nothing to do")
        return
    for req in reqs_cancelled:
        if not debug_mode:
            try:
                sense.delete_link(instance_uuid=req.sense_uuid)
                mark_requests([req], "DELETED", session=session)
            except Exception as e:
                logging.error(f"Failed to delete link for {req.rule_id}, {e}, will try again")

class SENSEStatusUpdaterDaemon(DaemonBase):
    @databased
    def process(self, debug_mode=False, session=None):
        if not debug_mode:
            reqs_provisioned = Request.from_status(status=["STAGED", "PROVISIONED", "CANCELED", "STALE", "DECIDED", "FINISHED"], session=session)
            if reqs_provisioned == []:
                logging.debug("status_updater: nothing to do")
                return
            for req in reqs_provisioned:
                status = sense.get_sense_circuit_status(req.sense_uuid)
                req.update_sense_circuit_status(status=status, session=session)
                if req.sense_provisioned_at is None and re.match(r"(CREATE|MODIFY|REINSTATE) - READY$", status):
                    req.update({"sense_provisioned_at": datetime.utcnow()})
        else:
            logging.debug("status_updater: skipping status update in debug mode")

class SENSEStagerDaemon(DaemonBase):
    @databased
    def process(self, debug_mode=False, session=None):
        def stage_sense_link(req, session):
            if not debug_mode:
                try:
                    sense_uuid, max_bandwidth = sense.stage_link(
                        src_uri=get_site(req.src_site, attr="sense_uri", session=session),
                        dst_uri=get_site(req.dst_site, attr="sense_uri", session=session),
                        src_ipv6=req.src_ipv6_block,
                        dst_ipv6=req.dst_ipv6_block,
                        vlan_range=get_vlan_range(req.src_site, req.dst_site, session=session),
                        instance_uuid="",
                        alias=req.rule_id
                    )
                    req.update({"sense_uuid": sense_uuid, "max_bandwidth": max_bandwidth})
                    mark_requests([req], "STAGED", session)
                except Exception as e:
                    logging.error(f"Failed to stage link for {req.rule_id}, {e}, will try again")
            else:
                try:
                    req.update({"sense_uuid": "debug", "max_bandwidth": 10000})
                    mark_requests([req], "STAGED", session)
                except Exception as e:
                    logging.error(f"Failed to stage link for {req.rule_id}, {e}, will try again")
        reqs_init = Request.from_status(status=["ALLOCATED"], session=session)
        if reqs_init == []:
            logging.debug("stager: nothing to do")
            return
        with ThreadPoolExecutor(max_workers=4) as executor:
            for req in reqs_init:
                executor.submit(stage_sense_link, req, session)