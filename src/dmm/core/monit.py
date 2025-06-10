import json
import requests
import re
import logging

from dmm.core.config import config_get

# Helper functions

class PrometheusUtils:
    def __init__(self):
        self.prometheus_user = config_get("prometheus", "user")
        self.prometheus_pass = config_get("prometheus", "password")
        self.prometheus_host = config_get("prometheus", "host")

    def submit_query(self, query_dict) -> dict:
        endpoint = "api/v1/query"
        query_addr = f"{self.prometheus_host}/{endpoint}"
        return requests.get(query_addr, params=query_dict, auth=(self.prometheus_user, self.prometheus_pass)).json()
    
    @staticmethod
    def get_val_from_response(response):
        return response["data"]["result"][0]["value"][1]
                
    def get_interfaces(self, ipv6) -> str:
        """
        Gets all interfaces with ipv6 addresses matching the given ipv6
        """
        #Change ipv6_pattern if needed
        ipv6_pattern = f"{ipv6[:-3]}[0-9a-fA-F]{{1,4}}"
        query = f"node_network_address_info{{address=~'{ipv6_pattern}'}}"
        response = self.submit_query({"query": query})

        interfaces = []
        
        #If response is a successful transfer and matches the given ipv6,
        #adds its interface data to the list
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
        """
        Returns the total number of bytes transmitted from a given Rucio RSE via a given
        ipv6 address
        """
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

    
class FTSMonitUtils:
    def __init__(self):
        self.fts_host = config_get("fts", "monit_host")
        self.fts_token = config_get("fts", "monit_auth_token")
        self.headers = {"Authorization": f"Bearer {self.fts_token}", "Content-Type": "application/json"}

    @staticmethod
    def get_val_from_response(response):
        return response["hits"]["hits"][0]["_source"]["data"]

    def submit_job_query(self, rule_id, query_params=[]) -> list:
        endpoint = "api/datasources/proxy/9233/monit_prod_fts_enr_complete*/_search"
        query_addr = f"{self.fts_host}/{endpoint}"
        data = {
            "size": 10,
            "query":{
                "bool":{
                    "filter":[{
                        "query_string": {
                            "analyze_wildcard": "true",
                            "query": f"data.file_metadata.rule_id:{rule_id}"
                        }
                    }]
                }
            },
            "_source": query_params
        }
        data_string = json.dumps(data)
        response = requests.get(query_addr, data=data_string, headers=self.headers).json()
        timestamps = [hit["_source"]["data"] for hit in response["hits"]["hits"]]
        return timestamps