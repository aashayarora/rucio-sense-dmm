from sqlmodel import Field, Relationship
from typing import List, Optional
import logging

from dmm.models.base import *

class Site(ModelBase, table=True):
    name: str = Field(primary_key=True)
    sense_uri: Optional[str] = Field(default=None)
    query_url: Optional[str] = Field(default=None)
    
    endpoints: List["Endpoint"] = Relationship(back_populates='site')
    
    site_request_src: List["Request"] = Relationship(back_populates='src_site', sa_relationship_kwargs={"foreign_keys": "[Request.src_site_]"}) 
    site_request_dst: List["Request"] = Relationship(back_populates='dst_site', sa_relationship_kwargs={"foreign_keys": "[Request.dst_site_]"}) 

    def __init__(self, **kwargs):
        super(Site, self).__init__(**kwargs)

    def __eq__(self, other):
        if not isinstance(other, Site):
            return NotImplemented
        return self.name == other.name

    @classmethod
    def from_name(cls, name, attr=None, session=None):
        logging.debug(f"SITE QUERY: sites from name: {name}")
        query = session.query(cls).filter(cls.name == name).first()
        return query