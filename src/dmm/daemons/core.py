import logging
import numpy as np
from scipy.optimize import linprog
import networkx as nx
import ipaddress

from dmm.daemons.base import DaemonBase

from dmm.db.request import Request
from dmm.db.endpoint import Endpoint
from dmm.db.mesh import Mesh
from dmm.db.session import databased

from dmm.utils.sense import get_allocation, free_allocation

class DeciderDaemon(DaemonBase):
    @databased
    def process(self, session=None):
        network_graph = nx.MultiGraph()
        # Get all active requests
        reqs = Request.from_status(status=["STAGED", "ALLOCATED", "MODIFIED", "DECIDED", "STALE", "PROVISIONED", "FINISHED", "CANCELED"], session=session)
        if reqs == []:
            logging.debug("decider: nothing to do")
            return
        for req in reqs:
            src_port_capacity = Mesh.max_bandwidth(req.src_site, session=session)
            network_graph.add_node(req.src_site, port_capacity=src_port_capacity)
            dst_port_capacity = Mesh.max_bandwidth(req.dst_site, session=session)
            network_graph.add_node(req.dst_site, port_capacity=dst_port_capacity)
            network_graph.add_edge(req.src_site, req.dst_site, rule_id=req.rule_id, priority=req.priority, bandwidth=req.bandwidth)
        
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
        print(g)
        print(b)
        print(type(b))
        optim_result = linprog(c, A_ub=A, b_ub=b, bounds=(0, None))

        if optim_result.success:
            x = optim_result.x  # Optimal flow on each edge
            bandwidths = x * np.array([data['priority'] for u, v, data in edges])
            for u, v, key, data in network_graph.edges(keys=True, data=True):
                total_priority = g[u][v]['priority']  # Get the summed priority in the simple graph
                if total_priority > 0:
                    proportion = data['priority'] / total_priority
                    bandwidth = bandwidths[edge_index[(u, v)]] * proportion
                    logging.debug(f"Allocating bandwidth {bandwidth} from {u} to {v}")
                    network_graph[u][v][key]['bandwidth'] = bandwidth
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
            req.mark_as(status="DECIDED", session=session)

        # for already provisioned reqs, modify bandwidth and mark as stale
        reqs_provisioned = Request.from_status(status=["MODIFIED", "PROVISIONED"], session=session)
        for req in reqs_provisioned:
            for _, _, key, data in network_graph.edges(keys=True, data=True):
                if "rule_id" in data and data["rule_id"] == req.rule_id:
                    allocated_bandwidth = int(data["bandwidth"])
            if allocated_bandwidth != req.bandwidth:
                req.update_bandwidth(allocated_bandwidth, session=session)
                req.mark_as(status="STALE", session=session)


class AllocatorDaemon(DaemonBase):
    @databased
    def process(self, session=None):
        reqs_init = Request.from_status(status=["INIT"], session=session)
        if len(reqs_init) == 0:
            logging.debug("allocator: nothing to do")
            return
        for new_request in reqs_init:  
            reqs_finished = Request.from_status(status=["FINISHED"], session=session)
            for req_fin in reqs_finished:
                if (req_fin.src_site == new_request.src_site and req_fin.dst_site == new_request.dst_site):
                    logging.debug(f"Request {new_request.rule_id} found a finished request {req_fin.rule_id} with same endpoints, reusing ipv6 blocks and urls.")
                    new_request.update({
                        "src_ipv6_block": req_fin.src_ipv6_block,
                        "dst_ipv6_block": req_fin.dst_ipv6_block,
                        "src_url": req_fin.src_url,
                        "dst_url": req_fin.dst_url,
                        "transfer_status": "ALLOCATED"
                    })
                    req_fin.mark_as(status="DELETED", session=session)
                    break
            else:
                logging.debug(f"Request {new_request.rule_id} did not find a finished request with same endpoints, allocating new ipv6 blocks and urls.")
                try:
                    free_src_ipv6 = get_allocation(new_request.src_site, new_request.rule_id)
                    free_src_ipv6 = ipaddress.IPv6Network(free_src_ipv6).compressed
                    
                    free_dst_ipv6 = get_allocation(new_request.dst_site, new_request.rule_id)
                    free_dst_ipv6 = ipaddress.IPv6Network(free_dst_ipv6).compressed

                    src_endpoint = Endpoint.for_rule(site_name=new_request.src_site, ip_block=free_src_ipv6, session=session)
                    dst_endpoint = Endpoint.for_rule(site_name=new_request.dst_site, ip_block=free_dst_ipv6, session=session)

                    if src_endpoint is None or dst_endpoint is None:
                        raise Exception("Could not find endpoints")    
                    
                    logging.debug(f"Got ipv6 blocks {src_endpoint.ip_block} and {dst_endpoint.ip_block} and urls {src_endpoint.hostname} and {dst_endpoint.hostname} for request {new_request.rule_id}")
                
                    new_request.update({
                        "src_ipv6_block": src_endpoint.ip_block,
                        "dst_ipv6_block": dst_endpoint.ip_block,
                        "src_url": src_endpoint.hostname,
                        "dst_url": dst_endpoint.hostname,
                        "transfer_status": "ALLOCATED"
                    })

                except Exception as e:
                    free_allocation(new_request.src_site, new_request.rule_id)
                    free_allocation(new_request.dst_site, new_request.rule_id)
                    logging.error(e)