import logging

from dmm.daemons.base import DaemonBase

from dmm.db.session import databased
from dmm.models.request import Request, RequestStatus
from dmm.models.mesh import Mesh

from dmm.core.config import config_get
from dmm.core.sense import stage_link, parse_staging_response, delete_instance
from dmm.core.utils import release_endpoints_and_addresses
from dmm.core.sense_triage import triage, NO_RESOURCES, NO_CAPACITY, NO_VLANS, NO_TOPOLOGY
from dmm.core.sense_model import check_path

class SENSEStagerDaemon(DaemonBase):
    def __init__(self, frequency, **kwargs):
        super().__init__(frequency, **kwargs)
        self.profile_uuid = config_get("sense", "profile_uuid")
        
    def process(self, **kwargs):
        self.run_once(**kwargs)

    @databased
    def run_once(self, session=None):
        reqs_allocated = Request.get_by_status(statuses=[RequestStatus.ALLOCATED], session=session)
        if reqs_allocated == []:
            return
        for req in reqs_allocated:
            try:
                vlan_range = Mesh.get_vlan_range(site_1=req.src_site, site_2=req.dst_site, session=session)
                if vlan_range is None:
                    logging.error(f"No VLAN range found for {req.rule_id}, marking as FAILED")
                    req.mark_failed(f"No VLAN range found between {req.src_site} and {req.dst_site}", session=session)
                    release_endpoints_and_addresses(req, session)
                    continue

                if req.sense_uuid:
                    self._discard_failed_instance(req, session)

                response = stage_link(
                    profile_uuid=self.profile_uuid,
                    src_site=req.src_site,
                    dst_site=req.dst_site,
                    src_ip_range=req.src_endpoint.ip_range,
                    dst_ip_range=req.dst_endpoint.ip_range,
                    vlan_range=vlan_range,
                    rule_id=req.rule_id
                )
                
                if not response:
                    logging.error(f"Staging returned empty response for {req.rule_id}")
                    continue
                    
                sense_uuid, bandwidth_mbps, src_uri, dst_uri = parse_staging_response(
                    response,
                    src_ip_range=req.src_endpoint.ip_range,
                    dst_ip_range=req.dst_endpoint.ip_range,
                )

                if not sense_uuid:
                    raise ValueError("No service_uuid in response")

                link_cap = Mesh.get_link_capacity(req.src_site, req.dst_site, session=session)
                if link_cap is not None:
                    if bandwidth_mbps > link_cap:
                        logging.warning(
                            f"SENSE reported {bandwidth_mbps:.0f} Mbps available for {req.rule_id}, "
                            f"but mesh link capacity is {link_cap} Mbps — capping to link capacity. "
                            "This may indicate SENSE returned total capacity instead of available capacity."
                        )
                        bandwidth_mbps = link_cap

                req.set_sense_uuid(sense_uuid, session=session)
                req.set_sense_uris(src_uri, dst_uri, session=session)
                req.set_available_bandwidth(bandwidth_mbps, session=session)
                req.clear_failure_reason(session=session)
                req.set_status(status=RequestStatus.STAGED, session=session)

            except Exception as e:
                logging.error(f"Failed to stage link for {req.rule_id}, {e}, will try again")
                failed_uuid = getattr(e, "sense_uuid", None)
                if failed_uuid:
                    req.set_sense_uuid(failed_uuid, session=session)
                    req.update({"available_bandwidth_mbps": None}, session=session)
                req.mark_retry(f"Staging failed: {e}", session=session)
                t = triage(e)
                if t.kind == NO_RESOURCES:
                    self._probe_path_on_no_resources(req, session)

    @staticmethod
    def _probe_path_on_no_resources(req, session):
        """After a NO_RESOURCES staging failure, probe the local model to refine the diagnosis.

        Runs check_path() (GET /model + local NML graph search; read-only, no side
        effects) and refines the failure_reason written by mark_retry() with the
        more specific verdict:

          NO_CAPACITY   — topology joined; bottleneck Mbps is the problem.
                          Also writes available_bandwidth_mbps so the optimizer
                          can see what the network actually has free.
          NO_VLANS      — topology joined at zero rate; VLAN ids are exhausted.
          NO_TOPOLOGY   — endpoints not joined in the model at all.
          FEASIBLE_IN_MODEL — model says it fits; SENSE-O refused for another reason.
          UNKNOWN       — fetch or parse failed; failure_reason is left as-is.
        """
        src = req.src_site.name if req.src_site else None
        dst = req.dst_site.name if req.dst_site else None
        need = req.allocated_bandwidth_mbps or 0.0

        if not src or not dst:
            return

        result = check_path(src_site=src, dst_site=dst, need_mbps=need)

        if result.verdict == "UNKNOWN":
            # Model fetch or parse failed; leave failure_reason from mark_retry as-is.
            logging.warning(
                f"sense_model probe for {req.rule_id} returned UNKNOWN: {result.detail}"
            )
            return

        logging.info(
            f"sense_model probe for {req.rule_id}: "
            f"verdict={result.verdict} | {result.detail}"
        )

        # Map verdict -> triage kind constant (for the refined failure_reason string).
        verdict_kind = {
            "NO_CAPACITY":       NO_CAPACITY,
            "NO_VLANS":          NO_VLANS,
            "NO_TOPOLOGY":       NO_TOPOLOGY,
            "FEASIBLE_IN_MODEL": "feasible_in_model",
        }.get(result.verdict, result.verdict.lower())

        refined_reason = f"[{verdict_kind}] {result.detail}"
        req.set_failure_reason(refined_reason, session=session)

        if result.verdict == "NO_CAPACITY" and result.bottleneck_free_mbps is not None:
            req.set_available_bandwidth(result.bottleneck_free_mbps, session=session)

    @staticmethod
    def _discard_failed_instance(req, session):
        """Delete the instance a previous staging attempt failed on, before staging anew."""
        try:
            delete_instance(req.sense_uuid)
        except Exception as e:
            logging.error(
                f"Failed to delete SENSE instance {req.sense_uuid} left by a failed staging "
                f"of {req.rule_id}, it may need deleting by hand: {e}"
            )
        req.set_sense_uuid(None, session=session)
        req.set_sense_circuit_status(None, session=session)