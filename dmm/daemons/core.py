from dmm.db.session import databased
from dmm.utils.db import get_request_by_status, mark_requests, update_bandwidth, get_site, get_unused_endpoint, update_site

@databased
def decider(network_graph=None, session=None):
    # Remove deleted requests from graph
    reqs_deleted = get_request_by_status(status=["DELETED"], session=session)
    for req_del in reqs_deleted:
        for u, v, key, attr in network_graph.edges(keys=True, data=True):
            if (attr["rule_id"] == req_del.rule_id):
                network_graph.remove_edge(req_del.src_site, req_del.dst_site, key=key)
                break

    # for prio modified reqs, update prio in graph
    reqs_modified = [req for req in get_request_by_status(status=["MODIFIED"], session=session)]
    for req in reqs_modified:
        for _, _, key, data in network_graph.edges(keys=True, data=True):
            if "rule_id" in data and data["rule_id"] == req.rule_id:
                data["priority"] = req.modified_priority

    # Get all active requests
    reqs =  get_request_by_status(status=["ALLOCATED", "MODIFIED", "STAGED", "DECIDED", "PROVISIONED", "FINISHED", "STALE"], session=session)
    for req in reqs:
        if not network_graph.has_node(req.src_site):
            network_graph.add_node(req.src_site, uplink_capacity=get_site(req.src_site, attr="port_capacity", session=session))
        if not network_graph.has_node(req.dst_site):
            network_graph.add_node(req.dst_site, uplink_capacity=get_site(req.dst_site, attr="port_capacity", session=session))
        if not any(attr["rule_id"] == req.rule_id for u, v, attr in network_graph.edges(data=True)):
            network_graph.add_edge(req.src_site, req.dst_site, rule_id=req.rule_id, priority=req.priority, bandwidth=req.bandwidth, max_bandwidth=req.max_bandwidth)
    
    # update bandwidths for each endpoint
    for src, dst, key, data in network_graph.edges(data=True, keys=True):
        src_capacity = network_graph.nodes[src]["uplink_capacity"]
        dst_capacity = network_graph.nodes[dst]["uplink_capacity"]
        priority = data["priority"]
        
        # bandwidth between two points can't exceed min of port capacity of either site
        min_capacity = min(src_capacity, dst_capacity)
        total_priority = sum(edge_data["priority"] for edge_data in network_graph[src][dst].values())
        
        # priority weighted share
        if total_priority == 0:
            updated_bandwidth = 0.0 
        else:
            updated_bandwidth = (min_capacity / total_priority) * priority
            
        network_graph[src][dst][key]["bandwidth"] = round(updated_bandwidth)

    # for each node, scale bandwidth by max / total assigned if total assigned exceeds max
    for node in network_graph.nodes:
        total_outgoing_bandwidth = sum(data["bandwidth"] for _, _, data in network_graph.edges(node, data=True))
        uplink_capacity = network_graph.nodes[node]["uplink_capacity"]
        
        if total_outgoing_bandwidth > uplink_capacity:
            scaling_factor = uplink_capacity / total_outgoing_bandwidth
            for _, _, data in network_graph.edges(node, data=True):
                data["bandwidth"] *= scaling_factor

    # for staged reqs, allocate new bandwidth
    reqs_staged = [req for req in get_request_by_status(status=["STAGED"], session=session)]
    for req in reqs_staged:
        for _, _, key, data in network_graph.edges(keys=True, data=True):
            if "rule_id" in data and data["rule_id"] == req.rule_id:
                allocated_bandwidth = int(data["bandwidth"])
        update_bandwidth(req, allocated_bandwidth, session=session)
        mark_requests([req], "DECIDED", session)

    # for already provisioned reqs, modify bandwidth and mark as stale
    reqs_provisioned = [req for req in get_request_by_status(status=["MODIFIED", "PROVISIONED"], session=session)]
    for req in reqs_provisioned:
        for _, _, key, data in network_graph.edges(keys=True, data=True):
            if "rule_id" in data and data["rule_id"] == req.rule_id:
                allocated_bandwidth = int(data["bandwidth"])
        if allocated_bandwidth != req.bandwidth:
            update_bandwidth(req, allocated_bandwidth, session=session)
            mark_requests([req], "STALE", session)

@databased
def allocator(session=None):
    reqs_init = [req_init for req_init in get_request_by_status(status=["INIT"], session=session)]
    reqs_finished = [req_fin for req_fin in get_request_by_status(status=["FINISHED"], session=session)]
    for new_request in reqs_init:        
        for req_fin in reqs_finished:
            if (req_fin.src_site == new_request.src_site and req_fin.dst_site == new_request.dst_site):
                new_request.update({
                    "src_ipv6_block": req_fin.src_ipv6_block,
                    "dst_ipv6_block": req_fin.dst_ipv6_block,
                    "src_url": req_fin.src_url,
                    "dst_url": req_fin.dst_url,
                    "transfer_status": "ALLOCATED"
                })
                mark_requests([req_fin], "DELETED", session)
                reqs_finished.remove(req_fin)
                break
        else:
            src_endpoint = get_unused_endpoint(new_request.src_site, session=session)
            dst_endpoint = get_unused_endpoint(new_request.dst_site, session=session)
            new_request.update({
                "src_ipv6_block": src_endpoint.ip_block,
                "dst_ipv6_block": dst_endpoint.ip_block,
                "src_url": src_endpoint.hostname,
                "dst_url": dst_endpoint.hostname,
                "transfer_status": "ALLOCATED"
            })