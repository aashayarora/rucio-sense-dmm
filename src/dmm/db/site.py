from dmm.db.base import *
from sqlalchemy.orm import relationship

class Site(BASE, ModelBase):
    name = Column(String(255), primary_key=True)
    sense_uri = Column(String(255))
    query_url = Column(String(255))

    endpoints = relationship('Endpoint', back_populates='site', cascade='all, delete-orphan')

    def __init__(self, **kwargs):
        super(Site, self).__init__(**kwargs)

    @classmethod
    def from_name(cls, name, attr=None, session=None):
        if attr:
            query = session.query(cls).filter(cls.name == name).first()
            return getattr(query, attr)
        else:
            return session.query(cls).filter(cls.name == name).first()