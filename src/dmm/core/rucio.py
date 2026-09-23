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
