import logging
import os
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from dmm.db.session import databased
from dmm.models.request import Request as DBRequest
from dmm.models.site import Site

from dmm.daemons.core.sites import RefreshSiteDBDaemon
from rucio.client import Client

current_directory = os.path.dirname(os.path.abspath(__file__))
templates_folder = os.path.join(current_directory, "templates")
static_folder = os.path.join(current_directory, "static")

templates = Jinja2Templates(directory=templates_folder)

api = FastAPI()
api.mount("/static", StaticFiles(directory=static_folder), name="static")

@api.get("/query/{rule_id}")
@databased
async def handle_client(rule_id: str, session=None):
    logging.info(f"Received request for rule_id: {rule_id}")
    try:
        req = DBRequest.from_id(rule_id, session=session)
        if req:
            if req.src_endpoint and req.dst_endpoint:
                result = {"source": req.src_endpoint.hostname, "destination": req.dst_endpoint.hostname}
                return JSONResponse(content=result)
            else:
                time.sleep(15)
                return HTTPException(status_code=404, detail="Request not yet allocated")
        else:
            raise HTTPException(status_code=404, detail="Request not found")
    except Exception as e:
        logging.error(f"Error processing client request: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@api.get("/")
@databased
async def get_dmm_status(request: Request, session=None):
    try:
        reqs = DBRequest.get_all(session=session)
        return templates.TemplateResponse("index.html", {"request": request, "data": reqs})
    except Exception as e:
        logging.error(e)
        raise HTTPException(status_code=500, detail="Internal server error")

@api.get("/sites")
@databased
async def get_sites(request: Request, session=None):
    try:
        sites = Site.get_all(session=session)
        return templates.TemplateResponse("sites.html", {"request": request, "data": sites})
    except Exception as e:
        logging.error(e)
        raise HTTPException(status_code=500, detail="Internal server error")

@api.get("/details/{rule_id}")
@databased
async def open_rule_details(request: Request, rule_id: str, session=None):
    try:
        req = DBRequest.from_id(rule_id, session=session)
        return templates.TemplateResponse("details.html", {"request": request, "data": req})
    except Exception as e:
        logging.error(e)
        raise HTTPException(status_code=500, detail="Internal server error")

@api.post("/mark_finished")
@databased
async def mark_finished(request: Request, session=None):
    try:
        data = await request.json()
        rule_id = data.get("rule_id")
        req = DBRequest.from_id(rule_id, session=session)
        if req.transfer_status == "NOT_SENSE":
            return "This is not a SENSE rule, what are you trying to do?"
        req.update_transfer_status("FINISHED", session=session)
        return "Request marked as finished"
    except Exception as e:
        logging.error(e)
        raise HTTPException(status_code=500, detail="Failed to mark request as finished")

@api.post("/update_fts_limit")
@databased
async def update_fts_limit(request: Request, session=None):
    try:
        data = await request.json()
        rule_id = data.get("rule_id")
        limit = data.get("limit")
        req = DBRequest.from_id(rule_id, session=session)
        if req.transfer_status == "NOT_SENSE":
            return "This is not a SENSE rule, what are you trying to do?"
        if req.transfer_status not in ["CANCELLED", "FINISHED", "DELETED"]:
            req.update_fts_limit_desired(limit=limit, session=session)
            return "FTS limit updated"
        else:
            raise HTTPException(status_code=400, detail="Cannot update FTS limit for cancelled, finished or deleted requests")
    except Exception as e:
        logging.error(e)
        raise HTTPException(status_code=500, detail="Failed to update FTS limit")

@api.post("/reinitialize")
@databased
async def reinitialize(request: Request, session=None):
    try:
        data = await request.json()
        rule_id = data.get("rule_id")
        req = DBRequest.from_id(rule_id, session=session)
        if req.transfer_status == "NOT_SENSE":
            return "This is not a SENSE rule, what are you trying to do?"
        req.update_transfer_status("ALLOCATED", session=session)
        return "Request reinitialized"
    except Exception as e:
        logging.error(e)
        raise HTTPException(status_code=500, detail="Failed to reinitialize request")

@api.post("/refresh_sites")
async def refresh_sites():
    try:
        daemon = RefreshSiteDBDaemon(frequency=1)
        daemon.run_once(client=Client(), session=None)
        return "Sites refreshed"
    except Exception as e:
        logging.error(e)
        raise HTTPException(status_code=500, detail="Failed to refresh sites")
    
@api.get("/health")
async def health_check():
    return {"status": "healthy"}