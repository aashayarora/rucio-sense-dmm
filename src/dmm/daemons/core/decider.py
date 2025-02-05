import logging
import numpy as np
import json
from scipy.optimize import linprog
import networkx as nx
from math import floor
import time

from sense.client.workflow_combined_api import WorkflowCombinedApi

from dmm.daemons.base import DaemonBase

from dmm.db.request import Request
from dmm.db.mesh import Mesh
from dmm.db.site import Site
from dmm.db.session import databased

from dmm.utils.config import config_get

class DeciderDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        self.profile_uuid = config_get("sense", "profile_uuid")
        self.max_window_size = config_get("decision", "max_window_size")
        self.default_window_size = config_get("decision", "default_window_size")
        self.max_window_size /= 3600
        
    @databased
    def process(self, session=None):
        multi_graph = self._build_multi_graph(session)
        
        if not multi_graph.nodes:
            return

        simple_graph, nodes, edges = self._simplify_graph(multi_graph)
        A, c, b, edge_index = self._prepare_optimization_matrices(simple_graph, nodes, edges)
        optim_result = self._optimize_bandwidth(A, b, c, edges)

        if optim_result.success:
            self._allocate_bandwidth(multi_graph, simple_graph, edges, edge_index, optim_result.x)
        else:
            logging.error("Optimization failed, no bandwidth allocated.")

        self._allocate_new_bandwidth(multi_graph, session)
        self._modify_existing_bandwidth(multi_graph, session)

    def _build_multi_graph(self, session) -> nx.MultiGraph:
        """
        Build a network graph from the requests in the database.
        The max available bandwidth is gotten from the Mesh table.
        """
        multi_graph = nx.MultiGraph()
        reqs = Request.from_status(status=["INIT", "ALLOCATED", "MODIFIED", "DECIDED", "STALE", "STAGED", "PROVISIONED", "FINISHED"], session=session) # get all requests which would affect the decision (i.e. don't consider requests that are in CANCELLED or FAILED state)
        if reqs == []:
            return multi_graph
        for req in reqs:
            src_port_capacity = Mesh.max_bandwidth(req.src_site, session=session)
            multi_graph.add_node(req.src_site.name, port_capacity=src_port_capacity)
            dst_port_capacity = Mesh.max_bandwidth(req.dst_site, session=session)
            multi_graph.add_node(req.dst_site.name, port_capacity=dst_port_capacity)
            multi_graph.add_edge(req.src_site.name, req.dst_site.name, rule_id=req.rule_id, priority=req.priority, bandwidth=req.bandwidth)
        return multi_graph

    def _simplify_graph(self, multi_graph) -> tuple:
        """
        Simplify the network graph by merging edges with the same source and destination nodes.
        """
        simple_graph = nx.Graph()
        simple_graph.add_nodes_from(multi_graph.nodes(data=True))

        for u, v, data in multi_graph.edges(data=True):
            priority = data['priority']
            if simple_graph.has_edge(u, v):
                simple_graph[u][v]['priority'] += priority # sum priorities of reqs with the same src and dst
            else:
                simple_graph.add_edge(u, v, priority=priority)
        
        return simple_graph, list(simple_graph.nodes), list(simple_graph.edges(data=True))

    def _prepare_optimization_matrices(self, simple_graph, nodes, edges) -> tuple:
        """
        Prepare the matrices for the linear programming optimization.
        """
        n_nodes = len(nodes)
        n_edges = len(edges)

        node_index = {node: i for i, node in enumerate(nodes)} # map nodes to indices
        edge_index = {edge[:2]: i for i, edge in enumerate(edges)} # map edges to indices

        A = np.zeros((n_nodes, n_edges)) # weighted adjacency matrix
        c = np.zeros(n_edges) # cost vector 

        for (u, v, data) in edges:
            i = node_index[u]
            j = node_index[v]
            priority = data['priority']
            edge_idx = edge_index[(u, v)]

            A[i, edge_idx] = priority
            A[j, edge_idx] = priority
            c[edge_idx] = -priority # (negative priorities because we want to maximize based on priorities (LPO performs minimization))

        b = np.array([simple_graph.nodes[node]['port_capacity'] for node in nodes]) # port capacity for each node
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
        return optim_result

    def _allocate_bandwidth(self, multi_graph, simple_graph, edges, edge_index, x) -> None:
        """
        Set the bandwidths in the graph based on the optimization result.
        @param multi_graph: the network multi_graph
        @param simple_graph: the simplified graph
        @param edges: the edges of the graph
        @param edge_index: the edge index mapping
        @param x: the optimization result
        """
        bandwidths = x * np.array([data['priority'] for u, v, data in edges])
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
        reqs_allocated = Request.from_status(status=["ALLOCATED"], session=session)
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

    def _maximum_bandwidth_query(self, req, session):
        workflow_api = WorkflowCombinedApi()
        time_window = self.default_window_size
        transfer_size = (req.rule_size / (1024 ** 3) * 8) # bytes to gigabits
        while time_window <= self.max_window_size:
            try:
                response = self._stage_tbmb_instance(req, time_window, workflow_api, session)
                for query in response["queries"]:
                    if query["asked"] == "total-block-maximum-bandwidth":
                        result = query["results"][0]
                        if "bandwidth" not in result:
                            raise ValueError(f"SENSE query failed for {req.rule_id}")
                        max_bandwidth = float(result["bandwidth"]) / (1000 ** 3)
                if transfer_size / max_bandwidth <= time_window:
                    workflow_api.instance_delete(si_uuid=workflow_api.si_uuid)
                    return max_bandwidth
                else:
                    time_window += self.default_window_size
                    time.sleep(5)
            except ValueError as e:
                logging.error(e)
        return None

    def _stage_tbmb_instance(self, req, time_window, workflow_api, session):
        if workflow_api.si_uuid is None:
            workflow_api.instance_new()
        
        vlan_range = Mesh.vlan_range(site_1=req.src_site, site_2=req.dst_site, session=session)
        intent = {
            "service_profile_uuid": self.profile_uuid,
            "queries": [
                {
                "ask": "edit",
                "options": [
                    {"data.connections[0].terminals[0].uri": Site.from_name(name=req.src_site.name, attr="sense_uri", session=session)},
                    {"data.connections[0].terminals[0].ipv6_prefix_list": req.src_endpoint.ip_range},
                    {"data.connections[0].terminals[1].uri": Site.from_name(name=req.dst_site.name, attr="sense_uri", session=session)},
                    {"data.connections[0].terminals[1].ipv6_prefix_list": req.dst_endpoint.ip_range},
                    {"data.connections[0].terminals[0].vlan_tag": vlan_range},
                    {"data.connections[0].terminals[1].vlan_tag": vlan_range}
                ]
                }, {
                "ask": "total-block-maximum-bandwidth",
                "options": [
                    {
                        "name": "Connection 1",
                        "start": "now",
                        "end-before": f"+{time_window}h"
                    }
                ]
                }
            ]
        }

        response = workflow_api.instance_create(json.dumps(intent))
        workflow_api.si_uuid = response["service_uuid"]

        if not self._good_response(response):
            raise ValueError(f"SENSE req staging failed for {req.rule_id}")
        
        return response

    @staticmethod
    def _good_response(response):
        return bool(response and not any("ERROR" in r for r in response))