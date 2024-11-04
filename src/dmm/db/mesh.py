from dmm.db.base import *

class Mesh(BASE, ModelBase):
    id = Column(Integer, autoincrement=True, primary_key=True)
    site_1 = Column(String(255), ForeignKey('site.name'))
    site_2 = Column(String(255), ForeignKey('site.name'))
    vlan_range_start = Column(Integer())
    vlan_range_end = Column(Integer())
    maximum_bandwidth = Column(Integer())

    site1 = relationship("Site", foreign_keys=[site_1])
    site2 = relationship("Site", foreign_keys=[site_2])

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @classmethod
    def vlan_range(cls, site_1, site_2, session=None):
        mesh = session.query(cls).filter(
            or_(cls.site1 == site_1, cls.site1 == site_2),
            or_(cls.site2 == site_1, cls.site2 == site_2)
        ).first()
        if not mesh:
            return None
        if mesh.vlan_range_start == -1 or mesh.vlan_range_end == -1:
            return "any"
        return f"{mesh.vlan_range_start}-{mesh.vlan_range_end}"
       
    @classmethod
    def max_bandwidth(cls, site, session=None):
        meshes = session.query(cls).filter(
            or_(cls.site1 == site, cls.site2 == site)
        ).all()
        if not meshes:
            return None
        return max(mesh.maximum_bandwidth for mesh in meshes)