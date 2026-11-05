from dmm.daemons.base import DaemonBase
from dmm.db.session import databased
from dmm.core.allocation import refresh_all_sites
from dmm.core.rucio import rucio_unreachable

class RefreshSiteDBDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
    
    def process(self, **kwargs):
        self.run_once(**kwargs)

    def waiting_for(self):
        return "rucio" if rucio_unreachable(self.kwargs.get("client")) else None

    @databased
    def run_once(self, client=None, session=None) -> None:
        """
        Refresh sites from Rucio and SENSE.
        Delegates to core/allocation.refresh_all_sites for the actual logic.
        """
        refresh_all_sites(client, session)