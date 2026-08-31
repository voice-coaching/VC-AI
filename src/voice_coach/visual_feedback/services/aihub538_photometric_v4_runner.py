"""Production facade wiring admitted nonlocked source FDs into P6 v4."""
from __future__ import annotations
from contextlib import AbstractContextManager
from typing import Callable,Protocol,Mapping
from dataclasses import dataclass
from threading import RLock
from weakref import WeakKeyDictionary,ref
import os
from ...pronunciation_attempts.adapters.video_qc import VideoQcCandidateEvidence
from ...pronunciation_attempts.models import Rgb24Sample
from ..adapters.aihub538_photometric_v4_production_factory import Aihub538PhotometricV4ProductionFactory
from ..adapters.aihub538_photometric_v4_sampling import require_aihub538_photometric_v4_eight_frame_sample
from ..adapters.aihub538_photometric_source_admission import Aihub538MemberSourceMappingCapabilityV4,Aihub538PhotometricSourceAdmissionAuthority,begin_aihub538_member_source_open,finish_aihub538_member_source_open,issue_aihub538_member_source_mapping,mark_aihub538_member_measurement_bound
from ..adapters.aihub538_photometric_v4_store import Aihub538PhotometricV4IssuanceStore
from ..models.aihub538_label_capacity import Aihub538LabelCapacityAggregate
from ..models.aihub538_p6_remediation import Aihub538P6RemediationAuditReceipt
from ..models.integrity import sha256_document
from .aihub538_photometric_calibration_v4 import Aihub538PhotometricCalibrationV4Service,Aihub538PhotometricMeasurementV4,Aihub538PhotometricPairLineageV4,bind_aihub538_photometric_measurements_v4,issue_aihub538_photometric_cohort_capability_v4

class Aihub538AdmittedNonlockedSourceOpener(Protocol):
    def open_member_fd(self,mapping:Aihub538MemberSourceMappingCapabilityV4)->AbstractContextManager[int]:...

@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class Aihub538PhotometricV4ProductionRunResult:
    evidence:object
    capability_sha256:str
    def to_json_object(self): return self.evidence.to_json_object()
    def __getattr__(self,name): return getattr(self.evidence,name)

_RUNS=WeakKeyDictionary(); _RUN_LOCK=RLock()
def consume_aihub538_photometric_v4_production_run(result:Aihub538PhotometricV4ProductionRunResult,*,runner:"Aihub538PhotometricV4MultiPairRunner",store:Aihub538PhotometricV4IssuanceStore,remediation_receipt:Aihub538P6RemediationAuditReceipt):
    with _RUN_LOCK:
        row=_RUNS.get(result) if type(result) is Aihub538PhotometricV4ProductionRunResult and type(runner) is Aihub538PhotometricV4MultiPairRunner and type(store) is Aihub538PhotometricV4IssuanceStore and type(remediation_receipt) is Aihub538P6RemediationAuditReceipt else None
        evidence=result.evidence if row is not None else None
        if row is None or row["state"]!="issued" or row["pid"]!=os.getpid() or row["store"]() is not store or row["runner"]() is not runner or row["result"]() is not result or row["remediation"] is not remediation_receipt or row["evidence"] is not evidence or row["directory_commitment_sha256"]!=store.directory_commitment_sha256 or result.capability_sha256!=row["capability_sha256"] or any(getattr(evidence,name)!=row[name] for name in ("artifact_sha256","remediation_receipt_sha256","ordered_pair_lineage_commitment_sha256","source_manifest_sha256","admission_receipt_sha256","selected_member_commitment_sha256","selected_member_count","ordered_sampling_receipt_commitment_sha256","ordered_sampling_receipt_count","pair_count")): raise ValueError("p6_v4_production_run_capability_invalid")
        row["state"]="consuming"
        return result.evidence

def finalize_aihub538_photometric_v4_production_run(result:Aihub538PhotometricV4ProductionRunResult,*,runner:"Aihub538PhotometricV4MultiPairRunner",store:Aihub538PhotometricV4IssuanceStore,remediation_receipt:Aihub538P6RemediationAuditReceipt,succeeded:bool)->None:
    with _RUN_LOCK:
        row=_RUNS.get(result)
        if row is None or row["state"]!="consuming" or row["pid"]!=os.getpid() or type(runner) is not Aihub538PhotometricV4MultiPairRunner or row["runner"]() is not runner or type(store) is not Aihub538PhotometricV4IssuanceStore or row["store"]() is not store or row["remediation"] is not remediation_receipt or row["evidence"] is not result.evidence or row["directory_commitment_sha256"]!=store.directory_commitment_sha256 or type(succeeded) is not bool: raise ValueError("p6_v4_production_run_capability_invalid")
        _RUNS.pop(result,None)

class Aihub538PhotometricV4ProductionRunner:
    def __init__(self,*,source_opener:Aihub538AdmittedNonlockedSourceOpener,source_authority:Aihub538PhotometricSourceAdmissionAuthority,qc_measure:Callable[[Rgb24Sample],VideoQcCandidateEvidence],production_factory:Aihub538PhotometricV4ProductionFactory,issuance_store:Aihub538PhotometricV4IssuanceStore)->None:
        if type(production_factory) is not Aihub538PhotometricV4ProductionFactory: raise ValueError("p6_v4_production_factory_required")
        self._opener=source_opener; self._authority=source_authority; self._qc=qc_measure; self._factory=production_factory; self._store=issuance_store
    def run(self,*,remediation_receipt:Aihub538P6RemediationAuditReceipt,candidate_aggregates:tuple[Aihub538LabelCapacityAggregate,...],source_manifest_sha256:str,admission_receipt_sha256:str,protocol)->object:
        capability=issue_aihub538_photometric_cohort_capability_v4(remediation_receipt=remediation_receipt,candidate_aggregates=candidate_aggregates,source_manifest_sha256=source_manifest_sha256,admission_receipt_sha256=admission_receipt_sha256,issuance_store=self._store)
        rows=[]; color=None; sampler=self._factory.color_sampler()
        for member in capability.members:
            mapping=issue_aihub538_member_source_mapping(authority=self._authority,member=member)
            begin_aihub538_member_source_open(mapping); succeeded=False
            try:
                with self._opener.open_member_fd(mapping) as fd: bundle=sampler.sample(video_fd=fd)
                sample=require_aihub538_photometric_v4_eight_frame_sample(bundle.sample,receipt=bundle.sampling_receipt,policy=self._factory.policy)
                color,probe,conversion=bundle.colorimetry_receipt,bundle.source_probe_receipt,bundle.source_conversion_receipt
                candidate=self._qc(sample); mark_aihub538_member_measurement_bound(mapping); succeeded=True
            finally: finish_aihub538_member_source_open(mapping,succeeded=succeeded)
            rows.append(Aihub538PhotometricMeasurementV4(member,probe,conversion,candidate,mapping,bundle))
        bound=bind_aihub538_photometric_measurements_v4(capability=capability,measurements=tuple(rows))
        assert color is not None
        return Aihub538PhotometricCalibrationV4Service(protocol=protocol,colorimetry_receipt=color).calibrate(calibration_input=bound)

class Aihub538PhotometricV4MultiPairRunner:
    """Resolve every selected member through its exact ordered pair route."""
    def __init__(self,*,routes:Mapping[str,tuple[Aihub538PhotometricSourceAdmissionAuthority,Aihub538AdmittedNonlockedSourceOpener]],qc_measure:Callable[[Rgb24Sample],VideoQcCandidateEvidence],production_factory:Aihub538PhotometricV4ProductionFactory,issuance_store:Aihub538PhotometricV4IssuanceStore)->None:
        if not isinstance(routes,Mapping) or not routes: raise ValueError("p6_v4_source_routes_required")
        if type(production_factory) is not Aihub538PhotometricV4ProductionFactory: raise ValueError("p6_v4_production_factory_required")
        self._routes=dict(routes); self._qc=qc_measure; self._factory=production_factory; self._store=issuance_store
    def run(self,*,remediation_receipt:Aihub538P6RemediationAuditReceipt,candidate_aggregates:tuple[Aihub538LabelCapacityAggregate,...],pair_lineages:tuple[Aihub538PhotometricPairLineageV4,...],protocol)->object:
        if type(self) is not Aihub538PhotometricV4MultiPairRunner: raise ValueError("p6_v4_exact_multi_pair_runner_required")
        from .aihub538_photometric_v4_execution import frozen_aihub538_photometric_v4_protocol
        if protocol!=frozen_aihub538_photometric_v4_protocol(): raise ValueError("p6_v4_frozen_calibration_protocol_required")
        if tuple(self._routes)!=tuple(pair.label_archive_sha256 for pair in pair_lineages): raise ValueError("p6_v4_source_route_order_invalid")
        source_vector=sha256_document({"schema_version":"voice_coach.p6_v4_ordered_source_manifests.v1","length":len(pair_lineages),"values":[pair.source_manifest_sha256 for pair in pair_lineages]})
        admission_vector=sha256_document({"schema_version":"voice_coach.p6_v4_ordered_admission_receipts.v1","length":len(pair_lineages),"values":[pair.admission_receipt_sha256 for pair in pair_lineages]})
        capability=issue_aihub538_photometric_cohort_capability_v4(remediation_receipt=remediation_receipt,candidate_aggregates=candidate_aggregates,source_manifest_sha256=source_vector,admission_receipt_sha256=admission_vector,issuance_store=self._store,pair_lineages=pair_lineages)
        rows=[]; color=None; sampler=self._factory.color_sampler()
        for member in capability.members:
            authority,opener=self._routes[member.label_archive_sha256]; mapping=issue_aihub538_member_source_mapping(authority=authority,member=member)
            pair=next(pair for pair in pair_lineages if pair.label_archive_sha256==member.label_archive_sha256)
            if mapping.source_archive_sha256!=pair.source_archive_sha256 or mapping.source_content_root_sha256!=pair.source_content_root_sha256: raise ValueError("p6_v4_source_route_lineage_invalid")
            begin_aihub538_member_source_open(mapping); succeeded=False
            try:
                with opener.open_member_fd(mapping) as fd: bundle=sampler.sample(video_fd=fd)
                sample=require_aihub538_photometric_v4_eight_frame_sample(bundle.sample,receipt=bundle.sampling_receipt,policy=self._factory.policy)
                color,probe,conversion=bundle.colorimetry_receipt,bundle.source_probe_receipt,bundle.source_conversion_receipt
                candidate=self._qc(sample); mark_aihub538_member_measurement_bound(mapping); succeeded=True
            finally: finish_aihub538_member_source_open(mapping,succeeded=succeeded)
            rows.append(Aihub538PhotometricMeasurementV4(member,probe,conversion,candidate,mapping,bundle))
        bound=bind_aihub538_photometric_measurements_v4(capability=capability,measurements=tuple(rows)); assert color is not None
        evidence=Aihub538PhotometricCalibrationV4Service(protocol=protocol,colorimetry_receipt=color).calibrate(calibration_input=bound)
        if evidence.pair_count!=3 or evidence.selected_member_count!=144 or evidence.ordered_sampling_receipt_count!=144: raise ValueError("p6_v4_exact_production_run_identity_required")
        capability_sha=sha256_document({"schema_version":"voice_coach.p6_v4_production_run_capability.v1","artifact_sha256":evidence.artifact_sha256,"remediation_receipt_sha256":evidence.remediation_receipt_sha256,"pair_count":evidence.pair_count,"ordered_pair_lineage_commitment_sha256":evidence.ordered_pair_lineage_commitment_sha256,"source_manifest_sha256":evidence.source_manifest_sha256,"admission_receipt_sha256":evidence.admission_receipt_sha256,"selected_member_commitment_sha256":evidence.selected_member_commitment_sha256,"selected_member_count":evidence.selected_member_count,"ordered_sampling_receipt_commitment_sha256":evidence.ordered_sampling_receipt_commitment_sha256,"ordered_sampling_receipt_count":evidence.ordered_sampling_receipt_count})
        result=Aihub538PhotometricV4ProductionRunResult(evidence,capability_sha)
        with _RUN_LOCK:_RUNS[result]={"pid":os.getpid(),"runner":ref(self),"store":ref(self._store),"result":ref(result),"remediation":remediation_receipt,"evidence":evidence,"directory_commitment_sha256":self._store.directory_commitment_sha256,"capability_sha256":capability_sha,"state":"issued",**{name:getattr(evidence,name) for name in ("artifact_sha256","remediation_receipt_sha256","ordered_pair_lineage_commitment_sha256","source_manifest_sha256","admission_receipt_sha256","selected_member_commitment_sha256","selected_member_count","ordered_sampling_receipt_commitment_sha256","ordered_sampling_receipt_count","pair_count")}}
        return result

__all__=["Aihub538AdmittedNonlockedSourceOpener","Aihub538PhotometricV4ProductionRunner","Aihub538PhotometricV4MultiPairRunner","Aihub538PhotometricV4ProductionRunResult","consume_aihub538_photometric_v4_production_run","finalize_aihub538_photometric_v4_production_run"]
