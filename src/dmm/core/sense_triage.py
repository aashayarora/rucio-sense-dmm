"""
Triage of SENSE-O failures: what kind of failure it is, and what to do about it.

Phase 1 of the SENSE recovery workflow maps errors to actions with fixed rules.
The SENSE client raises every HTTP error as
    ValueError("Returned code <N> with error <exception>. Full json return: {...}")
so both the code and SENSE-O's own message are in the text. The message is
checked first: SENSE-O reports a site RM it cannot reach as a 500, and that is
the transient failure the workflow most needs to retry, not escalate.

Kinds are bounded strings, so they can be used as metric labels.
"""
import re
from typing import NamedTuple, Optional

from dmm.core.config import config_get_int

RETRY = "retry"
ESCALATE = "escalate"

# A site RM is down or flapping, or SENSE-O itself is unreachable. Usually gone
# within minutes, so it gets several retries with a growing wait.
RM_UNREACHABLE = "rm_unreachable"
# The path could not be compiled for lack of bandwidth, VLANs or subnets. Often
# an old instance still holds them, which starting over cleans up, so one retry.
NO_RESOURCES = "no_resources"
# Refined sub-kinds of NO_RESOURCES, written into failure_reason after the
# local model probe runs. triage() still returns NO_RESOURCES; these constants
# are used only in stager._probe_path_on_no_resources() and in RECOMMENDATIONS.
NO_CAPACITY = "no_capacity"    # topology joined but bottleneck is out of free Mbps
NO_VLANS = "no_vlans"          # topology joined at zero rate but no VLAN id is free
NO_TOPOLOGY = "no_topology"    # endpoints not joined in the model at all
# SENSE-O no longer knows the instance.
INSTANCE_GONE = "instance_gone"
# A circuit reached a FAILED state while DMM was watching it.
CIRCUIT_FAILED = "circuit_failed"
# DMM's credentials were refused. Retrying cannot fix that.
AUTH = "auth"
# Any other server-side error from SENSE-O.
SENSE_ERROR = "sense_error"
OTHER = "other"

_RM_UNREACHABLE = (
    "failed to communicate with subsystem",
    "connection refused",
    "connection reset",
    "connection aborted",
    "max retries exceeded",
    "name or service not known",
    "dns failure",
    "read timed out",
    "gateway time-out",
    "gateway timeout",
    "service unavailable",
    "bad gateway",
)
_NO_RESOURCES = (
    "cannot find feasible path",
    "no feasible path",
    "insufficient bandwidth",
    "not enough bandwidth",
    "no available vlan",
    "vlan unavailable",
    "no free vlan",
)

_HTTP_CODE = re.compile(r"returned code (\d{3})|\((\d{3})\) reason")

# What an admin should do about each kind, for escalation messages and alerts.
RECOMMENDATIONS = {
    RM_UNREACHABLE: "check the site RM named in the error is up and reachable from SENSE-O",
    NO_RESOURCES: "look for stale SENSE instances holding bandwidth, VLANs or subnets on this path and cancel them",
    NO_CAPACITY:  "free bandwidth on the bottleneck port (cancel stale SENSE instances holding it, or provision hardware)",
    NO_VLANS:     "free a VLAN id on the blocked cable (cancel stale SENSE instances holding VLANs on this path)",
    NO_TOPOLOGY:  "check that the site RM for the missing domain is running and publishing its topology to SENSE-O",
    INSTANCE_GONE: "check SENSE-O for the instance; DMM no longer finds it",
    CIRCUIT_FAILED: "check the instance's failure in SENSE-O and the site RMs on its path",
    AUTH: "renew DMM's SENSE-O credentials",
    SENSE_ERROR: "check the SENSE-O error in the DMM log and SENSE-O's own logs",
    OTHER: "check the error in the DMM log",
    "stalled": "check why SENSE-O has not verified the instance; a site RM on the path may be down",
    "retries_exhausted": "check the instance in SENSE-O; DMM's automatic recovery gave up",
}


class Triage(NamedTuple):
    kind: str
    action: str
    max_retries: int
    backoff_seconds: int


def http_code(error) -> Optional[int]:
    match = _HTTP_CODE.search(str(error or "").lower())
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def triage(error) -> Triage:
    """Classify a SENSE failure, given the exception or its recorded text."""
    text = str(error or "").lower()
    code = http_code(text)
    max_retries = config_get_int("sense", "max_retries", default=3)

    if any(needle in text for needle in _RM_UNREACHABLE) or code in (502, 503, 504):
        return Triage(
            RM_UNREACHABLE, RETRY,
            config_get_int("sense", "transient_max_retries", default=5, constraint="nonneg"),
            config_get_int("sense", "transient_backoff_seconds", default=300, constraint="nonneg"),
        )
    if any(needle in text for needle in _NO_RESOURCES):
        return Triage(
            NO_RESOURCES, RETRY,
            config_get_int("sense", "no_resources_max_retries", default=1, constraint="nonneg"),
            0,
        )
    if "no longer known to sense-o" in text:
        return Triage(INSTANCE_GONE, RETRY, max_retries, 0)
    if code in (401, 403):
        return Triage(AUTH, ESCALATE, 0, 0)
    if code == 404:
        return Triage(INSTANCE_GONE, RETRY, max_retries, 0)
    if code is not None and code >= 500:
        return Triage(SENSE_ERROR, ESCALATE, 0, 0)
    if " - failed" in text:
        return Triage(CIRCUIT_FAILED, RETRY, max_retries, 0)
    return Triage(OTHER, RETRY, max_retries, 0)


def backoff_seconds(result: Triage, retries_done: int) -> int:
    """Wait before the next retry: the base, doubled per retry already made, at most an hour."""
    if result.backoff_seconds <= 0:
        return 0
    return min(result.backoff_seconds * (2 ** max(0, retries_done - 1)), 3600)
