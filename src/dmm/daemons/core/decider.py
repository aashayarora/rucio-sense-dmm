import logging
import time
from datetime import datetime, timezone
import numpy as np
from scipy.optimize import linprog
import networkx as nx
from math import ceil, floor

from dmm.daemons.base import DaemonBase

from dmm.models.request import Request, RequestStatus
from dmm.models.mesh import Mesh
from dmm.models.path_port import PathPort
from dmm.db.session import databased
from dmm.core.config import config_get, config_get_float
from dmm.core.least_waste import MAX_RULES, final_allocation
from dmm.core.sense import calendar_hours, is_circuit_active, is_being_provisioned

# How close the binary search gets the floor to its best value. An allocation that
# sits on the floor is up to this far below a round number, so rounding allows for
# it - flooring 99997 Mbps to 99000 turned search noise into SENSE modifies.
_PRECISION_MBPS = 5


def _whole_gbps(mbps):
    return floor(mbps / 1000) * 1000


_TICK_S = 60


def _whole_gbps_share(bandwidth):
    """
    Down to whole Gbps, at least 1 Gbps if anything. The search leaves the floor up
    to _PRECISION_MBPS short, which is not a reason to give up a whole Gbps.
    """
    rounded = floor((bandwidth + _PRECISION_MBPS) // 1000) * 1000
    return 1000 if bandwidth > 0 and rounded == 0 else rounded


def _calendar_now(req):
    """SENSE-O's figure for the path when req was staged: its calendar's first slot, or the window's minimum."""
    slots = (req.path_calendar or {}).get("slots")
    return sorted(slots)[0][2] if slots else req.available_bandwidth_mbps


def _staged_at(req):
    """When req was staged, from its calendar, or None without one."""
    at = (req.path_calendar or {}).get("at")
    return datetime.fromtimestamp(at, timezone.utc).replace(tzinfo=None) if at else None


def _ports_known(data, ports):
    """Whether every port on an edge's path has a known capacity, so the port rows bound it."""
    path = data.get('path_ports') or ()
    return bool(path) and bool(ports) and all(uri in ports for uri in path)


def _calendar_reservations(live, total, horizon, ports_known=False):
    """
    The part of an edge's share (total Mbps) the pair's latest SENSE-O calendar
    shows won't be free later, as least_waste reservations [(start, end, Mbps)]
    in ticks from now.

    The calendar's first slot is what was free at staging; pair_available is that
    plus what DMM held on the pair, so at time t DMM has pair_available less the
    calendar's drop since then. DMM's circuits are open-ended, so a step in the
    calendar is someone else's reservation starting or ending. When the ports
    bound the edge (ports_known) its share stands in for pair_available, so the
    drop comes off the share in full.
    """
    staged = [d for _, d in live if (d.get('calendar') or {}).get('slots')]
    if not staged:
        return []
    latest = max(staged, key=lambda d: (d.get('calendar') or {}).get('at') or 0)
    slots = sorted(latest['calendar']['slots'])
    free_then = slots[0][2]
    pair_available = total if ports_known else (latest.get('pair_available') or free_then)
    now = time.time()
    out = []
    for start, end, free in slots:
        held = total - (pair_available - (free_then - free))
        a = max(0.0, (start - now) / _TICK_S)
        b = min(float(horizon), (end - now) / _TICK_S)
        if held > 0 and b > a:
            out.append((floor(a), ceil(b), min(held, total)))
    return out


def _plan_fits(allocations, reservations, total):
    """Every share positive, within the edge's total, and clear of the reservations."""
    eps = 1e-6
    for x1, x2, y1, y2, _ in allocations:
        if y2 - y1 <= 0 or y2 > total + eps:
            return False
        if any(x1 < b - eps and x2 > a + eps and y1 < h - eps for a, b, h in reservations):
            return False
    return True


def _remaining_mbit(req):
    """What the rule has left to move, in Mbit, or None when its size isn't known."""
    if not req.rule_size or req.rule_size <= 0:
        return None
    done = (req.fts_summary or {}).get("bytes_done") or 0
    return max(req.rule_size - done, 1e5) * 8 / 1e6


def _max_utilization():
    """
    The share of each site's, link's and port's capacity DMM's circuits may hold
    between them, [decider] max_utilization (0.8 by default).

    Holding all of a port leaves SENSE-O no room to compile another circuit across
    it: it refuses with "cannot find feasible path", before the decider could make
    room. In prod that failed every rule staged behind a circuit holding the whole
    of UCSD's 200G (2026-11-04..11). What's left over also carries best-effort
    transfers.
    """
    u = config_get_float("decider", "max_utilization", default=0.8, constraint="pos")
    if u > 1:
        raise ValueError(f"[decider] max_utilization must be at most 1, not {u}")
    return u


class DeciderDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        
    def process(self, **kwargs):
        self.run_once(**kwargs)
    
    @databased
    def run_once(self, session=None):
        self._record_pair_available(session)
        multi_graph = self._build_multi_graph(session)
        
        if not multi_graph.nodes:
            return

        simple_graph, nodes, edges = self._simplify_graph(multi_graph)
        ports = self._load_ports(edges, session)
        A, c, b, edge_index = self._prepare_optimization_matrices(simple_graph, nodes, edges, ports)
        current = np.array([data['current'] for _, _, data in edges], dtype=float)
        optim_result = self._optimize_bandwidth(A, b, c, edges, current)

        self._allocate_bandwidth(multi_graph, simple_graph, edges, edge_index, optim_result)
        self._reject_unschedulable(multi_graph, session)

        self._allocate_new_bandwidth(multi_graph, session)
        self._modify_existing_bandwidth(multi_graph, session)

    def _build_multi_graph(self, session) -> nx.MultiGraph:
        """
        Build a network graph from the requests in the database.
        The link capacity is taken from the Mesh table.

        FINISHED requests are kept in the graph as *fixed* reservations at their
        allocated bandwidth: they don't compete in the priority split, but the
        capacity they hold stays reserved (for a potential circuit takeover) until the
        request is cancelled or taken over. This way active requests get readjusted
        exactly once — when the finished circuit's final disposition is known.

        The reservation only lasts while a takeover is still possible: see
        _holds_reservation.
        """
        multi_graph = nx.MultiGraph()
        reqs = Request.get_by_status(statuses=[RequestStatus.MODIFIED, RequestStatus.DECIDED, RequestStatus.STALE, RequestStatus.STAGED, RequestStatus.PROVISIONED, RequestStatus.FINISHED], session=session) # get all requests which would affect the decision (i.e. don't consider requests that are in CANCELLED or FAILED state)
        if reqs == []:
            return multi_graph
        for req in reqs:
            if not req.src_site or not req.dst_site:
                logging.error(f"Request {req.rule_id} is missing source or destination site, excluding from optimization")
                continue
            link_capacity_mbps = Mesh.get_link_capacity(req.src_site, req.dst_site, session=session)
            if link_capacity_mbps is None:
                logging.error(f"No link capacity found for {req.src_site.name}-{req.dst_site.name}, excluding request {req.rule_id} from optimization")
                continue
            is_fixed = req.transfer_status == RequestStatus.FINISHED
            if is_fixed and not self._holds_reservation(req):
                logging.debug(
                    f"Finished request {req.rule_id} no longer reserves capacity: "
                    f"circuit {req.sense_uuid} is {req.sense_circuit_status}"
                )
                continue
            self._add_site_node(multi_graph, req.src_site.name, link_capacity_mbps)
            self._add_site_node(multi_graph, req.dst_site.name, link_capacity_mbps)
            # Without a pair_available (rows that predate it) a request adds its own
            # available bandwidth, capped at the link, to what its pair may have.
            max_bandwidth = link_capacity_mbps
            if req.available_bandwidth_mbps is not None:
                max_bandwidth = min(link_capacity_mbps, _whole_gbps(req.available_bandwidth_mbps))
            multi_graph.add_edge(
                req.src_site.name, req.dst_site.name,
                rule_id=req.rule_id,
                priority=req.priority or 0,  # guard against None priority
                bandwidth=req.allocated_bandwidth_mbps,
                link_capacity=link_capacity_mbps,
                max_bandwidth=max_bandwidth,
                pair_available=req.pair_available_mbps,
                pair_available_at=req.pair_available_at,
                fixed=is_fixed,
                fixed_bandwidth=(req.allocated_bandwidth_mbps or 0) if is_fixed else 0,
                path_ports=list(req.path_ports or []),
                size_mbit=_remaining_mbit(req),
                committed=is_circuit_active(req.sense_circuit_status) or is_being_provisioned(req.sense_circuit_status),
                calendar=req.path_calendar,
            )
        return multi_graph

    def _record_pair_available(self, session) -> None:
        """
        For each request staged since the last run, record what SENSE-O has free for
        DMM on its site pair: what its staging found free now (the calendar's first
        slot; the window's minimum without a calendar) plus what DMM's circuits on
        the pair hold. Later changes in the calendar are the schedule's to handle.
        Each figure is stamped with its staging, so an edge takes the latest
        staging's figure even when one run records several.

        SENSE-O's figure for a new rule is what its path has left after every circuit
        on it, DMM's own included, and DMM shares a pair between its requests, so a
        new rule adds its available bandwidth to what the pair already had. It adds
        it to what DMM's circuits there hold, not to what they were offered: one not
        up yet holds nothing, and SENSE-O reported its bandwidth as free again.
        """
        staged = [
            r for r in Request.get_by_status(statuses=[RequestStatus.STAGED], session=session)
            if _calendar_now(r) is not None and r.src_site_ and r.dst_site_
        ]
        if not staged:
            return
        holders = [
            r for r in Request.get_by_status(
                statuses=[RequestStatus.PROVISIONED, RequestStatus.STALE, RequestStatus.MODIFIED,
                          RequestStatus.DECIDED, RequestStatus.FINISHED],
                session=session, use_lock=False,
            )
            if self._holds_reservation(r)
        ]
        for req in staged:
            pair = {req.src_site_, req.dst_site_}
            held = sum(
                self._held_on_sense(r) for r in holders
                if r.rule_id != req.rule_id and r.sense_uuid != req.sense_uuid
                and {r.src_site_, r.dst_site_} == pair
            )
            free = _calendar_now(req)
            logging.info(
                f"SENSE-O has {free + held:.4f} Mbps free for DMM on "
                f"{req.src_site_}-{req.dst_site_}: {free:.4f} reported for "
                f"{req.rule_id} + {held:.0f} held by DMM circuits on the pair"
            )
            req.set_pair_available(free + held, at=_staged_at(req), session=session)

    @staticmethod
    def _holds_reservation(req) -> bool:
        """
        Whether a finished request still keeps its capacity reserved for a takeover.

        Only while it has a circuit that is up, or on its way up (a create or modify
        still committing when the rule finished). Once SENSE is cancelling the circuit - or has failed to - the
        allocator will never reuse it, and holding its capacity starves the live
        requests for as long as the cancel keeps failing.
        """
        return bool(req.sense_uuid) and (
            is_circuit_active(req.sense_circuit_status)
            or is_being_provisioned(req.sense_circuit_status)
        )

    @staticmethod
    def _add_site_node(graph, site_name, link_capacity_mbps) -> None:
        """
        A site's capacity constraint must not be overwritten by whichever request is
        processed last — keep the maximum of its adjacent link capacities.
        """
        if graph.has_node(site_name):
            current = graph.nodes[site_name].get('link_capacity_mbps')
            if current is None or link_capacity_mbps > current:
                graph.nodes[site_name]['link_capacity_mbps'] = link_capacity_mbps
        else:
            graph.add_node(site_name, link_capacity_mbps=link_capacity_mbps)

    def _simplify_graph(self, multi_graph) -> tuple:
        """
        Simplify the network graph by merging edges with the same source and destination
        nodes. Fixed (finished) requests contribute reserved bandwidth instead of priority.
        """
        simple_graph = nx.Graph()
        simple_graph.add_nodes_from(multi_graph.nodes(data=True))

        for u, v, data in multi_graph.edges(data=True):
            priority = 0 if data.get('fixed') else data['priority']
            fixed_bandwidth = data.get('fixed_bandwidth', 0)
            link_capacity = data['link_capacity']
            # Without a pair_available on the edge, its live requests' own available
            # bandwidths added up; a finished reservation adds nothing.
            max_bandwidth = 0 if data.get('fixed') else data.get('max_bandwidth', link_capacity)
            pair_available, pair_available_at = data.get('pair_available'), data.get('pair_available_at')
            # What the edge's live requests hold now; NaN once any of them has
            # nothing decided yet, since there is then no allocation to keep.
            current = 0.0 if data.get('fixed') else (data['bandwidth'] if data['bandwidth'] is not None else np.nan)
            path_ports = set(data.get('path_ports') or ())
            if simple_graph.has_edge(u, v):
                edge = simple_graph[u][v]
                edge['path_ports'] |= path_ports
                edge['priority'] += priority
                edge['fixed_bandwidth'] += fixed_bandwidth
                edge['current'] += current
                edge['max_bandwidth'] += max_bandwidth
                # The physical link capacity is fixed — take the max (not sum) so we
                # don't artificially inflate the upper-bound constraint in the LP.
                edge['link_capacity'] = max(edge['link_capacity'], link_capacity)
                # The latest staging on the pair saw everything staged before it.
                if pair_available is not None and (
                    edge['pair_available_at'] is None or pair_available_at > edge['pair_available_at']
                ):
                    edge['pair_available'], edge['pair_available_at'] = pair_available, pair_available_at
            else:
                simple_graph.add_edge(u, v, priority=priority, fixed_bandwidth=fixed_bandwidth,
                                      link_capacity=link_capacity, max_bandwidth=max_bandwidth,
                                      pair_available=pair_available,
                                      pair_available_at=pair_available_at if pair_available is not None else None,
                                      current=current, path_ports=path_ports)

        return simple_graph, list(simple_graph.nodes), list(simple_graph.edges(data=True))

    def _load_ports(self, edges, session) -> dict:
        """
        The ports each edge's circuits cross, and what each port can give DMM.

        An edge crosses the ports of every request on it - a staged one's are those
        of the candidate SENSE-O compiled - and those of its pair's last provisioned
        circuit, since SENSE-O doesn't always provision on the route it staged.
        Sets each edge's path_ports to that, and returns {port uri: (capacity Mbps,
        Mbps held there by circuits that aren't DMM's)} for the ports with a known
        capacity (PathPort). Ports without one constrain nothing.
        """
        for u, v, data in edges:
            mesh = Mesh.get_by_sites(u, v, session=session, use_lock=False)
            data['path_ports'] = set(data.get('path_ports') or ()) | set((mesh.path_ports if mesh else None) or ())
        uris = set().union(*(data['path_ports'] for _, _, data in edges)) if edges else set()
        return {
            uri: (row.capacity_mbps, row.foreign_mbps or 0)
            for uri, row in PathPort.get_by_uris(uris, session=session).items()
            if row.capacity_mbps is not None
        }

    def _port_rows(self, edges, ports, utilization=1.0) -> tuple:
        """
        One constraint per port: the edges whose circuits cross it carry no more than
        utilization of its capacity, less what circuits that aren't DMM's hold there
        and the finished reservations crossing it. Everything reserved on the port
        then stays within utilization of it, whoever holds it.
        """
        rows, limits, shared = [], [], []
        for uri, (capacity, foreign) in sorted(ports.items()):
            crossing = [i for i, (_, _, data) in enumerate(edges) if uri in (data.get('path_ports') or ())]
            if not crossing:
                continue
            row = np.zeros(len(edges))
            row[crossing] = 1
            reserved = sum(edges[i][2]['fixed_bandwidth'] for i in crossing)
            limit = utilization * capacity - foreign - reserved
            rows.append(row)
            limits.append(limit)
            if len(crossing) > 1:
                pairs = ", ".join(f"{edges[i][0]}-{edges[i][1]}" for i in crossing)
                shared.append(f"{uri.split(':', 5)[-1]} {limit:.0f} Mbps ({pairs})")
        # Said once per change, not every cycle.
        if shared != getattr(self, '_shared_ports', None):
            self._shared_ports = shared
            if shared:
                logging.info(f"Ports shared by site pairs: {'; '.join(shared)}")
        return np.array(rows).reshape(len(rows), len(edges)), np.array(limits, dtype=float)

    def _prepare_optimization_matrices(self, simple_graph, nodes, edges, ports=None) -> tuple:
        """
        Prepare the matrices for the linear programming optimization.
        Capacity reserved by finished circuits is subtracted from both the per-edge
        and the per-node constraints before optimizing the active requests.

        Constraints: each site carries no more than its largest mesh link, each
        port in ports no more than it has free for DMM (see _port_rows), and each
        edge no more than its link allows. Site, link and port capacities count at
        _max_utilization() of their size.

        An edge with a port of unknown capacity on its path is also held to
        SENSE-O's figure for the pair, taken as it is: it already leaves out what
        other circuits hold, and the rows for the sites and ports cap the total
        anyway. Once every port is known the port rows bound the edge as SENSE-O
        does, without the figure's snapshot of DMM's circuits on other pairs:
        SENSE-O counted those as taken at staging, though the decider may move
        them, and the figure is renewed only by the next staging on the pair.
        Marks each edge's data with ports_known.
        """
        n_edges = len(edges)
        edge_index = {edge[:2]: i for i, edge in enumerate(edges)}
        util = _max_utilization()

        c = np.zeros(n_edges)
        for i, (u, v, data) in enumerate(edges):
            priority = data['priority']
            c[i] = -priority

        A = nx.incidence_matrix(simple_graph, nodelist=nodes, edgelist=edges).toarray()
        b = np.array([util * simple_graph.nodes[node]['link_capacity_mbps'] for node in nodes], dtype=float)

        node_index = {node: i for i, node in enumerate(nodes)}
        for u, v, data in edges:
            b[node_index[u]] -= data['fixed_bandwidth']
            b[node_index[v]] -= data['fixed_bandwidth']

        # pair_available counts finished reservations too (it adds back everything
        # DMM held on the pair), so they come off both it and the link.
        bounds, figures = [], []
        for u, v, data in edges:
            data['ports_known'] = _ports_known(data, ports)
            if data['ports_known']:
                bounds.append(util * data['link_capacity'] - data['fixed_bandwidth'])
            elif data.get('pair_available') is not None:
                bounds.append(min(util * data['link_capacity'], _whole_gbps(data['pair_available']))
                              - data['fixed_bandwidth'])
                figures.append(f"{u}-{v} {data['pair_available']:.0f} Mbps")
            else:
                bounds.append(min(util * data['link_capacity'] - data['fixed_bandwidth'], data['max_bandwidth']))
        edge_bounds = np.array(bounds, dtype=float)
        if figures != getattr(self, '_pair_figures', None):
            self._pair_figures = figures
            if figures:
                logging.info(f"Pairs held to SENSE-O's figure at staging, a port on their path having "
                             f"no known capacity: {'; '.join(figures)}")

        port_A, port_b = self._port_rows(edges, ports or {}, util)

        A = np.vstack([A, port_A, np.eye(n_edges)])
        b = np.concatenate([b, port_b, edge_bounds])
        b = np.clip(b, 0, None)

        return A, c, b, edge_index

    def _optimize_bandwidth(self, A, b, c, edges, current=None) -> object:
        """
        Optimize the bandwidth allocation using linear programming.
        Binary-searches over the minimum per-edge lower bound to find the highest
        feasible floor — O(log(capacity/precision)) LP solves instead of O(capacity/precision).

        Only edges with a live request (priority > 0) take part. An edge left holding
        nothing but finished reservations is pinned to 0: nobody can be given its
        bandwidth, and flooring it would take that share away from the live edges
        sharing its sites.

        The floor and the weighted total rarely pin down one allocation: two edges
        sharing a site can usually trade capacity without changing either. The
        solver then returns whichever corner it reaches first, which depends on the
        order requests came out of the database, so a re-run after an unrelated
        change could swap 150/50 for 50/150 - two SENSE modifies for nothing. Among
        the equally good allocations, _keep_current picks the one closest to what
        the edges hold now (current, NaN for an edge with no allocation yet).
        """
        n_edges = len(edges)
        precision_mbps = _PRECISION_MBPS
        live = c < 0

        def floored(floor):
            return [(floor, None) if live[i] else (0, 0) for i in range(n_edges)]

        # Verify a solution exists with lower_bound=0 before searching.
        base_result = linprog(c, A_ub=A, b_ub=b, bounds=floored(0), method='highs')
        if not base_result.success:
            raise ValueError("No feasible solution found for the optimization problem.")

        optim_result = base_result
        if not live.any():
            return optim_result.x

        # The highest any live edge can be floored is the tightest constraint it is in.
        lo = 0
        hi = int(np.min(b[(A[:, live] != 0).any(axis=1)]))

        while hi - lo > precision_mbps:
            mid = (lo + hi) / 2
            bounds = floored(mid)
            result = linprog(c, A_ub=A, b_ub=b, bounds=bounds, method='highs')
            if result.success:
                lo = mid
                optim_result = result
            else:
                hi = mid

        if current is None:
            return optim_result.x
        return self._keep_current(A, b, c, floored(lo), optim_result.x, current)

    @staticmethod
    def _keep_current(A, b, c, bounds, x, current):
        """
        An allocation as good as x - same floor, same weighted total to within the
        search precision - that moves the edges with a current allocation as little
        as possible (sum of |x - current|), or x itself if the LP won't say.
        """
        n_edges = len(x)
        sticky = (~np.isnan(current)) & (c < 0)
        if not sticky.any():
            return x
        current = np.nan_to_num(current)
        # Variables: x (n_edges), then one deviation d_e >= |x_e - current_e| each.
        objective = np.concatenate([np.zeros(n_edges), sticky.astype(float)])
        rows = [np.hstack([A, np.zeros((A.shape[0], n_edges))])]
        limits = [b]
        # Keep the weighted total: c.x <= c.x* + slack.
        slack = _PRECISION_MBPS * float(np.max(np.abs(c)))
        rows.append(np.concatenate([c, np.zeros(n_edges)])[None, :])
        limits.append([float(c @ x) + slack])
        eye = np.eye(n_edges)
        rows.append(np.hstack([eye, -eye])[sticky])
        limits.append(current[sticky])
        rows.append(np.hstack([-eye, -eye])[sticky])
        limits.append(-current[sticky])
        d_bounds = [(0, None) if sticky[i] else (0, 0) for i in range(n_edges)]
        result = linprog(objective, A_ub=np.vstack(rows), b_ub=np.concatenate([np.ravel(l) for l in limits]),
                         bounds=list(bounds) + d_bounds, method='highs')
        if not result.success:
            logging.warning(f"Could not keep current allocations ({result.message}), using the first solution found")
            return x
        return result.x[:n_edges]

    def _allocate_bandwidth(self, multi_graph, simple_graph, edges, edge_index, bandwidths) -> None:
        """
        Split each edge's bandwidth between its live requests and set it in the graph.

        With [decider] split = least_waste (the default), an edge's share is
        scheduled by the requests' remaining sizes and priorities (_schedule_edge),
        and a request the schedule can't fit within the horizon is marked
        'rejected'. Otherwise, or when there is no usable schedule, the share is
        split in proportion to priority.
        """
        least_waste = config_get("decider", "split", default="least_waste") == "least_waste"
        live_by_edge = {}
        for u, v, key, data in multi_graph.edges(keys=True, data=True):
            if not data.get('fixed'):
                live_by_edge.setdefault((u, v), []).append((key, data))
        for (u, v), live in live_by_edge.items():
            total_priority = simple_graph[u][v]['priority']
            if total_priority <= 0:
                logging.warning(f"Total priority is 0 for edge {u}->{v}, setting bandwidth to 0")
                for key, _ in live:
                    multi_graph[u][v][key]['bandwidth'] = 0
                continue
            total = bandwidths[edge_index[(u, v)]]
            ports_known = simple_graph[u][v].get('ports_known', False)
            schedule = self._schedule_edge(u, v, live, total, ports_known) if least_waste else None
            for key, data in live:
                if schedule is None:
                    bandwidth = total * (data['priority'] / total_priority)
                elif key in schedule[0]:
                    bandwidth = schedule[0][key]
                else:
                    multi_graph[u][v][key]['bandwidth'] = 0
                    multi_graph[u][v][key]['rejected'] = schedule[1][key]
                    continue
                multi_graph[u][v][key]['bandwidth'] = _whole_gbps_share(bandwidth)

    def _schedule_edge(self, u, v, live, total, ports_known=False):
        """
        Schedule an edge's live requests within its share (total Mbps) with
        dmm.core.least_waste: by remaining size and priority, over the next
        schedule_horizon_hours, around what SENSE-O's latest calendar for the pair
        shows others will hold beyond what they held at staging.

        Returns ({key: Mbps}, {key: why it was dropped}), or None to split the
        share by priority as before when nothing is reserved later. Without a
        usable schedule (a size isn't known, too many requests, the plan fails its
        checks or would drop a request whose circuit is up) the split is by
        priority over the least the pair will have within the horizon.
        """
        if total <= 0:
            return None
        hours = calendar_hours()
        horizon = hours * 3600 // _TICK_S
        reservations = _calendar_reservations(live, total, horizon, ports_known)
        lowest = total - max((h for _, _, h in reservations), default=0)
        if lowest >= total:
            fallback = None
        elif lowest > 0:
            psum = sum(d['priority'] for _, d in live) or 1
            fallback = ({key: lowest * (d['priority'] / psum) for key, d in live}, {})
        else:
            logging.warning(f"{u}-{v} is reserved by others in full within {hours} h, splitting it by priority")
            fallback = None
        if len(live) > MAX_RULES or any(d.get('size_mbit') is None for _, d in live):
            return fallback
        rules = [(d['size_mbit'] / _TICK_S, max(d['priority'] or 0, 1)) for _, d in live]
        keep = [i for i, (_, d) in enumerate(live) if d.get('committed')]
        try:
            allocations, dropped = final_allocation(reservations, (total, horizon), rules, keep=keep)
        except Exception as e:
            logging.warning(f"Could not schedule {u}-{v}, splitting it by priority: {e}")
            return fallback
        placed = {rid - 1 for *_, rid in allocations}
        if (not _plan_fits(allocations, reservations, total) or any(i in keep for i in dropped)
                or placed | set(dropped) != set(range(len(live)))):
            logging.warning(f"The schedule for {u}-{v} overlaps a reservation or drops a running circuit, "
                            f"splitting it by priority within {lowest:.0f} Mbps")
            return fallback
        shares = {live[rid - 1][0]: y2 - y1 for _, _, y1, y2, rid in allocations}
        for x1, x2, y1, y2, rid in allocations:
            logging.debug(f"Schedule {u}-{v}: {live[rid - 1][1]['rule_id']} {y2 - y1:.0f} Mbps, "
                          f"done in {x2 * _TICK_S / 3600:.1f} h")
        why = {live[i][0]: (f"Can't move its remaining {live[i][1]['size_mbit'] / 8e6:.2f} TB within {hours} h "
                            f"in the {total / 1000:.0f} Gbps {u}-{v} has for DMM's rules")
               for i in dropped}
        return shares, why

    def _reject_unschedulable(self, multi_graph, session) -> None:
        """Reject the requests the schedule dropped; none of them has a circuit up yet."""
        for _, _, data in multi_graph.edges(data=True):
            if not data.get('rejected'):
                continue
            req = Request.get_by_id(data['rule_id'], session=session)
            if req is None or req.transfer_status not in (RequestStatus.STAGED, RequestStatus.DECIDED):
                continue
            if is_circuit_active(req.sense_circuit_status) or is_being_provisioned(req.sense_circuit_status):
                continue
            req.reject(data['rejected'], session=session)

    def _allocate_new_bandwidth(self, multi_graph, session) -> None:
        """
        Allocate bandwidth for new requests and mark them as decided
        """
        reqs_allocated = Request.get_by_status(statuses=[RequestStatus.STAGED], session=session)
        for req in reqs_allocated:
            allocated_bandwidth = None  # Initialize to prevent NameError
            for _, _, key, data in multi_graph.edges(keys=True, data=True):
                if "rule_id" in data and data["rule_id"] == req.rule_id:
                    allocated_bandwidth = int(data["bandwidth"])
                    break  # Found the matching edge, no need to continue
            
            if allocated_bandwidth is None:
                logging.error(f"Could not find bandwidth allocation for request {req.rule_id} in multi_graph")
                continue  # Skip this request, don't update it
                
            req.set_allocated_bandwidth(allocated_bandwidth, session=session)
            logging.info(f"Allocated bandwidth for request {req.rule_id}: {allocated_bandwidth}")
            req.set_status(status=RequestStatus.DECIDED, session=session)

    def _modify_existing_bandwidth(self, multi_graph, session) -> None:
        """
        Modify the bandwidth for existing requests and mark them as stale.

        """
        reqs_provisioned = Request.get_by_status(statuses=[RequestStatus.MODIFIED, RequestStatus.PROVISIONED, RequestStatus.DECIDED], session=session)
        min_upward_step = config_get_float("decider", "min_upward_modify_fraction", default=0.1, constraint="nonneg")
        util = _max_utilization()
        for req in reqs_provisioned:
            allocated_bandwidth = None  # Initialize to prevent NameError
            req_u = None  # source-node name on the multi_graph (needed for cap logic)
            for u, v, key, data in multi_graph.edges(keys=True, data=True):
                if "rule_id" in data and data["rule_id"] == req.rule_id:
                    allocated_bandwidth = int(data["bandwidth"])
                    req_u = u
                    break  # Found the matching edge, no need to continue

            if allocated_bandwidth is None:
                logging.error(f"Could not find bandwidth allocation for request {req.rule_id} in multi_graph")
                continue  # Skip this request, don't update it

            if req_u is not None and allocated_bandwidth > (req.allocated_bandwidth_mbps or 0):
                link_capacity = multi_graph.nodes[req_u].get('link_capacity_mbps', allocated_bandwidth)
                cotenant_statuses = [
                    RequestStatus.PROVISIONED, RequestStatus.STALE, RequestStatus.MODIFIED,
                    RequestStatus.DECIDED, RequestStatus.FINISHED,
                ]
                cotenant_reqs = Request.get_by_status(
                    statuses=cotenant_statuses, session=session, use_lock=False
                )
                reserved_by_others = sum(
                    self._held_on_sense(r)
                    for r in cotenant_reqs
                    if r.rule_id != req.rule_id
                    and (
                        (r.src_site_ == req.src_site_ and r.dst_site_ == req.dst_site_)
                        or (r.src_site_ == req.dst_site_ and r.dst_site_ == req.src_site_)
                    )
                    and r.sense_uuid is not None
                    and (r.transfer_status != RequestStatus.FINISHED or self._holds_reservation(r))
                )
                # Within the same share the plan keeps to, so growing into room a
                # co-tenant's cut hasn't freed yet can't fill the link meanwhile.
                # Never below what it has now: this guards a raise, not a cut.
                capped = int(max(min(allocated_bandwidth, util * link_capacity - reserved_by_others),
                                 req.allocated_bandwidth_mbps or 0))
                if capped != allocated_bandwidth:
                    logging.info(
                        f"Capping upward MODIFY for {req.rule_id}: "
                        f"LP={allocated_bandwidth} → capped={capped} Mbps "
                        f"({reserved_by_others} Mbps reserved by co-tenant circuits with active SENSE UUIDs)"
                    )
                allocated_bandwidth = capped

            if allocated_bandwidth == req.allocated_bandwidth_mbps:
                continue

            circuit_committed = (
                is_circuit_active(req.sense_circuit_status)
                or is_being_provisioned(req.sense_circuit_status)
            )
            if req.transfer_status == RequestStatus.DECIDED and not circuit_committed:
                req.set_allocated_bandwidth(allocated_bandwidth, session=session)
                logging.info(f"Updated decided bandwidth for not-yet-provisioned request {req.rule_id}: {allocated_bandwidth}")
                continue

            # Every change to a committed circuit costs a SENSE modify, a minute or so
            # of STALE. Small increases aren't worth that; decreases always go through,
            # since skipping one leaves the link oversubscribed once others grow into it.
            current = req.allocated_bandwidth_mbps or 0
            if current > 0 and current < allocated_bandwidth < current * (1 + min_upward_step):
                logging.debug(
                    f"Skipping upward change for {req.rule_id}: {current} → {allocated_bandwidth} Mbps "
                    f"is below the {min_upward_step:.0%} minimum step"
                )
                continue

            req.set_previous_bandwidth(req.allocated_bandwidth_mbps, session=session)
            req.set_allocated_bandwidth(allocated_bandwidth, session=session)
            logging.info(f"Modified bandwidth for request {req.rule_id}: {allocated_bandwidth}")
            # The modifier counts this modify's failures in sense_retries, so it must
            # not inherit the ones left over from creating the circuit.
            req.update({"sense_retries": 0}, session=session)
            req.set_status(status=RequestStatus.STALE, session=session)

    @staticmethod
    def _held_on_sense(req) -> float:
        """
        Bandwidth a request's circuit holds on SENSE right now. A STALE request's
        allocation is the target of a modify that has not gone through yet, so
        until it does SENSE still holds the previous one if that is larger.
        """
        allocated = req.allocated_bandwidth_mbps or 0
        if req.transfer_status == RequestStatus.STALE:
            return max(allocated, req.previous_bandwidth_mbps or 0)
        return allocated

    @staticmethod
    def _good_response(response):
        return bool(response and not any("ERROR" in r for r in response))