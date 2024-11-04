import logging
import numpy as np
from scipy.optimize import linprog
import networkx as nx

from dmm.daemons.base import DaemonBase

from dmm.db.request import Request
from dmm.db.mesh import Mesh
from dmm.db.session import databased

class DeciderDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        
    @databased
    def process(self, session=None):
        network_graph = self._build_network_graph(session)
        
        if not network_graph.nodes:
            return

        g, nodes, edges = self._simplify_graph(network_graph)
        A, c, b, edge_index = self._prepare_optimization_matrices(g, nodes, edges)
        optim_result = self._optimize_bandwidth(A, b, c, edge_index, edges)

        if optim_result.success:
            self._allocate_bandwidth(network_graph, g, edges, edge_index, optim_result.x)
        else:
            logging.error("Optimization failed, no bandwidth allocated.")

        self._process_requests(network_graph, session)

    def _build_network_graph(self, session):
        network_graph = nx.MultiGraph()
        reqs = Request.from_status(status=["STAGED", "ALLOCATED", "MODIFIED", "DECIDED", "STALE", "PROVISIONED", "FINISHED", "CANCELED"], session=session)
        if reqs == []:
            return network_graph
        for req in reqs:
            src_port_capacity = Mesh.max_bandwidth(req.src_site, session=session)
            network_graph.add_node(req.src_site.name, port_capacity=src_port_capacity)
            dst_port_capacity = Mesh.max_bandwidth(req.dst_site, session=session)
            network_graph.add_node(req.dst_site.name, port_capacity=dst_port_capacity)
            network_graph.add_edge(req.src_site.name, req.dst_site.name, rule_id=req.rule_id, priority=req.priority, bandwidth=req.bandwidth)
        return network_graph

    def _simplify_graph(self, network_graph):
        g = nx.Graph()
        g.add_nodes_from(network_graph.nodes(data=True))

        for u, v, data in network_graph.edges(data=True):
            priority = data['priority']
            if g.has_edge(u, v):
                g[u][v]['priority'] += priority
            else:
                g.add_edge(u, v, priority=priority)

        nodes = list(g.nodes)
        edges = list(g.edges(data=True))
        return g, nodes, edges

    def _prepare_optimization_matrices(self, g, nodes, edges):
        n_nodes = len(nodes)
        n_edges = len(edges)

        node_index = {node: i for i, node in enumerate(nodes)}
        edge_index = {edge[:2]: i for i, edge in enumerate(edges)}

        A = np.zeros((n_nodes, n_edges))
        c = np.zeros(n_edges)

        for (u, v, data) in edges:
            i = node_index[u]
            j = node_index[v]
            priority = data['priority']
            edge_idx = edge_index[(u, v)]

            A[i, edge_idx] = priority
            A[j, edge_idx] = priority
            c[edge_idx] = -priority

        b = np.array([g.nodes[node]['port_capacity'] for node in nodes])
        return A, c, b, edge_index

    def _optimize_bandwidth(self, A, b, c, edge_index, edges):
        optim_result = None
        lower_bound = 0
        while True:
            bounds = [(lower_bound, None) for _ in range(len(edges))]
            curr_optim_result = linprog(c, A_ub=A, b_ub=b, bounds=bounds, method='highs')
            if not curr_optim_result.success:
                break
            else:
                optim_result = curr_optim_result
                lower_bound += 5
        return optim_result

    def _allocate_bandwidth(self, network_graph, g, edges, edge_index, x):
        bandwidths = x * np.array([data['priority'] for u, v, data in edges])
        for u, v, key, data in network_graph.edges(keys=True, data=True):
            total_priority = g[u][v]['priority']
            if total_priority > 0:
                proportion = data['priority'] / total_priority
                bandwidth = bandwidths[edge_index[(u, v)]] * proportion
                network_graph[u][v][key]['bandwidth'] = round(bandwidth, -2)
            else:
                network_graph[u][v][key]['bandwidth'] = 0

    def _process_requests(self, network_graph, session):
        self._allocate_new_bandwidth(network_graph, session)
        self._modify_existing_bandwidth(network_graph, session)

    def _allocate_new_bandwidth(self, network_graph, session):
        reqs_staged = Request.from_status(status=["STAGED"], session=session)
        for req in reqs_staged:
            for _, _, key, data in network_graph.edges(keys=True, data=True):
                if "rule_id" in data and data["rule_id"] == req.rule_id:
                    allocated_bandwidth = int(data["bandwidth"])
            req.update_bandwidth(allocated_bandwidth, session=session)
            logging.info(f"Allocated bandwidth for request {req.rule_id}: {allocated_bandwidth}")
            req.mark_as(status="DECIDED", session=session)

    def _modify_existing_bandwidth(self, network_graph, session):
        reqs_provisioned = Request.from_status(status=["MODIFIED", "PROVISIONED"], session=session)
        for req in reqs_provisioned:
            for _, _, key, data in network_graph.edges(keys=True, data=True):
                if "rule_id" in data and data["rule_id"] == req.rule_id:
                    allocated_bandwidth = int(data["bandwidth"])
            if allocated_bandwidth != req.bandwidth:
                req.update_bandwidth(allocated_bandwidth, session=session)
                logging.info(f"Modified bandwidth for request {req.rule_id}: {allocated_bandwidth}")
                req.mark_as(status="STALE", session=session)
