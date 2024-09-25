from dmm.daemons.base import DaemonBase
from dmm.utils.monit import PrometheusUtils
from datetime import datetime, timedelta

from dmm.db.request import Request

from dmm.db.session import databased

class MonitDaemon(DaemonBase, PrometheusUtils):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        PrometheusUtils.__init__(self)

    @databased
    def process(self, session=None):
        reqs = Request.from_status(["PROVISIONED"], session=session)
        for req in reqs:
            timestamp = round(datetime.timestamp(datetime.now()))
            bytes_now = self.get_all_bytes_at_t(timestamp, req.src_ipv6_block)
            if req.prometheus_bytes is None:
                req.update_prometheus_bytes(bytes_now, session=session)
                continue
            throughput = round((bytes_now - req.prometheus_bytes) / self.frequency / 1024 / 1024 * 8, 2)
            req.update_prometheus_throughput(throughput, session=session)
            req.update_prometheus_bytes(bytes_now, session=session)

            if (req.prometheus_throughput is not None):
                if(req.prometheus_throughput < 0.8 * req.bandwidth):
                    req.update_health("BAD")
                else:
                    req.update_health("GOOD")
    