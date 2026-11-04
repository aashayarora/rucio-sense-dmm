import logging
import threading
from time import sleep, time
import os

from dmm.core.health import write_heartbeat

class DaemonBase:
    def __init__(self, frequency, kwargs=None):
        self.frequency = frequency
        self.kwargs = kwargs or {}
        self.thread = None
        self.running = True
        self.started_at = 0
        self.last_success = 0
        self.waiting = None

    def process(self):
        raise NotImplementedError("Subclasses must implement this method")
    
    def run_once(self, **kwargs):
        raise NotImplementedError("Subclasses must implement this method")

    def waiting_for(self):
        """
        What this daemon cannot run a cycle without right now, or None. Asked
        before every cycle; a daemon kept waiting is asked again each second
        and runs as soon as it may, rather than a whole period late.
        """
        return None

    def status(self):
        """Anything besides liveness this daemon publishes in its heartbeat."""
        return {}

    def _publish(self, running):
        write_heartbeat(self.__class__.__name__, self.frequency,
                        self.started_at, self.last_success, running,
                        waiting_for=self.waiting, **self.status())

    def _must_wait(self):
        """Whether this cycle has to wait, publishing any change in that."""
        waiting = self.waiting_for()
        if waiting != self.waiting:
            if waiting:
                logging.info(f"{self.__class__.__name__} waiting for {waiting}")
            else:
                logging.info(f"{self.__class__.__name__} no longer waiting for {self.waiting}")
            self.waiting = waiting
            self._publish(running=True)
        return bool(waiting)

    def run_daemon(self, process, lock, **kwargs):
        if self.frequency < 0:
            logging.info(f"frequency is set to negative, not starting the daemon.")
            self._publish(running=False)
            return

        self.started_at = time()
        self._publish(running=True)
        try:
            while self.running:
                if self._must_wait():
                    sleep(1)
                    continue
                try:
                    with lock:
                        # Asked again with the lock held: the wait for it is
                        # longest when a daemon ahead is hung on the very
                        # outage this one would be waiting out.
                        if self._must_wait():
                            continue
                        try:
                            process(**kwargs)
                            # Only a completed cycle counts. Staleness is
                            # measured from this, so bumping it on entry would
                            # make a wedged daemon look healthy.
                            self.last_success = time()
                            self._publish(running=True)
                        except Exception as e:
                            logging.error(f"Error in {self.__class__.__name__}: {e}", exc_info=True)
                except Exception as e:
                    logging.error(f"Unexpected error: {e}", exc_info=True)

                # Sleep in smaller intervals to allow for faster shutdown
                sleep_remaining = self.frequency
                while sleep_remaining > 0 and self.running:
                    sleep(min(1, sleep_remaining))
                    sleep_remaining -= 1
        finally:
            # Also covers the thread dying outside the loop, which nothing
            # else notices.
            self._publish(running=False)

    def start(self, lock):
        logging.info(f"Starting {self.__class__.__name__}")
        self._publish(running=False)
        try:
            self.thread = threading.Thread(
                target=self.run_daemon,
                args=(self.process, lock),
                kwargs=self.kwargs,
                name=self.__class__.__name__,
                daemon=True
            )
            self.thread.start()
            return self.thread
        except Exception as e:
            logging.error(f"Error starting {self.__class__.__name__}: {e}", exc_info=True)
            return None

    def stop(self):
        logging.info(f"Stopping {self.__class__.__name__}")
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=10)