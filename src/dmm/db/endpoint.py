from dmm.db.base import *
from dmm.db.request import Request

class Endpoint(BASE, ModelBase):
    id = Column(Integer, autoincrement=True, primary_key=True)
    site_name = Column(String(255), ForeignKey('site.name'))
    ip_block = Column(String(255), unique=True)
    hostname = Column(String(255), unique=True)
    in_use = Column(Boolean)

    site = relationship('Site', back_populates='endpoints')

    ep_request_src = relationship('Request', back_populates='src_endpoint', foreign_keys=[Request.src_endpoint_])
    ep_request_dst = relationship('Request', back_populates='dst_endpoint', foreign_keys=[Request.dst_endpoint_])

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @classmethod
    def all(cls, session=None):
        return [ep for ep in session.query(cls).all()]
    
    @classmethod
    def from_hostname(cls, hostname, session=None):
        return session.query(cls).filter(cls.hostname == hostname).first()
    
    @classmethod
    def from_site(cls, site_name, session=None):
        return session.query(cls).filter(cls.site_name == site_name).all()
    
    @classmethod
    def for_rule(cls, site_name, ip_block, session=None):
        return session.query(cls).filter(cls.site_name == site_name, cls.ip_block == ip_block).first()

    def mark_inuse(self, in_use, session=None):
        self.in_use = in_use
        self.save(session)    