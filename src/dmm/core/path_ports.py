"""
The ports DMM's circuits cross, and what SENSE-O's model says each carries.

The mesh gives each site pair one capacity, but pairs share ports it knows
nothing about. Every Caltech circuit leaves through Caltech's
dellos10_s0:Port-channel_102 (280G), whether it goes to UCSD or SDSC. Caltech's
circuits to UCSD and to SDSC also share CENIC's aristaeos_s0 Port-Channel501
(300G) and Port-Channel502 (400G), and FNAL-SDSC uses Port-Channel502 too.
Planning a pair against the mesh alone put 400G through those ports, and CENIC
refused the circuits ("Insufficient bandwidth on aristaeos_s0 Port-Channel501").

Each request records the ports its circuit crosses (Request.path_ports, from
the stage and provision responses), and each pair the route of its last
provisioned circuit (Mesh.path_ports): staging sometimes compiles a candidate
over a spare route, and its available bandwidth is then that route's, not the
one SENSE-O provisions on. refresh_path_ports keeps PathPort rows with each
port's capacity and what circuits that aren't DMM's hold on it. The decider
limits the circuits crossing a port to that.
"""
import logging

from sqlmodel import select

from dmm.core.sense_model import read_ports
from dmm.models.base import utcnow
from dmm.models.mesh import Mesh
from dmm.models.path_port import PathPort
from dmm.models.request import Request


def _short(uri):
    """urn:ogf:network:sc-test.cenic.net:2020:aristaeos_s0:Port-Channel501 -> aristaeos_s0:Port-Channel501"""
    parts = uri.split(":")
    return ":".join(parts[5:]) if len(parts) > 5 else uri


def refresh_path_ports(uris, session):
    """
    Re-read the capacity of these ports, and of every port already known, from
    SENSE-O's model (one GET /model, ~7 s).

    What a port's holds come to, less those of DMM's own circuits (any request's
    sense_uuid), is what the decider can't give out there. DMM's circuits are
    left out because the decider plans those itself, which also covers one still
    committing that the model doesn't show yet.

    Never raises, and rolls back only its own writes on a failure: the caller is
    in the middle of staging or provisioning, which must be kept. The rows then
    keep what they had and the decider goes on planning with them (or, for ports
    never read, without them).
    """
    try:
        with session.begin_nested():
            _refresh(set(uris), session)
    except Exception as e:
        logging.warning(f"Could not refresh port capacities from SENSE-O's model, keeping what DMM had: {e}")


def _refresh(uris, session):
    known = {row.uri: row for row in PathPort.get_all(session=session)}
    routes = {p for mesh in Mesh.get_all(session=session) for p in (mesh.path_ports or [])}
    wanted = uris | set(known) | routes
    if not wanted:
        return
    ports = read_ports(wanted)

    dmm_uuids = set(session.exec(select(Request.sense_uuid).where(Request.sense_uuid.is_not(None))).all())
    now = utcnow()
    for uri, port in sorted(ports.items()):
        foreign = {holder: mbps for holder, mbps in port["holds"].items() if mbps and holder not in dmm_uuids}
        foreign_mbps = sum(foreign.values())
        row = known.get(uri) or PathPort(uri=uri)
        if (row.capacity_mbps, row.foreign_mbps) != (port["capacity"], foreign_mbps):
            held_by = f" ({', '.join(f'{h} {m:.0f}' for h, m in sorted(foreign.items()))})" if foreign else ""
            logging.info(
                f"Port {_short(uri)}: {port['capacity']:.0f} Mbps, {foreign_mbps:.0f} Mbps of it held "
                f"by circuits that aren't DMM's{held_by}"
            )
        row.update({
            "capacity_mbps": port["capacity"],
            "foreign_mbps": foreign_mbps,
            "foreign_holds": foreign,
            "read_at": now,
        }, session=session)

    unread = sorted(uris - set(ports) - set(known))
    if unread:
        logging.warning(
            f"SENSE-O's model gives no capacity for {', '.join(_short(u) for u in unread)}; "
            "the decider can't limit the circuits crossing them"
        )
