from sqlmodel import Field, Relationship
from typing import Optional
from dmm.db.base import *

class Request(ModelBase, table=True):
    rule_id: str = Field(primary_key=True)
    transfer_status: Optional[str] = Field(default=None)
    priority: Optional[int] = Field(default=None)
    modified_priority: Optional[int] = Field(default=None)
    max_bandwidth: Optional[float] = Field(default=None)
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

    src_site: Optional["Site"] = Relationship(back_populates='site_request_src', sa_relationship_kwargs={"foreign_keys": "[Request.src_site_]"})
    dst_site: Optional["Site"] = Relationship(back_populates='site_request_dst', sa_relationship_kwargs={"foreign_keys": "[Request.dst_site_]"})
    src_endpoint: Optional["Endpoint"] = Relationship(back_populates='ep_request_src', sa_relationship_kwargs={"foreign_keys": "[Request.src_endpoint_]"})
    dst_endpoint: Optional["Endpoint"] = Relationship(back_populates='ep_request_dst', sa_relationship_kwargs={"foreign_keys": "[Request.dst_endpoint_]"})

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def __eq__(self, other):
        return self.rule_id == other.rule_id

    @classmethod
    def from_status(cls, status=None, session=None):
        return [req for req in session.query(cls).filter(cls.transfer_status.in_(status)).all()]

    @classmethod
    def from_id(cls, rule_id, session=None):
        return session.query(cls).filter(cls.rule_id == rule_id).first()
    
    def mark_as(self, status, session=None):
        self.transfer_status = status 
        self.save(session)

    def update_bandwidth(self, bandwidth, session=None):
        self.bandwidth = bandwidth
        self.save(session)

    def update_priority(self, priority, session=None):
        self.priority = priority
        self.modified_priority = priority
        self.save(session)

    def update_sense_circuit_status(self, status, session=None):
        self.sense_circuit_status = status
        self.save(session)
    
    def update_fts_limit_current(self, limit, session=None):
        self.fts_limit_current = limit
        self.save(session)

    def update_fts_limit_desired(self, limit, session=None):
        self.fts_limit_desired = limit
        self.save(session)

    def update_prometheus_bytes(self, prom_bytes, session=None):
        self.prometheus_bytes = prom_bytes
        self.save(session)

    def update_prometheus_throughput(self, throughput, session=None):
        self.prometheus_throughput = throughput
        self.save(session)

    def update_health(self, health, session=None):
        self.health = health
        self.save(session)