from sqlmodel import Field, Relationship
from typing import List, Optional

import logging

from dmm.models.base import *

class Endpoint(ModelBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    site_name: Optional[str] = Field(default=None, foreign_key='site.name')
    ip_range: Optional[str] = Field(default=None, unique=True)
    hostname: Optional[str] = Field(default=None)
    in_use: Optional[bool] = Field(default=None)

    site: Optional["Site"] = Relationship(back_populates='endpoints')
    
    ep_request_src: List["Request"] = Relationship(back_populates='src_endpoint', sa_relationship_kwargs={"foreign_keys": "[Request.src_endpoint_]"})
    ep_request_dst: List["Request"] = Relationship(back_populates='dst_endpoint', sa_relationship_kwargs={"foreign_keys": "[Request.dst_endpoint_]"})

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @classmethod
    def all(cls, session=None):
        return [ep for ep in session.query(cls).all()]
    
    @classmethod
    def from_iprange(cls, iprange, session=None):
        logging.debug(f"ENDPOINT QUERY: ip range {iprange}")
        return session.query(cls).filter(cls.ip_range == iprange).first()
    
    @classmethod
    def from_site(cls, site_name, session=None):
        logging.debug(f"ENDPOINT QUERY: site name {site_name}")
        return session.query(cls).filter(cls.site_name == site_name).all()
    
    @classmethod
    def for_rule(cls, site_name, ip_range, session=None):
        logging.debug(f"ENDPOINT QUERY: site name {site_name} and ip range {ip_range}")
        return session.query(cls).filter(cls.site_name == site_name, cls.ip_range == ip_range).first()

    def mark_inuse(self, in_use, session=None):
        logging.debug(f"Marking endpoint {self.ip_range} as {'inuse' if in_use else 'free'}")
        self.in_use = in_use
        self.save(session)