from sqlmodel import Field, Relationship, select
from typing import List, Optional

from dmm.models.base import *

class Site(ModelBase, table=True):
    name: str = Field(primary_key=True)
    sense_uri: Optional[str] = Field(default=None)
    query_url: Optional[str] = Field(default=None)
    
    endpoints: List["Endpoint"] = Relationship(back_populates='site')
    
    requests_as_source: List["Request"] = Relationship(
        back_populates='src_site', 
        sa_relationship_kwargs={"foreign_keys": "[Request.src_site_]"}
    ) 
    requests_as_destination: List["Request"] = Relationship(
        back_populates='dst_site', 
        sa_relationship_kwargs={"foreign_keys": "[Request.dst_site_]"}
    ) 

    def __init__(self, **kwargs):
        super(Site, self).__init__(**kwargs)

    def __eq__(self, other):
        if not isinstance(other, Site):
            return NotImplemented
        return self.name == other.name

    @classmethod
    def get_by_name(cls, name: str, session=None, use_lock: bool = True):
        statement = select(cls).where(cls.name == name)
        if use_lock:
            statement = statement.with_for_update()
        return session.exec(statement).first()
