"""Model to JSON-safe dict conversion for the /api endpoints.

Kept separate from frontend.py so the shape of the API is readable in one
place, and so the HTML handlers and the JSON handlers cannot drift apart in
what they consider a "request".

Everything here is read-only and takes already-loaded model instances; no
queries, no session, no locking.
"""

from datetime import datetime
from typing import Optional


def _iso(value: Optional[datetime]) -> Optional[str]:
    """Datetimes as ISO-8601 UTC.

    Columns are written naive by some daemons and aware by others, so
    normalise rather than emit two formats. Naive values are UTC by
    convention -- see the DMM_CHANGELOG note on clock consistency.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.isoformat() + "Z"
    return value.isoformat()


def endpoint_to_dict(endpoint) -> Optional[dict]:
    if endpoint is None:
        return None
    return {
        "id": endpoint.id,
        "site_name": endpoint.site_name,
        "hostname": endpoint.hostname,
        "ip_range": endpoint.ip_range,
        "protocol": endpoint.protocol,
        "is_allocated": bool(endpoint.is_allocated),
    }


def request_to_dict(req, detail: bool = True) -> dict:
    """Serialise a Request.

    detail=False drops the long URI and endpoint fields, for list responses
    where they would dominate the payload.
    """
    src_site = req.src_site.name if req.src_site else None
    dst_site = req.dst_site.name if req.dst_site else None

    data = {
        "rule_id": req.rule_id,
        "transfer_status": req.transfer_status,
        "sense_circuit_status": req.sense_circuit_status,
        # The join key for rule -> SENSE instance -> rtmon dashboard.
        "sense_uuid": req.sense_uuid,
        "sense_affiliated": bool(req.sense_affiliated),
        "health": req.health,
        "sense_retries": req.sense_retries,
        "failure_reason": req.failure_reason,
        "priority": req.priority,
        "modified_priority": req.modified_priority,
        "rule_size": req.rule_size,
        "src_site": src_site,
        "dst_site": dst_site,
        # Rucio's own names. Several logical sites can map to one physical
        # site, so these are not always equal to src_site/dst_site.
        "src_rse": req.src_logical_site or src_site,
        "dst_rse": req.dst_logical_site or dst_site,
        "bandwidth": {
            "allocated_mbps": req.allocated_bandwidth_mbps,
            "available_mbps": req.available_bandwidth_mbps,
            "previous_mbps": req.previous_bandwidth_mbps,
        },
        "fts": {
            "streams_current": req.fts_streams_current,
            "streams_desired": req.fts_streams_desired,
        },
        "measured": {
            "throughput_mbps": req.prometheus_throughput,
            "bytes": req.prometheus_bytes,
        },
        "timestamps": {
            "created_at": _iso(req.created_at),
            "updated_at": _iso(req.updated_at),
            "sense_provisioned_at": _iso(req.sense_provisioned_at),
            "rucio_finished_at": _iso(req.rucio_finished_at),
            "failed_at": _iso(req.failed_at),
        },
    }

    if detail:
        data["sense_src_uri"] = req.sense_src_uri
        data["sense_dst_uri"] = req.sense_dst_uri
        data["sense_alloc_rule_id"] = req.sense_alloc_rule_id
        data["src_pool_site"] = req.src_pool_site
        data["dst_pool_site"] = req.dst_pool_site
        data["src_endpoint"] = endpoint_to_dict(req.src_endpoint)
        data["dst_endpoint"] = endpoint_to_dict(req.dst_endpoint)

    return data


def site_to_dict(site, detail: bool = True) -> dict:
    endpoints = list(site.endpoints or [])
    allocated = sum(1 for e in endpoints if e.is_allocated)
    data = {
        "name": site.name,
        "sense_uri": site.sense_uri,
        "query_url": site.query_url,
        "endpoints_total": len(endpoints),
        "endpoints_allocated": allocated,
        "endpoints_free": len(endpoints) - allocated,
    }
    if detail:
        data["endpoints"] = [endpoint_to_dict(e) for e in endpoints]
    return data


def mesh_to_dict(link) -> dict:
    return {
        "id": link.id,
        "site_1": link.site_1,
        "site_2": link.site_2,
        "vlan_range": link.vlan_range,
        "link_capacity_mbps": link.link_capacity_mbps,
    }
