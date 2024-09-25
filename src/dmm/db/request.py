from dmm.db.base import *

class Request(BASE, ModelBase):
    rule_id = Column(String(255), primary_key=True)
    transfer_status = Column(String(255))
    src_site = Column(String(255))
    src_ipv6_block = Column(String(255))
    src_url = Column(String(255))
    dst_site = Column(String(255))
    dst_ipv6_block = Column(String(255))
    dst_url = Column(String(255))
    priority = Column(Integer())
    modified_priority = Column(Integer())
    max_bandwidth = Column(Float())
    bandwidth = Column(Float())
    sense_uuid = Column(String(255))
    sense_circuit_status = Column(String(255))
    fts_modified = Column(Boolean())
    sense_provisioned_at = Column(DateTime())
    prometheus_throughput = Column(Float())
    prometheus_bytes = Column(Float())
    health = Column(String(255))

    def __init__(self, **kwargs):
        super(Request, self).__init__(**kwargs)

    def __eq__(self, other):
        return self.rule_id == other.rule_id

    @classmethod
    def from_status(cls, status=None, session=None):
        return [req for req in session.query(cls).filter(cls.transfer_status.in_(status)).all()]

    @classmethod
    def from_id(cls, rule_id, session=None):
        return session.query(cls).filter(cls.rule_id == rule_id).first()
    
    @classmethod
    def get_all(cls, session=None):
        return [req for req in session.query(cls).all()]
    
    def mark_as(self, status, session=None):
        self.transfer_status = status 
        self.fts_modified = False
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
    
    def mark_fts_modified(self, session=None):
        self.fts_modified = True
        self.save(session)

    def update_prometheus_bytes(self, prom_bytes, session=None):
        self.prometheus_bytes = prom_bytes
        self.save(session)

    def update_prometheus_throughput(self, throughput, session=None):
        self.prometheus_throughput = throughput
        self.save(session)

    def update_request_health(self, health, session=None):
        self.health = health
        self.save(session)