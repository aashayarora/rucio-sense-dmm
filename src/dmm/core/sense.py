"""
Core SENSE API operations and logic.
This module contains all SENSE-related API calls and business logic,
extracted from the SENSE daemons for better separation of concerns.
"""
import logging
import json
import html
import ipaddress
import re

from sense.client.workflow_combined_api import WorkflowCombinedApi
from sense.client.discover_api import DiscoverApi
from dmm.models.request import SenseCircuitStatus

class StagingError(ValueError):
    def __init__(self, message, sense_uuid=None):
        super().__init__(message)
        self.sense_uuid = sense_uuid

def _good_response(response):
    """Check if SENSE API response is valid (no errors)."""
    if not response:
        return False
    if isinstance(response, dict):
        return not response.get("error")
    if isinstance(response, str):
        return "error" not in response.lower()
    if isinstance(response, (list, tuple)):
        return not any(
            (isinstance(r, dict) and r.get("error")) or (isinstance(r, str) and "error" in r.lower())
            for r in response
        )
    return True

def _log_response(action, rule_id, response):
    """
    Log what SENSE-O answered to a rule's request, so it shows in the rule's log.

    A create or modify response carries the instance's model, ~60 KB of escaped
    Turtle that dwarfs the rest and would push the rule's other lines out of the
    log. The rest goes in at INFO; the model only at DEBUG, for when the circuit
    VLANs parsed from it look wrong.
    """
    if isinstance(response, dict) and "model" in response:
        rest = {k: v for k, v in response.items() if k != "model"}
        logging.info(f"SENSE {action} response for {rule_id}: {json.dumps(rest, default=str)}")
        logging.debug(f"SENSE {action} model for {rule_id}: {json.dumps(response['model'], default=str)}")
    else:
        logging.info(f"SENSE {action} response for {rule_id}: {json.dumps(response, default=str)}")

def get_instance_status(sense_uuid):
    """
    Get the status of a SENSE instance.
    
    Args:
        sense_uuid: The SENSE instance UUID
        
    Returns:
        Status string or "UNKNOWN" if not available
    """
    workflow_api = WorkflowCombinedApi()
    try:
        status = workflow_api.instance_get_status(si_uuid=sense_uuid) or "UNKNOWN"
    except Exception as e:
        # SENSE-O answers 404 for an instance it no longer has.
        if "returned code 404" not in str(e).lower():
            raise
        status = "UNKNOWN"
    logging.debug(f"SENSE status of {sense_uuid}: {status}")
    return status

def get_last_state(sense_uuid):
    """
    The phase a failed instance reached before it failed, e.g. "COMPILED".

    Only SENSE-O's instance listing has it. None when it can't be found.
    """
    try:
        instances = DiscoverApi().discover_service_instances_get().get("instances", [])
    except Exception as e:
        logging.warning(f"Could not look up the last state of SENSE instance {sense_uuid}: {e}")
        return None
    for instance in instances:
        if instance.get("referenceUUID") == sense_uuid:
            return instance.get("lastState")
    return None

def verify_instance(sense_uuid):
    """
    Ask SENSE-O to run its verification of an instance again.

    The client's instance_operate() only allows provision, cancel and reprovision,
    so this goes through the call underneath it.
    """
    workflow_api = WorkflowCombinedApi()
    response = workflow_api.instance_si_uuid_action_put_with_http_info("verify", si_uuid=sense_uuid)
    logging.debug(f"SENSE verify response for {sense_uuid}: {json.dumps(response, default=str)}")
    return response

def discard_instance(sense_uuid, status):
    """
    Remove an instance that cannot be recovered, so the request can start over.

    An instance that failed before propagating holds nothing at the sites and is
    simply deleted, as is one that only compiled. One that failed later, or is
    live, holds resources at the sites and is force-cancelled first. Raises if
    the instance can't be removed yet; the caller tries again next cycle.
    """
    if status == "UNKNOWN":
        # Gone, or created but never given a status because its staging failed.
        try:
            delete_instance(sense_uuid)
        except Exception as e:
            logging.debug(f"Delete of SENSE instance {sense_uuid} with no status: {e}")
        return
    if is_being_cancelled(status):
        raise ValueError(f"SENSE instance {sense_uuid} is still cancelling ({status})")
    needs_cancel = not (is_create_compiled(status) or is_cancel_ready(status))
    if needs_cancel and status.endswith("FAILED"):
        last_state = get_last_state(sense_uuid)
        if last_state in ("INIT", "COMPILED"):
            logging.info(f"SENSE instance {sense_uuid} failed after {last_state}, deleting without cancel")
            needs_cancel = False
    if needs_cancel:
        cancel_link(sense_uuid, status)
    delete_instance(sense_uuid)

def stage_link(profile_uuid, src_site, dst_site, src_ip_range, dst_ip_range, vlan_range, rule_id):
    """
    Stage a new SENSE link by creating a new instance.
    
    Args:
        profile_uuid: SENSE service profile UUID
        src_site: Source site object with sense_uri
        dst_site: Destination site object with sense_uri
        src_ip_range: Source endpoint IPv6 range
        dst_ip_range: Destination endpoint IPv6 range
        vlan_range: VLAN range for the connection
        rule_id: Rule ID to use as alias
        
    Returns:
        Response dict containing service_uuid, bandwidth info, and URIs
        
    Raises:
        StagingError: If staging fails. The instance is left in place for the
        caller to record and clean up, and the error carries its uuid.
    """
    workflow_api = None
    try:
        workflow_api = WorkflowCombinedApi()
        workflow_api.instance_new()
        intent = {
            "service_profile_uuid": profile_uuid,
            "queries": [
                {
                    "ask": "edit",
                    "options": [
                        {"data.connections[0].terminals[0].uri": src_site.sense_uri},
                        {"data.connections[0].terminals[0].ipv6_prefix_list": src_ip_range},
                        {"data.connections[0].terminals[1].uri": dst_site.sense_uri},
                        {"data.connections[0].terminals[1].ipv6_prefix_list": dst_ip_range},
                        {"data.connections[0].terminals[0].vlan_tag": vlan_range},
                        {"data.connections[0].terminals[1].vlan_tag": vlan_range}
                    ]
                }, 
                {
                    "ask": "total-block-maximum-bandwidth",
                    "options": [
                        {
                            "name": "Connection 1",
                            "start": "now",
                            "end-before": "+24h"
                        }
                    ]
                },
                {
                    "ask": "extract-result-values",
                    "options": [
                        {
                            "sparql": "SELECT DISTINCT ?ipv6_subnet_uri ?ipv6_subnet WHERE {?route mrs:routeFrom ?ipv6_subnet_uri. ?ipv6_subnet_uri mrs:type 'ipv6-prefix-list'. ?ipv6_subnet_uri mrs:value ?ipv6_subnet}"
                        }
                    ]
                }
            ],
            "alias": rule_id
        }
        logging.debug(f"SENSE stage intent for {rule_id} on {getattr(workflow_api, 'si_uuid', None)}: {json.dumps(intent)}")
        response = workflow_api.instance_create(json.dumps(intent))
        _log_response("stage", rule_id, response)
        if not _good_response(response):
            raise ValueError(f"SENSE req staging failed for {rule_id}: {json.dumps(response, default=str)[:500]}")
        if isinstance(response, dict) and not response.get("service_uuid"):
            response["service_uuid"] = workflow_api.si_uuid
        return response
    except Exception as e:
        sense_uuid = getattr(workflow_api, "si_uuid", None)
        logging.error(f"Failed to stage link for {rule_id} on SENSE instance {sense_uuid}: {e}")
        raise StagingError(f"Failed to stage link for {rule_id}: {e}", sense_uuid=sense_uuid) from e

def parse_staging_response(response, src_ip_range=None, dst_ip_range=None):
    """
    Parse the response from stage_link to extract relevant information.

    Args:
        response: Response dict from stage_link
        src_ip_range: Known source IPv6 prefix (used to match URIs to correct endpoints)
        dst_ip_range: Known destination IPv6 prefix (used to match URIs to correct endpoints)

    Returns:
        Tuple of (sense_uuid, bandwidth_mbps, src_uri, dst_uri)
    """
    if not isinstance(response, dict):
        raise ValueError(f"Invalid staging response type: {type(response)}")

    sense_uuid = response.get("service_uuid")
    queries = response.get("queries")
    if not sense_uuid:
        raise ValueError("Missing service_uuid in staging response")
    if not isinstance(queries, list) or len(queries) < 3:
        raise ValueError("Staging response has incomplete queries payload")

    bw_query = queries[1] if isinstance(queries[1], dict) else {}
    bw_results = bw_query.get("results", [])
    if not bw_results:
        raise ValueError("Missing bandwidth results in staging response")
    raw_bw_bps = int((bw_results[0] or {}).get("bandwidth", 0))
    bandwidth_mbps = raw_bw_bps / (1000 * 1000)
    logging.debug(f"Staging bandwidth: raw={raw_bw_bps} bps → {bandwidth_mbps:.0f} Mbps")

    extract_query = queries[2] if isinstance(queries[2], dict) else {}
    extract_results = extract_query.get("results", [])
    if len(extract_results) < 2:
        raise ValueError("Missing endpoint extraction results in staging response")

    src_uri, dst_uri = _match_uris_to_endpoints(extract_results, src_ip_range, dst_ip_range)
    if not src_uri or not dst_uri:
        raise ValueError("Missing source or destination IPv6 subnet URI in staging response")

    return sense_uuid, bandwidth_mbps, src_uri, dst_uri

def _match_uris_to_endpoints(extract_results, src_ip_range, dst_ip_range):
    """
    Match SPARQL extract results to source/destination endpoints by subnet value.
    Falls back to positional order if IP ranges are not provided or no match is found.
    """
    if src_ip_range and dst_ip_range:
        src_uri = dst_uri = None
        try:
            src_compressed = ipaddress.IPv6Network(src_ip_range).compressed
            dst_compressed = ipaddress.IPv6Network(dst_ip_range).compressed
        except ValueError:
            src_compressed = dst_compressed = None

        for entry in extract_results:
            if not isinstance(entry, dict):
                continue
            subnet = entry.get("ipv6_subnet", "")
            uri = entry.get("ipv6_subnet_uri")
            try:
                subnet_compressed = ipaddress.IPv6Network(subnet).compressed if subnet else None
            except ValueError:
                subnet_compressed = None

            if src_compressed and subnet_compressed == src_compressed:
                src_uri = uri
            elif dst_compressed and subnet_compressed == dst_compressed:
                dst_uri = uri

        if src_uri and dst_uri:
            return src_uri, dst_uri

        logging.warning(
            "Could not match SPARQL results to known IP ranges "
            f"(src={src_ip_range}, dst={dst_ip_range}); falling back to positional order"
        )

    # Positional fallback (original behaviour)
    src_uri = (extract_results[0] or {}).get("ipv6_subnet_uri")
    dst_uri = (extract_results[1] or {}).get("ipv6_subnet_uri")
    return src_uri, dst_uri

# A circuit's port on a switch, in the model delta SENSE-O returns when it compiles:
#   urn:ogf:network:<domain>:<switch>:<port>:conn+<uuid>:...:vlanport+<tag>
_VLANPORT = re.compile(r"(urn:ogf:network:[^\s<>\"]+?):vlanport\+(\d+)")


def _vlan_near(ports, prefix):
    """The single VLAN tag on ports under prefix, or None if there are none or several."""
    tags = {tag for uri, tag in ports if uri.startswith(prefix + ":")}
    if len(tags) > 1:
        logging.warning(f"Circuit has several VLANs under {prefix}: {sorted(tags)}, not picking one")
        return None
    return int(tags.pop()) if tags else None


def parse_circuit_vlans(response, src_uri, dst_uri, src_site_uri=None, dst_site_uri=None):
    """
    The circuit's VLAN tag at each end, from the model in a provision or modify response.

    Staging compiles a throwaway candidate: SENSE-O picks the VLAN again when the
    provisioning intent compiles, so only a provision or modify response describes
    the circuit that actually carries traffic. Tags can also differ between the two
    ends when a network in between translates them.

    Each end is looked up on the switch its subnet URI hangs off
    (urn:...:dellos10_s0:service+rst-ipv6:...), falling back to the whole site
    domain. Returns (src_vlan, dst_vlan); either is None when the model doesn't say.
    """
    model = response.get("model") if isinstance(response, dict) else None
    if not model:
        return None, None
    ports = _VLANPORT.findall(html.unescape(model))

    vlans = []
    for subnet_uri, site_uri in ((src_uri, src_site_uri), (dst_uri, dst_site_uri)):
        vlan = None
        if subnet_uri and ":service+" in subnet_uri:
            vlan = _vlan_near(ports, subnet_uri.split(":service+", 1)[0])
        if vlan is None and site_uri:
            vlan = _vlan_near(ports, site_uri)
        vlans.append(vlan)
    return tuple(vlans)

def provision_link(sense_uuid, profile_uuid, bandwidth_mbps, src_site, dst_site, 
                   src_ip_range, dst_ip_range, vlan_range, rule_id):
    """
    Provision a SENSE link (commit and activate).
    
    Args:
        sense_uuid: SENSE instance UUID
        profile_uuid: SENSE service profile UUID
        bandwidth_mbps: Bandwidth to provision in Mbps
        src_site: Source site object with sense_uri
        dst_site: Destination site object with sense_uri
        src_ip_range: Source endpoint IPv6 range
        dst_ip_range: Destination endpoint IPv6 range
        vlan_range: VLAN range for the connection
        rule_id: Rule ID for logging
        
    Returns:
        API response
        
    Raises:
        ValueError: If provisioning fails
    """
    try:
        workflow_api = WorkflowCombinedApi()
        workflow_api.si_uuid = sense_uuid
        intent = {
            "service_profile_uuid": profile_uuid,
            "queries": [
                {
                    "ask": "edit",
                    "options": [
                        {"data.connections[0].bandwidth.capacity": str(int(bandwidth_mbps))},
                        {"data.connections[0].terminals[0].uri": src_site.sense_uri},
                        {"data.connections[0].terminals[0].ipv6_prefix_list": src_ip_range},
                        {"data.connections[0].terminals[1].uri": dst_site.sense_uri},
                        {"data.connections[0].terminals[1].ipv6_prefix_list": dst_ip_range},
                        {"data.connections[0].terminals[0].vlan_tag": vlan_range},
                        {"data.connections[0].terminals[1].vlan_tag": vlan_range}
                    ]
                }
            ],
            "alias": rule_id
        }
        logging.debug(f"SENSE provision intent for {rule_id} on {sense_uuid}: {json.dumps(intent)}")
        response = workflow_api.instance_create(json.dumps(intent))
        _log_response("provision create", rule_id, response)
        if not _good_response(response):
            raise ValueError(f"Failed to create instance for request {rule_id}, response: {response}")
        operate_response = workflow_api.instance_operate("provision", sync="true")
        _log_response("provision operate", rule_id, operate_response)
        return response
    except Exception as e:
        logging.error(f"Failed to provision request {rule_id}: {e}")
        raise

def modify_link(sense_uuid, profile_uuid, bandwidth_mbps, src_site, dst_site,
                src_ip_range, dst_ip_range, vlan_range, rule_id):
    """
    Modify an existing SENSE link's bandwidth.
    
    Args:
        sense_uuid: SENSE instance UUID
        profile_uuid: SENSE service profile UUID
        bandwidth_mbps: New bandwidth in Mbps
        src_site: Source site object with sense_uri
        dst_site: Destination site object with sense_uri
        src_ip_range: Source endpoint IPv6 range
        dst_ip_range: Destination endpoint IPv6 range
        vlan_range: VLAN range for the connection
        rule_id: Rule ID for logging
        
    Returns:
        API response
        
    Raises:
        Exception: If modification fails
    """
    try:
        workflow_api = WorkflowCombinedApi()
        workflow_api.si_uuid = sense_uuid
        intent = {
            "service_profile_uuid": profile_uuid,
            "queries": [
                {
                    "ask": "edit",
                    "options": [
                        {"data.connections[0].bandwidth.capacity": str(int(bandwidth_mbps))},
                    ]
                }
            ]
        }
        logging.debug(f"SENSE modify intent for {rule_id} on {sense_uuid}: {json.dumps(intent)}")
        response = workflow_api.instance_modify(json.dumps(intent), sync="true")
        _log_response("modify", rule_id, response)
        return response
    except Exception as e:
        logging.error(f"Failed to modify request {rule_id}: {e}")
        raise

def cancel_link(sense_uuid, status=None):
    """
    Cancel a SENSE link.
    
    Args:
        sense_uuid: SENSE instance UUID
        status: Current circuit status (optional, for determining force cancel)
        
    Returns:
        API response
    """
    workflow_api = WorkflowCombinedApi()
    force_cancel = "READY" not in (status or "")
    logging.debug(f"SENSE cancel {sense_uuid} (status={status}, force={force_cancel})")
    response = workflow_api.instance_operate("cancel", si_uuid=sense_uuid, sync="true", force=str(force_cancel).lower())
    logging.debug(f"SENSE cancel response for {sense_uuid}: {json.dumps(response, default=str)}")
    return response

def delete_instance(sense_uuid):
    """
    Delete a SENSE instance.
    
    Args:
        sense_uuid: SENSE instance UUID
        
    Returns:
        API response
    """
    workflow_api = WorkflowCombinedApi()
    response = workflow_api.instance_delete(si_uuid=sense_uuid)
    logging.debug(f"SENSE delete response for {sense_uuid}: {json.dumps(response, default=str)}")
    return response


def is_cancel_ready(status):
    """Check if instance is in CANCEL-READY state."""
    return status == SenseCircuitStatus.CANCEL_READY.value

def is_create_compiled(status):
    """Check if instance is in CREATE-COMPILED state."""
    return status == SenseCircuitStatus.CREATE_COMPILED.value

def is_ready_for_cancel(status):
    """Check if instance is in a state that can be cancelled."""
    return status in {
        SenseCircuitStatus.CREATE_READY.value,
        SenseCircuitStatus.MODIFY_READY.value,
        SenseCircuitStatus.REINSTATE_READY.value
    }

def is_create_ready(status):
    """Check if instance is in CREATE-READY state."""
    return status == SenseCircuitStatus.CREATE_READY.value

def is_create_failed(status):
    """Check if instance creation has failed."""
    return status == SenseCircuitStatus.CREATE_FAILED.value

def is_modify_failed(status):
    """Check if instance modification has failed."""
    return status == SenseCircuitStatus.MODIFY_FAILED.value

def is_cancel_compiled(status):
    """Check if a SENSE circuit cancel attempt failed during propagate (after COMPILED)."""
    return status == SenseCircuitStatus.CANCEL_COMPILED.value

def is_cancel_failed(status):
    """Check if a SENSE circuit cancel attempt has failed.

    SENSE-O emits CANCEL - FAILED when an internal error occurs during
    teardown.  The correct recovery is a force-cancel retry:
    cancel_link() automatically sets force=true whenever 'READY' is absent
    from the status string, so no extra logic is needed at the call site.
    """
    return status == SenseCircuitStatus.CANCEL_FAILED.value

def is_ready_for_modify(status):
    """Check if instance is in a state that can be modified."""
    return status in {
        SenseCircuitStatus.CREATE_READY.value,
        SenseCircuitStatus.MODIFY_READY.value,
        SenseCircuitStatus.REINSTATE_READY.value
    }

def is_being_cancelled(status):
    """Check if SENSE has accepted a cancel and is still processing it.
    
    Returns True when the instance is in CANCEL-COMMITTING or CANCEL-COMMITTED,
    meaning a previous cancel call was accepted by SENSE (even if it timed out
    on our side with a 504). Re-issuing cancel_link() in these states causes
    a 500 BadRequestException storm — callers should wait for CANCEL-READY instead.
    """
    return status in {
        SenseCircuitStatus.CANCEL_COMMITTING.value,
        SenseCircuitStatus.CANCEL_COMMITTED.value
    }

def is_being_modified(status):
    """Check if instance is currently being modified."""
    return status in {
        SenseCircuitStatus.MODIFY_COMMITTING.value,
        SenseCircuitStatus.MODIFY_COMMITTED.value
    }

def is_being_provisioned(status):
    """Check if instance is currently being provisioned."""
    return status in {
        SenseCircuitStatus.CREATE_COMMITTING.value,
        SenseCircuitStatus.CREATE_COMMITTED.value,
        SenseCircuitStatus.MODIFY_COMMITTING.value,
        SenseCircuitStatus.MODIFY_COMMITTED.value
    }

def is_circuit_active(status):
    """Check if the circuit is up and stable (carrying traffic)."""
    return status in {
        SenseCircuitStatus.CREATE_READY.value,
        SenseCircuitStatus.MODIFY_READY.value,
        SenseCircuitStatus.REINSTATE_READY.value,
    }

def is_affiliated_state(status):
    """Check if instance is in a state where endpoints should be affiliated."""
    return status in {
        SenseCircuitStatus.CREATE_COMPILED.value,
        SenseCircuitStatus.CREATE_COMMITTED.value,
        SenseCircuitStatus.CREATE_COMMITTING.value,
        SenseCircuitStatus.CREATE_READY.value
    }

def is_stalled_status(status):
    """Committed but never verified: SENSE-O stops here when a site RM doesn't confirm."""
    return status in {
        SenseCircuitStatus.CREATE_COMMITTED.value,
        SenseCircuitStatus.MODIFY_COMMITTED.value,
        SenseCircuitStatus.CANCEL_COMMITTED.value,
    }
