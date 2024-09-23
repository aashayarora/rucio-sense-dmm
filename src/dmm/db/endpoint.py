from dmm.db.base import *
from sqlalchemy.orm import relationship

class Endpoint(BASE, ModelBase):
    id = Column(Integer(), autoincrement=True, primary_key=True)
    site_name = Column(String(255), ForeignKey('site.name'))
    ip_block = Column(String(255))
    hostname = Column(String(255))

    site = relationship('Site', back_populates='endpoints')

    def __init__(self, **kwargs):
        super(Endpoint, self).__init__(**kwargs)

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