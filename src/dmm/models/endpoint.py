from sqlmodel import Field, Relationship, select
from typing import List, Optional

import logging

from dmm.models.base import *

class Endpoint(ModelBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    site_name: Optional[str] = Field(default=None, foreign_key='site.name')
    ip_range: Optional[str] = Field(default=None, unique=True)
    protocol: Optional[str] = Field(default=None)
    hostname: Optional[str] = Field(default=None)
    is_allocated: Optional[bool] = Field(default=False)  # Renamed from in_use for clarity

    site: Optional["Site"] = Relationship(back_populates='endpoints')
    
    requests_as_source: List["Request"] = Relationship(
        back_populates='src_endpoint', 
        sa_relationship_kwargs={"foreign_keys": "[Request.src_endpoint_]"}
    )
    requests_as_destination: List["Request"] = Relationship(
        back_populates='dst_endpoint', 
        sa_relationship_kwargs={"foreign_keys": "[Request.dst_endpoint_]"}
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @classmethod
    def get_all(cls, session=None, use_lock: bool = True):
        statement = select(cls)
        if use_lock:
            statement = statement.with_for_update()
        return list(session.exec(statement).all())
    
    @classmethod
    def get_by_ip_range(cls, ip_range: str, session=None, use_lock: bool = True):
        statement = select(cls).where(cls.ip_range == ip_range)
        if use_lock:
            statement = statement.with_for_update()
        return session.exec(statement).first()
    
    @classmethod
    def get_by_site(cls, site_name: str, session=None, use_lock: bool = True):
        statement = select(cls).where(cls.site_name == site_name)
        if use_lock:
            statement = statement.with_for_update()
        return list(session.exec(statement).all())
    
    @classmethod
    def get_for_allocation(cls, site_name: str, ip_range: str, session=None):
        statement = (
            select(cls)
            .where(cls.site_name == site_name)
            .where(cls.ip_range == ip_range)
            .with_for_update()
        )
        return session.exec(statement).first()

    def set_allocated(self, is_allocated: bool, session=None):
        logging.info(f"Setting endpoint {self.ip_range} allocation status to {is_allocated}")
        self.is_allocated = is_allocated
        self.save(session)