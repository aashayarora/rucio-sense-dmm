import json
import logging

from rucio.common.exception import RuleNotFound

def list_replication_rules(client):
    """
    List all replication rules from Rucio.
    
    Args:
        client: Rucio client instance
        
    Returns:
        List of replication rules
        
    Raises:
        Exception: If listing fails
    """
    try:
        return client.list_replication_rules()
    except Exception as e:
        logging.error(f"Failed to list Rucio rules: {e}", exc_info=True)
        raise

def get_replication_rule(client, rule_id):
    """
    Get a specific replication rule from Rucio.
    
    Args:
        client: Rucio client instance
        rule_id: The rule ID to fetch
        
    Returns:
        Rule dict or None if not found
        
    Raises:
        RuleNotFound: If the rule doesn't exist
    """
    rule = client.get_replication_rule(rule_id)
    logging.debug(f"Rucio rule {rule_id}: {json.dumps(rule, default=str)}")
    return rule

def get_rule_status(client, rule_id):
    """
    Get the status of a replication rule.
    
    Args:
        client: Rucio client instance
        rule_id: The rule ID to check
        
    Returns:
        Status string ('OK', 'STUCK', 'REPLICATING', etc.) or None if not found
        
    Raises:
        RuleNotFound: If the rule doesn't exist
    """
    return get_replication_rule(client, rule_id)['state']

def get_rule_priority(client, rule_id):
    """
    Get the priority of a replication rule.
    
    Args:
        client: Rucio client instance
        rule_id: The rule ID to check
        
    Returns:
        Priority value
        
    Raises:
        RuleNotFound: If the rule doesn't exist
    """
    return get_replication_rule(client, rule_id)['priority']

def is_rule_finished(status):
    """
    Check if a rule status indicates the transfer is finished.
    
    Args:
        status: Rule status string
        
    Returns:
        True if finished (OK or STUCK), False otherwise
    """
    return status in ("OK", "STUCK")

def is_rule_ok(status):
    """
    Check if a rule status indicates success.
    
    Args:
        status: Rule status string
        
    Returns:
        True if OK, False otherwise
    """
    return status == "OK"

def is_rule_stuck(status):
    """
    Check if a rule status indicates it's stuck.
    
    Args:
        status: Rule status string
        
    Returns:
        True if STUCK, False otherwise
    """
    return status == "STUCK"

def list_files(client, scope, name):
    """
    List files in a dataset/container.
    
    Args:
        client: Rucio client instance
        scope: Scope of the dataset
        name: Name of the dataset
        
    Returns:
        Generator of file dicts
    """
    return client.list_files(scope=scope, name=name)

def get_rule_size(client, scope, name):
    """
    Get the total size of files in a rule's dataset.
    
    Args:
        client: Rucio client instance
        scope: Scope of the dataset
        name: Name of the dataset
        
    Returns:
        Total size in bytes, 0 if error or no files
    """
    try:
        files = list(client.list_files(scope=scope, name=name))
        total_bytes = sum([f.get("bytes", 0) for f in files if f.get("bytes") is not None])
        logging.debug(f"Rucio size of {scope}:{name}: {total_bytes} bytes in {len(files)} files")
        return total_bytes if total_bytes > 0 else 0
    except Exception as e:
        logging.error(f"Failed to get rule size: {e}", exc_info=True)
        return 0

def list_rses(client):
    """
    List all RSEs from Rucio.
    
    Args:
        client: Rucio client instance
        
    Returns:
        List of RSE dicts
    """
    return client.list_rses()

def get_rse(client, rse_name):
    """
    Get RSE details.
    
    Args:
        client: Rucio client instance
        rse_name: Name of the RSE
        
    Returns:
        RSE dict
    """
    rse = client.get_rse(rse_name)
    logging.debug(f"Rucio RSE {rse_name}: {json.dumps(rse, default=str)}")
    return rse

def get_rse_protocol(client, rse_name):
    """
    Get the primary protocol scheme for an RSE.
    
    Args:
        client: Rucio client instance
        rse_name: Name of the RSE
        
    Returns:
        Protocol scheme string or None if not found
    """
    rse = get_rse(client, rse_name)
    if not rse:
        return None
    
    protocols = rse.get('protocols', [])
    if not protocols:
        return None
    
    return protocols[0].get('scheme')

def parse_rule_for_request(rule):
    """
    Extract relevant information from a Rucio rule for creating a request.
    
    Args:
        rule: Rucio rule dict
        
    Returns:
        Dict with rule_id, src_site_name, dst_site_name, priority, scope, name, activity
    """
    return {
        'rule_id': rule['id'],
        'src_site_name': rule.get('source_replica_expression'),
        'dst_site_name': rule.get('rse_expression'),
        'priority': rule.get('priority', 3),
        'scope': rule.get('scope'),
        'name': rule.get('name'),
        'activity': rule.get('activity'),
    }

def is_sense_rule(rule):
    """
    Check if a rule is a SENSE rule based on activity.
    
    Args:
        rule: Rucio rule dict
        
    Returns:
        True if SENSE rule, False otherwise
    """
    activity = rule.get('activity')
    return activity and "sense" in activity.lower()

# Rucio request states: SUBMITTED requests are in flight in FTS; DONE ones have
# been archived to the request history.
_LIVE_REQUEST_STATES = "S"
_DONE_REQUEST_STATES = "D"
_HISTORY_PAGE_SIZE = 100

def get_fts_job_ids(client, src_rse, dst_rse, rule_id, history_pages=0):
    """
    FTS jobs carrying a rule's transfers between two RSEs.

    Requests live in Rucio's requests table while FTS has them, and are archived
    to the history once they finish. The live table is asked first; the history
    only if that finds nothing and history_pages allows it. Both are queried per
    RSE pair, not per rule, so the history is paged until the rule turns up, and
    history_pages caps that - a busy pair's history is far longer than any one
    rule's.

    Args:
        client: Rucio client instance
        src_rse: Source RSE name, as Rucio knows it
        dst_rse: Destination RSE name, as Rucio knows it
        rule_id: The rule whose jobs to find
        history_pages: Most history pages to read, 0 for none

    Returns:
        Dict of FTS job id to the FTS server it was submitted to (None if Rucio
        didn't record one)
    """
    jobs = {}

    def collect(records):
        for rec in records:
            if rec.get("rule_id") == rule_id and rec.get("external_id"):
                jobs[rec["external_id"]] = rec.get("external_host")

    try:
        collect(client.list_requests(src_rse=src_rse, dst_rse=dst_rse, request_states=_LIVE_REQUEST_STATES))
    except Exception as e:
        logging.warning(f"Could not list live Rucio requests {src_rse} -> {dst_rse}: {e}")

    if jobs:
        return jobs

    for page in range(history_pages):
        try:
            records = list(client.list_requests_history(
                src_rse=src_rse, dst_rse=dst_rse, request_states=_DONE_REQUEST_STATES,
                offset=page * _HISTORY_PAGE_SIZE, limit=_HISTORY_PAGE_SIZE,
            ))
        except Exception as e:
            logging.warning(f"Could not list Rucio request history {src_rse} -> {dst_rse}: {e}")
            break
        collect(records)
        if jobs or len(records) < _HISTORY_PAGE_SIZE:
            break

    return jobs
