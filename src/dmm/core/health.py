"""
Daemon liveness, published so the frontend can read it.

The frontend runs in a `multiprocessing.Process`, so any daemon state kept in
memory is invisible to the process that answers `/health`. The two share only
the database, and the database is one of the things health has to be able to
report on, so it cannot be the channel. A directory of small files can be
written by the parent and read by the child without either knowing the other
exists.
"""
import json
import logging
import os
from time import time

HEARTBEAT_DIR = os.environ.get("DMM_HEARTBEAT_DIR", "/tmp/dmm-heartbeats")

# A daemon that has missed five consecutive cycles is not running slow.
STALE_CYCLES = 5


def _path(daemon):
    return os.path.join(HEARTBEAT_DIR, daemon)


def write_heartbeat(daemon, frequency, started, last_success, running):
    """
    Publish one daemon's liveness. Never raises — a daemon must not die because
    its heartbeat could not be written.
    """
    try:
        os.makedirs(HEARTBEAT_DIR, exist_ok=True)
        payload = json.dumps({
            "frequency": frequency,
            "started": started,
            "last_success": last_success,
            "running": bool(running),
        })
        # Written to a side file and renamed so a reader mid-write sees the
        # previous heartbeat rather than half of this one.
        tmp = f"{_path(daemon)}.{os.getpid()}.tmp"
        with open(tmp, "w") as handle:
            handle.write(payload)
        os.replace(tmp, _path(daemon))
    except Exception as e:
        logging.warning(f"could not write heartbeat for {daemon}: {e}")


def reset_heartbeats():
    """
    Drop heartbeats from a previous run. Without this a daemon that has been
    renamed or removed leaves a file behind that can never be refreshed, and
    health stays red forever for a daemon that no longer exists.
    """
    try:
        for name in os.listdir(HEARTBEAT_DIR):
            os.remove(_path(name))
    except FileNotFoundError:
        pass
    except Exception as e:
        logging.warning(f"could not clear stale heartbeats: {e}")


def read_heartbeats():
    try:
        names = os.listdir(HEARTBEAT_DIR)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logging.warning(f"could not read heartbeat directory: {e}")
        return {}

    heartbeats = {}
    for name in names:
        if name.endswith(".tmp"):
            continue
        try:
            with open(_path(name)) as handle:
                heartbeats[name] = json.load(handle)
        except Exception as e:
            logging.warning(f"could not read heartbeat for {name}: {e}")
    return heartbeats


def _classify(heartbeat, now):
    frequency = heartbeat.get("frequency") or 0
    started = heartbeat.get("started") or 0
    last_success = heartbeat.get("last_success") or 0

    if frequency < 0:
        # Deliberately turned off in the config, not broken.
        return "disabled", None
    if not heartbeat.get("running"):
        return "stopped", None

    # Until the first cycle lands there is nothing to measure staleness from
    # except the start, which is what keeps a slow first cycle from reading as a
    # failure and restarting the pod.
    age = now - (last_success or started)
    if frequency > 0 and age > STALE_CYCLES * frequency:
        return "stale", age
    if not last_success:
        return "starting", age
    return "ok", age


# States that mean the daemon is not doing its job.
UNHEALTHY_STATES = ("stopped", "stale")


def daemon_report(now=None):
    now = now if now is not None else time()
    report = []
    for daemon, heartbeat in sorted(read_heartbeats().items()):
        state, age = _classify(heartbeat, now)
        entry = {
            "daemon": daemon,
            "state": state,
            "frequency_seconds": heartbeat.get("frequency"),
        }
        if age is not None:
            entry["seconds_since_success"] = round(age, 1)
        report.append(entry)
    return report


def health_report(site_count=None, database_error=None, now=None):
    """
    Assemble the whole verdict. `site_count` and `database_error` are passed in
    rather than queried here so this module stays free of database imports and
    stays testable without one.
    """
    daemons = daemon_report(now=now)
    checks = []

    if database_error is not None:
        checks.append({"check": "database", "ok": False, "detail": str(database_error)})
    else:
        checks.append({"check": "database", "ok": True})
        # An empty site table is not a slow refresh, it is RucioInitDaemon
        # raising once per rule per cycle for as long as it stays empty. Row
        # timestamps are not used: refresh_all_sites leaves an unchanged site
        # untouched, so updated_at would report staleness that is not real.
        # Whether the refresh loop still runs is the RefreshSiteDBDaemon
        # heartbeat's job, above.
        checks.append({
            "check": "site_database",
            "ok": bool(site_count),
            "sites": site_count,
            "detail": None if site_count else "no sites known; every Rucio rule will fail",
        })

    if not daemons:
        checks.append({
            "check": "daemons",
            "ok": False,
            "detail": f"no heartbeats in {HEARTBEAT_DIR}; the daemon process is not running "
                      "or is not sharing this directory",
        })
    else:
        unhealthy = [d["daemon"] for d in daemons if d["state"] in UNHEALTHY_STATES]
        checks.append({
            "check": "daemons",
            "ok": not unhealthy,
            "detail": f"{', '.join(unhealthy)} not completing cycles" if unhealthy else None,
        })

    healthy = all(check["ok"] for check in checks)
    return healthy, {
        "status": "healthy" if healthy else "unhealthy",
        "checks": checks,
        "daemons": daemons,
    }
