from dmm.db.base import *
from dmm.db.request import Request

class Site(BASE, ModelBase):
    name = Column(String(255), primary_key=True)
    sense_uri = Column(String(255))
    query_url = Column(String(255))
    
    endpoints = relationship('Endpoint', back_populates='site', cascade='all, delete-orphan')

    site_request_src = relationship('Request', back_populates='src_site', foreign_keys=[Request.src_site_])
    site_request_dst = relationship('Request', back_populates='dst_site', foreign_keys=[Request.dst_site_])

    def __init__(self, **kwargs):
        super(Site, self).__init__(**kwargs)

    def __eq__(self, other):
        return self.name == other.name

    @classmethod
    def from_name(cls, name, attr=None, session=None):
        if attr:
            query = session.query(cls).filter(cls.name == name).first()
            return getattr(query, attr)
        else:
            return session.query(cls).filter(cls.name == name).first()