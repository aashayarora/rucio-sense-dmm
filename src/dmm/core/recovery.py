"""
Phase 1 of the SENSE recovery workflow: what DMM does when a circuit fails or
stalls, and when it stops trying and asks an admin.

  create  FAILED     -> start over: discard the instance and restage (handler)
  cancel  FAILED     -> force the cancel again, a bounded number of times
  modify  FAILED     -> hard modify: cancel, delete and rebuild the circuit
  any     COMMITTED  -> ask SENSE-O to verify after a while, then escalate (or,
                        for a modify, hard modify) if it is still there

Retries are triaged (dmm.core.sense_triage) so a site RM outage is retried with
a growing wait while a refused login is escalated at once. An escalation is a
"<stage>:<reason>" mark on the request, exported as dmm_request_sense_escalated
for Alertmanager, plus an ERROR log line saying what to do.
"""
import logging
from datetime import timedelta

from dmm.core.config import config_get_int
from dmm.core.sense import (
    cancel_link,
    delete_instance,
    get_instance_status,
    is_stalled_status,
    verify_instance,
)
from dmm.core.sense_triage import RECOMMENDATIONS, triage, backoff_seconds, ESCALATE
from dmm.models.base import utcnow
from dmm.models.request import RequestStatus, SenseCircuitStatus

CREATE = "create"
CANCEL = "cancel"
MODIFY = "modify"


def escalate(req, stage, reason, detail, session):
    """Mark the request as needing an admin, and say what they should do."""
    recommendation = RECOMMENDATIONS.get(reason, RECOMMENDATIONS["retries_exhausted"])
    if req.sense_escalation != f"{stage}:{reason}":
        logging.error(
            f"ESCALATION: SENSE {stage} of {req.rule_id} (instance {req.sense_uuid}, "
            f"{req.src_site_}->{req.dst_site_}) needs an admin: {reason}. "
            f"Recommended: {recommendation}. Last error: {detail}"
        )
    req.set_escalation(f"{stage}:{reason}", session=session)


def attempt_due(req, delay_seconds, session):
    """
    Whether a retry that should wait delay_seconds may run now.

    The first call starts the wait and returns False; the call after it has
    passed clears it and returns True.
    """
    if delay_seconds <= 0:
        return True
    now = utcnow()
    if req.sense_next_attempt_at is None:
        req.update({"sense_next_attempt_at": now + timedelta(seconds=delay_seconds)}, session=session)
        logging.info(f"Request {req.rule_id} waits {delay_seconds}s before its next SENSE retry")
        return False
    if now < req.sense_next_attempt_at:
        return False
    req.update({"sense_next_attempt_at": None}, session=session)
    return True


def reset_circuit(req, session, **extra):
    """Forget the circuit so the stager builds a new one on the same endpoints."""
    req.update({
        "sense_uuid": None,
        "sense_src_uri": None,
        "sense_dst_uri": None,
        "src_vlan": None,
        "dst_vlan": None,
        "sense_circuit_status": None,
        "sense_status_since": None,
        "sense_verify_sent_at": None,
        "sense_next_attempt_at": None,
        "sense_affiliated": False,
        "sense_provisioned_at": None,
        "available_bandwidth_mbps": None,
        "transfer_status": RequestStatus.ALLOCATED,
        **extra,
    }, session=session)


def hard_modify(req, status, why, session):
    """
    Cancel and delete the circuit and rebuild it from staging.

    Returns True if the request was sent back to be rebuilt. Escalates instead
    once the request has used up its hard modifies, and leaves it alone if the
    old circuit could not be removed (tried again next cycle).
    """
    max_hard = config_get_int("sense", "max_hard_modifies", default=1, constraint="nonneg")
    if (req.sense_hard_modifies or 0) >= max_hard:
        escalate(req, MODIFY, "retries_exhausted", why, session)
        return False

    failed_uuid = req.sense_uuid
    live_status = get_instance_status(failed_uuid)
    if live_status == "UNKNOWN":
        logging.warning(f"Circuit {failed_uuid} of {req.rule_id} is already gone from SENSE, rebuilding")
    else:
        try:
            cancel_link(failed_uuid, live_status)
            delete_instance(failed_uuid)
        except Exception as e:
            logging.error(
                f"Hard modify of {req.rule_id}: failed to cancel/delete {failed_uuid}: {e}, will retry next cycle",
                exc_info=True,
            )
            return False

    logging.warning(f"Hard modify of {req.rule_id}: removed {failed_uuid} ({why}), rebuilding from staging")
    reset_circuit(
        req, session,
        sense_retries=0,
        sense_hard_modifies=(req.sense_hard_modifies or 0) + 1,
        sense_escalation=None,
        sense_escalated_at=None,
    )
    return True


def retry_cancel(req, status, session):
    """
    Force a failed cancel again. Returns True if SENSE-O accepted it.

    Gives up and escalates after cancel_max_retries, or at once for an error
    triage says retrying won't fix. Once escalated, DMM only watches the
    circuit: if an admin gets it to CANCEL - READY the request carries on.
    """
    if req.sense_escalation:
        return False
    max_cancels = config_get_int("sense", "cancel_max_retries", default=3, constraint="nonneg")
    if (req.sense_cancel_retries or 0) >= max_cancels:
        escalate(req, CANCEL, "retries_exhausted", f"circuit still in {status}", session)
        return False
    if req.sense_next_attempt_at is not None and utcnow() < req.sense_next_attempt_at:
        return False

    req.update({"sense_cancel_retries": (req.sense_cancel_retries or 0) + 1}, session=session)
    try:
        cancel_link(req.sense_uuid, status)
        logging.info(f"Force-cancel {req.sense_cancel_retries}/{max_cancels} of {req.sense_uuid} ({req.rule_id}) accepted")
        return True
    except Exception as e:
        result = triage(e)
        if result.action == ESCALATE:
            escalate(req, CANCEL, result.kind, e, session)
        else:
            delay = backoff_seconds(result, req.sense_cancel_retries)
            if delay:
                req.update({"sense_next_attempt_at": utcnow() + timedelta(seconds=delay)}, session=session)
            logging.error(
                f"Force-cancel of {req.sense_uuid} ({req.rule_id}) failed ({result.kind}): {e}, "
                f"retrying in {delay}s or next cycle"
            )
        return False


def check_stall(req, session):
    """
    Act on a circuit stuck in a COMMITTED state.

    After stall_verify_seconds DMM asks SENSE-O to verify the instance once.
    After stall_escalate_seconds a stuck modify is rebuilt and anything else is
    escalated. A finished request's stuck circuit is the canceller's: it force-cancels it.
    """
    status = req.sense_circuit_status
    if not is_stalled_status(status) or req.sense_status_since is None:
        return
    age = (utcnow() - req.sense_status_since).total_seconds()
    verify_after = config_get_int("sense", "stall_verify_seconds", default=900, constraint="nonneg")
    escalate_after = config_get_int("sense", "stall_escalate_seconds", default=1800, constraint="nonneg")

    if age >= escalate_after:
        if status == SenseCircuitStatus.MODIFY_COMMITTED.value:
            if req.transfer_status in (RequestStatus.STALE, RequestStatus.PROVISIONED):
                hard_modify(req, status, f"stuck in {status} for {age:.0f}s", session)
                return
        stage = {"CREATE": CREATE, "MODIFY": MODIFY, "CANCEL": CANCEL}[status.split(" ")[0]]
        escalate(req, stage, "stalled", f"in {status} for {age:.0f}s", session)
        return

    if age >= verify_after and req.sense_verify_sent_at is None:
        logging.warning(f"Circuit {req.sense_uuid} of {req.rule_id} in {status} for {age:.0f}s, asking SENSE-O to verify it")
        # Recorded before the call, so a failing verify is not re-sent every cycle.
        req.update({"sense_verify_sent_at": utcnow()}, session=session)
        try:
            verify_instance(req.sense_uuid)
        except Exception as e:
            logging.error(f"Verify of {req.sense_uuid} ({req.rule_id}) failed: {e}")
