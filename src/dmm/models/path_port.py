from datetime import datetime
from typing import Optional

from sqlalchemy import Column, JSON
from sqlmodel import Field, select

from dmm.models.base import *


class PathPort(ModelBase, table=True):
    """
    A switch port that a DMM circuit crosses, with what SENSE-O's model says it carries.

    Site pairs share ports the mesh knows nothing about: every Caltech circuit
    leaves through the same port of Caltech's switch, whichever site it goes to,
    and Caltech's circuits to UCSD and to SDSC cross the same CENIC trunks. The
    decider limits the circuits crossing each port to its capacity, less what
    circuits that aren't DMM's hold on it.

    Rows are refreshed from GET /model when a request is staged (see
    dmm.core.sense_model.read_ports).
    """
    uri: str = Field(primary_key=True)
    capacity_mbps: Optional[float] = Field(default=None)
    # Held on the port by circuits that aren't DMM's (and capacity the domain
    # reports as used without itemising it) when the model was last read.
    foreign_mbps: Optional[float] = Field(default=None)
    # Who holds the foreign_mbps: {instance uuid, or the hold's name: Mbps}.
    foreign_holds: Optional[dict] = Field(default=None, sa_column=Column(JSON, nullable=True))
    read_at: Optional[datetime] = Field(default=None)

    @classmethod
    def get_by_uris(cls, uris, session=None) -> dict:
        uris = list(uris)
        if not uris:
            return {}
        rows = session.exec(select(cls).where(cls.uri.in_(uris))).all()
        return {row.uri: row for row in rows}
