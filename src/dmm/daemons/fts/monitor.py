import logging
from collections import Counter
from datetime import datetime, timezone

from dmm.daemons.base import DaemonBase
from dmm.models.base import utcnow
from dmm.models.request import Request, RequestStatus
from dmm.db.session import databased
from dmm.core.config import config_get_int
from dmm.core.fts import get_fts_job
from dmm.core.rucio import get_fts_job_ids

# Requests with a live circuit, which is when their transfers are worth watching.
_MONITORED_STATUSES = [
    RequestStatus.PROVISIONED, RequestStatus.STALE,
    RequestStatus.MODIFIED, RequestStatus.FINISHED,
]
# Rucio is done with these. A finished request can be cancelled before the monitor
# gets to it (no keep-alive), so a cancelled one is followed until its jobs are final,
# for at most this long after the rule finished: each pass pages Rucio's history.
_RUCIO_DONE_STATUSES = (RequestStatus.FINISHED, RequestStatus.CANCELED)
_CANCELED_FOLLOW_SECONDS = 600

# FTS job states nothing changes after. A job in one is not asked about again.
_FINAL_JOB_STATES = {"FINISHED", "FAILED", "FINISHEDDIRTY", "CANCELED"}


class FTSMonitorDaemon(DaemonBase):
    """
    Follows a rule's transfers through the FTS jobs carrying them.

    Rucio knows which FTS job each of a rule's transfers went into, FTS knows how
    each job is getting on, and the roll-up of every job is the rule's progress:
    files by state, bytes delivered, and the throughput they were delivered at.

    Job ids are remembered on the request. Rucio lists a transfer as in flight only
    until it finishes, after which it is in the request history, which is kept per
    RSE pair and has to be paged through to find one rule. So the history is only
    searched when the in-flight list can't be relied on: before any job has been
    seen, since a small rule can finish between two polls, and once Rucio is done
    with the rule, to catch jobs that came and went between polls.
    """

    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        self.history_pages = config_get_int("monit", "history_pages", default=10, constraint="nonneg")

    def process(self, **kwargs):
        self.run_once(**kwargs)

    @databased
    def run_once(self, client=None, session=None):
        reqs = Request.get_by_status(statuses=_MONITORED_STATUSES + [RequestStatus.CANCELED], session=session)
        for req in reqs:
            if req.transfer_status == RequestStatus.CANCELED and not _still_followed(req):
                continue
            try:
                self._monitor(req, client, session)
            except Exception as e:
                logging.error(f"Error monitoring FTS jobs for request {req.rule_id}: {e}", exc_info=True)

    def _monitor(self, req, client, session):
        src_rse = req.src_logical_site or (req.src_site.name if req.src_site else None)
        dst_rse = req.dst_logical_site or (req.dst_site.name if req.dst_site else None)
        if not src_rse or not dst_rse:
            logging.warning(f"Request {req.rule_id} has no source or destination RSE, cannot look up its FTS jobs")
            return

        jobs = {job_id: dict(job) for job_id, job in (req.fts_jobs or {}).items()}

        search_history = not jobs or req.transfer_status in _RUCIO_DONE_STATUSES
        found = get_fts_job_ids(
            client, src_rse, dst_rse, req.rule_id,
            history_pages=self.history_pages if search_history else 0,
        )
        for job_id, host in found.items():
            if job_id not in jobs:
                logging.info(f"Found FTS job {job_id} on {host} for request {req.rule_id}")
                jobs[job_id] = {"host": host, "state": None}

        complete = True
        for job_id, job in jobs.items():
            if job.get("state") in _FINAL_JOB_STATES:
                continue
            reply = get_fts_job(job_id, job.get("host"))
            if reply is None:
                # Keep what was last known; the job is asked about again next pass.
                complete = False
                continue
            jobs[job_id] = {"host": job.get("host"), **_job_record(reply)}

        req.set_fts_monitoring(jobs, _summarize(jobs, req.fts_summary, complete), session=session)


def _still_followed(req):
    """Whether a cancelled request's jobs are still worth a look."""
    jobs = req.fts_jobs or {}
    if jobs and all(job.get("state") in _FINAL_JOB_STATES for job in jobs.values()):
        return False
    finished_at = req.rucio_finished_at
    return finished_at is not None and (utcnow() - finished_at).total_seconds() < _CANCELED_FOLLOW_SECONDS


def _epoch(value):
    """FTS timestamps are ISO-8601 UTC without a zone; return epoch seconds or None."""
    if not value:
        return None
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


def _job_record(reply):
    """What is kept of one FTS job: its state and the totals over its files."""
    files = reply.get("files") or []
    done = [f for f in files if f.get("file_state") == "FINISHED"]
    starts = [t for t in (_epoch(f.get("start_time")) for f in done) if t is not None]
    finishes = [t for t in (_epoch(f.get("finish_time")) for f in done) if t is not None]
    return {
        "state": reply.get("job_state"),
        "files": dict(Counter(f.get("file_state") or "UNKNOWN" for f in files)),
        "bytes": sum(float(f.get("filesize") or 0) for f in files),
        "bytes_done": sum(float(f.get("filesize") or 0) for f in done),
        "first_start": min(starts, default=None),
        "last_finish": max(finishes, default=None),
    }


def _summarize(jobs, previous=None, complete=True):
    """
    The rule's progress across all its jobs.

    Throughput is what was delivered over the time it took: finished bytes from
    the first finished file's start to the last one's finish. It describes files
    that are done, so it says nothing about a transfer that is stuck right now -
    the file counts and progress_at do.

    progress_at is when DMM last saw the delivered bytes grow, on DMM's own clock
    rather than FTS's, so a stall can be timed without trusting the two to agree.
    complete says whether FTS answered for every unfinished job this pass; when it
    didn't, the figures are partly stale.
    """
    files = Counter()
    for job in jobs.values():
        files.update(job.get("files") or {})
    bytes_done = sum(job.get("bytes_done") or 0 for job in jobs.values())
    starts = [job["first_start"] for job in jobs.values() if job.get("first_start") is not None]
    finishes = [job["last_finish"] for job in jobs.values() if job.get("last_finish") is not None]

    now = utcnow().isoformat()
    previous = previous or {}
    progress_at = previous.get("progress_at")
    if progress_at is None or bytes_done > (previous.get("bytes_done") or 0):
        progress_at = now

    throughput_mbps = None
    if starts and finishes and max(finishes) > min(starts):
        # Bandwidth everywhere else in DMM is decimal Mbps.
        throughput_mbps = round(bytes_done * 8 / 1e6 / (max(finishes) - min(starts)), 2)

    return {
        "jobs": len(jobs),
        "job_states": dict(Counter(job.get("state") or "UNKNOWN" for job in jobs.values())),
        "files": dict(files),
        "bytes": sum(job.get("bytes") or 0 for job in jobs.values()),
        "bytes_done": bytes_done,
        "throughput_mbps": throughput_mbps,
        "progress_at": progress_at,
        "complete": complete,
        "checked_at": now,
    }
