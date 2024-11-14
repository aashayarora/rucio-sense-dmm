from flask import Flask, Response, render_template, request
import logging
import json
import os
import time

from dmm.db.session import databased
from dmm.db.request import Request
from dmm.db.site import Site

current_directory = os.path.dirname(os.path.abspath(__file__))
templates_folder = os.path.join(current_directory, "templates")
frontend_app = Flask(__name__, template_folder=templates_folder)

@frontend_app.route("/query/<rule_id>", methods=["GET"])
@databased
def handle_client(rule_id, session=None):
    logging.info(f"Received request for rule_id: {rule_id}")
    try:
        req = Request.from_id(rule_id, session=session)
        if req and req.src_endpoint and req.dst_endpoint:
            result = json.dumps({"source": req.src_endpoint.hostname, "destination": req.dst_endpoint.hostname})
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

@frontend_app.route("/", methods=["GET"])
@databased
def get_dmm_status(session=None):
    reqs = Request.get_all(session=session)
    try:
        return render_template("index.html", data=reqs)
    except Exception as e:
        logging.error(e)
        return str(e)

@frontend_app.route("/sites", methods=["GET"])
@databased
def get_sites(session=None):
    sites = Site.get_all(session=session)
    try:
        return render_template("sites.html", data=sites)
    except Exception as e:
        logging.error(e)
        return "Problem in the DMM frontend\n"

# When users click on "See More" button, get detailed metrics
@frontend_app.route("/details/<rule_id>", methods=["GET", "POST"])
@databased
def open_rule_details(rule_id, session=None):
    try:
        req = Request.from_id(rule_id, session=session)
        return render_template("details.html", data=req)
    except Exception as e:
        logging.error(e)
        return "Failed to retrieve rule info\n"

@frontend_app.route("/mark_finished", methods=["POST"])
@databased
def mark_finished(session=None):
    try:
        rule_id = request.get_json().get("rule_id")
        req = Request.from_id(rule_id, session=session)
        req.mark_as("FINISHED", session=session)
        return "Request marked as finished"
    except Exception as e:
        logging.error(e)
        return "Failed to mark request as finished\n"

@frontend_app.route("/update_fts_limit", methods=["POST"])
@databased
def update_fts_limit(session=None):
    try:
        data = request.get_json()
        rule_id = data.get("rule_id")
        limit = data.get("limit")
        req = Request.from_id(rule_id, session=session)
        if req.transfer_status not in ["CANCELLED", "FINISHED", "DELETED"]:
            req.update_fts_limit_desired(limit=limit, session=session)
            return "FTS limit updated"
        else:
            return "Cannot update FTS limit for cancelled, finished or deleted requests"
    except Exception as e:
        logging.error(e)
        return "Failed to update FTS limit\n"

@frontend_app.route("/reinitialize", methods=["POST"])
@databased
def reinitialize(session=None):
    try:
        rule_id = request.get_json().get("rule_id")
        req = Request.from_id(rule_id, session=session)
        req.mark_as("ALLOCATED", session=session)
        return "Request reinitialize"
    except Exception as e:
        logging.error(e)
        return "Failed to reinitialize request\n"

@frontend_app.route("/logs", methods=["GET"])
def stream_logs():
    def generate():
        while True:
            try:
                with open("dmm.log", 'r') as file:
                    while True:
                        line = file.readline()
                        if line:
                            yield f"data: {line}"
                        else:
                            time.sleep(1)  # Wait for new content
            except Exception as e:
                yield f"ERROR: {str(e)}"
    return Response(generate(), content_type="text/event-stream")