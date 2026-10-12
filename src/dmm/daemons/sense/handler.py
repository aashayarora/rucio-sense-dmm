import logging
from collections import defaultdict
from math import floor

from dmm.core.config import config_get_int
from dmm.core.recovery import CREATE, attempt_due, check_stall, escalate, reset_circuit
from dmm.core.sense_triage import ESCALATE, backoff_seconds, triage
from dmm.daemons.base import DaemonBase
from dmm.models.base import utcnow
from dmm.models.request import Request, RequestStatus, SenseCircuitStatus
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
    discard_instance,
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
        default_streams = config_get_int("fts", "default_num_streams", default=20)
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

    # An instance in one of these can still be provisioned, or already has been.
    _USABLE_CREATE_STATUSES = {
        SenseCircuitStatus.CREATE_COMPILED.value,
        SenseCircuitStatus.CREATE_COMMITTING.value,
        SenseCircuitStatus.CREATE_COMMITTED.value,
        SenseCircuitStatus.CREATE_READY.value,
    }

    def _handle_retry(self, req, session) -> bool:
        """
        Act on a RETRY, the create path of the recovery workflow.

        The last error is triaged: an error retrying can't fix, or one whose retries
        are used up, fails the request and escalates. Otherwise, after any backoff
        the triage asks for, the request resumes from DECIDED if its instance can
        still be provisioned, or starts over from ALLOCATED with the instance
        discarded. That includes a request holding the uuid of an instance its
        staging failed on: only a successful staging records the available bandwidth.

        Returns True if the request went back to DECIDED, so its status is polled now.
        """
        result = triage(req.failure_reason)
        retries = req.sense_retries or 0
        if result.action == ESCALATE or retries >= result.max_retries:
            last_error = req.failure_reason or "unknown error"
            escalate(req, CREATE, result.kind, last_error, session)
            if result.action == ESCALATE:
                reason = f"SENSE error that retrying won't fix ({result.kind}): {last_error}"
            else:
                reason = f"Reached max SENSE retries ({retries}) for {result.kind}. Last error: {last_error}"
            logging.warning(f"Request {req.rule_id}: {reason}. Marking as failed.")
            req.mark_failed(reason, session=session)
            self._cleanup_failed_instance(req)
            release_endpoints_and_addresses(req, session)
            return False

        if not attempt_due(req, backoff_seconds(result, retries + 1), session):
            return False

        status = get_instance_status(req.sense_uuid) if req.sense_uuid else None
        staged = req.sense_uuid and req.available_bandwidth_mbps is not None
        if staged and status in self._USABLE_CREATE_STATUSES:
            target = RequestStatus.DECIDED
        else:
            if req.sense_uuid:
                try:
                    discard_instance(req.sense_uuid, status)
                except Exception as e:
                    logging.error(f"Could not discard SENSE instance {req.sense_uuid} of {req.rule_id}: {e}, will retry next cycle")
                    return False
                logging.info(f"Discarded SENSE instance {req.sense_uuid} ({status}) of {req.rule_id}, restaging")
                reset_circuit(req, session)
            target = RequestStatus.ALLOCATED

        logging.info(f"Request {req.rule_id} retry {retries + 1}/{result.max_retries} ({result.kind}), resuming from {target}")
        req.increment_sense_retries(session=session)
        req.set_status(target, session=session)
        return target == RequestStatus.DECIDED

    @databased
    def run_once(self, session=None):
        reqs = Request.get_by_status(statuses=[RequestStatus.RETRY, RequestStatus.STAGED, RequestStatus.PROVISIONED, RequestStatus.CANCELED, RequestStatus.STALE, RequestStatus.DECIDED], session=session)
        if not reqs:
            return
        
        for req in reqs:
            if req.transfer_status == RequestStatus.RETRY:
                try:
                    if not self._handle_retry(req, session):
                        continue
                except Exception as e:
                    logging.error(f"Failed to retry {req.rule_id}: {e}", exc_info=True)
                    continue

            if req.sense_uuid is None:
                continue

            try:
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
                    logging.info(f"Request {req.rule_id} is not affiliated with SENSE instance {req.sense_uuid}, affiliating now.")
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
                    logging.info(f"Request {req.rule_id} is ready, updating sense_provisioned_at to current time.")
                    req.update({"sense_provisioned_at": utcnow()}, session=session)

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

                elif req.transfer_status == RequestStatus.PROVISIONED and is_modify_failed(status):
                    # A modification entered MODIFY - FAILED while the request was already
                    # PROVISIONED (e.g. SENSE-O internally retried and failed).  Re-queue as
                    # STALE so the modifier's MODIFY_FAILED handler can cancel + rebuild.
                    logging.warning(
                        f"Request {req.rule_id} circuit {req.sense_uuid} entered MODIFY - FAILED "
                        "while PROVISIONED; re-queuing as STALE for modifier to cancel and rebuild"
                    )
                    req.set_status(RequestStatus.STALE, session=session)

                check_stall(req, session)
            except Exception as e:
                # Isolate per-request failures so one bad SENSE call doesn't roll back
                # the whole cycle's updates.
                logging.error(f"Failed to update SENSE status for {req.rule_id}: {e}", exc_info=True)
                continue

        try:
            self._rebalance_fts_streams(session)
        except Exception as e:
            logging.error(f"Failed to rebalance FTS streams: {e}", exc_info=True)

    @staticmethod
    def _cleanup_failed_instance(req) -> None:
        """Best-effort removal of the SENSE instance of a permanently failed request."""
        if not req.sense_uuid:
            return
        try:
            discard_instance(req.sense_uuid, get_instance_status(req.sense_uuid))
        except Exception as e:
            logging.error(
                f"Failed to remove SENSE instance {req.sense_uuid} for failed request {req.rule_id}, "
                f"it may need removing by hand: {e}"
            )
