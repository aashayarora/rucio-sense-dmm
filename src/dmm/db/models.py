from sqlalchemy import Column, Integer, String, Float, JSON, DateTime, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base, declared_attr
from sqlalchemy.orm import relationship
from datetime import datetime

BASE = declarative_base()

class ModelBase(object):
    def __setitem__(self, key, value):
        setattr(self, key, value)
    def __getitem__(self, key):
        return getattr(self, key)
    @declared_attr
    def created_at(cls):
        return Column("created_at", DateTime, default=datetime.utcnow)
    @declared_attr
    def updated_at(cls):
        return Column("updated_at", DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    def save(self, session=None):
        """Save this object"""
        session.add(self)
        session.commit()
    def delete(self, session=None):
        """Delete this object"""
        session.delete(self)
    def update(self, values, session=None):
        """dict.update() behaviour."""
        for k, v in values.items():
            self[k] = v

# DMM Data Structures
class Request(BASE, ModelBase):
    __tablename__ = "requests"
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
    bytes_at_t = Column(JSON())

    def __init__(self, **kwargs):
        super(Request, self).__init__(**kwargs)

class Site(BASE, ModelBase):
    __tablename__ = "sites"
    name = Column(String(255), primary_key=True)
    sense_uri = Column(String(255))
    query_url = Column(String(255))
    endpoints = relationship('Endpoint', back_populates='site', cascade='all, delete-orphan')

    def __init__(self, **kwargs):
        super(Site, self).__init__(**kwargs)

class Endpoint(BASE, ModelBase):
    __tablename__ = "endpoints"
    id = Column(Integer(), autoincrement=True, primary_key=True)
    site = relationship('Site', back_populates='endpoints')
    site_name = Column(String(255), ForeignKey('sites.name'))
    ip_block = Column(String(255))
    hostname = Column(String(255))

    def __init__(self, **kwargs):
        super(Endpoint, self).__init__(**kwargs)

class Mesh(BASE, ModelBase):
    __tablename__ = 'mesh'
    id = Column(Integer, autoincrement=True, primary_key=True)
    site_1 = Column(String(255), ForeignKey('sites.name'))
    site_2 = Column(String(255), ForeignKey('sites.name'))
    vlan_range_start = Column(Integer())
    vlan_range_end = Column(Integer())
    max_bandwidth = Column(Integer())

    # Define relationships
    site1 = relationship("Site", foreign_keys=[site_1])
    site2 = relationship("Site", foreign_keys=[site_2])

    def __init__(self, **kwargs):
        super(Mesh, self).__init__(**kwargs)

# class Throughput(BASE, ModelBase):
#     __tablename__ = "throughput"
#     ipv6 = Column(String(255), primary_key=True)
#     interval_1 = Column(Integer())
#     interval_2 = Column(Integer())
#     interval_3 = Column(Integer())
#     interval_4 = Column(Integer())
#     interval_5 = Column(Integer())

#     def __init__(self, **kwargs):
#         super(Throughput, self).__init__(**kwargs)