from flask import Flask, Response, render_template, url_for, request
import logging
import json
import os

from dmm.db.session import databased
from dmm.utils.db import get_request_from_id, get_request_cursor
from dmm.utils.monit import prom_get_throughput_at_t, fts_submit_job_query

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

@frontend_app.route("/status", methods=["GET", "POST"])
@databased
def get_dmm_status(session=None):
    cursor = get_request_cursor(session=session)
    data = cursor.fetchall() 
    try:
        return render_template("index.html", data=data)
    except Exception as e:
        logging.error(e)
        return "Problem in the DMM frontend\n"

''' 
Need some way to integrate particular rule information from Prometheus 
Current metrics: transfer state (might be redunandt with circuit status), transfer start time,
                    transfer end time, transfer completion time, transfer operation time (how long it took),
                    transfer file size, volume of data transferred, number of retries, errors (if any)
Is there a way to constantly refresh this so people don't constantly have to reopen the page? 
'''
@frontend_app.route("/details/<rule_id>", methods=["GET", "POST"])
@databased
def open_rule_details(rule_id,session=None):
    logging.info(f"Retrieving information for rule_id: {rule_id}")
    try:
        req = get_request_from_id(rule_id, session=session)
        cursor = get_request_cursor(session=session)
        cursor.execute(f"SELECT rule_id, bandwidth, sense_circuit_status, src_ipv6_block, sense_provisioned_at FROM requests WHERE rule_id = '{req.rule_id}'")
        data = cursor.fetchone()
        rule_id, bandwidth, sense_circuit_status, src_ipv6, prov_time = data
        logging.debug(f"Cursor data: {data}")
        fts_query = fts_submit_job_query(rule_id)
        logging.debug(fts_query)
        final_state, error = fts_query['t_final_transfer_state'], fts_query['tr_error_category']
        #prom_get_throughput_at_t()
        logging.debug(f"Final state: {final_state}, Error: {error}")
        
        return render_template("details.html",rule_id=rule_id, bandwidth=bandwidth, 
                               sense_circuit_status=sense_circuit_status)
                            #    t_final_state=final_state, tr_error_category=error)
    except Exception as e:
        logging.error(e)
        return "Failed to retrieve rule info\n"

@frontend_app.route('/process_id', methods=['POST'])
@databased
def process_id(session=None):
    data = request.json
    rule_id = data.get('rule_id')
    logging.debug(f"Rule ID:{rule_id}")
    url = url_for('open_rule_details', rule_id=rule_id, _external=True)
    return url

# def fts_get_monit_metrics(rule_id):
#     result_query = fts_submit_job_query(rule_id)
#     transferred_vol = result_query['transferred_vol']
#     #transferred_vol @ timestamp
#     src_ip, dst_ip = result_query['src_hostname'], result_query['dst_hostname']
#     src_ipv6 = socket.getaddrinfo(src_ip,None)[-1][-1][0]
#     dst_ipv6 = socket.getaddrinfo(dst_ip,None)[-1][-1][0] 

'''
Potential trends to graph:
Correlation between src/dst sites and transfer rates
Success/failure rates at a given src/dst, given time
Throughput at given time (see high traffic) - time series graph
'''
@frontend_app.route('/statistics', methods=['GET','POST'])
@databased
def see_correlations(session=None):
    return render_template("correlation.html")
