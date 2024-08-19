import logging
import json
from datetime import datetime

from dmm.db.session import databased, get_engine
from dmm.db.models import *
from dmm.utils.monit import prom_get_throughput_at_t, fts_submit_job_query, prom_get_all_bytes_at_t
from dmm.utils.db import get_requests, get_request_cursor, update_bytes_at_t

'''
What we need to accomplish here:
1) Get all PROVISIONED transfers that have a sense circuit created
2) Get the current bandwidth being transmitted from Prometheus
3) Compare with requested bandwidth from DMM
4) Raise alert if throughput is larger - errors are occurring
'''
#Integrate this with the frontend with 3 categories: If requested - real = 10%, say it's Excellent, within 20% say OK, if > 20%, alerg
@databased
def online_monitoring(query_frequency=10, session=None):
    #For ease of looking in the terminal
    print("###############################################################")
    logging.debug("Getting network transfer data")
    #If there are requests that are provisioned, continue
    #Otherwise return
    reqs = get_requests(status=["PROVISIONED"],session=session)
    if reqs:  
        logging.debug(f"Calculating bandwidth")
    else:
        logging.debug("No data found")
        return
    
    for req in reqs:
        #Get the assigned bandwidth and ipv6 for the given request
        bandwidth, ipv6 = req.bandwidth, req.src_ipv6_block
        logging.debug(f"Requested Bandwidth: {bandwidth}, IPV6: {ipv6}")  
        
        #Get the actual volume of bytes being transferred at current time
        timestamp = round(datetime.timestamp(datetime.now()))
        current_volume = prom_get_all_bytes_at_t(timestamp, ipv6)
        logging.debug(f"Current volume: {current_volume}")

        #Update the given volume in the database
        update_bytes_at_t(req, current_volume, session=session)
        bandwidth_volumes = req.bytes_at_t
        logging.debug(f"Data: {bandwidth_volumes}")

        #Calculate true throughput vs. requested throughput
        total = sum(bandwidth_volumes.values())
        logging.debug(f"Total bytes at t: {total}")
        current_throughput = (total) / (5*query_frequency) #Change this to 5 * self.sense_daemon_frequency
        logging.debug(f"Requested throughput: {bandwidth}, Actual throughput: {current_throughput}")

'''
What we need to accomplish here:
1) Get all PROVISIONED transfers that have a sense circuit created
2) Get the bandwidth transmitted from Prometheus from start to end
3) Compare with total file_size of data from FTS
4) Raise alert if throughput is larger - transfers failed
'''
@databased
def offline_monitoring(session=None):
    reqs = get_requests(status=["FINISHED"],session=session)
    if reqs == []:
        logging.debug("No transfers running")
    else:
        logging.debug("Getting network transfer data")
        cursor = get_request_cursor(session=session)
        cursor.execute(f"SELECT rule_id, src_ipv6_block, sense_provisioned_at FROM requests WHERE sense_provisioned_at IS NOT NULL")
        data = cursor.fetchall()
        ipv6_pattern = f"{ipv6[:-3]}[0-9a-fA-F]{{1,4}}"
        for row in data:
            rule_id, ipv6, sense_provisioned_at = row
            #Adjust for actual throughput calculation
            actual_throughput = prom_get_throughput_at_t(sense_provisioned_at,ipv6)
            fts_data = fts_submit_job_query(rule_id)
            size = 0
            for transfer in fts_data:
                size += transfer['file_size']
            timestamps = [hit["_source"]["data"] for hit in fts_data["hits"]["hits"]]
            start_timestamps = [entry['tr_timestamp_start'] for entry in timestamps]
            complete_timestamps = [entry['tr_timestamp_complete'] for entry in timestamps]
            min_complete_timestamp = min(start_timestamps)
            max_complete_timestamp = max(complete_timestamps)
            time_dif = max_complete_timestamp - min_complete_timestamp
            #Change calculations for throughput
            estimated_throughput = size / time_dif
            
            if actual_throughput > estimated_throughput:
                logging.error(f"Transfers for rule_id: {rule_id} failed")
            elif actual_throughput < estimated_throughput:
                logging.error(f"Transfers for rule_id: {rule_id} had lower throughput than expected")

# @databased
# def online_monitoring(session=None):
#     reqs = get_requests(status=["PROVISIONED"],session=session)
#     if reqs == []:
#         logging.debug("No transfers running")
#     else:
#         logging.debug("Getting network transfer data")
#         cursor = get_request_cursor(session=session)
#         cursor.execute(f"SELECT src_ipv6_block FROM requests WHERE sense_provisioned_at IS NOT NULL")
#         data = cursor.fetchall()
#         if data:  
#             logging.debug(data[0])
#         else:
#             logging.debug("No data found.")
#         for row in data:
#             ipv6 = row   
#             ipv6_pattern = f"{ipv6[:-3]}[0-9a-fA-F]{{1,4}}"         
#             #Check calculation for requested bandwidth
#             cursor.execute(f"SELECT SUM(bandwidth) FROM requests WHERE src_ipv6_block REGEXP '{ipv6_pattern}'")
#             req_throughput = cursor.fetchone()[0]
#             #Are we doing this for all transfers, not just ones at a certain ipv6?
#             #current_throughput = prom_get_throughput_at_t(sense_provisioned_at,ipv6)
#             timestamp = round(datetime.timestamp(datetime.now()))
#             current_volume = prom_get_all_bytes_at_t(timestamp, ipv6)
#             check_intervals(current_volume, session=session)
#             interval_cursor = get_interval_cursor()
#             interval_query = "SELECT (interval_1 + interval_2 + interval_3 + interval_4 + interval_5) AS total_sum FROM bytes"
#             current_throughput = ((interval_cursor.execute(interval_query).fetchone()[0]) /
#                                     (50)) #Change this to 5 * self.sense_daemon_frequency
#             logging.debug(f"Current throughput: {current_throughput}, Requested throughput: {req_throughput}")
#             if current_throughput > req_throughput:
#                 logging.debug(f"Error in transferring data for {ipv6}")
            

