from dmm.db.base import *
from dmm.db.request import Request
from dmm.db.site import Site
from dmm.db.endpoint import Endpoint
from dmm.db.mesh import Mesh
from dmm.db.session import get_engine

engine=get_engine()
BASE.metadata.create_all(bind=engine, checkfirst=True)