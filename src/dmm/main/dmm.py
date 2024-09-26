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
from dmm.daemons.core.monit import MonitDaemon

from dmm.frontend.frontend import frontend_app

class DMM:
    def __init__(self):
        self.port = config_get_int("dmm", "port")

        self.rucio_frequency = config_get_int("daemons", "rucio", default=60)
        self.fts_frequency = config_get_int("daemons", "fts", default=60)
        self.dmm_frequency = config_get_int("daemons", "dmm", default=60)
        self.sense_frequency = config_get_int("daemons", "sense", default=60)
        self.monit_frequency = config_get_int("daemons", "monit", default=60)
        self.sites_frequency = config_get_int("daemons", "db", default=7200)
        
        self.lock = Lock()
        
        try:
            self.rucio_client = Client()
        except Exception as e:
            logging.error(f"Failed to initialize Rucio client: {e}")
            raise "Failed to initialize Rucio client, exiting..."


    def start(self):
        logging.info("Starting Daemons")
        sitedb = RefreshSiteDBDaemon(frequency=self.sites_frequency)
        
        allocator = AllocatorDaemon(frequency=self.dmm_frequency)
        decider = DeciderDaemon(frequency=self.dmm_frequency)

        monit = MonitDaemon(frequency=self.monit_frequency)
        fts = FTSModifierDaemon(frequency=self.fts_frequency)
        
        rucio_init = RucioInitDaemon(frequency=self.rucio_frequency, kwargs={"client": self.rucio_client})
        rucio_modifier = RucioModifierDaemon(frequency=self.rucio_frequency, kwargs={"client": self.rucio_client})
        rucio_finisher = RucioFinisherDaemon(frequency=self.rucio_frequency, kwargs={"client": self.rucio_client})
        
        sense_updater = SENSEStatusUpdaterDaemon(frequency=self.sense_frequency)
        stager = SENSEStagerDaemon(frequency=self.sense_frequency)
        provision = SENSEProvisionerDaemon(frequency=self.sense_frequency)
        sense_modifier = SENSEModifierDaemon(frequency=self.sense_frequency)
        canceller = SENSECancellerDaemon(frequency=self.sense_frequency)
        deleter = SENSEDeleterDaemon(frequency=self.sense_frequency)

        sitedb.start(self.lock)
        fts.start(self.lock)
        allocator.start(self.lock)
        decider.start(self.lock)
        monit.start(self.lock)
        rucio_init.start(self.lock)
        rucio_modifier.start(self.lock)
        rucio_finisher.start(self.lock)
        sense_updater.start(self.lock)
        stager.start(self.lock)
        provision.start(self.lock)
        sense_modifier.start(self.lock)
        canceller.start(self.lock)
        deleter.start(self.lock)

        try:
            serve(frontend_app, port=self.port)
        except:
            logging.error(f"Failed to start frontend on {self.port}, trying default port 31601")
            serve(frontend_app, port=31601)

def main():
    logging.info("Starting DMM")
    dmm = DMM()
    dmm.start()