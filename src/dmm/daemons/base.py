import logging
from multiprocessing import Process
from time import sleep
import os

class DaemonBase:
    def __init__(self, frequency, kwargs={}):
        self.frequency = frequency
        self.kwargs = kwargs
        self.pid = None

    def process(self):
        raise NotImplementedError("Subclasses must implement this method")
    
    def run_daemon(self, process, lock, **kwargs):
        while True:
            with lock:
                logging.debug(f"{self.__class__.__name__} acquired lock")
                try:
                    process(**kwargs)
                except Exception as e:
                    logging.error(f"Error in {self.__class__.__name__}: {e}")
            logging.debug(f"{self.__class__.__name__} released lock, sleeping for {self.frequency} seconds")
            sleep(self.frequency)

    def start(self, lock):
        logging.info(f"Starting {self.__class__.__name__}")
        try:
            proc = Process(target=self.run_daemon,
                        args=(self.process, lock),
                        kwargs=self.kwargs,
                        name=self.__class__.__name__)
            proc.start()
            self.pid = proc.pid
        except Exception as e:
            logging.error(f"Error starting {self.__class__.__name__}: {e}")

    def stop(self):
        logging.info(f"Stopping {self.__class__.__name__}")
        if self.pid:
            os.kill(self.pid, 9)