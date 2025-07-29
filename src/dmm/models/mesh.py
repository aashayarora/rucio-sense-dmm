from sqlmodel import Field, Relationship, or_
from typing import Optional
import logging

from dmm.models.base import *

class Mesh(ModelBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    site_1: Optional[str] = Field(default=None, foreign_key='site.name')
    site_2: Optional[str] = Field(default=None, foreign_key='site.name')
    vlan_range_start: Optional[int] = Field(default=None)
    vlan_range_end: Optional[int] = Field(default=None)
    link_capacity: Optional[int] = Field(default=None)

    site1: Optional["Site"] = Relationship(sa_relationship_kwargs={"foreign_keys": "[Mesh.site_1]"})
    site2: Optional["Site"] = Relationship(sa_relationship_kwargs={"foreign_keys": "[Mesh.site_2]"})

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @classmethod
    def vlan_range(cls, site_1, site_2, session=None):
        site_1_name = site_1.name if hasattr(site_1, 'name') else site_1
        site_2_name = site_2.name if hasattr(site_2, 'name') else site_2

        logging.debug(f"MESH QUERY: checking if vlan range defined between {site_1_name} and {site_2_name}")
        mesh = session.query(cls).filter(
            or_(cls.site_1 == site_1_name, cls.site_1 == site_2_name),
            or_(cls.site_2 == site_1_name, cls.site_2 == site_2_name)
        ).first()
        if not mesh:
            return None
        if mesh.vlan_range_start == -1 or mesh.vlan_range_end == -1:
            return "any"
        return f"{mesh.vlan_range_start}-{mesh.vlan_range_end}"

    @classmethod
    def max_bandwidth(cls, site, session=None):
        # Convert Site object to name if needed
        site_name = site.name if hasattr(site, 'name') else site
        
        logging.debug(f"MESH QUERY: checking if max bandwidth defined for {site_name}")
        mesh = session.query(cls).filter(
            or_(cls.site_1 == site_name, cls.site_2 == site_name)
        ).first()
        if not mesh:
            return None
        return mesh.link_capacity