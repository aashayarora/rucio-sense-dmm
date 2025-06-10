import requests
import re
import logging

from dmm.daemons.base import DaemonBase
from datetime import datetime

from dmm.models.request import Request
from dmm.db.session import databased

from dmm.core.config import config_get

class MonitDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        self.prometheus_user = config_get("prometheus", "user")
        self.prometheus_pass = config_get("prometheus", "password")
        self.prometheus_host = config_get("prometheus", "host")

    @databased
    def process(self, session=None):
        reqs = Request.from_status(["PROVISIONED"], session=session)
        current_timestamp = round(datetime.timestamp(datetime.now()))

        for req in reqs:
            if req.sense_provisioned_at is None:
                continue

            bytes_now = self.get_all_bytes_at_t(current_timestamp, req.src_endpoint.ip_range)

            if req.prometheus_bytes is None:
                req.update_prometheus_bytes(bytes_now, session=session)
                continue

            throughput = self.calculate_throughput(bytes_now, req.prometheus_bytes)
            req.update_prometheus_throughput(throughput, session=session)
            req.update_prometheus_bytes(bytes_now, session=session)

            health_status = self.determine_health_status(throughput, req.bandwidth)
            req.update_health(health_status, session=session)

    def calculate_throughput(self, bytes_now, prometheus_bytes):
        return round((bytes_now - prometheus_bytes) / self.frequency / 1024 / 1024 / 1024 * 8, 2)

    def determine_health_status(self, throughput, bandwidth):
        return "0" if throughput < 0.8 * bandwidth else "1"

    def submit_query(self, query_dict) -> dict:
        endpoint = "api/v1/query"
        query_addr = f"{self.prometheus_host}/{endpoint}"
        return requests.get(query_addr, params=query_dict, auth=(self.prometheus_user, self.prometheus_pass)).json()
    
    @staticmethod
    def get_val_from_response(response):
        return response["data"]["result"][0]["value"][1]
                
    def get_interfaces(self, ipv6) -> str:
        ipv6_pattern = f"{ipv6[:-3]}[0-9a-fA-F]{{1,4}}"
        query = f"node_network_address_info{{address=~'{ipv6_pattern}'}}"
        response = self.submit_query({"query": query})

        interfaces = []
        if response["status"] == "success":        
            for metric in response["data"]["result"]:
                if re.match(ipv6_pattern, metric["metric"]["address"]):
                    interfaces.append(
                        (
                            metric["metric"]["device"], 
                            metric["metric"]["instance"], 
                            metric["metric"]["job"], 
                            metric["metric"]["sitename"]
                        )
                    )
        return interfaces

    def get_all_bytes_at_t(self, time, ipv6) -> float:
        transfers = self.get_interfaces(ipv6) 
        total_bytes = 0
        for transfer in transfers:
            device, instance, job, sitename = transfer[0], transfer[1], transfer[2], transfer[3]
            query_params = f"device=\"{device}\",instance=\"{instance}\",job=\"{job}\",sitename=\"{sitename}\""
            metric = f"node_network_transmit_bytes_total{{{query_params}}}"

            response = self.submit_query({"query": metric, "time": time})
            if response["status"] == "success":
                bytes_at_t = self.get_val_from_response(response)
            else:
                raise Exception(f"query {metric} failed")
            total_bytes += float(bytes_at_t)
        return total_bytes