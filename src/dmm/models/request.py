from sqlmodel import Field, Relationship, or_, select
from typing import Optional, List, ClassVar
from enum import Enum
from datetime import datetime, timedelta, timezone

import logging

from dmm.models.base import *

class RequestStatus(str, Enum):
    INIT        = "INIT"        # New rule detected, not yet processed
    NOT_SENSE   = "NOT_SENSE"   # Rule does not require a SENSE circuit
    ALLOCATED   = "ALLOCATED"   # IPv6 endpoints assigned, awaiting circuit staging
    RETRY       = "RETRY"       # Staging failed, will retry up to max_retries
    FAILED      = "FAILED"      # Permanently failed (retries exhausted or bad config)
    STAGED      = "STAGED"      # SENSE instance created, UUID known, awaiting decision
    DECIDED     = "DECIDED"     # Bandwidth decided by optimizer, awaiting provisioning
    PROVISIONED = "PROVISIONED" # SENSE circuit active at allocated bandwidth
    STALE       = "STALE"       # Bandwidth needs to change (re-optimization result)
    MODIFIED    = "MODIFIED"    # Rucio rule priority changed, triggers re-optimization
    FINISHED_R  = "FINISHED_R"  # Rucio rule done, circuit still live (keep-alive / reuse window)
    FINISHED    = "FINISHED"    # Circuit throttled to 1G, ready for cancellation
    CANCELED    = "CANCELED"    # SENSE circuit cancelled, endpoints freed
    DELETED     = "DELETED"     # SENSE instance deleted, record fully retired

class SenseCircuitStatus(str, Enum):
    CREATE_COMPILED    = "CREATE - COMPILED"
    CREATE_READY       = "CREATE - READY"
    CREATE_COMMITTING  = "CREATE - COMMITTING"
    CREATE_COMMITTED   = "CREATE - COMMITTED"
    CREATE_FAILED      = "CREATE - FAILED"
    MODIFY_READY       = "MODIFY - READY"
    MODIFY_COMMITTING  = "MODIFY - COMMITTING"
    MODIFY_COMMITTED   = "MODIFY - COMMITTED"
    MODIFY_FAILED      = "MODIFY - FAILED"
    REINSTATE_READY    = "REINSTATE - READY"
    CANCEL_COMMITTING  = "CANCEL - COMMITTING"
    CANCEL_COMMITTED   = "CANCEL - COMMITTED"
    CANCEL_READY       = "CANCEL - READY"

class Request(ModelBase, table=True):
    rule_id: str = Field(primary_key=True)
    transfer_status: Optional[str] = Field(default=None, index=True)
    priority: Optional[int] = Field(default=None)
    rule_size: Optional[float] = Field(default=None)
    modified_priority: Optional[int] = Field(default=None)
    available_bandwidth_mbps: Optional[float] = Field(default=None)  
    allocated_bandwidth_mbps: Optional[float] = Field(default=None)  
    previous_bandwidth_mbps: Optional[float] = Field(default=None)
    sense_uuid: Optional[str] = Field(default=None)
    sense_src_uri: Optional[str] = Field(default=None)  
    sense_dst_uri: Optional[str] = Field(default=None)  
    sense_circuit_status: Optional[str] = Field(default=None, index=True)
    sense_affiliated: Optional[bool] = Field(default=False)
    fts_streams_current: Optional[int] = Field(default=0)
    fts_streams_desired: Optional[int] = Field(default=None)
    sense_provisioned_at: Optional[datetime] = Field(default=None)
    rucio_finished_at: Optional[datetime] = Field(default=None)
    prometheus_throughput: Optional[float] = Field(default=None)
    prometheus_bytes: Optional[float] = Field(default=None)
    health: Optional[str] = Field(default=None)
    sense_retries: Optional[int] = Field(default=0)
    sense_alloc_rule_id: Optional[str] = Field(default=None)
    failure_reason: Optional[str] = Field(default=None)
    failed_at: Optional[datetime] = Field(default=None)

    # Site names as Rucio sent them. Several logical sites can map to one
    # physical site (T2_US_UCSD_Blackhole -> T2_US_UCSD); src_site_/dst_site_
    # below are always the physical site. The logical name picks the SENSE-O
    # subnet pool and nothing else.
    src_logical_site: Optional[str] = Field(default=None)
    dst_logical_site: Optional[str] = Field(default=None)

    src_site_: Optional[str] = Field(default=None, foreign_key='site.name')
    dst_site_: Optional[str] = Field(default=None, foreign_key='site.name')
    src_endpoint_: Optional[int] = Field(default=None, foreign_key='endpoint.id')
    dst_endpoint_: Optional[int] = Field(default=None, foreign_key='endpoint.id')

    src_site: Optional["Site"] = Relationship(
        back_populates='requests_as_source', 
        sa_relationship_kwargs={"foreign_keys": "[Request.src_site_]"}
    )
    dst_site: Optional["Site"] = Relationship(
        back_populates='requests_as_destination', 
        sa_relationship_kwargs={"foreign_keys": "[Request.dst_site_]"}
    )
    src_endpoint: Optional["Endpoint"] = Relationship(
        back_populates='requests_as_source', 
        sa_relationship_kwargs={"foreign_keys": "[Request.src_endpoint_]"}
    )
    dst_endpoint: Optional["Endpoint"] = Relationship(
        back_populates='requests_as_destination', 
        sa_relationship_kwargs={"foreign_keys": "[Request.dst_endpoint_]"}
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def __eq__(self, other):
        if not isinstance(other, Request):
            return NotImplemented
        return self.rule_id == other.rule_id

    # Requests created before multi-logical-site support have no logical site
    # recorded; back then the pool was named after the physical site, so falling
    # back to it keeps their allocations addressable.
    @property
    def src_pool_site(self) -> Optional[str]:
        """Logical site whose SENSE-O subnet pool holds the source allocation."""
        return self.src_logical_site or (self.src_site.name if self.src_site else None)

    @property
    def dst_pool_site(self) -> Optional[str]:
        """Logical site whose SENSE-O subnet pool holds the destination allocation."""
        return self.dst_logical_site or (self.dst_site.name if self.dst_site else None)

    # Requests in these states never change again, so exporting them forever
    # only grows the series count.
    TERMINAL_STATUSES: ClassVar[List[str]] = [
        RequestStatus.FAILED,
        RequestStatus.CANCELED,
        RequestStatus.DELETED,
    ]

    @classmethod
    def get_for_metrics(cls, session=None, terminal_window_hours: int = 6, limit: int = 5000):
        """Requests worth exporting: everything live, plus recently-finished ones.

        Unbounded export means a full table scan per scrape and a series count
        that only ever grows. Terminal requests are kept briefly so a rule that
        just failed is still visible on a dashboard.

        The cutoff uses the same clock ModelBase.save writes with, so the
        comparison is against like values.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=terminal_window_hours)
        statement = (
            select(cls)
            .where(or_(
                cls.transfer_status.notin_(cls.TERMINAL_STATUSES),
                cls.updated_at >= cutoff,
            ))
            .order_by(cls.updated_at.desc())
            .limit(limit)
        )
        return list(session.exec(statement).all())

    @classmethod
    def get_by_status(cls, statuses: List[str], session=None, use_lock: bool = True):
        logging.debug(f"REQUEST QUERY: statuses={statuses}, locked={use_lock}")
        statement = select(cls).where(cls.transfer_status.in_(statuses))
        if use_lock:
            statement = statement.with_for_update()
        return list(session.exec(statement).all())

    @classmethod
    def get_by_id(cls, rule_id: str, session=None, use_lock: bool = True):
        logging.debug(f"REQUEST QUERY: rule_id={rule_id}, locked={use_lock}")
        statement = select(cls).where(cls.rule_id == rule_id)
        if use_lock:
            statement = statement.with_for_update()
        return session.exec(statement).first()
    
    # Failure reasons can be long (tracebacks, SENSE error blobs); cap what we persist.
    FAILURE_REASON_MAX_LEN: ClassVar[int] = 2000

    @classmethod
    def _truncate_reason(cls, reason) -> Optional[str]:
        if reason is None:
            return None
        reason = str(reason).strip()
        if len(reason) > cls.FAILURE_REASON_MAX_LEN:
            reason = reason[: cls.FAILURE_REASON_MAX_LEN - 3] + "..."
        return reason

    def set_status(self, status: str, session=None):
        logging.debug(f"REQUEST UPDATE: {self.rule_id} -> status={status}")
        self.transfer_status = status
        self.save(session)

    def set_failure_reason(self, reason, session=None):
        reason = self._truncate_reason(reason)
        logging.debug(f"REQUEST UPDATE: {self.rule_id} -> failure_reason={reason}")
        self.failure_reason = reason
        self.save(session)

    def clear_failure_reason(self, session=None):
        if self.failure_reason is None and self.failed_at is None:
            return
        logging.debug(f"REQUEST UPDATE: {self.rule_id} -> clearing failure_reason")
        self.failure_reason = None
        self.failed_at = None
        self.save(session)

    def mark_failed(self, reason, session=None):
        """Permanently fail the request, recording why and when."""
        reason = self._truncate_reason(reason)
        logging.debug(f"REQUEST UPDATE: {self.rule_id} -> FAILED ({reason})")
        self.transfer_status = RequestStatus.FAILED
        self.failure_reason = reason
        self.failed_at = datetime.now()
        self.save(session)

    def mark_retry(self, reason, session=None):
        """Transient failure: record the reason but keep the request retrying."""
        reason = self._truncate_reason(reason)
        logging.debug(f"REQUEST UPDATE: {self.rule_id} -> RETRY ({reason})")
        self.transfer_status = RequestStatus.RETRY
        self.failure_reason = reason
        self.save(session)

    def set_available_bandwidth(self, bandwidth_mbps: float, session=None):
        logging.debug(f"REQUEST UPDATE: {self.rule_id} -> available_bandwidth={bandwidth_mbps} Mbps")
        self.available_bandwidth_mbps = bandwidth_mbps
        self.save(session)

    def set_sense_uuid(self, sense_uuid: str, session=None):
        logging.debug(f"REQUEST UPDATE: {self.rule_id} -> sense_uuid={sense_uuid}")
        self.sense_uuid = sense_uuid
        self.save(session)

    def set_sense_uris(self, src_uri: str, dst_uri: str, session=None):
        logging.debug(f"REQUEST UPDATE: {self.rule_id} -> src_uri={src_uri}, dst_uri={dst_uri}")
        self.sense_src_uri = src_uri
        self.sense_dst_uri = dst_uri
        self.save(session)
    
    def set_allocated_bandwidth(self, bandwidth_mbps: float, session=None):
        logging.debug(f"REQUEST UPDATE: {self.rule_id} -> allocated_bandwidth={bandwidth_mbps} Mbps")
        self.allocated_bandwidth_mbps = bandwidth_mbps
        self.save(session)

    def set_previous_bandwidth(self, bandwidth_mbps: float, session=None):
        logging.debug(f"REQUEST UPDATE: {self.rule_id} -> previous_bandwidth={bandwidth_mbps} Mbps")
        self.previous_bandwidth_mbps = bandwidth_mbps
        self.save(session)

    def set_priority(self, priority: int, session=None):
        logging.debug(f"REQUEST UPDATE: {self.rule_id} -> priority={priority}")
        self.priority = priority
        self.modified_priority = priority
        self.save(session)

    def increment_sense_retries(self, session=None):
        self.sense_retries = (self.sense_retries or 0) + 1
        logging.debug(f"REQUEST UPDATE: {self.rule_id} -> sense_retries={self.sense_retries}")
        self.save(session)

    def set_sense_circuit_status(self, status: str, session=None):
        logging.debug(f"REQUEST UPDATE: {self.rule_id} -> circuit_status={status}")
        self.sense_circuit_status = status
        self.save(session)
    
    def set_fts_streams(self, current: int = None, desired: int = None, session=None):
        if current is not None:
            logging.debug(f"REQUEST UPDATE: {self.rule_id} -> fts_streams_current={current}")
            self.fts_streams_current = current
        if desired is not None:
            logging.debug(f"REQUEST UPDATE: {self.rule_id} -> fts_streams_desired={desired}")
            self.fts_streams_desired = desired
        self.save(session)

    def set_prometheus_metrics(self, throughput: float = None, bytes_transferred: float = None, session=None):
        if throughput is not None:
            self.prometheus_throughput = throughput
        if bytes_transferred is not None:
            self.prometheus_bytes = bytes_transferred
        self.save(session)

    def set_health(self, health: str, session=None):
        self.health = health
        self.save(session)
