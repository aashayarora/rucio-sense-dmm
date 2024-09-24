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

from dmm.daemons.core.sites import RefreshSiteDBDaemon

from dmm.daemons.rucio.initializer import RucioInitDaemon
from dmm.daemons.rucio.modifier import RucioModifierDaemon
from dmm.daemons.rucio.finisher import RucioFinisherDaemon

from dmm.daemons.fts.modifier import FTSModifierDaemon

from dmm.daemons.sense.status_updater import SENSEStatusUpdaterDaemon
from dmm.daemons.sense.stager import SENSEStagerDaemon
from dmm.daemons.sense.provisioner import SENSEProvisionerDaemon
from dmm.daemons.sense.modifier import SENSEModifierDaemon
from dmm.daemons.sense.canceller import SENSECancellerDaemon
from dmm.daemons.sense.deleter import SENSEDeleterDaemon

from dmm.daemons.core.allocator import AllocatorDaemon
from dmm.daemons.core.decider import DeciderDaemon


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
        
        try:
            self.rucio_client = Client()
        except Exception as e:
            logging.error(f"Failed to initialize Rucio client: {e}")
            raise "Failed to initialize Rucio client, exiting..."


    def start(self):
        logging.info("Starting Daemons")

        sitedb = RefreshSiteDBDaemon()
        
        allocator = AllocatorDaemon()
        decider = DeciderDaemon()

        fts = FTSModifierDaemon()
        
        rucio_init = RucioInitDaemon(kwargs={"client": self.rucio_client})
        rucio_modifier = RucioModifierDaemon(kwargs={"client": self.rucio_client})
        rucio_finisher = RucioFinisherDaemon(kwargs={"client": self.rucio_client})
        
        sense_updater = SENSEStatusUpdaterDaemon()
        stager = SENSEStagerDaemon()
        provision = SENSEProvisionerDaemon()
        sense_modifier = SENSEModifierDaemon()
        canceller = SENSECancellerDaemon()
        deleter = SENSEDeleterDaemon()

        sitedb.start(self.database_builder_daemon_frequency, self.lock)
        fts.start(self.fts_daemon_frequency, self.lock)
        allocator.start(self.dmm_daemon_frequency, self.lock)
        decider.start(self.dmm_daemon_frequency, self.lock)
        rucio_init.start(self.rucio_daemon_frequency, self.lock)
        rucio_modifier.start(self.rucio_daemon_frequency, self.lock)
        rucio_finisher.start(self.rucio_daemon_frequency, self.lock)
        sense_updater.start(self.sense_daemon_frequency, self.lock)
        stager.start(self.sense_daemon_frequency, self.lock)
        provision.start(self.sense_daemon_frequency, self.lock)
        sense_modifier.start(self.sense_daemon_frequency, self.lock)
        canceller.start(self.sense_daemon_frequency, self.lock)
        deleter.start(self.sense_daemon_frequency, self.lock)

        try:
            serve(frontend_app, port=self.port)
        except:
            serve(frontend_app, port=8080)

def main():
    logging.info("Starting DMM")
    dmm = DMM()
    dmm.start()