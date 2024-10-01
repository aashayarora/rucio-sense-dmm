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
        network_graph = nx.MultiGraph()
        # Get all active requests
        reqs = Request.from_status(status=["STAGED", "ALLOCATED", "MODIFIED", "DECIDED", "STALE", "PROVISIONED", "FINISHED", "CANCELED"], session=session)
        if reqs == []:
            return
        for req in reqs:
            src_port_capacity = Mesh.max_bandwidth(req.src_site, session=session)
            network_graph.add_node(req.src_site.name, port_capacity=src_port_capacity)
            dst_port_capacity = Mesh.max_bandwidth(req.dst_site, session=session)
            network_graph.add_node(req.dst_site.name, port_capacity=dst_port_capacity)
            network_graph.add_edge(req.src_site.name, req.dst_site.name, rule_id=req.rule_id, priority=req.priority, bandwidth=req.bandwidth)
        
        # exit if graph is empty
        if not network_graph.nodes:
            return

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
        optim_result = linprog(c, A_ub=A, b_ub=b, bounds=(0, None))

        if optim_result.success:
            x = optim_result.x  # Optimal flow on each edge
            bandwidths = x * np.array([data['priority'] for u, v, data in edges])
            for u, v, key, data in network_graph.edges(keys=True, data=True):
                total_priority = g[u][v]['priority']  # Get the summed priority in the simple graph
                if total_priority > 0:
                    proportion = data['priority'] / total_priority
                    bandwidth = bandwidths[edge_index[(u, v)]] * proportion
                    network_graph[u][v][key]['bandwidth'] = round(bandwidth, -2) # round to nearest 100
                else:
                    network_graph[u][v][key]['bandwidth'] = 0
        else:
            logging.error("Optimization failed, no bandwidth allocated.")

        # for staged reqs, allocate new bandwidth
        reqs_staged = Request.from_status(status=["STAGED"], session=session)
        for req in reqs_staged:
            for _, _, key, data in network_graph.edges(keys=True, data=True):
                if "rule_id" in data and data["rule_id"] == req.rule_id:
                    allocated_bandwidth = int(data["bandwidth"])
            req.update_bandwidth(allocated_bandwidth, session=session)
            logging.info(f"Allocated bandwidth for request {req.rule_id}: {allocated_bandwidth}")
            req.mark_as(status="DECIDED", session=session)

        # for already provisioned reqs, modify bandwidth and mark as stale
        reqs_provisioned = Request.from_status(status=["MODIFIED", "PROVISIONED"], session=session)
        for req in reqs_provisioned:
            for _, _, key, data in network_graph.edges(keys=True, data=True):
                if "rule_id" in data and data["rule_id"] == req.rule_id:
                    allocated_bandwidth = int(data["bandwidth"])
            if allocated_bandwidth != req.bandwidth:
                req.update_bandwidth(allocated_bandwidth, session=session)
                logging.info(f"Modified bandwidth for request {req.rule_id}: {allocated_bandwidth}")
                req.mark_as(status="STALE", session=session)

