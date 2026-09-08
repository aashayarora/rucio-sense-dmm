# DMM Changelog

---

## BUG-009 — `SENSEModifierDaemon`: non-transient HTTP 500 on FINISHED_R throttle call leaves request permanently stuck

**Date:** 2026-08-21
**Repo:** `rucio-sense-dmm`
**File:** `src/dmm/daemons/sense/modifier.py`
**Status:** Fixed

### Problem

When `SENSEModifierDaemon` processes a `FINISHED_R` request it calls `modify_link()` to
throttle the circuit down to 1 Gbps before handing off to `SENSECancellerDaemon`. If
SENSE-O returns a non-transient HTTP 500 (e.g. an MCE routing-policy constraint violation),
the exception handler had only two branches:

```python
except Exception as e:
    if is_sync_timeout(e):   # only matches HTTP 504 + "gateway" keyword
        ...                  # treat as in-flight, set MODIFY_COMMITTING
    else:
        logging.error(f"Failed to throttle {req.rule_id}: {e}", exc_info=True)
        # ← no status change, no retry counter, no escalation
```

Because `is_sync_timeout` checks for `"504"` + `"gateway time-out"` in the error string,
any other HTTP error — including a permanent 500 — fell into the `else` branch, which only
logged and returned. No DB field was changed.

The request stayed in `FINISHED_R` indefinitely. Every subsequent modifier cycle
(every 10 seconds) re-attempted the identical `modify_link()` call, received the identical
500, and logged the identical error. `SENSECancellerDaemon` only processes `FINISHED`
status — never `FINISHED_R` — so the circuit remained live at full allocated bandwidth,
IPv6 endpoints were never released, and `DeciderDaemon` continued accounting for the dead
transfer's bandwidth, starving active transfers.

Observed on 2026-08-21 for circuit `83fae717-9803-464a-a422-4efd186689fd` /
rule `bdb7f4c4192f47428b6ef86771792649` (T2_US_Caltech → T1_US_FNAL). SENSE-O returned:

```
MCE_SiteL3Routing-doSiteL3Routing-83fae717-... — Connection <...vt+routing-policy>
requires input sites with same number of assigned gateway addresses.
```

### Fix

In the `else` branch of the FINISHED_R exception handler, advance the request to `FINISHED`
after logging the error. The 1 Gbps throttle is a courtesy step — it is not required for
correctness. `SENSECancellerDaemon` can cancel the circuit safely from any READY state
regardless of its current bandwidth.

```python
# modifier.py — FINISHED_R exception handler (else branch)
else:
    logging.error(
        f"Failed to throttle {req.rule_id}: {e} — "
        "skipping throttle, advancing to FINISHED for cancellation",
        exc_info=True,
    )
    req.set_status(status=RequestStatus.FINISHED, session=session)  # ← added
```

The 504 path is unchanged. The error is still fully logged with traceback. Every
non-transient failure now unblocks the cancellation pipeline instead of stalling it.

**Change size:** 5 lines changed in `modifier.py`.

---

## BUG-008 — config_get* sentinel fix: allow default=None as a valid fallback value

**Date:** 2026-08-19  
**Repo:** `rucio-sense-dmm`  
**File:** `src/dmm/core/config.py`  
**Status:** Fixed

### Problem

`config_get`, `config_get_int`, and `config_get_bool` used `None` as both the
"no default provided" sentinel and a valid fallback return value. The guard
`if default is not None` evaluated to `False` when a caller passed
`default=None`, causing the exception to always re-raise instead of returning
`None`.

This made `SENSEHandlerDaemon` crash every cycle when a `PROVISIONED` site
pair had no entry in `[fts-streams]`. `_pair_stream_cap` called
`config_get_int("fts-streams", "<pair>", default=None)` expecting `None` on
a cache miss, but received `NoOptionError` instead. The daemon retried every
10 seconds with the same result, permanently blocking FTS stream rebalancing
until the missing config key was added manually.

### Fix

Introduced a module-level `_UNSET = object()` sentinel and updated all three
helpers to use `default=_UNSET` with the guard `if default is not _UNSET`.
This correctly distinguishes "caller wants `None` returned" from "caller
provided no default".

- Callers that **omit** `default` still raise on a missing key (unchanged)
- Callers that pass a **non-None value** still receive that value (unchanged)
- Callers that pass `default=None` now correctly receive `None` back (fixed)

All 39 existing call sites across the codebase are backward-compatible.

---

## FEATURE-001 — Multi-logical-site support: Rucio logical site names map to physical SENSE sites

**Date:** 2026-08-05  
**Repo:** `rucio-sense-dmm`  
**Author:** Xi Yang (xiyang@es.net)  
**Commit:** `5a3dfd9`  
**Files:** `src/dmm/core/sitemap.py` (new), `src/dmm/core/allocation.py`, `src/dmm/daemons/core/allocator.py`, `src/dmm/daemons/rucio/initializer.py`, `src/dmm/daemons/sense/handler.py`, `src/dmm/models/request.py`, templates, `dmm.cfg.sample`  
**Status:** Implemented

### Problem

Rucio may register several logical site names that all map to the same physical SENSE site (e.g. `T2_US_UCSD_Blackhole → T2_US_UCSD`). Each logical name gets its own SENSE-O subnet pool (`RUCIO_Site_BGP_Subnet_Pool-<logical>`), but SENSE discovery, mesh/VLAN ranges, endpoint table, link capacity, and FTS stream caps are all keyed on the physical name.

Three failure modes existed before this fix:

1. **Unknown logical site → infinite INIT loop**: `RucioInitDaemon` looked up the site name directly in the `Site` table. Any Rucio logical name that was not literally a physical site name caused a `ValueError` on every cycle with no recovery path — the rule was stuck in INIT forever.
2. **Bad circuit reuse — wrong endpoints handed to Rucio**: `AllocatorDaemon` matched FINISHED_R circuits for reuse on physical site alone. A `T2_US_UCSD` rule could adopt a `T2_US_UCSD_Blackhole` circuit, inheriting a blackhole endpoint hostname that Rucio would then use to route real data — data would be silently discarded.
3. **Same-physical-site rules loop silently**: A rule where both ends resolve to the same physical site cannot have a SENSE circuit provisioned. Previously the rule stayed in INIT indefinitely with no error surfaced.

### Fix

**New `src/dmm/core/sitemap.py`** — `resolve_physical_site(logical_name, session)`:
- First checks the `[site-aliases]` config section for an explicit override.
- If not found, strips trailing `_Ext`-style suffixes (e.g. `_Blackhole`, `_Test`) and checks if the remainder is a known physical site.
- Falls back to the name itself (preserves existing exact-match sites unchanged).
- Returns `None` for unresolvable names (recoverable — the site refresh daemon may add it later).

**`src/dmm/models/request.py`** — two new nullable columns + two properties:

```python
src_logical_site: Optional[str]   # Rucio's name — selects the SENSE-O subnet pool
dst_logical_site: Optional[str]
```

```python
@property
def src_pool_site(self) -> Optional[str]:
    """Logical site whose SENSE-O subnet pool holds the source allocation."""
    return self.src_logical_site or (self.src_site.name if self.src_site else None)

@property
def dst_pool_site(self) -> Optional[str]:
    """Logical site whose SENSE-O subnet pool holds the destination allocation."""
    return self.dst_logical_site or (self.dst_site.name if self.dst_site else None)
```

Requests predating this feature have `src_logical_site = NULL`; the property falls back to the physical site name, so existing allocations remain addressable.

**`src/dmm/daemons/rucio/initializer.py`** — use `resolve_physical_site()` instead of direct table lookup; record `src_logical_site`/`dst_logical_site` on the new `Request`. Rules where both ends resolve to the same physical site are immediately set to `FAILED` with a descriptive `failure_reason`.

**`src/dmm/daemons/core/allocator.py`** — circuit-reuse matching requires identical logical pair (same `src_pool_site` and `dst_pool_site`), preventing cross-logical-site endpoint inheritance. Subnet allocation uses `src_pool_site`/`dst_pool_site`; error messages now include the pool name for easier debugging.

**`src/dmm/daemons/sense/handler.py`** — `affiliate_endpoints` called with `src_pool_site`/`dst_pool_site` instead of `src_site.name`/`dst_site.name`.

**`dmm.cfg.sample`** — new `[site-aliases]` section (commented example: `T2_US_UCSD_Blackhole=T2_US_UCSD`).

### Before / After

| | Before | After |
|:---|:---|:---|
| Logical site not in `Site` table | Stuck in INIT forever — `ValueError` every cycle | `resolve_physical_site()` strips suffix → finds physical site; unresolvable → retries next cycle |
| Circuit reuse across logical sites | Normal rule can inherit blackhole endpoints | Reuse requires matching `src_pool_site` and `dst_pool_site` |
| Same-physical-site rule | Loops silently in INIT with no error | Immediately `FAILED` with human-readable `failure_reason` |
| Subnet pool naming | Always `RUCIO_Site_BGP_Subnet_Pool-<physical>` | `RUCIO_Site_BGP_Subnet_Pool-<logical>` — separate pools per logical name |

**Change size:** 333 insertions, 85 deletions across 11 files; 1 new file (`sitemap.py`).

---

## BUG-007 — Python 3.8 EOL + Starlette 1.x TemplateResponse crash + Helm scheduling failure

**Date:** 2026-08-05  
**Repo:** `rucio-sense-dmm`  
**Files:** `Dockerfile`, `src/dmm/api/frontend.py`, `etc/helm/templates/deployment.yaml`, `dmm.cfg.sample`  
**Status:** Fixed

### Problem

Three independent but co-shipped deployment failures:

1. **Python 3.8 EOL / runtime `TypeError`**: `Dockerfile` based on `python:3.7-slim-bullseye`. `frontend.py` uses Python 3.10+ union type syntax (e.g. `X | Y` in type annotations) that raises a `TypeError` at import time on Python < 3.10, making the entire DMM API process crash on startup.

2. **Starlette 1.x `TemplateResponse` API break**: Starlette 1.0 changed the `TemplateResponse` constructor — the `request` object is now the first positional argument, not a key inside the `context` dict. All three dashboard routes (`/`, `/sites`, `/details/{rule_id}`) raised a `TypeError` on every request, returning HTTP 500 errors and making the web UI completely unusable.

   ```python
   # Before (Starlette 0.x API — broken under Starlette 1.x)
   templates.TemplateResponse("index.html", {"request": request, "data": reqs})

   # After (Starlette 1.x API)
   templates.TemplateResponse(request, "index.html", {"data": reqs})
   ```

3. **Helm pod scheduling failure**: `deployment.yaml` had no `tolerations` block. Nautilus nodes carrying the `nautilus.io/reservation=sense:NoSchedule` taint rejected the DMM pod, leaving it permanently `Pending`.

### Fix

- **`Dockerfile`**: bump base image from `python:3.7-slim-bullseye` to `python:3.10-slim-bullseye`.
- **`src/dmm/api/frontend.py`**: update all three `TemplateResponse` calls to the Starlette 1.x signature; add `exc_info=True` to `logging.error()` in all three routes for full tracebacks.
- **`etc/helm/templates/deployment.yaml`**: add `{{- with .Values.tolerations }} tolerations: ... {{- end }}` block to pod spec.
- **`dmm.cfg.sample`**: expand with documented `[daemons]`, `[fts-streams]`, `[vlan-ranges]`, and `[site-aliases]` sections with inline comments.

### Before / After

| | Before | After |
|:---|:---|:---|
| DMM API startup on Python < 3.10 | `TypeError` at import — process crashes | Starts cleanly on Python 3.10 |
| Dashboard routes `/`, `/sites`, `/details/*` | HTTP 500 on every request | Pages render correctly |
| Pod scheduling on tainted Nautilus nodes | Pod stuck in `Pending` | Toleration applied — pod schedules |

**Change size:** 12 insertions, 7 deletions across 4 files.

---

## BUG-006 — SENSEModifierDaemon throttles FINISHED_R circuits too eagerly, closing the reuse window

**Date:** 2026-07-29  
**Repo:** `rucio-sense-dmm`  
**Files:** `src/dmm/daemons/sense/modifier.py`  
**Status:** Fixed

### Problem

When a Rucio rule completes, the affected request transitions to `FINISHED_R`. On the very next `SENSEModifierDaemon` cycle (≤10 seconds later), the daemon throttled the circuit to 1,000 Mbps and set the request to `FINISHED`. This happened before `AllocatorDaemon` had a chance to claim the circuit for a concurrently-arriving new rule.

The consequence: a new rule for the same site pair would find no `FINISHED_R` circuit available for reuse and fall through to `_allocate_new_endpoints`, consuming a fresh BGP subnet allocation. In environments where the subnet pool is at or near capacity, this forced allocation failure (no free subnets) or unnecessarily consumed a limited resource.

### Fix

New `finished_r_hold_seconds` config key (integer, non-negative, default `120`) in the `[sense]` section. In `SENSEModifierDaemon.run_once()`, before throttling a `FINISHED_R` request, the daemon checks elapsed time since `req.rucio_finished_at`:

```python
# modifier.py — FINISHED_R hold logic (added before the throttle block)
finished_r_hold_secs = config_get_int("sense", "finished_r_hold_seconds", default=120, constraint="nonneg")
if req.rucio_finished_at is not None:
    elapsed = (datetime.now() - req.rucio_finished_at).total_seconds()
    if elapsed < finished_r_hold_secs:
        logging.debug(
            f"Request {req.rule_id} entered FINISHED_R {elapsed:.0f}s ago, "
            f"holding for {finished_r_hold_secs}s before throttle/teardown"
        )
        continue
```

If `elapsed < finished_r_hold_secs`, the daemon skips throttle for this cycle and retries next cycle. Once the hold period expires with no reuse claim, throttle proceeds normally.

### Before / After

| | Before | After |
|:---|:---|:---|
| Time between FINISHED_R and throttle | ≤ one daemon cycle (~10 s) | Configurable grace period (default 120 s) |
| Circuit available for reuse | Only for ~10 s window | Held available for 2 minutes (configurable) |
| Subnet pool behavior under load | Unnecessary new allocations on back-to-back rules | Back-to-back rules reuse existing circuit |

**Config:** Add `finished_r_hold_seconds = 120` to `[sense]` in `dmm.cfg` to enable (default applies if omitted).

**Change size:** 13 lines added; 1 import added (`config_get_int`, `datetime`).

---

## BUG-005 — SENSEDeleterDaemon loops indefinitely when SENSE-O stalls in CANCEL - COMMITTED

**Date:** 2026-07-29  
**Repo:** `rucio-sense-dmm`  
**Files:** `src/dmm/daemons/sense/deleter.py`  
**Discovered via:** Test 0A (BASELINE LIFECYCLE) — logs `sense_1720_072926logs.txt` / `dmm_1720_072926logs.txt`  
**Status:** Open — no code fix implemented yet; documented as known limitation

### Problem

After `SENSECancellerDaemon` successfully issues `cancel_link()` and sets `transfer_status = CANCELED`,
`SENSEDeleterDaemon` takes over and is expected to poll until `sense_circuit_status = CANCEL - READY`,
then call `instance_delete()` to retire the SENSE instance.

In the test 0a run, the circuit entered `CANCEL - COMMITTED` at **18:14:10** and remained there through
the end of the log at **18:24:35+** — over 10 minutes with no SENSE-O-side transition to `CANCEL - READY`.
`SENSEDeleterDaemon` polled every ~10 seconds throughout, each cycle finding the same stuck status
and looping back. The request was permanently stuck in `CANCELED` DMM status with no recovery path.

```
18:14:10  sense_circuit_status → CANCEL - COMMITTED
18:14:20  SENSEDeleterDaemon: circuit not yet CANCEL-READY, skipping
18:14:30  SENSEDeleterDaemon: circuit not yet CANCEL-READY, skipping
...
18:24:35  SENSEDeleterDaemon: circuit not yet CANCEL-READY, skipping   ← end of log
```

### Root Cause

Two issues:

1. **SENSE-O internal failure**: SENSE-O failed to advance from `CANCEL - COMMITTED` to `CANCEL - READY`.
   This is a SENSE-O-side bug (not DMM), but DMM has no timeout or escalation to handle it.
2. **DMM has no stuck-cancel timeout**: `SENSEDeleterDaemon` has no maximum wait duration before
   alerting or attempting a recovery action (e.g., forced `instance_delete()`). It polls forever.

Note: This is distinct from BUG-004. BUG-004 fixed the `SENSECancellerDaemon` storm caused by
re-issuing cancel to a circuit already in `CANCEL - COMMITTING`. BUG-005 is the subsequent
phase — the circuit accepted the cancel and reached `CANCEL - COMMITTED` but SENSE-O
never finalized it to `CANCEL - READY`.

### Proposed Fix (not yet implemented)

Add a `cancel_committed_timeout_seconds` config parameter (suggested: 600s). In
`SENSEDeleterDaemon.run_once()`, track the elapsed time since `sense_circuit_status` entered
`CANCEL - COMMITTED`. If the timeout expires, attempt a forced `instance_delete()` and log
a high-severity alert for operator review.

```python
# deleter.py — proposed addition
elapsed = (datetime.utcnow() - req.canceled_at).total_seconds()
if elapsed > cancel_committed_timeout_seconds:
    logging.error(
        f"Circuit {req.sense_uuid} stuck in CANCEL - COMMITTED for {elapsed:.0f}s. "
        "Attempting forced instance_delete()."
    )
    try:
        instance_delete(req.sense_uuid)
        req.set_status(RequestStatus.DELETED, session=session)
    except Exception as e:
        logging.error(f"Forced delete failed: {e}")
    continue
```

### Before / After

| | Before (current) | After (proposed) |
|:---|:---|:---|
| Circuit stuck in CANCEL - COMMITTED | DMM polls forever — no recovery | Times out after N minutes → forced delete |
| Operator awareness | None — only visible via DB query | High-severity error log line |
| Request fate | Permanently stuck in `CANCELED` | Eventually reaches `DELETED` |

**Mitigation until fixed:** Manually query `sense_circuit_status` for requests in `CANCELED` status.
If stuck in `CANCEL - COMMITTED` for >10 minutes, manually call `instance_delete()` via SENSE-O API
or use the DMM REST endpoint `POST /reinitialize_sense` to force the request back to `ALLOCATED`.

---

## BUG-004 — SENSECancellerDaemon fires 500 BadRequest storm when cancel times out with 504

**Date:** 2026-07-30  
**Repo:** `rucio-sense-dmm`  
**Files:** `src/dmm/daemons/sense/canceller.py`, `src/dmm/core/sense.py`, `src/dmm/models/request.py`  
**Discovered via:** Test 2H (POOL EXHAUSTION RECOVERY) — logs `sense_1245_073026logs.txt` / `dmm_1245_072826logs.txt`

### Problem

When `PUT /cancel?sync=true` returns 504 (gateway timeout), SENSE has already accepted the cancel
and begins transitioning internally to `CANCEL - COMMITTING`. The canceller was reading
`req.sense_circuit_status` from the DB on every subsequent cycle — still `CREATE - READY` from
before the cancel call — passing it through `is_ready_for_cancel()` (returns True), and
re-issuing `cancel_link()` indefinitely. SENSE responded to every re-issued cancel with:

```
500 BadRequestException: Instance cannot cancel or release while in CANCEL - COMMITTING state
```

96 such errors were observed in the Test 2H run at ~10-second intervals before the circuit finally
reached `CANCEL - READY` and the storm stopped on its own.

Additionally, `CANCEL - COMMITTING` and `CANCEL - COMMITTED` were completely missing from the
`SenseCircuitStatus` enum, so DMM had no way to represent or check for these states even if it
had fetched the live status.

### Root Cause

Two compounding issues:
1. The canceller trusted the stale DB column (`sense_circuit_status`) rather than fetching
   the live status from SENSE before deciding whether to re-issue a cancel call.
2. `CANCEL - COMMITTING` and `CANCEL - COMMITTED` were not modelled in the enum, so there was
   no way to detect the "cancel already in progress" state even with a live fetch.

### Fix — 3 files changed (55 insertions, 14 deletions)

**`src/dmm/models/request.py`** — Add missing cancel intermediate states to the enum:

```python
class SenseCircuitStatus(str, Enum):
    ...
    CANCEL_COMMITTING  = "CANCEL - COMMITTING"
    CANCEL_COMMITTED   = "CANCEL - COMMITTED"
    CANCEL_READY       = "CANCEL - READY"
```

**`src/dmm/core/sense.py`** — Add `is_being_cancelled()` helper:

```python
def is_being_cancelled(status):
    """Returns True when SENSE has accepted a cancel and is still processing it.
    Re-issuing cancel_link() in CANCEL-COMMITTING or CANCEL-COMMITTED causes a
    500 BadRequestException storm — callers should wait for CANCEL-READY instead.
    """
    return status in {
        SenseCircuitStatus.CANCEL_COMMITTING.value,
        SenseCircuitStatus.CANCEL_COMMITTED.value,
    }
```

**`src/dmm/daemons/sense/canceller.py`** — Fetch live status before every cancel decision:

```python
# Always fetch the live status from SENSE before deciding how to cancel.
# The DB column (sense_circuit_status) may be stale if a previous cancel
# call timed out (504) and SENSE has already transitioned internally.
try:
    live_status = get_instance_status(req.sense_uuid)
except Exception as status_err:
    logging.warning(
        f"Could not fetch live circuit status for {req.sense_uuid}: {status_err}. "
        "Will retry next cycle."
    )
    continue

# Persist the refreshed status so other daemons see the correct state.
req.set_sense_circuit_status(live_status, session=session)

if is_being_cancelled(live_status):
    # SENSE has accepted the cancel (CANCEL-COMMITTING / CANCEL-COMMITTED).
    # Do NOT re-issue cancel_link() — that causes the 500 BadRequest storm.
    logging.debug(
        f"Circuit {req.sense_uuid} is already being cancelled on SENSE side "
        f"(status={live_status}). Waiting for CANCEL-READY."
    )
    continue
```

### Before / After

| | Before | After |
|:---|:---|:---|
| 504 on first cancel | DB status stays stale (`CREATE - READY`) | Live status fetched → `CANCEL - COMMITTING` |
| Next cycle action | `is_ready_for_cancel()` → True → re-fires cancel → 500 BadRequest | `is_being_cancelled()` → True → skip, wait |
| Storm duration | ~96 errors over ~960s until SENSE self-completes | 0 errors — skips silently until `CANCEL - READY` |

**Change size:** ~30 lines in `canceller.py`; 13 lines in `sense.py`; 2 lines in `request.py`.

---

## BUG-002 — Decider assigns full link capacity to B when A's SENSE SiteRM reservation still exists

**Date:** 2026-07-24  
**Repo:** `rucio-sense-dmm`  
**File:** `src/dmm/daemons/core/decider.py`  
**Method:** `_modify_existing_bandwidth`  
**Discovered via:** Test 2G (DESCENDING) — logs `sense_1130_071626logs.txt` / `dmm_1130_071526logs.txt`

### Problem

When request A finishes (Rucio rule done), the SENSEModifierDaemon throttles A's circuit to 1,000 Mbps and sets A's DMM status to `FINISHED`. Eventually A advances to `CANCELED` then `DELETED`, at which point A's DB record drops out of `_build_multi_graph` (CANCELED/DELETED are not in the status list). In the next Decider cycle, B is the sole request in the graph and the LP assigns B the full link capacity (100,000 Mbps).

However, A's SENSE circuit is not immediately removed from the SiteRM when A reaches `DELETED` in DMM. The SiteRM still counts A's 1,000 Mbps reservation until the SENSE `DELETE_SLICE` completes. When the SENSEModifierDaemon issues `modify_link(B, 100,000)`, the SENSE SiteRM rejects with an overlap/oversubscription error — 100,000 + 1,000 > 100,000.

In Test 2G the specific sequence is:
- 18:36:08 — A throttled to 1,000 Mbps; A → FINISHED in DMM
- 18:36:45 — B gets `modify_link(100,000)` → SENSE rejects (`OverlapException`)

### Fix

In `_modify_existing_bandwidth`, before issuing an upward MODIFY, cap the LP-assigned bandwidth by subtracting the `allocated_bandwidth_mbps` of all co-tenant requests that still have a live `sense_uuid` (PROVISIONED, STALE, MODIFIED, DECIDED, FINISHED, or FINISHED_R). This prevents requesting more capacity from SENSE than the SiteRM actually has free.

```python
# decider.py — _modify_existing_bandwidth (added after the multi_graph edge lookup)
if req_u is not None and allocated_bandwidth > (req.allocated_bandwidth_mbps or 0):
    link_capacity = multi_graph.nodes[req_u].get('link_capacity_mbps', allocated_bandwidth)
    cotenant_statuses = [
        RequestStatus.PROVISIONED, RequestStatus.STALE, RequestStatus.MODIFIED,
        RequestStatus.DECIDED, RequestStatus.FINISHED, RequestStatus.FINISHED_R,
    ]
    cotenant_reqs = Request.get_by_status(
        statuses=cotenant_statuses, session=session, use_lock=False
    )
    reserved_by_others = sum(
        (r.allocated_bandwidth_mbps or 0)
        for r in cotenant_reqs
        if r.rule_id != req.rule_id
        and (
            (r.src_site_ == req.src_site_ and r.dst_site_ == req.dst_site_)
            or (r.src_site_ == req.dst_site_ and r.dst_site_ == req.src_site_)
        )
        and r.sense_uuid is not None
    )
    capped = int(min(allocated_bandwidth, link_capacity - reserved_by_others))
    if capped != allocated_bandwidth:
        logging.info(
            f"Capping upward MODIFY for {req.rule_id}: "
            f"LP={allocated_bandwidth} → capped={capped} Mbps "
            f"({reserved_by_others} Mbps reserved by co-tenant circuits with active SENSE UUIDs)"
        )
    allocated_bandwidth = capped
```

For Test 2G: `reserved_by_others` = A's 1,000 Mbps → `capped` = 99,000 Mbps → SENSE accepts.

**Change size:** ~30 lines added; also captures `u` from the edge loop (was `_`).

---

## BUG-003 — MODIFY-FAILED + SENSE auto-delete leaves B stuck in STALE forever

**Date:** 2026-07-24  
**Repo:** `rucio-sense-dmm`  
**File:** `src/dmm/daemons/sense/modifier.py`  
**Method:** `run_once` → `is_modify_failed` block  
**Discovered via:** Test 2G (DESCENDING) — logs `sense_1130_071626logs.txt` / `dmm_1130_071526logs.txt`

### Problem

When SENSE rejects a MODIFY with `MODIFY-FAILED`, the SENSEModifierDaemon's recovery block correctly tries to cancel/delete the failed circuit and then reset the DMM request to `ALLOCATED` for re-staging. However, SENSE's own ConsistencyService can auto-cancel and delete the circuit before the DMM handler runs (within the ~30-second SENSE consistency window). When the DMM then calls `cancel_link(failed_uuid)` or `delete_instance(failed_uuid)`, SENSE returns a "not found" error, which raises an exception. The `except` block does `continue`, which skips the `req.update({...ALLOCATED...})` reset entirely. B is left in `STALE` status with a stale `sense_uuid` that no longer exists in SENSE, and it cannot recover.

In Test 2G the specific sequence is:
- 18:36:45 — B's MODIFY fails; SENSE sets B to `MODIFY-FAILED`
- 18:37:XX — SENSE ConsistencyService auto-cancels and deletes B's circuit
- 18:38:16 — SENSEModifierDaemon tries `cancel_link(B.uuid)` → exception → `continue` → B stuck in STALE

### Fix

Before attempting `cancel_link`/`delete_instance`, explicitly query SENSE for the circuit's current status. If SENSE returns `"UNKNOWN"` (circuit already gone), skip the cancel/delete entirely and proceed directly to the `ALLOCATED` reset. This makes recovery unconditional for the "already deleted" case and preserves the existing exception → retry path for genuine cancel/delete failures.

```python
# sense/modifier.py — is_modify_failed handler
current_sense_status = get_instance_status(failed_uuid)
if current_sense_status == "UNKNOWN":
    logging.warning(
        f"Circuit {failed_uuid} is already gone from SENSE (status=UNKNOWN) "
        f"— skipping cancel/delete, resetting {req.rule_id} to ALLOCATED"
    )
else:
    try:
        cancel_link(failed_uuid, status)
        delete_instance(failed_uuid)
    except Exception as e:
        logging.error(f"...: {e} — will retry next cycle", exc_info=True)
        continue
req.update({...ALLOCATED...}, session=session)
```

Also added `get_instance_status` to the import from `dmm.core.sense`.

**Recovery path after fix:** B resets to `ALLOCATED` → `SENSEStagerDaemon` creates a fresh SENSE instance → `SENSEProvisionerDaemon` provisions it (with the corrected bandwidth from BUG-002 fix).

**Change size:** ~10 lines changed; 1 import added.

---

## BUG-001 — Cross-request `session.rollback()` erases circuit reuse in `AllocatorDaemon`

**Date:** 2026-07-24  
**Repo:** `rucio-sense-dmm`  
**File:** `src/dmm/daemons/core/allocator.py`  
**Discovered via:** Test 2J (REUSE RACE) — logs `sense_1325_071726logs.txt` / `dmm_1325_071726logs.txt`

### Problem

`AllocatorDaemon.run_once` processes all INIT requests in a single for-loop sharing one SQLAlchemy session (injected by the `@databased` decorator). When an earlier request succeeds in `_reuse_finished_request` (writes `new_request → PROVISIONED` and `req_fin → DELETED` into the session), those writes are left uncommitted. If any later request fails in `_allocate_new_endpoints` and calls `session.rollback()`, the rollback is session-wide and silently erases the earlier request's reuse writes. The earlier request reverts to INIT, while the FINISHED_R request it was meant to reuse moves on to FINISHED (via SENSEModifierDaemon), permanently closing the reuse window.

### Impact

The higher-priority request that correctly won circuit reuse ends up FAILED. In environments where the BGP subnet pool is at capacity during teardown (infrastructure constraint — only 2 subnets), circuit reuse is the only viable path: the request cannot fall back to `_allocate_new_endpoints` and has no recovery.

### Fix

Added `session.commit()` immediately after the reuse writes in `_reuse_finished_request`, before `return True`. This durably persists the reuse decision before the loop moves to the next request, so any subsequent `session.rollback()` has nothing of this request left to undo.

```python
# allocator.py — _reuse_finished_request
req_fin.set_status(status=RequestStatus.DELETED, session=session)
session.commit()  # commit reuse atomically — prevents a later rollback from erasing this
claimed_rule_ids.add(req_fin.rule_id)
return True
```

**Change size:** 1 line added.

---