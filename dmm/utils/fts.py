import logging
import json
import requests

from dmm.utils.config import config_get

def setup_request(req):
    logging.debug('For a given request, returns the url, certification, path, source port, and destination port')
    url = config_get("fts", "fts_host")
    cert = (config_get("fts", "cert"), config_get("fts", "key"))
    capath = "/etc/grid-security/certificates/"

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    try:
        src_url_no_port = "davs://" + req.src_url.split(":")[0]
        dst_url_no_port = "davs://" + req.dst_url.split(":")[0]
    except:
        logging.exception("Error while parsing source and destination URLs")
        raise
    
    return url, cert, capath, headers, src_url_no_port, dst_url_no_port

def modify_link_config(req, max_active, min_active):
    logging.debug('Modifies link configuration')
    url, cert, capath, headers, src_url_no_port, dst_url_no_port = setup_request(req)

    data = {
        "symbolicname": "-".join([src_url_no_port, dst_url_no_port]),
        "source": src_url_no_port,
        "destination": dst_url_no_port,
        "max_active": max_active,
        "min_active": min_active,
        "nostreams": 0,
        "optimizer_mode": 0,
        "no_delegation": False,
        "tcp_buffer_size": 0
    }
    
    data = json.dumps(data)
    try:
        response = requests.post(url + "/config/links", headers=headers, cert=cert, verify=capath, data=data)
        logging.info(f"FTS link config modified, response: {response}")
        return (response.status_code == 200)
    except:
        logging.exception("Error while modifying FTS link config")
        return None
    
def modify_se_config(req, max_inbound, max_outbound):
    logging.debug('Updates storage configuration')
    url, cert, capath, headers, src_url_no_port, dst_url_no_port = setup_request(req)
    data = {
        src_url_no_port: {
            "se_info": {
                "inbound_max_active": None,
                "inbound_max_throughput": None,
                "outbound_max_active": max_outbound,
                "outbound_max_throughput": None,
                "udt": None,
                "ipv6": None,
                "se_metadata": None,
                "site": None,
                "debug_level": None,
                "eviction": None
            }
        },
        dst_url_no_port: {
            "se_info": {
                "inbound_max_active": max_inbound,
                "inbound_max_throughput": None,
                "outbound_max_active": None,
                "outbound_max_throughput": None,
                "udt": None,
                "ipv6": None,
                "se_metadata": None,
                "site": None,
                "debug_level": None,
                "eviction": None
            }
        }
    }
    try:
        data = json.dumps(data)
        response = requests.post(url + "/config/se", headers=headers, cert=cert, verify=capath, data=data)
        logging.info(f"FTS storage config modified, response: {response}")
        return (response.status_code == 200)
    except: 
        logging.exception("Error while modifying FTS storage config")
        return None