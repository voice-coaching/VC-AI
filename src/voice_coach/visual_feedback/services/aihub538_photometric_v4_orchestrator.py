"""Fail-closed multi-pair P6 v4 production orchestration core."""
from __future__ import annotations
from dataclasses import dataclass
import json,os,stat
from pathlib import Path
from typing import Callable
from ..adapters.aihub538_photometric_source_admission import Aihub538PhotometricSourceAdmissionAuthority,close_aihub538_photometric_source_admission_authority
from ..adapters.aihub538_photometric_v4_store import Aihub538PhotometricV4IssuanceStore
from ..models.aihub538_label_capacity import Aihub538LabelCapacityAggregate
from ..models.aihub538_p6_label_preparation import Aihub538P6LabelPreparationReceipt
from ..models.aihub538_p6_remediation import Aihub538P6RemediationAuditReceipt
from ..models.aihub538_training_evidence import Aihub538TrainingAdmissionReceipt,aihub538_shard_admission_policy_sha256
from ..models.aihub538_nonlocked_shards import aihub538_nonlocked_shard_by_id
from ..models.aihub538_photometric_v4_production_policy import AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256
from ..models.integrity import require_sha256,sha256_document
from .aihub538_photometric_calibration_v4 import Aihub538PhotometricPairLineageV4,aihub538_photometric_v4_ordered_pair_lineage_commitment
from .aihub538_photometric_v4_runner import Aihub538AdmittedNonlockedSourceOpener,Aihub538PhotometricV4MultiPairRunner,consume_aihub538_photometric_v4_production_run,finalize_aihub538_photometric_v4_production_run

@dataclass(frozen=True,slots=True)
class Aihub538PhotometricPreparedPairV4:
    preparation_receipt:Aihub538P6LabelPreparationReceipt
    label_aggregate:Aihub538LabelCapacityAggregate
    admission_receipt:Aihub538TrainingAdmissionReceipt
    source_manifest_sha256:str
    production_execution_policy_sha256:str
    authority:Aihub538PhotometricSourceAdmissionAuthority
    opener:Aihub538AdmittedNonlockedSourceOpener
    def validate(self,*,expected_shard_id:str|None=None)->Aihub538PhotometricPairLineageV4:
        require_sha256(self.source_manifest_sha256,"source_manifest_sha256"); require_sha256(self.production_execution_policy_sha256,"production_execution_policy_sha256"); self.preparation_receipt._validate(); self.admission_receipt._validate(); shard=aihub538_nonlocked_shard_by_id(self.admission_receipt.shard_id)
        if (expected_shard_id is not None and shard.shard_id!=expected_shard_id) or self.production_execution_policy_sha256!=AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256 or self.preparation_receipt.shard_id!=f"tl{shard.label_role_component[2:]}_capacity_audit" or self.preparation_receipt.label_role_component!=shard.label_role_component or self.admission_receipt.admission_policy_sha256!=aihub538_shard_admission_policy_sha256(shard) or self.source_manifest_sha256!=self.admission_receipt.admission_manifest_sha256 or self.preparation_receipt.label_zip_sha256!=self.label_aggregate.label_archive_sha256 or self.preparation_receipt.label_zip_content_root_sha256!=self.label_aggregate.label_content_root_sha256 or self.admission_receipt.label_archive_sha256!=self.label_aggregate.label_archive_sha256 or self.admission_receipt.label_zip_content_root_sha256!=self.label_aggregate.label_content_root_sha256 or self.preparation_receipt.label_member_count!=len(self.label_aggregate.items) or self.admission_receipt.label_count!=len(self.label_aggregate.items) or self.preparation_receipt.actor_count!=len({item.actor_token_sha256 for item in self.label_aggregate.items}) or self.preparation_receipt.attempt_count!=len({item.attempt_token_sha256 for item in self.label_aggregate.items}) or self.preparation_receipt.identity_namespace_commitment_sha256!=self.label_aggregate.identity_namespace_commitment_sha256 or self.admission_receipt.identity_namespace_commitment_sha256!=self.label_aggregate.identity_namespace_commitment_sha256 or self.preparation_receipt.member_namespace_commitment_sha256!=self.label_aggregate.member_namespace_commitment_sha256: raise ValueError("p6_v4_prepared_pair_lineage_invalid")
        return Aihub538PhotometricPairLineageV4(self.label_aggregate.label_archive_sha256,self.label_aggregate.label_content_root_sha256,self.admission_receipt.source_archive_sha256,self.admission_receipt.source_zip_content_root_sha256,self.source_manifest_sha256,self.admission_receipt.receipt_sha256,self.admission_receipt.admission_policy_sha256,self.production_execution_policy_sha256)

class Aihub538PhotometricV4ProductionOrchestrator:
    def __init__(self,*,runner_factory:Callable[...,Aihub538PhotometricV4MultiPairRunner],issuance_store:Aihub538PhotometricV4IssuanceStore,output_directory:Path)->None:
        if not output_directory.is_absolute(): raise ValueError("p6_v4_output_directory_invalid")
        output_directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        if output_directory.is_symlink() or stat.S_IMODE(output_directory.stat().st_mode)!=0o700: raise ValueError("p6_v4_output_directory_invalid")
        self._factory=runner_factory; self._store=issuance_store; self._output=output_directory
    def run(self,*,remediation_receipt:Aihub538P6RemediationAuditReceipt,pairs:tuple[Aihub538PhotometricPreparedPairV4,...],protocol)->dict[str,object]:
        evidence=None; cleanup=False
        try:
            from .aihub538_photometric_v4_execution import frozen_aihub538_photometric_v4_protocol
            if protocol!=frozen_aihub538_photometric_v4_protocol(): raise ValueError("p6_v4_frozen_calibration_protocol_required")
            remediation_receipt._validate()
            if not remediation_receipt.is_feasible or len(pairs)!=len(remediation_receipt.evaluated_prefix_lineage): raise ValueError("p6_v4_exact_feasible_prefix_required")
            lineages=tuple(pair.validate(expected_shard_id=f"ts{177+index}_tl{177+index}") for index,pair in enumerate(pairs))
            if tuple(pair.label_aggregate.label_archive_sha256 for pair in pairs)!=tuple(row.label_archive_sha256 for row in remediation_receipt.evaluated_prefix_lineage): raise ValueError("p6_v4_exact_feasible_prefix_order_invalid")
            routes={lineage.label_archive_sha256:(pair.authority,pair.opener) for lineage,pair in zip(lineages,pairs,strict=True)}
            runner=self._factory(routes=routes,issuance_store=self._store)
            if type(runner) is not Aihub538PhotometricV4MultiPairRunner: raise ValueError("p6_v4_exact_multi_pair_runner_required")
            run_result=runner.run(remediation_receipt=remediation_receipt,candidate_aggregates=tuple(pair.label_aggregate for pair in pairs),pair_lineages=lineages,protocol=protocol)
            evidence=consume_aihub538_photometric_v4_production_run(run_result,runner=runner,store=self._store,remediation_receipt=remediation_receipt)
            if evidence.production_execution_policy_sha256!=AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256: raise ValueError("p6_v4_production_execution_policy_invalid")
            if evidence.pair_count!=3 or evidence.selected_member_count!=144 or evidence.ordered_sampling_receipt_count!=144 or evidence.remediation_receipt_sha256!=remediation_receipt.receipt_sha256 or evidence.ordered_pair_lineage_commitment_sha256!=aihub538_photometric_v4_ordered_pair_lineage_commitment(lineages) or evidence.source_manifest_sha256!=sha256_document({"schema_version":"voice_coach.p6_v4_ordered_source_manifests.v1","length":3,"values":[lineage.source_manifest_sha256 for lineage in lineages]}) or evidence.admission_receipt_sha256!=sha256_document({"schema_version":"voice_coach.p6_v4_ordered_admission_receipts.v1","length":3,"values":[lineage.admission_receipt_sha256 for lineage in lineages]}): raise ValueError("p6_v4_production_run_lineage_invalid")
            artifact=self._output/"p6-photometric-v4-artifact.json"; temporary=self._output/".p6-photometric-v4-artifact.tmp"
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400)
            try:
                raw=(json.dumps(evidence.to_json_object(),sort_keys=True,separators=(",",":"))+"\n").encode(); offset=0
                while offset<len(raw):
                    written=os.write(fd,raw[offset:])
                    if written<=0: raise OSError("artifact_write_failed")
                    offset+=written
                os.fsync(fd); os.close(fd); fd=-1; os.link(temporary,artifact,follow_symlinks=False); os.unlink(temporary)
            except BaseException:
                if fd>=0: os.close(fd)
                try: os.unlink(temporary)
                except OSError: pass
                raise
            directory_fd=os.open(self._output,os.O_RDONLY|os.O_DIRECTORY)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
            reload_receipt=self._store.launch_fresh_process_reload(artifact_path=artifact,artifact_sha256=evidence.artifact_sha256); self._store.validate_fresh_process_reload_receipt(reload_receipt,artifact_path=artifact)
            finalize_aihub538_photometric_v4_production_run(run_result,runner=runner,store=self._store,remediation_receipt=remediation_receipt,succeeded=True)
        finally:
            if 'run_result' in locals():
                try: finalize_aihub538_photometric_v4_production_run(run_result,runner=runner,store=self._store,remediation_receipt=remediation_receipt,succeeded=False)
                except ValueError: pass
            for pair in pairs:
                try: close_aihub538_photometric_source_admission_authority(pair.authority)
                except ValueError: pass
            cleanup=True
        if evidence is None: raise ValueError("p6_v4_orchestration_failed")
        unsigned={"schema_version":"voice_coach.p6_v4_production_completion.v1","artifact_sha256":evidence.artifact_sha256,"production_execution_policy_sha256":evidence.production_execution_policy_sha256,"pair_count":evidence.pair_count,"remediation_receipt_sha256":evidence.remediation_receipt_sha256,"ordered_pair_lineage_commitment_sha256":evidence.ordered_pair_lineage_commitment_sha256,"source_manifest_sha256":evidence.source_manifest_sha256,"admission_receipt_sha256":evidence.admission_receipt_sha256,"selected_member_commitment_sha256":evidence.selected_member_commitment_sha256,"selected_member_count":evidence.selected_member_count,"ordered_sampling_receipt_commitment_sha256":evidence.ordered_sampling_receipt_commitment_sha256,"ordered_sampling_receipt_count":evidence.ordered_sampling_receipt_count,"fresh_process_reload_completed":True,"restricted_sources_closed":cleanup,"promotion_authorized":False,"locked_partition_accessed":False}
        return {**unsigned,"receipt_sha256":sha256_document(unsigned)}

__all__=["Aihub538PhotometricPreparedPairV4","Aihub538PhotometricV4ProductionOrchestrator"]
