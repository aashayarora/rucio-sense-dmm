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
        logging.debug(f"Prometheus query {query_addr}: {json.dumps(query_dict)}")
        try:
            response = requests.get(
                query_addr,
                params=query_dict,
                auth=(self.prometheus_user, self.prometheus_pass),
                timeout=15,
            )
            logging.debug(f"Prometheus response {response.status_code}: {response.text}")
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logging.error(f"Prometheus query request failed for {query_addr}: {e}", exc_info=True)
            raise
        except ValueError as e:
            logging.error(f"Prometheus query returned invalid JSON for {query_addr}: {e}", exc_info=True)
            raise
    
    @staticmethod
    def get_val_from_response(response):
        try:
            return response["data"]["result"][0]["value"][1]
        except (KeyError, IndexError, TypeError) as e:
            raise ValueError(f"Invalid Prometheus response format: {response}") from e
                
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
        if response.get("status") == "success":
            for metric in response.get("data", {}).get("result", []):
                metric_data = metric.get("metric", {}) if isinstance(metric, dict) else {}
                address = metric_data.get("address")
                if address and re.match(ipv6_pattern, address):
                    interfaces.append(
                        (
                            metric_data.get("device"),
                            metric_data.get("instance"),
                            metric_data.get("job"),
                            metric_data.get("sitename"),
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
            if response.get("status") == "success" and response.get("data", {}).get("result"):
                bytes_at_t = self.get_val_from_response(response)
            else:
                raise ValueError(f"query {metric} failed or returned empty result")
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

    def submit_job_query(self, rule_id, query_params=None) -> list:
        if query_params is None:
            query_params = []
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
        logging.debug(f"FTS Monit query for {rule_id} {query_addr}: {data_string}")
        try:
            response_obj = requests.get(query_addr, data=data_string, headers=self.headers, timeout=15)
            logging.debug(f"FTS Monit response for {rule_id} {response_obj.status_code}: {response_obj.text}")
            response_obj.raise_for_status()
            response = response_obj.json()
        except requests.RequestException as e:
            logging.error(f"FTS Monit request failed for rule {rule_id}: {e}", exc_info=True)
            raise
        except ValueError as e:
            logging.error(f"FTS Monit returned invalid JSON for rule {rule_id}: {e}", exc_info=True)
            raise
        hits = response.get("hits", {}).get("hits", [])
        timestamps = [hit.get("_source", {}).get("data") for hit in hits if hit.get("_source", {}).get("data") is not None]
        return timestamps