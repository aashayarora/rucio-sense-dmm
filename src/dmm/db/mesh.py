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
        super(Mesh, self).__init__(**kwargs)

    @classmethod
    def vlan_range(cls, site_1, site_2, session=None):
        mesh = session.query(cls).filter(or_(cls.site1 == site_1, cls.site1 == site_2), or_(cls.site2 == site_1, cls.site2 == site_2)).first()
        vlan_range_start = mesh.vlan_range_start
        vlan_range_end = mesh.vlan_range_end
        if vlan_range_start == -1 or vlan_range_end == -1:
            return "any"
        else:
            return f"{vlan_range_start}-{vlan_range_end}"
       
    @classmethod
    def max_bandwidth(cls, site, session=None):
        mesh = session.query(cls).filter(or_(cls.site1 == site, cls.site2 == site)).all()
        bandwidths = {m.maximum_bandwidth for m in mesh}
        return max(bandwidths)