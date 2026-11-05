from dmm.daemons.base import DaemonBase


class RucioHeartbeatDaemon(DaemonBase):
    """
    Asks Rucio once a period whether it is answering.

    The answer is what the other Rucio daemons wait on and what /health reports
    as its rucio check. A cycle completes whether or not Rucio answered: this
    daemon's own liveness is only whether it is still asking.
    """
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)

    def process(self, **kwargs):
        self.run_once(**kwargs)

    def run_once(self, client=None):
        client.check()

    def status(self):
        return {"rucio": self.kwargs["client"].state()}
