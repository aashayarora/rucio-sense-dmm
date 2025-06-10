from sqlmodel import Field, Relationship
from typing import Optional

import logging

from dmm.models.base import *

class Request(ModelBase, table=True):
    rule_id: str = Field(primary_key=True)
    transfer_status: Optional[str] = Field(default=None)
    priority: Optional[int] = Field(default=None)
    rule_size: Optional[float] = Field(default=None)
    modified_priority: Optional[int] = Field(default=None)
    available_bandwidth: Optional[float] = Field(default=None)
    bandwidth: Optional[float] = Field(default=None)
    sense_uuid: Optional[str] = Field(default=None)
    sense_circuit_status: Optional[str] = Field(default=None)
    fts_limit_current: Optional[int] = Field(default=0)
    fts_limit_desired: Optional[int] = Field(default=None)
    sense_provisioned_at: Optional[datetime] = Field(default=None)
    prometheus_throughput: Optional[float] = Field(default=None)
    prometheus_bytes: Optional[float] = Field(default=None)
    health: Optional[str] = Field(default=None)
    
    src_site_: Optional[str] = Field(default=None, foreign_key='site.name')
    dst_site_: Optional[str] = Field(default=None, foreign_key='site.name')
    src_endpoint_: Optional[int] = Field(default=None, foreign_key='endpoint.id')
    dst_endpoint_: Optional[int] = Field(default=None, foreign_key='endpoint.id')

    src_site: Optional["Site"] = Relationship(back_populates='site_request_src', 
                sa_relationship_kwargs={"foreign_keys": "[Request.src_site_]"})
    dst_site: Optional["Site"] = Relationship(back_populates='site_request_dst', 
                sa_relationship_kwargs={"foreign_keys": "[Request.dst_site_]"})
    src_endpoint: Optional["Endpoint"] = Relationship(back_populates='ep_request_src', 
                sa_relationship_kwargs={"foreign_keys": "[Request.src_endpoint_]"})
    dst_endpoint: Optional["Endpoint"] = Relationship(back_populates='ep_request_dst', 
                sa_relationship_kwargs={"foreign_keys": "[Request.dst_endpoint_]"})

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def __eq__(self, other):
        return self.rule_id == other.rule_id

    @classmethod
    def from_status(cls, status=None, session=None):
        logging.debug(f"REQUEST QUERY: requests from status: {status}")
        return [req for req in session.query(cls).filter(cls.transfer_status.in_(status)).all()]

    @classmethod
    def from_id(cls, rule_id, session=None):
        logging.debug(f"REQUEST QUERY: requests from rule_id: {rule_id}")
        return session.query(cls).filter(cls.rule_id == rule_id).first()
    
    def mark_as(self, status, session=None):
        logging.debug(f"REQUEST UPDATE: marking request {self.rule_id} as {status}")
        self.transfer_status = status 
        self.save(session)

    def update_available_bandwidth(self, bandwidth, session=None):
        logging.debug(f"REQUEST UPDATE: updating available bandwidth for request {self.rule_id} to {bandwidth}")
        self.available_bandwidth = bandwidth
        self.save(session)

    def update_sense_uuid(self, sense_uuid, session=None):
        logging.debug(f"REQUEST UPDATE: updating sense UUID for request {self.rule_id} to {sense_uuid}")
        self.sense_uuid = sense_uuid
        self.save(session)

    def update_bandwidth(self, bandwidth, session=None):
        logging.debug(f"REQUEST UPDATE: updating bandwidth for request {self.rule_id} to {bandwidth}")
        self.bandwidth = bandwidth
        self.save(session)

    def update_priority(self, priority, session=None):
        logging.debug(f"REQUEST UPDATE: updating priority for request {self.rule_id} to {priority}")
        self.priority = priority
        self.modified_priority = priority
        self.save(session)

    def update_sense_circuit_status(self, status, session=None):
        logging.debug(f"REQUEST UPDATE: updating sense circuit status for request {self.rule_id} to {status}")
        self.sense_circuit_status = status
        self.save(session)
    
    def update_fts_limit_current(self, limit, session=None):
        logging.debug(f"REQUEST UPDATE: updating fts limit current for request {self.rule_id} to {limit}")
        self.fts_limit_current = limit
        self.save(session)

    def update_fts_limit_desired(self, limit, session=None):
        logging.debug(f"REQUEST UPDATE: updating fts limit desired for request {self.rule_id} to {limit}")
        self.fts_limit_desired = limit
        self.save(session)

    def update_prometheus_bytes(self, prom_bytes, session=None):
        logging.debug(f"REQUEST UPDATE: updating prometheus bytes for request {self.rule_id} to {prom_bytes}")
        self.prometheus_bytes = prom_bytes
        self.save(session)

    def update_prometheus_throughput(self, throughput, session=None):
        logging.debug(f"REQUEST UPDATE: updating prometheus throughput for request {self.rule_id} to {throughput}")
        self.prometheus_throughput = throughput
        self.save(session)

    def update_health(self, health, session=None):
        self.health = health
        self.save(session)