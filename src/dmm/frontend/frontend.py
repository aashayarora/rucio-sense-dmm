import logging
import os
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from dmm.db.session import databased
from dmm.db.request import Request as DBRequest
from dmm.db.site import Site

current_directory = os.path.dirname(os.path.abspath(__file__))
templates_folder = os.path.join(current_directory, "templates")
templates = Jinja2Templates(directory=templates_folder)

frontend_app = FastAPI()

@frontend_app.get("/query/{rule_id}")
@databased
async def handle_client(rule_id: str, session=None):
    logging.info(f"Received request for rule_id: {rule_id}")
    try:
        req = DBRequest.from_id(rule_id, session=session)
        if req and req.src_endpoint and req.dst_endpoint:
            result = {"source": req.src_endpoint.hostname, "destination": req.dst_endpoint.hostname}
            return JSONResponse(content=result)
        else:
            raise HTTPException(status_code=404, detail="Request not found")
    except Exception as e:
        logging.error(f"Error processing client request: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@frontend_app.get("/")
@databased
async def get_dmm_status(request: Request, session=None):
    try:
        reqs = DBRequest.get_all(session=session)
        return templates.TemplateResponse("index.html", {"request": request, "data": reqs})
    except Exception as e:
        logging.error(e)
        raise HTTPException(status_code=500, detail="Internal server error")

@frontend_app.get("/sites")
@databased
async def get_sites(request: Request, session=None):
    try:
        sites = Site.get_all(session=session)
        return templates.TemplateResponse("sites.html", {"request": request, "data": sites})
    except Exception as e:
        logging.error(e)
        raise HTTPException(status_code=500, detail="Internal server error")

@frontend_app.get("/details/{rule_id}")
@databased
async def open_rule_details(request: Request, rule_id: str, session=None):
    try:
        req = DBRequest.from_id(rule_id, session=session)
        return templates.TemplateResponse("details.html", {"request": request, "data": req})
    except Exception as e:
        logging.error(e)
        raise HTTPException(status_code=500, detail="Internal server error")

@frontend_app.post("/mark_finished")
@databased
async def mark_finished(request: Request, session=None):
    try:
        data = await request.json()
        rule_id = data.get("rule_id")
        req = DBRequest.from_id(rule_id, session=session)
        req.mark_as("FINISHED", session=session)
        return "Request marked as finished"
    except Exception as e:
        logging.error(e)
        raise HTTPException(status_code=500, detail="Failed to mark request as finished")

@frontend_app.post("/update_fts_limit")
@databased
async def update_fts_limit(request: Request, session=None):
    try:
        data = await request.json()
        rule_id = data.get("rule_id")
        limit = data.get("limit")
        req = DBRequest.from_id(rule_id, session=session)
        if req.transfer_status not in ["CANCELLED", "FINISHED", "DELETED"]:
            req.update_fts_limit_desired(limit=limit, session=session)
            return "FTS limit updated"
        else:
            raise HTTPException(status_code=400, detail="Cannot update FTS limit for cancelled, finished or deleted requests")
    except Exception as e:
        logging.error(e)
        raise HTTPException(status_code=500, detail="Failed to update FTS limit")

@frontend_app.post("/reinitialize")
@databased
async def reinitialize(request: Request, session=None):
    try:
        data = await request.json()
        rule_id = data.get("rule_id")
        req = DBRequest.from_id(rule_id, session=session)
        req.mark_as("ALLOCATED", session=session)
        return "Request reinitialized"
    except Exception as e:
        logging.error(e)
        raise HTTPException(status_code=500, detail="Failed to reinitialize request")