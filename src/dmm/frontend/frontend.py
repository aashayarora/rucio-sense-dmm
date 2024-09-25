from flask import Flask, Response, render_template
import logging
import json
import os

from dmm.db.session import databased
from dmm.db.request import Request

current_directory = os.path.dirname(os.path.abspath(__file__))
templates_folder = os.path.join(current_directory, "templates")
frontend_app = Flask(__name__, template_folder=templates_folder)

@frontend_app.route("/query/<rule_id>", methods=["GET"])
@databased
def handle_client(rule_id, session=None):
    logging.info(f"Received request for rule_id: {rule_id}")
    try:
        req = Request.from_id(rule_id, session=session)
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

@frontend_app.route("/", methods=["GET"])
@databased
def get_dmm_status(session=None):
    reqs = Request.get_all(session=session)
    try:
        return render_template("index.html", data=reqs)
    except Exception as e:
        logging.error(e)
        return "Problem in the DMM frontend\n"
    
# When users click on "See More" button, get detailed metrics
@frontend_app.route("/details/<rule_id>", methods=["GET", "POST"])
@databased
def open_rule_details(rule_id,session=None):
    try:
        req = Request.from_id(rule_id, session=session)
        return render_template("details.html", data=req)
    except Exception as e:
        logging.error(e)
        return "Failed to retrieve rule info\n"