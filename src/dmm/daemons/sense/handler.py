import logging
from datetime import datetime
from collections import defaultdict
from math import floor

from dmm.core.config import config_get_int
from dmm.daemons.base import DaemonBase
from dmm.models.request import Request, RequestStatus
from dmm.db.session import databased

from dmm.core.sense import (
    get_instance_status,
    is_affiliated_state,
    is_circuit_active,
    is_create_ready,
    is_create_failed,
    is_modify_failed,
    is_being_modified,
    is_ready_for_modify,
)
from dmm.core.allocation import affiliate_endpoints
from dmm.core.utils import release_endpoints_and_addresses

class SENSEHandlerDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        
    def process(self, **kwargs):
        self.run_once(**kwargs)

    @staticmethod
    def _split_streams_proportionally(reqs, max_streams: int) -> dict:
        if not reqs:
            return {}

        max_streams = max(0, int(max_streams or 0))
        n_reqs = len(reqs)

        if max_streams == 0:
            return {req.rule_id: 0 for req in reqs}

        priorities = [max(0, int(req.priority or 0)) for req in reqs]
        total_priority = sum(priorities)

        if total_priority == 0:
            share, remainder = divmod(max_streams, n_reqs)
            return {req.rule_id: share + (1 if i < remainder else 0) for i, req in enumerate(reqs)}

        exact_shares = [max_streams * (prio / total_priority) for prio in priorities]
        floored_shares = [floor(share) for share in exact_shares]
        allocation = {req.rule_id: floored_shares[idx] for idx, req in enumerate(reqs)}

        assigned = sum(floored_shares)
        remainder = max_streams - assigned
        if remainder > 0:
            ranked = sorted(
                [(idx, exact_shares[idx] - floored_shares[idx], priorities[idx], reqs[idx].rule_id) for idx in range(n_reqs)],
                key=lambda x: (-x[1], -x[2], x[3])
            )
            for i in range(remainder):
                req_idx = ranked[i][0]
                allocation[reqs[req_idx].rule_id] += 1

        return allocation

    @staticmethod
    def _pair_stream_cap(src_site_name: str, dst_site_name: str) -> int:
        default_streams = config_get_int("fts", "default_num_streams", default=200)
        pair_stream = config_get_int("fts-streams", f"{src_site_name}-{dst_site_name}", default=None)
        if pair_stream is None:
            pair_stream = config_get_int("fts-streams", f"{dst_site_name}-{src_site_name}", default=None)
        return pair_stream if pair_stream is not None else default_streams

    def _rebalance_fts_streams(self, session) -> None:
        active_reqs = Request.get_by_status(
            statuses=[RequestStatus.PROVISIONED],
            session=session,
        )
        if not active_reqs:
            return

        grouped_by_pair = defaultdict(list)
        for req in active_reqs:
            if not is_circuit_active(req.sense_circuit_status):
                continue
            if not req.src_site or not req.dst_site:
                continue
            if not req.src_endpoint or not req.dst_endpoint:
                continue
            grouped_by_pair[(req.src_site.name, req.dst_site.name)].append(req)

        for (src_site_name, dst_site_name), reqs in grouped_by_pair.items():
            max_streams = self._pair_stream_cap(src_site_name, dst_site_name)
            allocations = self._split_streams_proportionally(reqs, max_streams)

            for req in reqs:
                desired = allocations.get(req.rule_id, 0)
                if req.fts_streams_desired != desired:
                    req.set_fts_streams(desired=desired, session=session)
                    logging.info(
                        f"Rebalanced FTS streams for {req.rule_id} on {src_site_name}->{dst_site_name}: "
                        f"priority={req.priority}, desired={desired}, pair_max={max_streams}"
                    )

    @staticmethod
    def _retry_target_status(req) -> RequestStatus:
        """
        Decide where to resume after RETRY.
        - If a SENSE instance already exists, resume from DECIDED (re-provision path)
        - Otherwise resume from ALLOCATED (re-stage path)
        """
        return RequestStatus.DECIDED if req.sense_uuid else RequestStatus.ALLOCATED

    @databased
    def run_once(self, session=None):
        reqs = Request.get_by_status(statuses=[RequestStatus.RETRY, RequestStatus.STAGED, RequestStatus.PROVISIONED, RequestStatus.CANCELED, RequestStatus.STALE, RequestStatus.DECIDED, RequestStatus.FINISHED_R], session=session)
        if not reqs:
            return
        
        for req in reqs:
            if req.transfer_status == RequestStatus.RETRY:
                if req.sense_retries < config_get_int("sense", "max_retries", default=3):
                    logging.info(f"Request {req.rule_id} has {req.sense_retries} retries, less than max retries. Retrying.")
                    req.increment_sense_retries(session=session)
                    target_status = self._retry_target_status(req)
                    req.set_status(target_status, session=session)
                else:
                    logging.warning(f"Request {req.rule_id} has reached max SENSE retries. Marking as failed.")
                    last_error = req.failure_reason or "unknown error"
                    req.mark_failed(f"Reached max SENSE retries ({req.sense_retries}). Last error: {last_error}", session=session)
                    release_endpoints_and_addresses(req, session)
            
            if req.sense_uuid is None:
                continue

            # Capture the DB-side circuit status BEFORE polling SENSE so we can
            # detect state transitions (e.g. MODIFY_COMMITTING → MODIFY_READY).
            prev_circuit_status = req.sense_circuit_status

            status = get_instance_status(req.sense_uuid)
            req.set_sense_circuit_status(status=status, session=session)

            # Affiliate endpoints when ready
            if not req.sense_affiliated and is_affiliated_state(status):
                if not req.src_pool_site or not req.dst_pool_site:
                    logging.error(
                        f"Request {req.rule_id} has no source/destination site, cannot affiliate endpoints"
                    )
                    continue
                logging.debug(f"Request {req.rule_id} is not affiliated with SENSE instance {req.sense_uuid}, affiliating now.")
                try:
                    affiliate_endpoints(
                        sense_uuid=req.sense_uuid,
                        src_pool_site=req.src_pool_site,
                        dst_pool_site=req.dst_pool_site,
                        rule_id=req.rule_id,
                        sense_src_uri=req.sense_src_uri,
                        sense_dst_uri=req.sense_dst_uri
                    )
                    req.update({"sense_affiliated": True}, session=session)
                except Exception as e:
                    logging.error(f"Failed to affiliate endpoints for {req.rule_id}, will retry next cycle: {e}")
                    continue

            if not req.sense_provisioned_at and is_create_ready(status):
                logging.debug(f"Request {req.rule_id} is ready, updating sense_provisioned_at to current time.")
                req.update({"sense_provisioned_at": datetime.now()}, session=session)

            elif req.transfer_status in [RequestStatus.PROVISIONED] and is_create_failed(status):
                logging.warning(
                    f"Request {req.rule_id} reached CREATE_FAILED after being PROVISIONED; "
                    "marking as RETRY to re-enter SENSE retry flow"
                )
                req.mark_retry(f"Circuit reached {status} after being PROVISIONED", session=session)

            elif (
                req.transfer_status == RequestStatus.STALE
                and is_being_modified(prev_circuit_status)
                and is_ready_for_modify(status)
            ):
                # The modifier set circuit_status = MODIFY_COMMITTING optimistically when
                # it called modify_link.  SENSE has now confirmed the delta was applied
                # (transitioned back to a READY state).  Safe to mark PROVISIONED.
                logging.info(
                    f"Request {req.rule_id} SENSE modification confirmed "
                    f"({prev_circuit_status} → {status}), marking as PROVISIONED"
                )
                req.set_status(RequestStatus.PROVISIONED, session=session)

            elif (
                req.transfer_status == RequestStatus.FINISHED_R
                and is_being_modified(prev_circuit_status)
                and is_ready_for_modify(status)
            ):
                # The modifier submitted a throttle (to 1 Gbps) for this finished request
                # and set MODIFY_COMMITTING.  SENSE has now confirmed the throttle was applied
                # (circuit back in READY state).  Safe to mark FINISHED so the canceller proceeds.
                logging.info(
                    f"Request {req.rule_id} throttle modification confirmed "
                    f"({prev_circuit_status} → {status}), marking as FINISHED"
                )
                req.set_status(RequestStatus.FINISHED, session=session)

            elif req.transfer_status == RequestStatus.PROVISIONED and is_modify_failed(status):
                # A modification entered MODIFY - FAILED while the request was already
                # PROVISIONED (e.g. SENSE-O internally retried and failed).  Re-queue as
                # STALE so the modifier's MODIFY_FAILED handler can cancel + rebuild.
                logging.warning(
                    f"Request {req.rule_id} circuit {req.sense_uuid} entered MODIFY - FAILED "
                    "while PROVISIONED; re-queuing as STALE for modifier to cancel and rebuild"
                )
                req.set_status(RequestStatus.STALE, session=session)

        self._rebalance_fts_streams(session)