import json
import requests
import re
import statistics
from datetime import datetime, timedelta, timezone

from dmm.utils.config import config_get

# Helper functions
def prom_submit_query(query_dict) -> dict:
    prometheus_user = config_get("prometheus", "user")
    prometheus_pass = config_get("prometheus", "password")
    prometheus_host = config_get("prometheus", "host")

    endpoint = "api/v1/query"
    query_addr = f"{prometheus_host}/{endpoint}"
    return requests.get(query_addr, params=query_dict, auth=(prometheus_user, prometheus_pass)).json()

def prom_get_val_from_response(response):
    """Extract desired value from typical location in Prometheus response"""
    print(response)
    return response["data"]["result"][0]["value"][1]
    
def prom_get_interface(ipv6) -> str:
    response = prom_submit_query({"query": "node_network_address_info"})
    if response["status"] == "success":
        for metric in response["data"]["result"]:
            if re.match(rf'{ipv6[:-3]}[0-9a-fA-F]{{1,4}}',metric["metric"]["address"]) != None:
            #if metric["metric"]["address"] == ipv6:
                return (metric["metric"]["device"], metric["metric"]["instance"], metric["metric"]["job"], metric["metric"]["sitename"])

def prom_get_all_interface(ipv6) -> str:
    """
    Gets all interfaces with ipv6 addresses matching the given ipv6
    """
    #Change ipv6_pattern if needed
    ipv6_pattern = f"{ipv6[:-3]}[0-9a-fA-F]{{1,4}}"
    query = f"node_network_address_info{{address=~'{ipv6_pattern}'}}"
    response = prom_submit_query({"query": query})

    interfaces = []
    
    #If response is a successful transfer and matches the given ipv6,
    #adds its interface data to the list
    if response["status"] == "success":        
        for metric in response["data"]["result"]:
            if re.match(ipv6_pattern, metric["metric"]["address"]):
                interfaces.append(
                    (
                        metric["metric"]["device"], 
                        metric["metric"]["instance"], 
                        metric["metric"]["job"], 
                        metric["metric"]["sitename"]
                    )
                )
    return interfaces

def prom_get_total_bytes_at_t(time, ipv6) -> float:
    """
    Returns the total number of bytes transmitted from a given Rucio RSE via a given
    ipv6 address
    """
    device, instance, job, sitename = prom_get_interface(ipv6)
    query_params = f"device=\"{device}\",instance=\"{instance}\",job=\"{job}\",sitename=\"{sitename}\""
    print(query_params)
    metric = f"node_network_transmit_bytes_total{{{query_params}}}"
    # Get bytes transferred at the start time
    response = prom_submit_query({"query": metric, "time": time})
    print(response)
    if response is not None and response["status"] == "success":
        bytes_at_t = prom_get_val_from_response(response)
    else:
        raise Exception(f"query {metric} failed")
    return float(bytes_at_t)

def prom_get_all_bytes_at_t(time, ipv6) -> float:
    """
    Returns the total number of bytes transmitted from a given Rucio RSE via a given
    ipv6 address
    """
    transfers = prom_get_all_interface(ipv6) 
    total_bytes = 0
    for transfer in transfers:
        device, instance, job, sitename = transfer[0], transfer[1], transfer[2], transfer[3]
        query_params = f"device=\"{device}\",instance=\"{instance}\",job=\"{job}\",sitename=\"{sitename}\""
        metric = f"node_network_transmit_bytes_total{{{query_params}}}"
        # Get bytes transferred at the start time
        response = prom_submit_query({"query": metric, "time": time})
        if response is not None and response["status"] == "success":
            bytes_at_t = prom_get_val_from_response(response)
        else:
            raise Exception(f"query {metric} failed")
        #Add the bytes at given time to running total
        total_bytes += float(bytes_at_t)
    return total_bytes

# t_avg_over should be time when transfer ended for given rule_id
# time stamp rule ending - time stamp provision kicking in
# The IP address here is src_ipv6 + dst_ipv6 (only src_ipv6 for now)
def prom_get_throughput_at_t(time, ipv6, t_avg_over=None) -> float:
    bytes_transmitted = sum([i * prom_get_all_bytes_at_t(time + i * 0.5 * t_avg_over, ipv6) for i in [-1,1]])
    # TODO account for bin edges
    return bytes_transmitted / (t_avg_over)

def fts_get_val_from_response(response):
    """Extract desired value from typical location in Prometheus response"""
    return response["hits"]["hits"][0]["_source"]["data"]

# Modified to include list of values we want from the fts query
# Values should be in the format "data.[PARAMETER_NAME]"
# If left empty, will get all possible values
def fts_submit_job_query(rule_id, query_params=[]):    
    fts_host = config_get("fts", "monit_host")
    fts_token = config_get("fts", "monit_auth_token")
    headers = {"Authorization": f"Bearer {fts_token}", "Content-Type": "application/json"}
    endpoint = "api/datasources/proxy/9233/monit_prod_fts_enr_complete*/_search"
    query_addr = f"{fts_host}/{endpoint}"
    data = {
        "size": 100,
        "query":{
            "bool":{
                "filter":[{
                    "query_string": {
                        "analyze_wildcard": "true",
                        "query": f"data.file_metadata.rule_id:{rule_id}"
                    }
                }]
            }
        },
        "_source": query_params
    }
    data_string = json.dumps(data)
    response = requests.get(query_addr, data=data_string, headers=headers).json()
    timestamps = [hit["_source"]["data"] for hit in response["hits"]["hits"]]
    return timestamps

# Returns timestamps and file size from FTS
# This method may be redundant, can delete if needed
def fts_check_transfer_state(rule_id):
    success = fts_submit_job_query(rule_id, ['data.t_final_transfer_state'])
    if success=='Ok':
        return True
    else:
        return False    

#Transfers must be an iterable with tr_timestamp_start, tr_timestamp_end, and file_size
def calculate_throughput_sliding_window_fixed(transfers, start_time, end_time, window_size=None, step_size=None):
    '''
    Calculates the throughput using the sliding window method
    '''
    # Converts timestamps from milliseconds into datetime format
    start_time = datetime.fromtimestamp(start_time / 1000.0, tz=timezone.utc)
    end_time = datetime.fromtimestamp(end_time / 1000.0, tz=timezone.utc)
    
    # Calculate the total seconds between start_time and end_time
    total_seconds = (end_time - start_time).total_seconds()
    # Can adjust this based on how big we want the windows to actually be
    if window_size == None: 
        window_size = total_seconds / 20
    if step_size==None:    
        step_size = window_size / 2
        num_windows = int((total_seconds - window_size) // step_size) + 1
    
    throughputs = []

    # Iterates through each window
    for i in range(num_windows):
        # Calculates the window start as time added to start_time based on step size
        window_start = start_time + timedelta(seconds=i * step_size)
        # Calculates window end as time added to window start time based on window size
        window_end = window_start + timedelta(seconds=window_size)
        
        # Holds the throughput that happened within a given window
        window_throughput = 0

        # Get data needed for calculation
        for transfer in transfers:
            transfer_start = transfer['tr_timestamp_start']
            transfer_end = transfer['tr_timestamp_complete']
            file_size = transfer['file_size']          
            # Converts timestamps from milliseconds into datetime format, may need to change timezone
            transfer_start = datetime.fromtimestamp(transfer_start / 1000.0, tz=timezone.utc)
            transfer_end = datetime.fromtimestamp(transfer_end / 1000.0, tz=timezone.utc)
            
            # If the transfer doesn't overlap with the given window, move on
            if transfer_end <= window_start or transfer_start >= window_end:
                continue
            
            # Otherwise calculate the overlap of the transfer with the given window
            overlap_start = max(window_start, transfer_start)
            overlap_end = min(window_end, transfer_end)
            overlap_seconds = (overlap_end - overlap_start).total_seconds()
            
            # Calculate the fraction of the transfer that falls within this window
            total_transfer_seconds = (transfer_end - transfer_start).total_seconds()
            transfer_fraction = (overlap_seconds / total_transfer_seconds) * file_size
            
            # Add the fraction of the file size to the window_throughput
            window_throughput += transfer_fraction
        
        # Right now throughput is in bytes per second, can change this if necessary
        throughputs.append(window_throughput / window_size)
        
    # Calculate average and standard deviation, can add additional metrics if needed
    average_throughput = sum(throughputs)/len(throughputs)
    std_deviation = statistics.pstdev(throughputs)
    
    return throughputs, average_throughput, std_deviation

# Everything here is purely for testing
if __name__ == "__main__":
    query_params = ['data.tr_id','data.tr_timestamp_start', 'data.tr_timestamp_complete', 'file_size',
                    'data.t__error_message','data.t_failure_phase','data.t_final_transfer_state']
    fts_query = fts_submit_job_query("ab8c31e2835b473d8a8aaf7cc27c86e6", query_params)
    print(fts_query[0]['t__error_message'])
    
    start_timestamp = min([s['tr_timestamp_start'] for s in fts_query])
    comp_timestamp = max([s['tr_timestamp_complete'] for s in fts_query])
    errors = {}
    completed_transfers = []

    for transfer in fts_query:
        if transfer['t_final_transfer_state'] == "Ok":
            completed_transfers.append(transfer)
        else:
            msg = transfer['t__error_message']
            phase = transfer['t_failure_phase']
            errors.update({
                transfer['tr_id']: [msg,phase]
            })
    
    throughputs, average_throughput, std_deviation = calculate_throughput_sliding_window_fixed(completed_transfers, start_timestamp, comp_timestamp)
    print(f"Throughputs: {throughputs}")
    print(f"Average throughput: {average_throughput}")
    print(f"Standard deviation: {std_deviation}")
       