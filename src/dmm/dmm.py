import os
import argparse
import logging
import threading

import multiprocessing
import uvicorn

from rucio.client import Client
from dmm.core.config import config_get_int
from dmm.core.health import reset_heartbeats
from dmm.api.frontend import auth_enabled

from dmm.daemons.core.sites import RefreshSiteDBDaemon

from dmm.daemons.rucio.initializer import RucioInitDaemon
from dmm.daemons.rucio.modifier import RucioModifierDaemon
from dmm.daemons.rucio.finisher import RucioFinisherDaemon

from dmm.daemons.fts.modifier import FTSModifierDaemon

from dmm.daemons.sense.handler import SENSEHandlerDaemon
from dmm.daemons.sense.stager import SENSEStagerDaemon
from dmm.daemons.sense.provisioner import SENSEProvisionerDaemon
from dmm.daemons.sense.modifier import SENSEModifierDaemon
from dmm.daemons.sense.canceller import SENSECancellerDaemon
from dmm.daemons.sense.deleter import SENSEDeleterDaemon

from dmm.daemons.core.allocator import AllocatorDaemon
from dmm.daemons.core.decider import DeciderDaemon
from dmm.daemons.core.monit import MonitDaemon

from dmm.api.frontend import api

class DMM:
    def __init__(self) -> None:
        self.port = config_get_int("dmm", "port")

        # frequencies at which daemons run (in seconds)
        self.rucio_frequency = config_get_int("daemons", "rucio", default=60, constraint="nonneg")
        self.dmm_frequency = config_get_int("daemons", "dmm", default=60, constraint="nonneg")
        self.sense_frequency = config_get_int("daemons", "sense", default=60, constraint="nonneg")
        self.monit_frequency = config_get_int("daemons", "monit", default=60, constraint="nonneg")
        self.sites_frequency = config_get_int("daemons", "db", default=7200, constraint="nonneg")
        self.fts_frequency = config_get_int("daemons", "fts", default=60)

        self.lock = threading.Lock()
        self.running = True
        self.threads = []

        try:
            self.rucio_client = Client()
        except Exception as e:
            logging.error(f"Failed to initialize Rucio client: {e}")
            raise ConnectionError("Failed to initialize Rucio client, exiting...")
    
    @staticmethod
    def run_server(port):
        try:
            uvicorn.run(api, host="0.0.0.0", port=port)
        except BaseException as e:
            logging.error(f"Failed to start frontend on {port}, trying default port 31601", exc_info=True)
            uvicorn.run(api, host="0.0.0.0", port=31601)

    def start(self) -> None:
        logging.info("Starting Daemons")

        # Heartbeats from a previous run would otherwise keep a renamed or
        # removed daemon permanently red.
        reset_heartbeats()

        if not auth_enabled():
            logging.warning(
                "[dmm] api_token is not set: mark_finished, update_fts_limit, "
                "reinitialize_sense, reinitialize_request and refresh_sites accept "
                "unauthenticated POSTs from anyone who can reach this service"
            )
        
        logging.info("Initializing site database - this must complete before other daemons start")
        sitedb = RefreshSiteDBDaemon(frequency=self.sites_frequency, kwargs={"client": self.rucio_client})
        try:
            sitedb.run_once(client=self.rucio_client, session=None)
            logging.info("Site database initialization completed successfully")
        except Exception as e:
            logging.error(f"Failed to initialize site database: {e}", exc_info=True)
            logging.warning("Continuing with daemon startup, but some daemons may fail without site data")
        
        allocator = AllocatorDaemon(frequency=self.dmm_frequency)
        decider = DeciderDaemon(frequency=self.dmm_frequency)

        monit = MonitDaemon(frequency=self.monit_frequency)
        fts = FTSModifierDaemon(frequency=self.fts_frequency)
        
        rucio_init = RucioInitDaemon(frequency=self.rucio_frequency, kwargs={"client": self.rucio_client})
        rucio_modifier = RucioModifierDaemon(frequency=self.rucio_frequency, kwargs={"client": self.rucio_client})
        rucio_finisher = RucioFinisherDaemon(frequency=self.rucio_frequency, kwargs={"client": self.rucio_client})
        
        sense_updater = SENSEHandlerDaemon(frequency=self.sense_frequency)
        stager = SENSEStagerDaemon(frequency=self.sense_frequency)
        provision = SENSEProvisionerDaemon(frequency=self.sense_frequency)
        sense_modifier = SENSEModifierDaemon(frequency=self.sense_frequency)
        canceller = SENSECancellerDaemon(frequency=self.sense_frequency)
        deleter = SENSEDeleterDaemon(frequency=self.sense_frequency)

        daemons = [
            sitedb, fts, allocator, decider, monit,
            rucio_init, rucio_modifier, rucio_finisher,
            sense_updater, stager, provision, sense_modifier,
            canceller, deleter
        ]

        logging.info("Starting all daemon threads")
        for daemon in daemons:
            thread = daemon.start(self.lock)
            if thread:
                self.threads.append(thread)

        frontend_process = multiprocessing.Process(target=self.run_server, args=(self.port,), name="Frontend")
        frontend_process.start()
        
        logging.info("All daemons and frontend started successfully")

    def stop(self) -> None:
        logging.info("Stopping DMM and all daemons")
        self.running = False
        
        for thread in self.threads:
            if thread.is_alive():
                thread.join(timeout=10)
        
        logging.info("DMM stopped successfully")

def main():
    argparser = argparse.ArgumentParser()
    argparser.add_argument("--config", help="Path to the configuration file")

    args = argparser.parse_args()

    if args.config:
        os.environ["DMM_CONFIG"] = args.config

    logging.info("Starting DMM")
    dmm = DMM()
    dmm.start()
        

if __name__ == "__main__":
    main()