import logging
import numpy as np
from scipy.optimize import linprog
import networkx as nx
from math import floor

from dmm.daemons.base import DaemonBase

from dmm.models.request import Request
from dmm.models.mesh import Mesh
from dmm.db.session import databased

class DeciderDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        
    @databased
    def process(self, session=None):
        multi_graph = self._build_multi_graph(session)
        
        if not multi_graph.nodes:
            return

        simple_graph, nodes, edges = self._simplify_graph(multi_graph)
        A, c, b, edge_index = self._prepare_optimization_matrices(simple_graph, nodes, edges)
        optim_result = self._optimize_bandwidth(A, b, c, edges)

        self._allocate_bandwidth(multi_graph, simple_graph, edges, edge_index, optim_result)

        self._allocate_new_bandwidth(multi_graph, session)
        self._modify_existing_bandwidth(multi_graph, session)

    def _build_multi_graph(self, session) -> nx.MultiGraph:
        """
        Build a network graph from the requests in the database.
        The max available bandwidth is gotten from the Mesh table.
        """
        multi_graph = nx.MultiGraph()
        reqs = Request.from_status(status=["MODIFIED", "DECIDED", "STALE", "STAGED", "PROVISIONED", "FINISHED"], session=session) # get all requests which would affect the decision (i.e. don't consider requests that are in CANCELLED or FAILED state)
        if reqs == []:
            return multi_graph
        for req in reqs:
            src_port_capacity = Mesh.max_bandwidth(req.src_site, session=session)
            multi_graph.add_node(req.src_site.name, port_capacity=src_port_capacity)
            dst_port_capacity = Mesh.max_bandwidth(req.dst_site, session=session)
            multi_graph.add_node(req.dst_site.name, port_capacity=dst_port_capacity)
            multi_graph.add_edge(req.src_site.name, req.dst_site.name, rule_id=req.rule_id, priority=req.priority, bandwidth=req.bandwidth, available_bandwidth=req.available_bandwidth)
        return multi_graph

    def _simplify_graph(self, multi_graph) -> tuple:
        """
        Simplify the network graph by merging edges with the same source and destination nodes.
        """
        simple_graph = nx.Graph()
        simple_graph.add_nodes_from(multi_graph.nodes(data=True))

        for u, v, data in multi_graph.edges(data=True):
            priority = data['priority']
            available_bandwidth = data.get('available_bandwidth', 1000)
            if simple_graph.has_edge(u, v):
                simple_graph[u][v]['priority'] += priority # sum priorities of reqs with the same src and dst
                simple_graph[u][v]['available_bandwidth'] += available_bandwidth # sum available bandwidths of reqs with the same src and dst
            else:
                simple_graph.add_edge(u, v, priority=priority, available_bandwidth=available_bandwidth)
        
        return simple_graph, list(simple_graph.nodes), list(simple_graph.edges(data=True))

    def _prepare_optimization_matrices(self, simple_graph, nodes, edges) -> tuple:
        """
        Prepare the matrices for the linear programming optimization.
        """
        n_edges = len(edges)
        edge_index = {edge[:2]: i for i, edge in enumerate(edges)}

        c = np.zeros(n_edges)
        for i, (u, v, data) in enumerate(edges):
            priority = data['priority']
            c[i] = -priority
        
        A = nx.incidence_matrix(simple_graph, nodelist=nodes, edgelist=edges).toarray()
        b = np.array([simple_graph.nodes[node]['port_capacity'] for node in nodes])

        available_bandwidths = np.array([data['available_bandwidth'] for _, _, data in edges])

        A = np.vstack([A, np.eye(n_edges)])
        b = np.concatenate([b, available_bandwidths])

        return A, c, b, edge_index

    def _optimize_bandwidth(self, A, b, c, edges) -> object:
        """
        Optimize the bandwidth allocation using linear programming.
        """
        optim_result = None
        lower_bound = 0
        while True:
            # keep trying until the optimization fails (i.e. the lower bound is too high)
            # we can show that the ideal case is when the lower bound is the maximum possible value
            bounds = [(lower_bound, None) for _ in range(len(edges))]
            curr_optim_result = linprog(c, A_ub=A, b_ub=b, bounds=bounds, method='highs')
            if not curr_optim_result.success:
                break
            else:
                optim_result = curr_optim_result
                lower_bound += 5
        if optim_result is None:
            raise ValueError("No feasible solution found for the optimization problem.")
        if not optim_result.success:
            raise ValueError("Optimization failed.")
        return optim_result.x

    def _allocate_bandwidth(self, multi_graph, simple_graph, edges, edge_index, bandwidths) -> None:
        """
        Set the bandwidths in the graph based on the optimization result.
        @param multi_graph: the network multi_graph
        @param simple_graph: the simplified graph
        @param edges: the edges of the graph
        @param edge_index: the edge index mapping
        @param x: the optimization result
        """
        for u, v, key, data in multi_graph.edges(keys=True, data=True):
            total_priority = simple_graph[u][v]['priority']
            if total_priority > 0:
                proportion = data['priority'] / total_priority
                bandwidth = bandwidths[edge_index[(u, v)]] * proportion
                multi_graph[u][v][key]['bandwidth'] = floor(bandwidth // 1000) * 1000 # round to lowest 1000 because SENSE doesn't like it otherwise, probably should be a configurable value
            else:
                multi_graph[u][v][key]['bandwidth'] = 0

    def _allocate_new_bandwidth(self, multi_graph, session) -> None:
        """
        Allocate bandwidth for new requests and mark them as decided
        """
        reqs_allocated = Request.from_status(status=["STAGED"], session=session)
        for req in reqs_allocated:
            for _, _, key, data in multi_graph.edges(keys=True, data=True):
                if "rule_id" in data and data["rule_id"] == req.rule_id:
                    allocated_bandwidth = int(data["bandwidth"])
            req.update_bandwidth(allocated_bandwidth, session=session)
            logging.info(f"Allocated bandwidth for request {req.rule_id}: {allocated_bandwidth}")
            req.mark_as(status="DECIDED", session=session)

    def _modify_existing_bandwidth(self, multi_graph, session) -> None:
        """
        Modify the bandwidth for existing requests and mark them as stale
        """
        reqs_provisioned = Request.from_status(status=["MODIFIED", "PROVISIONED"], session=session)
        for req in reqs_provisioned:
            for _, _, key, data in multi_graph.edges(keys=True, data=True):
                if "rule_id" in data and data["rule_id"] == req.rule_id:
                    allocated_bandwidth = int(data["bandwidth"])
            if allocated_bandwidth != req.bandwidth:
                req.update_bandwidth(allocated_bandwidth, session=session)
                logging.info(f"Modified bandwidth for request {req.rule_id}: {allocated_bandwidth}")
                req.mark_as(status="STALE", session=session)

    @staticmethod
    def _good_response(response):
        return bool(response and not any("ERROR" in r for r in response))