from flask import Flask, Response, render_template, url_for, request
import logging
import json
import os
from datetime import datetime

from dmm.db.session import databased
from dmm.utils.db import get_request_from_id, get_request_cursor, get_requests, update_bytes_at_t
from dmm.utils.monit import prom_get_throughput_at_t, fts_submit_job_query, prom_get_all_bytes_at_t, calculate_throughput_sliding_window_fixed

current_directory = os.path.dirname(os.path.abspath(__file__))
templates_folder = os.path.join(current_directory, "templates")
frontend_app = Flask(__name__, template_folder=templates_folder)

@frontend_app.route("/query/<rule_id>", methods=["GET"])
@databased
def handle_client(rule_id, session=None):
    logging.info(f"Received request for rule_id: {rule_id}")
    try:
        req = get_request_from_id(rule_id, session=session)
        if req and req.src_url and req.dst_url:
            result = json.dumps({"source": req.src_url, "destination": req.dst_url})
            response = Response(result, content_type="application/json")
            response.headers.add("Content-Type", "application/json")
            return response
        else:
            response = Response("", status=404)
            response.headers.add("Content-Type", "text/plain")
            return response
    except Exception as e:
        logging.error(f"Error processing client request: {str(e)}")
        response = Response("", status=500)
        response.headers.add("Content-Type", "text/plain")
        return response

@frontend_app.route("/", methods=["GET", "POST"])
@databased
def get_dmm_status(session=None):
    cursor = get_request_cursor(session=session)
    data = cursor.fetchall() 
    try:
        return render_template("index.html", data=data)
    except Exception as e:
        logging.error(e)
        return "Problem in the DMM frontend\n"

# When users click on "See More" button, get detailed metrics
@frontend_app.route("/details/<rule_id>", methods=["GET", "POST"])
@databased
def open_rule_details(rule_id,session=None):
    logging.info(f"Retrieving information for rule_id: {rule_id}")
    try:
        req = get_request_from_id(rule_id, session=session)
        cursor = get_request_cursor(session=session)
        cursor.execute(f"SELECT rule_id, transfer_status, bandwidth, sense_circuit_status, src_ipv6_block, bandwidth, sense_provisioned_at FROM requests WHERE rule_id = '{req.rule_id}'")
        data = cursor.fetchone()
        rule_id, transfer_status, bandwidth, sense_circuit_status, src_ipv6, bandwidth, prov_time = data
        logging.debug(f"Cursor data: {data}")

        # Can edit this if needed for testing
        if prov_time is not None:
            timestamp = round(datetime.timestamp(datetime.now()))
            current_volume = prom_get_all_bytes_at_t(timestamp, src_ipv6)
            update_bytes_at_t(req, current_volume, session=session)
            bandwidth_volumes = req.bytes_at_t
            total = sum(bandwidth_volumes.values())
            current_throughput = (total) / (50) #Change this to 5 * self.sense_daemon_frequency

            # Check if current throughput is good, OK, or error-filled
            if abs(bandwidth - current_throughput) <= (0.10 * bandwidth):
                dmm_status = "Transfer throughput in excellent condition"
            elif abs(bandwidth - current_throughput) <= (0.2 * bandwidth):
                dmm_status = "Transfer throughput in OK condition"
            else:
                dmm_status = "Errors occurring in transfer"
        else:
            dmm_status = "No bandwidth has been provisioned"
        
        # Can adjust if needed
        # If the transfer is finished, get FTS data
        if transfer_status == "FINISHED":
            query_params = ['data.tr_id','data.tr_timestamp_start', 'data.tr_timestamp_complete', 'file_size',
                    'data.t__error_message','data.t_failure_phase','data.t_final_transfer_state']
            fts_query = fts_submit_job_query("ab8c31e2835b473d8a8aaf7cc27c86e6", query_params)
            
            comp_timestamp = max([s['tr_timestamp_complete'] for s in fts_query])
            transfer_end = datetime.fromtimestamp(comp_timestamp / 1000.0)
            time_dif = (transfer_end - prov_time).total_seconds()
            start_bytes = prom_get_all_bytes_at_t(prov_time, req.src_ipv6_block)
            end_bytes = prom_get_all_bytes_at_t(transfer_end, req.src_ipv6_block)
            prom_throughput = (start_bytes + end_bytes) / time_dif

            errors = {}
            completed_transfers = []

            for transfer in fts_query:
                # If the transfer was successful, append to list of successful transfers for throughput calc
                if transfer['t_final_transfer_state'] == "Ok":
                    completed_transfers.append(transfer)
                else:
                    # Otherwise, save error data and display it
                    msg = transfer['t__error_message']
                    phase = transfer['t_failure_phase']
                    errors.update({
                        transfer['tr_id']: [msg,phase]
                    })
            
            # Calculate the average throughput for the given transfer
            throughputs, average_throughput, std_deviation = calculate_throughput_sliding_window_fixed(completed_transfers, prov_time, comp_timestamp)
        else:
            errors = None
            average_throughput = "No FTS Data"
            prom_throughput = "Transfer still running"

        return render_template("details.html",rule_id=rule_id, bandwidth=bandwidth, 
                                sense_circuit_status=sense_circuit_status,
                                final_transfer_state=transfer_status,
                                dmm_status=dmm_status, error_transfers=errors,
                                avg_fts_throughput=average_throughput, prom_throughput=prom_throughput)

    except Exception as e:
        logging.error(e)
        return "Failed to retrieve rule info\n"

# Helper method for open_rule_details
@frontend_app.route('/process_id', methods=['POST'])
@databased
def process_id(session=None):
    data = request.json
    rule_id = data.get('rule_id')
    logging.debug(f"Rule ID:{rule_id}")
    url = url_for('open_rule_details', rule_id=rule_id, _external=True)
    return url
