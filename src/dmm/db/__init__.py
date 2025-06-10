from dmm.models.base import *
from dmm.models.request import Request
from dmm.models.site import Site
from dmm.models.endpoint import Endpoint
from dmm.models.mesh import Mesh

from dmm.db.session import get_engine

engine = get_engine()
SQLModel.metadata.create_all(engine)