"""
Read-only local path check against the SENSE-O topology model.

When SENSE-O refuses a staging request with "cannot find feasible path", the
HTTP error body says nothing about WHY.  This module fetches the current model
via GET /model (pure read, no side effects) and runs the same three-stage path
search that sense_path_check.py runs interactively, returning a structured
verdict the stager can log and act on:

    FEASIBLE_IN_MODEL  -- model says it fits; SENSE-O refused for another reason
    NO_CAPACITY        -- topology is connected but the requested Mbps are not free
    NO_VLANS           -- topology is connected at zero rate but no VLAN id is free
    NO_TOPOLOGY        -- the two endpoints are not joined in the model at all
    UNKNOWN            -- fetch or parse failed; verdict is unavailable

The NML graph parsing logic (Index, search, bottleneck, blocked_link) is
derived from sense_path_check.py, which was originally written as a standalone
diagnostic script.  The fetch uses the existing SDK client
(WorkflowCombinedApi) so it reuses DMM's auth, retry and TLS configuration
without any additional setup.
"""
import collections
import json
import logging
import re
from typing import NamedTuple, Optional

from dmm.core.config import config_get_bool

# --------------------------------------------------------------------------
# NML / MRS namespace constants
# --------------------------------------------------------------------------

NML  = "http://schemas.ogf.org/nml/2013/03/base#"
MRS  = "http://schemas.ogf.org/mrs/2013/12/topology#"
RDFT = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

# Unit factors to Mbps.  Anything else is treated as unknown rather than guessed.
UNIT_MBPS = {
    "bps":    1e-6, "bit/s": 1e-6,
    "kbps":   1e-3, "kbit":  1e-3,
    "mbps":   1.0,  "mbit":  1.0,  "mbit/s": 1.0, "mb/s": 1.0,
    "gbps":   1e3,  "gbit":  1e3,  "gbit/s": 1e3, "gb/s": 1e3,
    "tbps":   1e6,  "tbit":  1e6,
}

# A name matching more than this many ports is a category, not a single endpoint.
MAX_CANDIDATES = 400

# --------------------------------------------------------------------------
# public result type
# --------------------------------------------------------------------------

class PathCheckResult(NamedTuple):
    verdict: str                          # FEASIBLE_IN_MODEL | NO_CAPACITY | NO_VLANS | NO_TOPOLOGY | UNKNOWN
    bottleneck_free_mbps: Optional[float] # set for NO_CAPACITY only
    bottleneck_port: Optional[str]        # set for NO_CAPACITY only
    detail: str                           # human-readable summary for logging


# --------------------------------------------------------------------------
# small helpers  (private)
# --------------------------------------------------------------------------

def _one(po, pred):
    vals = po.get(pred) or []
    return vals[0].get("value") if vals else None


def _all(po, pred):
    return [v.get("value") for v in (po.get(pred) or []) if v.get("value")]


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _domain_of(uri):
    """`urn:ogf:network:<domain>:<year>:...` -> "<domain>:<year>"."""
    parts = str(uri).split(":")
    return ":".join(parts[3:5]) if len(parts) > 4 else (
        parts[3] if len(parts) > 3 else "")


def _short(uri):
    """The readable tail of a URN: what follows the domain and its year."""
    parts = str(uri).split(":")
    if len(parts) > 5 and parts[4].isdigit():
        return ":".join(parts[5:]) or uri
    if len(parts) > 4:
        return ":".join(parts[4:])
    return uri


def _norm(text):
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


def _mbps(value, unit):
    """(Mbps float, unit_was_known bool).  Returns (None, True) when value is absent."""
    v = _num(value)
    if v is None:
        return None, True
    factor = UNIT_MBPS.get(str(unit or "").strip().lower())
    if factor is None:
        return None, False
    return v * factor, True


def _fmt_labels(labels, cap=6):
    """Compact human-readable representation of a VLAN window."""
    if labels is None:
        return "any"
    if not labels:
        return "none free"
    runs, vals = [], sorted(labels)
    start = prev = vals[0]
    for v in vals[1:]:
        if v == prev + 1:
            prev = v
            continue
        runs.append((start, prev))
        start = prev = v
    runs.append((start, prev))
    text = ",".join(f"{a}-{b}" if a != b else str(a) for a, b in runs[:cap])
    return text + (f" and {len(runs) - cap} more" if len(runs) > cap else "")


# --------------------------------------------------------------------------
# VLAN label helpers  (private)
# --------------------------------------------------------------------------

def _parse_labels(text):
    """"3600-3619,3700" -> {3600..3619, 3700}.  Junk is skipped, not fatal."""
    out = set()
    for part in re.split(r"[,\s]+", str(text or "").strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if lo <= hi and hi - lo <= 8192:
                out.update(range(lo, hi + 1))
        elif part.isdigit():
            out.add(int(part))
    return out


def _intersect(a, b):
    """VLAN ids two ports have in common.

    None on either side means UNCONSTRAINED (intersects to whatever the other
    side allows).  An empty set means blocked.  These two are opposite answers
    and must not be conflated.
    """
    if a is None and b is None:
        return None
    if a is None:
        return set(b)
    if b is None:
        return set(a)
    return a & b


# --------------------------------------------------------------------------
# Index: the model read into what a path question needs
# --------------------------------------------------------------------------

class Index:
    """Ports, who owns them, what joins them, and what is left on each.

    Directly derived from the Index class in sense_path_check.py.
    """

    def __init__(self, graph, meta=None):
        self.graph = graph
        self.meta  = meta or {}
        types = {s: set(_all(po, RDFT)) for s, po in graph.items()}

        def typed(t):
            return [s for s, ts in types.items() if t in ts]

        # Which SiteRM site a domain is, from its metadata service.
        self.site_of = {}
        for subj, po in graph.items():
            if _one(po, MRS + "tag") == "/sitename":
                value = _one(po, MRS + "value")
                if value:
                    self.site_of[_domain_of(subj)] = value

        ports = set(typed(NML + "BidirectionalPort"))
        self.parent, self.children = {}, collections.defaultdict(list)
        for p in ports:
            for c in _all(graph.get(p, {}), NML + "hasBidirectionalPort"):
                if c != p:
                    self.parent[c] = p
                    if c not in self.children[p]:
                        self.children[p].append(c)

        self.nodes, self.port_owner = {}, {}
        for n in typed(NML + "Node"):
            po = graph.get(n, {})
            self.nodes[n] = {
                "name":   _one(po, NML + "name") or _short(n),
                "domain": _domain_of(n),
                "site":   self.site_of.get(_domain_of(n), ""),
                "kind":   "host",
            }
            for p in _all(po, NML + "hasBidirectionalPort"):
                if p not in self.parent:
                    self.port_owner[p] = n

        # Cross-connects: two ports are switchable only when a SwitchingService
        # or RoutingService lists them both.  A Topology grouping is NOT this.
        self.svc_members, self.port_svcs = {}, collections.defaultdict(set)
        switching = (set(typed(NML + "SwitchingService"))
                     | set(typed(MRS + "RoutingService")))
        owner_of_svc = {}
        for subj in list(self.nodes) + typed(NML + "Topology"):
            for svc in _all(graph.get(subj, {}), NML + "hasService"):
                if svc in switching:
                    owner_of_svc[svc] = subj
        for svc in switching:
            members = [p for p in _all(graph.get(svc, {}),
                                       NML + "hasBidirectionalPort")
                       if p not in self.parent]
            if not members:
                continue
            self.svc_members[svc] = set(members)
            for p in members:
                self.port_svcs[p].add(svc)
            if owner_of_svc.get(svc) in self.nodes:
                self.nodes[owner_of_svc[svc]]["kind"] = "switch"

        # Ports whose owner is a Topology (transit domain modelled as one switch).
        for svc, members in self.svc_members.items():
            owner = owner_of_svc.get(svc)
            if not owner or owner in self.nodes:
                continue
            for p in members:
                if p in self.port_owner:
                    continue
                if owner not in self.nodes:
                    po = graph.get(owner, {})
                    self.nodes[owner] = {
                        "name":   _one(po, NML + "name") or _domain_of(owner),
                        "domain": _domain_of(owner),
                        "site":   self.site_of.get(_domain_of(owner), ""),
                        "kind":   "domain",
                    }
                self.port_owner[p] = owner

        # Anything still unclaimed: the longest node URI that prefixes the port.
        by_len = sorted(self.nodes, key=len, reverse=True)
        for p in ports:
            if p in self.parent or p in self.port_owner:
                continue
            for n in by_len:
                if p.startswith(n + ":"):
                    self.port_owner[p] = n
                    break

        self.node_ports = collections.defaultdict(list)
        for p, n in self.port_owner.items():
            self.node_ports[n].append(p)

        # Cables: nml:isAlias, symmetric.
        self.alias = collections.defaultdict(set)
        for p in ports:
            if p in self.parent:
                continue
            for other in _all(graph.get(p, {}), NML + "isAlias"):
                if other != p and other not in self.parent:
                    self.alias[p].add(other)
                    self.alias[other].add(p)

        self.port_info = {p: self._port(p)
                          for p in set(self.port_owner) | set(self.alias)}

    # -- per-port internals ------------------------------------------------

    def _bw(self, subj):
        """The bandwidth service of a port, normalised to Mbps, or None."""
        g = self.graph
        for svc in _all(g.get(subj, {}), NML + "hasService"):
            po = g.get(svc, {})
            ts = set(_all(po, RDFT))
            if MRS + "BandwidthService" not in ts and _one(po, MRS + "type") \
                    not in ("guaranteedCapped", "softCapped", "bestEffort"):
                continue
            unit = _one(po, MRS + "unit")
            out = {"svc": svc, "unit": unit or "",
                   "type": _one(po, MRS + "type") or ""}
            ok = True
            for key, pred in (("max",        "maximumCapacity"),
                               ("available",  "availableCapacity"),
                               ("reservable", "reservableCapacity"),
                               ("individual", "individualCapacity")):
                out[key], good = _mbps(_one(po, MRS + pred), unit)
                ok = ok and good
            out["unit_ok"] = ok
            return out
        return None

    def _labels(self, subj):
        """(VLAN window or None, raw text).

        None means no label group at all (unconstrained).
        An empty set means a label group exists but is empty (blocked).
        These two are opposite answers.
        """
        g = self.graph
        groups = _all(g.get(subj, {}), NML + "hasLabelGroup")
        fixed  = _all(g.get(subj, {}), NML + "hasLabel")
        if not groups and not fixed:
            return None, ""
        window, raws = set(), []
        for grp in groups:
            raw = _one(g.get(grp, {}), NML + "values") or ""
            raws.append(raw)
            window |= _parse_labels(raw)
        for lab in fixed:
            val = _one(g.get(lab, {}), NML + "value")
            if val is not None:
                raws.append(str(val))
                window |= _parse_labels(val)
        return window, ",".join(r for r in raws if r)

    @staticmethod
    def _is_hold(po):
        """Is this child of a port a reservation, or part of the port itself?

        A vlanport (reservation) carries hasLabel, an mrs:tag, existsDuring,
        or belongsTo.  A LAG member does not; counting it as a hold causes the
        parent port to report committed > capacity, blocking every path.
        """
        return bool(_all(po, NML + "hasLabel") or _one(po, MRS + "tag")
                    or _all(po, NML + "existsDuring")
                    or _one(po, MRS + "belongsTo"))

    def reservations(self, port):
        """What holds capacity on a port, deduplicated."""
        g = self.graph
        rows, seen_sub, seen_svc = [], set(), set()
        for c in self.children.get(port, []):
            if c in seen_sub:
                continue
            seen_sub.add(c)
            po = g.get(c, {})
            if not self._is_hold(po):
                continue
            bw = self._bw(c)
            if bw and bw["svc"] in seen_svc:
                continue
            if bw:
                seen_svc.add(bw["svc"])
            labels, _raw = self._labels(c)
            amount = None
            if bw:
                amount = next((bw[k] for k in ("reservable", "max", "individual")
                               if bw.get(k) is not None), None)
            rows.append({"subport": c, "name": _short(c),
                         "vlan": min(labels) if labels else None,
                         "tag":  _one(po, MRS + "tag") or "",
                         "mbps": amount})
        rows.sort(key=lambda r: -(r["mbps"] or 0))
        return rows

    def _port(self, p):
        bw            = self._bw(p)
        window, raw   = self._labels(p)
        holds         = self.reservations(p)
        in_use        = {r["vlan"] for r in holds if r["vlan"] is not None}
        total         = bw["max"] if bw else None
        committed     = sum(r["mbps"] or 0 for r in holds)
        free, notes   = None, []
        if bw and not bw["unit_ok"]:
            notes.append(f"unknown capacity unit {bw['unit']!r}")
        if total is not None:
            free = total - committed
            if bw.get("available") is not None and bw["available"] < free:
                notes.append("the domain's published availableCapacity is "
                             "below what the itemised reservations leave")
                free = bw["available"]
            if committed > total:
                notes.append("itemised reservations exceed the port's own "
                             "maximumCapacity")
            free = max(0.0, free)
        owner     = self.port_owner.get(p, "")
        node      = self.nodes.get(owner, {})
        return {"uri": p, "name": _short(p), "node": owner,
                "node_name": node.get("name", ""),
                "domain": _domain_of(p),
                "site": node.get("site") or self.site_of.get(_domain_of(p), ""),
                "total": total, "committed": committed, "free": free,
                "labels": window, "labels_raw": raw,
                "labels_free": (window - in_use) if window is not None else None,
                "reservations": holds, "notes": notes}

    # -- reachability -------------------------------------------------------

    def neighbours(self, p):
        """(next port, kind): across a cable ('link') or through a switch ('switch')."""
        for q in self.alias.get(p, ()):
            yield q, "link"
        for svc in self.port_svcs.get(p, ()):
            for q in self.svc_members.get(svc, ()):
                if q != p:
                    yield q, "switch"

    def link_window(self, a, b):
        ia, ib = self.port_info.get(a), self.port_info.get(b)
        wa = ia["labels_free"] if ia else None
        wb = ib["labels_free"] if ib else None
        return wa, wb, _intersect(wa, wb)

    def free(self, p):
        info = self.port_info.get(p)
        return info["free"] if info else None

    # -- endpoint resolution -----------------------------------------------

    def resolve(self, name):
        """(ports, how) for an endpoint named any of the ways people name it."""
        n = _norm(name)
        if not n:
            return [], "nothing to resolve"

        # Exact port URI or its readable tail.
        exact = [p for p in self.port_info
                 if _norm(p) == n or _norm(_short(p)) == n]
        if exact:
            return sorted(exact), "an exact port"

        # Node, by its declared nml:name or its URI tail.
        hits = [x for x, i in self.nodes.items()
                if _norm(i["name"]) == n or _norm(_short(x)) == n]
        if hits:
            return (sorted(p for x in hits for p in self.node_ports.get(x, [])),
                    f"{len(hits)} node(s) by name")

        # SiteRM sitename (what a Rucio RSE resolves to): exact first, then contains.
        doms = [d for d, s in self.site_of.items() if _norm(s) == n]
        if not doms:
            doms = [d for d, s in self.site_of.items() if n and n in _norm(s)]
        if not doms:
            doms = [d for d in {i["domain"] for i in self.nodes.values()}
                    if n and n in _norm(d)]
        if doms:
            xs = [x for x, i in self.nodes.items() if i["domain"] in doms]
            return (sorted(p for x in xs for p in self.node_ports.get(x, [])),
                    f"{len(doms)} domain(s): {', '.join(sorted(doms)[:4])}")

        # Last resort: substring of a node name.
        hits = [x for x, i in self.nodes.items() if n in _norm(i["name"])]
        if hits:
            return (sorted(p for x in hits for p in self.node_ports.get(x, [])),
                    f"{len(hits)} node(s) by partial name (loose)")
        return [], "nothing in the model matches that name"


# --------------------------------------------------------------------------
# model fetch  (private)
# --------------------------------------------------------------------------

def _parse_model(raw):
    """(graph dict, meta dict) from a raw /model response body (str or dict).

    Raises ValueError on any structural problem so the caller can surface a
    clean UNKNOWN verdict instead of an unhandled exception.
    """
    if isinstance(raw, dict):
        payload = raw
    elif isinstance(raw, list):
        payload = raw[0] if raw else {}
    else:
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"GET /model response is not JSON: {exc}") from exc
        if isinstance(payload, list):
            payload = payload[0] if payload else {}

    ttl = payload.get("ttlModel")
    if not ttl:
        raise ValueError("GET /model response contains no ttlModel field")

    try:
        graph = json.loads(ttl) if isinstance(ttl, str) else ttl
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"ttlModel is not RDF/JSON — if SENSE-O started sending real "
            f"Turtle the parser cannot handle it: {exc}"
        ) from exc

    if not isinstance(graph, dict):
        raise ValueError("ttlModel did not parse to a JSON object")

    meta = {
        "version":       payload.get("version", ""),
        "creation_time": payload.get("creationTime", ""),
        "status":        payload.get("status", ""),
        "subjects":      len(graph),
        "bytes":         len(ttl) if isinstance(ttl, str) else 0,
    }
    return graph, meta


def _fetch_model():
    """Fetch GET /model via the SDK client and return (graph, meta).

    Uses the same WorkflowCombinedApi instance (and therefore the same auth,
    TLS and retry config) as all other SENSE calls in DMM.  Raises on any
    error so check_path() can convert it to an UNKNOWN verdict.
    """
    # Import here to avoid a circular import at module load time.
    from sense.client.workflow_combined_api import WorkflowCombinedApi  # noqa: PLC0415
    api = WorkflowCombinedApi()
    raw = api.client.request("GET", "/model", accept_type="json")
    return _parse_model(raw)




# --------------------------------------------------------------------------
# path search
# --------------------------------------------------------------------------

def _search(ix, sources, targets, need=None, labels=True):
    """BFS from any source to any target port.

    need:   Mbps every port must have free (None = no capacity constraint).
    labels: if True, refuse a cable whose two VLAN windows do not overlap.
    """
    targets = set(targets)

    def ok(p):
        cap = ix.free(p)
        # Unknown capacity is treated as unconstrained, not as zero.
        return need is None or cap is None or cap >= need

    start = [s for s in sources if ok(s)]
    prev, seen = {}, set(start)
    queue = collections.deque(start)
    while queue:
        p = queue.popleft()
        if p in targets:
            path = [p]
            while path[0] in prev:
                path.insert(0, prev[path[0]])
            return path
        for q, kind in ix.neighbours(p):
            if q in seen or not ok(q):
                continue
            if labels and kind == "link":
                _wa, _wb, meet = ix.link_window(p, q)
                if meet is not None and not meet:
                    continue
            seen.add(q)
            prev[q] = p
            queue.append(q)
    return None


def _bottleneck(ix, path):
    """(port URI, free Mbps) of the tightest port on the path, or (None, None)."""
    worst, worst_free = None, None
    for p in path:
        f = ix.free(p)
        if f is None:
            continue
        if worst_free is None or f < worst_free:
            worst, worst_free = p, f
    return worst, worst_free


def _blocked_link(ix, path):
    """The first cable on the path whose two VLAN windows do not overlap."""
    for a, b in zip(path, path[1:]):
        if b in ix.alias.get(a, ()):
            wa, wb, meet = ix.link_window(a, b)
            if meet is not None and not meet:
                return a, b, wa, wb
    return None


# --------------------------------------------------------------------------
# the three-stage ladder
# --------------------------------------------------------------------------

def _run_ladder(ix, srcs, dsts, need_mbps):
    """Run the three probes and return a PathCheckResult.

    Stage 1: requested rate + VLAN continuity  -> FEASIBLE_IN_MODEL if found
    Stage 2: zero rate    + VLAN continuity    -> NO_CAPACITY if found
    Stage 3: zero rate,   VLANs ignored        -> NO_VLANS if found
    (none)                                     -> NO_TOPOLOGY
    """
    # Stage 1: does the model carry the full request?
    path1 = _search(ix, srcs, dsts, need=need_mbps, labels=True)
    if path1:
        return PathCheckResult(
            verdict="FEASIBLE_IN_MODEL",
            bottleneck_free_mbps=None,
            bottleneck_port=None,
            detail=(
                "The model carries the request end to end at the requested "
                f"rate ({need_mbps:.0f} Mbps) with a VLAN free the whole way. "
                "SENSE-O refused for something outside capacity and VLAN "
                "continuity — possibly a policy, a per-domain driver refusal, "
                "or the model changed between SENSE-O's attempt and this read."
            ),
        )

    # Stage 2: is the topology connected, ignoring bandwidth?
    path2 = _search(ix, srcs, dsts, need=None, labels=True)
    if path2:
        worst, worst_free = _bottleneck(ix, path2)
        free_str = f"{worst_free:.0f}" if worst_free is not None else "unknown"
        port_str = _short(worst) if worst else "unknown"
        return PathCheckResult(
            verdict="NO_CAPACITY",
            bottleneck_free_mbps=worst_free,
            bottleneck_port=worst,
            detail=(
                f"A zero-bandwidth path exists, so the topology is joined "
                f"end to end, but the request failed on free bandwidth alone. "
                f"Bottleneck: {port_str} with {free_str} Mbps free "
                f"(requested {need_mbps:.0f} Mbps)."
            ),
        )

    # Stage 3: is there any route at all, even without VLAN continuity?
    path3 = _search(ix, srcs, dsts, need=None, labels=False)
    if path3:
        hit = _blocked_link(ix, path3)
        if hit:
            a, b, wa, wb = hit
            detail = (
                f"A route exists but has no VLAN id free end to end. "
                f"First blocked cable: {_short(a)} <-> {_short(b)} "
                f"(free labels: {_fmt_labels(wa)} | {_fmt_labels(wb)}). "
                "Freeing a VLAN on this cable will fix it; "
                "adding bandwidth will not."
            )
        else:
            detail = (
                "A route exists but has no VLAN id free end to end. "
                "Freeing a VLAN on the path will fix it; "
                "adding bandwidth will not."
            )
        return PathCheckResult(
            verdict="NO_VLANS",
            bottleneck_free_mbps=None,
            bottleneck_port=None,
            detail=detail,
        )

    # No route at any rate with VLANs ignored.
    return PathCheckResult(
        verdict="NO_TOPOLOGY",
        bottleneck_free_mbps=None,
        bottleneck_port=None,
        detail=(
            "There is no route between these endpoints in this model at any "
            "rate, even with VLANs ignored. The ports to route over are simply "
            "not present. The usual cause is a site RM driver that has stopped "
            "publishing its domain, silently removing a whole site's ports."
        ),
    )



# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------

def check_path(src_site: str, dst_site: str, need_mbps: float) -> PathCheckResult:
    """Best-effort local path check: fetch the model and diagnose a NO_RESOURCES failure.

    Returns a PathCheckResult with verdict in:
        FEASIBLE_IN_MODEL, NO_CAPACITY, NO_VLANS, NO_TOPOLOGY, UNKNOWN

    Never raises: any error (fetch failure, parse error, unresolvable site
    name) is caught and returned as verdict=UNKNOWN so the caller degrades
    gracefully.
    """
    if not config_get_bool("sense", "path_check_enabled", default=True):
        return PathCheckResult(
            verdict="UNKNOWN",
            bottleneck_free_mbps=None,
            bottleneck_port=None,
            detail="path check disabled via config (sense.path_check_enabled = false)",
        )

    try:
        graph, meta = _fetch_model()
    except Exception as exc:
        logging.warning(f"sense_model: GET /model failed, path check skipped: {exc}")
        return PathCheckResult(
            verdict="UNKNOWN",
            bottleneck_free_mbps=None,
            bottleneck_port=None,
            detail=f"GET /model failed: {exc}",
        )

    try:
        ix = Index(graph, meta)
    except Exception as exc:
        logging.warning(f"sense_model: failed to build topology index: {exc}")
        return PathCheckResult(
            verdict="UNKNOWN",
            bottleneck_free_mbps=None,
            bottleneck_port=None,
            detail=f"topology index build failed: {exc}",
        )

    srcs, how_src = ix.resolve(src_site)
    dsts, how_dst = ix.resolve(dst_site)

    if not srcs:
        msg = f"source site {src_site!r} not found in model ({how_src})"
        logging.warning(f"sense_model: {msg}")
        return PathCheckResult(verdict="UNKNOWN", bottleneck_free_mbps=None,
                               bottleneck_port=None, detail=msg)
    if not dsts:
        msg = f"destination site {dst_site!r} not found in model ({how_dst})"
        logging.warning(f"sense_model: {msg}")
        return PathCheckResult(verdict="UNKNOWN", bottleneck_free_mbps=None,
                               bottleneck_port=None, detail=msg)

    if len(srcs) > MAX_CANDIDATES or len(dsts) > MAX_CANDIDATES:
        logging.warning(
            f"sense_model: site name resolved to too many ports "
            f"(src={len(srcs)}, dst={len(dsts)}); path check skipped"
        )
        return PathCheckResult(
            verdict="UNKNOWN",
            bottleneck_free_mbps=None,
            bottleneck_port=None,
            detail=f"site name too broad: src={len(srcs)} ports, dst={len(dsts)} ports",
        )

    logging.debug(
        f"sense_model: resolved {src_site!r} -> {len(srcs)} port(s) ({how_src}), "
        f"{dst_site!r} -> {len(dsts)} port(s) ({how_dst}); "
        f"model v{meta.get('version') or 'unknown'}, "
        f"{meta.get('subjects', 0):,} subjects"
    )

    try:
        return _run_ladder(ix, srcs, dsts, need_mbps)
    except Exception as exc:
        logging.warning(f"sense_model: ladder search failed: {exc}", exc_info=True)
        return PathCheckResult(
            verdict="UNKNOWN",
            bottleneck_free_mbps=None,
            bottleneck_port=None,
            detail=f"ladder search failed: {exc}",
        )
