# DMM configuration

DMM reads one INI file, `dmm.cfg`. [`dmm.cfg.sample`](../dmm.cfg.sample) is a starting point holding the options a deployment usually sets. This page describes every option DMM reads, including the ones the sample leaves out because their defaults are fine.

## Where DMM looks

In order:

1. `--config <path>` on the command line
2. the `DMM_CONFIG` environment variable (the Docker image sets it to `/opt/dmm/dmm.cfg`)
3. `/opt/dmm/dmm.cfg`

DMM refuses to start if none of these is a readable file. On Kubernetes the Helm chart mounts the file from a secret at `/opt/dmm/dmm.cfg`.

DMM also needs three things that are not set in `dmm.cfg`:

- `rucio.cfg`, the Rucio client's own configuration
- `.sense-o-auth.yaml`, the SENSE-O client's OAuth credentials
- the X.509 certificate and key for FTS, whose paths are set in `[fts]`

## File format

- Write keys as `key=value`. A colon (`key: value`) is not accepted.
- Comments must be on a line of their own, starting with `#` or `;`. A comment after a value (`port=80 ; web`) becomes part of the value.
- Key names are case-insensitive, so `T2_US_SDSC-T2_US_Caltech` and `t2_us_sdsc-t2_us_caltech` are the same key. Values keep their case.
- A missing optional key takes the default listed below. An empty value (`key=`) is an empty string, not the default. For the options the sample leaves empty, an empty string means "off" or "not set" (see each option).
- DMM parses the file once, at startup. Any change needs a restart.

## Required options

DMM can't run without these:

| Section | Key | Why |
|---|---|---|
| `[dmm]` | `port` | the port the web frontend and API listen on |
| `[sense]` | `profile_uuid` | the SENSE-O service profile every circuit is staged from |
| `[fts]` | `fts_host`, `cert`, `key` | FTS endpoint and client certificate, for setting per-link stream limits |

Everything else has a default.

---

## `[dmm]`

| Key | Default | Meaning |
|---|---|---|
| `port` | required | Port for the frontend and API. If DMM can't bind it, it falls back to 31601. |
| `log_level` | `INFO` | Python log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `api_token` | unset | Shared secret for the POST endpoints that change state (`mark_finished`, `update_fts_limit`, `reinitialize_sense`, `reinitialize_request`, `refresh_sites`). Clients send it as a bearer token. If neither this nor `[auth]` login is set, those endpoints accept anyone who can reach DMM, and DMM logs a warning at startup. Set one of the two whenever DMM is reachable by anyone else. |

## `[auth]`: web login

Optional OpenID Connect login against SENSE-O's Keycloak. Login is on when `client_id` is set, and off when it is empty or missing.

| Key | Default | Meaning |
|---|---|---|
| `client_id` | unset | Keycloak client ID. Setting it turns login on. |
| `redirect_uri` | required once `client_id` is set | The callback URL registered for the client, e.g. `https://dmm.example.org/auth/callback`. If `client_id` is set and this is not, DMM fails at startup rather than leaving the frontend open. |
| `client_secret` | unset | Client secret for a confidential client. Leave it empty for a public client: the code exchange then relies on PKCE alone. |
| `metadata_url` | `https://sense-o.es.net:8543/realms/StackV/.well-known/openid-configuration` | The OIDC discovery document. |
| `session_hours` | `12` | How long a login session lasts. |
| `session_secret` | random at each start | Key that signs session cookies. If it is unset, every restart logs everyone out (they get back in without retyping a password while their Keycloak session lasts). |

## `[db]`

| Key | Default | Meaning |
|---|---|---|
| `db_type` | `postgresql` | `postgresql` (or `postgres`) or `sqlite`. |
| `username` | `dmm` | PostgreSQL user. |
| `password` | `dmm` | PostgreSQL password. |
| `db_host` | `localhost` | PostgreSQL host. |
| `db_port` | `5432` | PostgreSQL port. |
| `db_name` | `dmm` | PostgreSQL database. |
| `db_file` | `dmm.db` | SQLite file, used only when `db_type=sqlite`. |

## `[sense]`: circuits, retries and escalation

| Key | Default | Meaning |
|---|---|---|
| `profile_uuid` | required | SENSE-O service profile used to stage and modify every circuit. |
| `sense_keep_alive_seconds` | `60` | How long a finished rule's circuit stays up before it is cancelled, so the next rule on the same site pair can reuse it. A circuit that was never provisioned is cancelled at once. |
| `keep_failed_instances` | `false` | If `true`, a request that fails leaves its SENSE-O instance in place for troubleshooting instead of deleting it. |
| `path_check_enabled` | `true` | After SENSE-O refuses a staging for lack of resources, fetch its `/model` and search the path locally to narrow the reason down to `NO_CAPACITY`, `NO_VLANS`, `NO_TOPOLOGY` or `FEASIBLE_IN_MODEL`. Set to `false` to skip this check, e.g. if `/model` is slow. |
| `max_retries` | `3` | Retries for SENSE errors that don't fall into one of the cases below: a failed circuit, an instance SENSE-O no longer knows, other errors. |
| `transient_max_retries` | `5` | Retries when SENSE-O or a site's resource manager is unreachable (connection errors, HTTP 502/503/504). |
| `transient_backoff_seconds` | `300` | First wait before retrying an unreachable error. It doubles after each retry, up to an hour. |
| `no_resources_max_retries` | `1` | Restages after SENSE-O refuses a staging for lack of resources ("cannot find feasible path"). |
| `no_resources_backoff_seconds` | `120` | First wait before such a restage. It doubles after each retry, up to an hour. |
| `no_resources_blocked_max_retries` | `3` | Restages allowed instead of `no_resources_max_retries` when the refusal came while DMM's own circuits were holding the path, which can clear as they shrink or end. Each refused instance stays in `CREATE - FAILED` for an admin to delete, and an `ESCALATION` log line names it. |
| `max_hard_modifies` | `1` | How many times a circuit whose modify failed or stalled may be torn down and rebuilt from staging before DMM escalates. |
| `cancel_max_retries` | `3` | Force-cancels of a circuit whose cancel failed, before DMM escalates. |
| `stall_verify_seconds` | `900` | Once a circuit has sat in a `COMMITTED` state this long, DMM asks SENSE-O to verify it (once). |
| `stall_escalate_seconds` | `1800` | Once a circuit has sat in a `COMMITTED` state this long, DMM rebuilds it if it was stuck in a modify and escalates otherwise. A finished request's stuck circuit is force-cancelled. |

DMM does not retry authentication errors (HTTP 401/403) or other SENSE-O 5xx errors. It escalates them straight away.

## `[rtmon]`

| Key | Default | Meaning |
|---|---|---|
| `deployment` | auto | The RTMon deployment each ready circuit is registered with, spelled exactly as SENSE-O has it (`GET /addon/RTMon` lists them; on sense-o.es.net it is `Real Time Mon`). If it is unset or empty, DMM asks SENSE-O and uses the answer only if exactly one deployment is registered. With several, DMM logs their names and registers nothing, because registering gives that deployment's delegate user READ access to the instance. Set it on any orchestrator with more than one deployment. |

## `[decider]`: bandwidth allocation

| Key | Default | Meaning |
|---|---|---|
| `max_utilization` | `0.8` | Fraction of each site's, link's and known port's capacity that DMM's circuits may hold between them (on a port, together with circuits that aren't DMM's). Must be greater than 0 and at most 1. The rest is left for best-effort transfers and so SENSE-O always has room to compile the next circuit. At `1.0` one circuit can hold a whole port, and SENSE-O then refuses every other staging across it. |
| `split` | `least_waste` | How a site pair's bandwidth is divided among its rules. `least_waste` schedules them by remaining size and priority around SENSE-O's path calendar for the pair (`dmm.core.least_waste`), and rejects a new rule that can't finish within the horizon. Any other value (e.g. `priority`) splits the bandwidth in proportion to priority alone. |
| `schedule_horizon_hours` | `24` | The window that staging asks SENSE-O's path-bandwidth calendar for, and the schedule's horizon. At least 1. |
| `min_upward_modify_fraction` | `0.1` | DMM doesn't raise a live circuit's bandwidth by less than this fraction of its current bandwidth, because every change costs a SENSE modify. Decreases are always applied. |

See [decision-algorithm.md](decision-algorithm.md) for how these fit together.

## `[prometheus]`: circuit throughput

| Key | Default | Meaning |
|---|---|---|
| `host` | unset | Base URL of the Prometheus that scrapes the SiteRM VLAN counters. If it is empty, counter-based throughput monitoring is off and health is judged from the rule's FTS jobs instead. |
| `user` | unset | Basic-auth user. If it is empty, DMM sends no credentials. |
| `password` | unset | Basic-auth password. |

## `[monit]`: health and throughput tuning

This section isn't in the sample. Every key has a default.

| Key | Default | Meaning |
|---|---|---|
| `health_grace_seconds` | `300` | After a circuit is provisioned, its health stays unknown for this long rather than reading as unhealthy. |
| `health_min_throughput_mbps` | `1.0` | Below this throughput, nothing is moving on the circuit. |
| `health_min_utilization` | `0.0` (off) | Also count a circuit as unhealthy when its throughput is below this fraction of its allocated bandwidth. It is off because a transfer that doesn't fill its circuit needs tuning, not fixing: scoring that as a fault marked almost every request unhealthy. |
| `health_strikes` | `2` | Consecutive bad readings before a circuit is called unhealthy. |
| `throughput_window_seconds` | `300` | Throughput is averaged over this much counter history, not over two consecutive samples. |
| `health_fts_stall_seconds` | `1800` | When health is judged from FTS (no usable counters): files pending with no bytes delivered for this long counts as a stall. |
| `health_fts_max_age_seconds` | `600` | An FTS summary older than this, or one FTS didn't fully answer, is not judged, so an FTS outage doesn't look like stalled transfers. |
| `circuit_path_retry_seconds` | `600` | Wait before asking SENSE-O again for a circuit's path manifest after it couldn't give one. |
| `history_pages` | `10` | Pages of Rucio's per-RSE-pair request history searched to find a rule's FTS jobs. `0` disables the history search. |
| `utilization_excellent` | `0.9` | Web UI only: throughput/allocation ratio shown as EXCELLENT. |
| `utilization_ok` | `0.8` | Web UI only: ratio shown as OK (below it, LOW). |

## `[fts]`

| Key | Default | Meaning |
|---|---|---|
| `fts_host` | required | FTS REST endpoint, e.g. `https://fts3-pilot.cern.ch:8446`. |
| `cert` | required | Client certificate for FTS. |
| `key` | required | Its private key. |
| `default_num_streams` | `20` | Stream limit for a site pair that has no entry in `[fts-streams]`. |
| `max_delete_retries` | `3` | Attempts at removing a finished request's link/SE limits from FTS. After that DMM stops trying and leaves them in place, which does no harm. |

## `[rucio]`

| Key | Default | Meaning |
|---|---|---|
| `activities` | `SENSE Test` | Comma-separated Rucio activities whose rules DMM takes on. They must be spelled exactly as in Rucio, case included. Keep this in step with the Rucio server's `[dmm] activities`, which decides which transfers are routed over DMM's circuits. |
| `rule_lookback_hours` | `24` | Only rules created within this many hours are listed. |
| `heartbeat_timeout` | `30` | Seconds the heartbeat waits for Rucio before treating it as unreachable. Daemons that need Rucio pause until it answers again. |
| `max_retries` | `2` | Number of tries `GET /query/{rule_id}` (the endpoint the Rucio patch calls) makes, 15 s apart, while waiting for the rule's endpoints to be allocated. Despite the section, this setting doesn't retry Rucio calls. |

## `[daemons]`: polling intervals

Seconds between runs of each group of daemons.

| Key | Default | Daemons |
|---|---|---|
| `rucio` | `60` | Rucio initializer, modifier, finisher |
| `dmm` | `60` | allocator, decider |
| `sense` | `60` | SENSE handler, stager, provisioner, modifier, canceller, deleter |
| `fts` | `60` | FTS stream-limit modifier |
| `monit` | `60` | circuit health monitor, FTS job monitor |
| `db` | `7200` | site database refresh (sites, endpoints, mesh links from SENSE) |
| `rucio_heartbeat` | `60` | Rucio reachability probe. Must be greater than 0. |

## Sections keyed by site pair

The next three sections have no fixed keys: each key is a site name or a pair of site names.

### `[fts-streams]`

```ini
T2_US_SDSC-T2_US_Caltech=400
```

FTS stream limit for traffic between two sites, keyed `SRC-DST` by physical site name. If `SRC-DST` is missing, DMM tries `DST-SRC`, then falls back to `[fts] default_num_streams`. A pair's limit is shared among its active rules.

### `[vlan-ranges]`

```ini
T2_US_SDSC-T2_US_Caltech=3602-3606
```

The VLAN range SENSE-O may pick from when staging a circuit between two physical sites, in either order. A pair without an entry uses `any`.

DMM reads this section only when it first creates the mesh link for a pair, during a site database refresh. A mesh link that already exists keeps the range it was created with, so changing an entry here has no effect on an existing pair until its mesh row is removed.

### `[site-aliases]`

```ini
T2_US_UCSD_Blackhole=T2_US_UCSD
```

Maps a Rucio site name to the physical SENSE site it belongs to. DMM uses the Rucio ("logical") name for one thing only: choosing the SENSE-O subnet pool, `RUCIO_Site_BGP_Subnet_Pool-<logical name>`. Everything else (SENSE site, VLAN ranges, link capacity, FTS stream limits) uses the physical name.

Names of the form `PhysicalSiteName_ExtName` are resolved without an entry: DMM tries the name itself, then drops trailing `_ExtName` parts until it finds a site it knows (never shorter than three parts, so never just `T2_US`). An entry is needed only when the physical name can't be derived that way. An entry always wins, which also makes it a way to pin a name to itself. The physical site must be one SENSE-O lists (the `/sitename` of a site's metadata service). The site database refresh asks SENSE-O for that list once per run and skips every Rucio site that resolves to none of them.

---

## Keys no longer read

Older configs may still have these. DMM ignores them, and they can be deleted:

| Section | Key |
|---|---|
| `[sense]` | `finished_r_hold_seconds` |
| `[fts]` | `monit_host`, `monit_auth_token` |
