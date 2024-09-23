import logging
import sys
import argparse

argparser = argparse.ArgumentParser()
argparser.add_argument("--log-level", default="debug", help="Set the log level")

args = argparser.parse_args()

logging.basicConfig(
    format="(%(threadName)s) [%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%m-%d-%Y %H:%M:%S %p",
    level=getattr(logging, args.log_level.upper()),
    handlers=[logging.FileHandler(filename="dmm.log"), logging.StreamHandler(sys.stdout)]
)

from multiprocessing import Lock
from waitress import serve

from rucio.client import Client
from dmm.utils.config import config_get_int, config_get_bool
from dmm.utils.orchestrator import fork

from dmm.daemons.rucio import RucioInitDaemon, RucioModifierDaemon, RucioFinisherDaemon
from dmm.daemons.fts import FTSModifierDaemon
from dmm.daemons.sense import status_updater, stager, provision, sense_modifier, canceller, deleter
from dmm.daemons.core import AllocatorDaemon, DeciderDaemon
from dmm.daemons.sites import RefreshSiteDBDaemon
from dmm.frontend.frontend import frontend_app

class DMM:
    def __init__(self):
        self.port = config_get_int("dmm", "port")
        self.debug_mode = config_get_bool("dmm", "debug_mode", default=False)

        if self.debug_mode:
            logging.info("Running in debug mode, sense will not be used")
        else:
            logging.info("Running in production mode, sense will be used")

        self.rucio_daemon_frequency = config_get_int("daemons", "rucio", default=60)
        self.fts_daemon_frequency = config_get_int("daemons", "fts", default=60)
        self.dmm_daemon_frequency = config_get_int("daemons", "dmm", default=60)
        self.sense_daemon_frequency = config_get_int("daemons", "sense", default=60)
        self.database_builder_daemon_frequency = config_get_int("daemons", "db", default=7200)
        
        self.lock = Lock()
        self.use_rucio = False
        
        try:
            self.rucio_client = Client()
            self.use_rucio = True
        except Exception as e:
            logging.error(f"Rucio not available, running in rucio-free mode")

    def start(self):
        logging.info("Starting Daemons")

        allocator = AllocatorDaemon()
        decider = DeciderDaemon()
        sitedb = RefreshSiteDBDaemon()
        fts = FTSModifierDaemon()
        rucio_init = RucioInitDaemon(kwargs={"client": self.rucio_client})
        rucio_modifier = RucioModifierDaemon(kwargs={"client": self.rucio_client})
        rucio_finisher = RucioFinisherDaemon(kwargs={"client": self.rucio_client})

        sitedb.start(self.database_builder_daemon_frequency, self.lock)
        fts.start(self.fts_daemon_frequency, self.lock)
        allocator.start(self.dmm_daemon_frequency, self.lock)
        decider.start(self.dmm_daemon_frequency, self.lock)
        rucio_init.start(self.rucio_daemon_frequency, self.lock)
        rucio_modifier.start(self.rucio_daemon_frequency, self.lock)
        rucio_finisher.start(self.rucio_daemon_frequency, self.lock)

        sense_daemons = {
            status_updater: {"debug_mode": self.debug_mode},
            stager: {"debug_mode": self.debug_mode}, 
            provision: {"debug_mode": self.debug_mode}, 
            sense_modifier: {"debug_mode": self.debug_mode},
            canceller: {"debug_mode": self.debug_mode},
            deleter: {"debug_mode": self.debug_mode}
        }
        fork(self.sense_daemon_frequency, self.lock, sense_daemons)
        
        try:
            serve(frontend_app, port=self.port)
        except:
            serve(frontend_app, port=8080)

def main():
    logging.info("Starting DMM")
    dmm = DMM()
    dmm.start()