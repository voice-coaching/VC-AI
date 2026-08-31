"""Synthetic non-person P6 photometric v4 authority tests."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import tempfile
import os
import subprocess
import json
import sys
import stat
from types import SimpleNamespace
from unittest.mock import patch
from functools import lru_cache
from pathlib import Path
import pytest
from contextlib import contextmanager

from voice_coach.pronunciation_attempts.adapters.video_qc import MEASUREMENT_REVISION, VideoQcCandidateEvidence, VideoQcMetrics
from voice_coach.visual_feedback.adapters.aihub538_ffmpeg_colorimetry import FfmpegRgbColorimetryAdapter, _validate_rgb_geometry
from voice_coach.visual_feedback.adapters.aihub538_photometric_v4_store import Aihub538PhotometricV4IssuanceError, Aihub538PhotometricV4IssuanceStore
from voice_coach.visual_feedback.adapters.aihub538_photometric_v4_production_factory import Aihub538PhotometricV4ProductionFactory
from voice_coach.visual_feedback.adapters.aihub538_photometric_source_admission import begin_aihub538_member_source_open,create_aihub538_photometric_source_admission_authority,finish_aihub538_member_source_open, issue_aihub538_member_source_mapping,mark_aihub538_member_measurement_bound
from voice_coach.visual_feedback.adapters.aihub538_actual_media_calibration import Aihub538ActualMediaCalibrationAdapter, _run_mux
from voice_coach.visual_feedback.errors import VisualFeedbackContractError
from voice_coach.visual_feedback.models.aihub538_label_capacity import Aihub538LabelCapacityAggregate, Aihub538LabelCapacityItem
from voice_coach.visual_feedback.models.aihub538_p6_remediation import AIHUB538_P6_ACTUAL_V3_DESIGN_LABEL_LINEAGE, AIHUB538_P6_ACTUAL_V3_FAILURE_ARTIFACT_SHA256, AIHUB538_P6_ACTUAL_V3_IDENTITY_NAMESPACE_COMMITMENT_SHA256, AIHUB538_P6_ACTUAL_V3_MEMBER_NAMESPACE_COMMITMENT_SHA256, AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS, Aihub538P6RemediationAuditReceipt, Aihub538P6RemediationLineage, _build_prefix_decision, _lineage
from voice_coach.visual_feedback.models.aihub538_photometric_calibration_v4 import (AIHUB538_PHOTOMETRIC_V4_FROZEN_SAFETY_AND_STATISTICS, AIHUB538_PHOTOMETRIC_V4_FROZEN_SAFETY_AND_STATISTICS_SHA256, FFMPEG_RGB_COLORIMETRY_GOLDEN_OUTPUT_SHA256, Aihub538ActorOutcomeDistributionV4, Aihub538HardSafetyEnvelopeV4, Aihub538PhotometricCalibrationEvidenceV4, Aihub538PhotometricCalibrationProtocolV4, FfmpegRgbColorimetryReceipt, FfmpegRgbSourceConversionReceiptV4, FfmpegSourceColorProbeReceiptV4)
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.models.aihub538_training_evidence import Aihub538TrainingAdmissionAggregate,Aihub538TrainingAdmissionReceipt,Aihub538TrainingAngleAggregate,Aihub538TrainingSampleAggregate,Aihub538RestrictedIdentitySet,Aihub538RestrictedMemberSet,aihub538_shard_admission_policy_sha256
from voice_coach.visual_feedback.models.aihub538_nonlocked_shards import AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS,aihub538_nonlocked_shard_by_id
from voice_coach.visual_feedback.models.aihub538_label_capacity import AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256
from voice_coach.visual_feedback.models.aihub538_p6_label_preparation import Aihub538P6LabelPreparationReceipt
from voice_coach.visual_feedback.models.aihub538_photometric_v4_production_policy import AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256
from voice_coach.visual_feedback.services.aihub538_photometric_v4_execution import frozen_aihub538_photometric_v4_protocol
from voice_coach.visual_feedback.services.aihub538_photometric_calibration_v4 import (Aihub538PhotometricCalibrationV4Error, Aihub538PhotometricCalibrationV4Service, Aihub538PhotometricCohortCapabilityV4, Aihub538PhotometricMeasurementV4,Aihub538PhotometricPairLineageV4, bind_aihub538_photometric_measurements_v4, issue_aihub538_photometric_cohort_capability_v4)
from voice_coach.visual_feedback.services.aihub538_photometric_v4_runner import Aihub538PhotometricV4ProductionRunner,Aihub538PhotometricV4MultiPairRunner
from voice_coach.visual_feedback.services.aihub538_photometric_v4_orchestrator import Aihub538PhotometricPreparedPairV4,Aihub538PhotometricV4ProductionOrchestrator
import voice_coach.visual_feedback.services.aihub538_photometric_v4_orchestrator as orchestrator_module
import voice_coach.visual_feedback.adapters.aihub538_photometric_source_admission as source_admission_module

def d(value: str) -> str: return hashlib.sha256(value.encode()).hexdigest()

def attach_fake_photometric_clip_opener(adapter, payload_for_token):
    @contextmanager
    def open_clip(*, label_selection_token_sha256):
        payload=payload_for_token(label_selection_token_sha256); fd=os.memfd_create("p6-v4-test-clip",os.MFD_CLOEXEC)
        try:
            os.write(fd,payload); os.lseek(fd,0,os.SEEK_SET); yield fd
        finally: os.close(fd)
    adapter._open_photometric_source_clip=open_clip

def aggregate(name: str, actors: tuple[str, ...]) -> Aihub538LabelCapacityAggregate:
    items=[]; paths=set()
    for actor in actors:
        for angle in "ABCDEFGHI":
            for index in range(2):
                marker=f"{name}:{actor}:{angle}:{index}"
                items.append(Aihub538LabelCapacityItem(d(f"sample:{marker}"), angle, d(f"actor:{actor}"), d(f"attempt:{marker}")))
                paths.add(d(f"path:{marker}"))
    return Aihub538LabelCapacityAggregate(d(f"archive:{name}"), d(f"content:{name}"), AIHUB538_P6_ACTUAL_V3_IDENTITY_NAMESPACE_COMMITMENT_SHA256, AIHUB538_P6_ACTUAL_V3_MEMBER_NAMESPACE_COMMITMENT_SHA256, tuple(items), frozenset(paths))

@lru_cache(maxsize=1)
def cohort():
    fresh=aggregate("tl177",tuple(f"fresh-{i}" for i in range(8)))
    design_lineage=tuple(Aihub538P6RemediationLineage(**{name:row[name] for name in Aihub538P6RemediationLineage.__dataclass_fields__}) for row in AIHUB538_P6_ACTUAL_V3_DESIGN_LABEL_LINEAGE)
    candidate_lineage=(_lineage(AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS[0],fresh),); decisions=(_build_prefix_decision((fresh,)),)
    values=dict(design_failure_artifact_receipt_sha256=AIHUB538_P6_ACTUAL_V3_FAILURE_ARTIFACT_SHA256,prior_prefix_receipt_sha256=None,identity_namespace_commitment_sha256=AIHUB538_P6_ACTUAL_V3_IDENTITY_NAMESPACE_COMMITMENT_SHA256,member_namespace_commitment_sha256=AIHUB538_P6_ACTUAL_V3_MEMBER_NAMESPACE_COMMITMENT_SHA256,design_used_lineage=design_lineage,evaluated_prefix_lineage=candidate_lineage,design_used_actor_count=8,design_used_attempt_count=96,prior_design_actor_intersection_count=0,prior_design_attempt_intersection_count=0,cross_archive_path_collision_count=0,prefix_decisions=decisions)
    draft=Aihub538P6RemediationAuditReceipt(**values,receipt_sha256="0"*64); receipt=Aihub538P6RemediationAuditReceipt(**values,receipt_sha256=sha256_document(draft.unsigned_json_object())); receipt._validate()
    return receipt,fresh

@lru_cache(maxsize=1)
def cohort3():
    aggregates=(aggregate("tl177",("fresh-0",)),aggregate("tl178",("fresh-1",)),aggregate("tl179",tuple(f"fresh-{i}" for i in range(2,8))))
    design=tuple(Aihub538P6RemediationLineage(**{name:row[name] for name in Aihub538P6RemediationLineage.__dataclass_fields__}) for row in AIHUB538_P6_ACTUAL_V3_DESIGN_LABEL_LINEAGE)
    candidate=tuple(_lineage(row,item) for row,item in zip(AIHUB538_P6_REMEDIATION_CANDIDATE_ROWS,aggregates,strict=False)); decisions=tuple(_build_prefix_decision(aggregates[:i]) for i in range(1,4))
    values=dict(design_failure_artifact_receipt_sha256=AIHUB538_P6_ACTUAL_V3_FAILURE_ARTIFACT_SHA256,prior_prefix_receipt_sha256=d("prior-prefix"),identity_namespace_commitment_sha256=AIHUB538_P6_ACTUAL_V3_IDENTITY_NAMESPACE_COMMITMENT_SHA256,member_namespace_commitment_sha256=AIHUB538_P6_ACTUAL_V3_MEMBER_NAMESPACE_COMMITMENT_SHA256,design_used_lineage=design,evaluated_prefix_lineage=candidate,design_used_actor_count=8,design_used_attempt_count=96,prior_design_actor_intersection_count=0,prior_design_attempt_intersection_count=0,cross_archive_path_collision_count=0,prefix_decisions=decisions)
    draft=Aihub538P6RemediationAuditReceipt(**values,receipt_sha256="0"*64); receipt=Aihub538P6RemediationAuditReceipt(**values,receipt_sha256=sha256_document(draft.unsigned_json_object())); receipt._validate(); return receipt,aggregates

def typed_prepared_pairs3(closed):
    _,aggregates=cohort3(); result=[]
    for index,label_aggregate in enumerate(aggregates):
        shard=aihub538_nonlocked_shard_by_id(f"ts{177+index}_tl{177+index}")
        samples=tuple(Aihub538TrainingSampleAggregate(sample_sha256=item.sample_sha256,angle=item.angle,noise_code=0,video_duration_seconds=1.0,audio_duration_seconds=1.0,frames_per_second=30.0,width=256,height=256,lip_bbox_frame_count=30,face_bbox_frame_count=30,sentence_count=1,sentence_duration_seconds=1.0,video_size_bytes=100,audio_size_bytes=100,video_crc32=1,audio_crc32=1,label_selection_token_sha256=d("selection:"+item.sample_sha256)) for item in label_aggregate.items)
        angles=tuple(Aihub538TrainingAngleAggregate(angle,sum(item.angle==angle for item in samples),sum(item.angle==angle for item in samples),sum(item.angle==angle for item in samples),sum(item.angle==angle for item in samples),30*sum(item.angle==angle for item in samples),sum(item.angle==angle for item in samples)) for angle in "ABCDEFGHI")
        training=Aihub538TrainingAdmissionAggregate(label_aggregate.label_archive_sha256,d(f"source-{index}"),label_aggregate.label_content_root_sha256,d(f"source-root-{index}"),samples,len({item.actor_token_sha256 for item in label_aggregate.items}),len(samples),angles,d(f"selection-root-{index}"),d(f"admission-manifest-{index}"),Aihub538RestrictedIdentitySet(label_aggregate.identity_namespace_commitment_sha256,frozenset(item.actor_token_sha256 for item in label_aggregate.items),frozenset(item.attempt_token_sha256 for item in label_aggregate.items)),shard_id=shard.shard_id,dataset_partition=shard.dataset_partition,source_role_component=shard.source_role_component,label_role_component=shard.label_role_component,ordered_file_keys=shard.ordered_file_keys,admission_policy_sha256=aihub538_shard_admission_policy_sha256(shard),sample_selection_rule=shard.sample_selection_rule,restricted_member_set=Aihub538RestrictedMemberSet(label_aggregate.member_namespace_commitment_sha256,frozenset(d("path:"+item.sample_sha256) for item in label_aggregate.items)))
        admission=Aihub538TrainingAdmissionReceipt.issue(training)
        preparation=Aihub538P6LabelPreparationReceipt.issue(shard_spec=AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[2+index],download_archive_sha256=d(f"download-{index}"),secure_download_receipt_sha256=d(f"secure-{index}"),download_verification_stage_sha256=d(f"download-stage-{index}"),outer_content_root_sha256=d(f"outer-{index}"),outer_extraction_stage_sha256=d(f"outer-stage-{index}"),split_archive_kind="zip",split_merge_receipt_sha256=d(f"merge-{index}"),inner_tar_repack_receipt_sha256=None,label_zip_sha256=label_aggregate.label_archive_sha256,label_zip_content_root_sha256=label_aggregate.label_content_root_sha256,label_zip_validation_stage_sha256=d(f"validation-{index}"),label_admission_receipt_sha256=d(f"label-admission-{index}"),label_member_count=len(label_aggregate.items),actor_count=len({item.actor_token_sha256 for item in label_aggregate.items}),attempt_count=len({item.attempt_token_sha256 for item in label_aggregate.items}),metadata_excluded_label_count=0,terminal_media_overrun_label_count=0,terminal_frame_tail_exception_label_count=0,maximum_complete_tail_frames_observed=0,nonterminal_media_overrun_label_count=0,sentence_overlap_label_count=0,invalid_time_label_count=0,nonconforming_fps_label_count=0,terminal_frame_tail_policy_sha256=AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256,identity_namespace_commitment_sha256=label_aggregate.identity_namespace_commitment_sha256,member_namespace_commitment_sha256=label_aggregate.member_namespace_commitment_sha256)
        label_token=label_aggregate.items[0].sample_sha256; public=SimpleNamespace(sample_sha256=d("paired:"+label_token),label_selection_token_sha256=label_token,angle=label_aggregate.items[0].angle,actor_token_sha256=label_aggregate.items[0].actor_token_sha256,attempt_token_sha256=label_aggregate.items[0].attempt_token_sha256); validation=lambda archive,root:SimpleNamespace(archive_sha256=archive,content_root_sha256=root); binding=SimpleNamespace(label=SimpleNamespace(validation_receipt=validation(admission.label_archive_sha256,admission.label_zip_content_root_sha256)),source=SimpleNamespace(validation_receipt=validation(admission.source_archive_sha256,admission.source_zip_content_root_sha256)),admission_receipt_sha256=admission.receipt_sha256,__post_init__=lambda:None)
        private_item=SimpleNamespace(public=public,video_info=SimpleNamespace(filename="unused.mp4")); adapter=object.__new__(Aihub538ActualMediaCalibrationAdapter); adapter._closed=False; adapter._inventory=(public,); adapter._private_items={public.sample_sha256:private_item}; adapter._private_by_label_selection_token={label_token:private_item}; adapter._archive_binding=binding; adapter._identity_hash_key=b"k"*32; adapter._assert_archive_descriptors_stable=lambda:None; adapter._source_zip=SimpleNamespace(read=lambda _info:b"unused"); adapter.close=lambda index=index:closed.append(index); attach_fake_photometric_clip_opener(adapter,lambda _token:b"unused")
        with patch("voice_coach.visual_feedback.adapters.aihub538_actual_media_calibration.assert_aihub538_live_actual_media_adapter"):
            authority=create_aihub538_photometric_source_admission_authority(adapter)
        result.append(Aihub538PhotometricPreparedPairV4(preparation,label_aggregate,admission,admission.admission_manifest_sha256,AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256,authority,SimpleNamespace()))
    return tuple(result)

def color():
    return FfmpegRgbColorimetryReceipt.issue(ffmpeg_revision=f"ffmpeg:test:{d('version')}",adapter_code_sha256=d("color-code"),observed_output_sha256=FFMPEG_RGB_COLORIMETRY_GOLDEN_OUTPUT_SHA256,observed_output_byte_count=48)

def protocol():
    return Aihub538PhotometricCalibrationProtocolV4("p6_v4_frozen",d("qc-policy"),MEASUREMENT_REVISION,d("qc-code"),Aihub538HardSafetyEnvelopeV4(0.75,0.125,0.9,0.9,0.75,0.7,0.7,0.2),4,4,4,0.9,0.25,6.0,0.5,0.5,0.75,0.95,1.6448536269514722,0.5)

def metrics():
    return VideoQcMetrics(8,8,0,1.0,0.0,110.0,0.1,0.02,8.0,8,112.0,0.1,0.01,10.0,1.0,1.0,0.01)

def candidate(marker: str, p: Aihub538PhotometricCalibrationProtocolV4, sample, decoded_digest: str):
    container=sample.container_sha256; timestamps=tuple(i*1000 for i in range(8))
    provenance={"schema_version":"voice_coach.video_qc_provenance.v2","asset_handle_id":sample.asset_handle_id,"container_sha256":container,"video_stream_index":0,"sample_timestamps_us":list(timestamps),"landmark_timestamps_ms":list(range(8)),"decoded_rgb_evidence_sha256":decoded_digest,"landmark_evidence_sha256":d("landmark:"+marker),"landmark_model_sha256":d("model"),"policy_receipt_sha256":p.video_qc_measurement_policy_sha256,"measurement_revision":MEASUREMENT_REVISION,"measurement_adapter_code_sha256":p.video_qc_measurement_adapter_code_sha256}
    unsigned={"schema_version":"voice_coach.video_qc_candidate.v2","status":"candidate_unvalidated",**{k:v for k,v in provenance.items() if k!="schema_version"},"metrics":metrics().to_json_object(),"reason_codes":[],"provenance_receipt_sha256":sha256_document(provenance)}
    return VideoQcCandidateEvidence("candidate_unvalidated",provenance["asset_handle_id"],container,0,timestamps,tuple(range(8)),provenance["decoded_rgb_evidence_sha256"],provenance["landmark_evidence_sha256"],provenance["landmark_model_sha256"],p.video_qc_measurement_policy_sha256,MEASUREMENT_REVISION,p.video_qc_measurement_adapter_code_sha256,metrics(),(),sha256_document(provenance),sha256_document(unsigned))

def decoded_sample_digest(sample):
    return sha256_document({"schema_version":"voice_coach.decoded_rgb_sample_evidence.v1","asset_handle_id":sample.asset_handle_id,"container_sha256":sample.container_sha256,"video_stream_index":sample.video_stream_index,"ffmpeg_revision":sample.ffmpeg_revision,"frames":[{"timestamp_us":frame.timestamp_us,"width":frame.width,"height":frame.height,"rgb24_sha256":hashlib.sha256(frame.rgb24).hexdigest()} for frame in sample.frames]})

def as_production_evidence(value):
    return Aihub538PhotometricCalibrationEvidenceV4.issue(protocol=value.protocol,colorimetry_receipt=value.colorimetry_receipt,policy=value.policy,cohort_separation_receipt=value.cohort_separation_receipt,source_manifest_sha256=value.source_manifest_sha256,admission_receipt_sha256=value.admission_receipt_sha256,development_aggregate=value.development_aggregate,calibration_aggregate=value.calibration_aggregate,actor_clustered_one_sided_ci=value.actor_clustered_one_sided_ci,production_execution_policy_sha256=AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256,ordered_sampling_receipt_commitment_sha256=d("synthetic-ordered-sampling-receipts"),pair_count=3,remediation_receipt_sha256=value.remediation_receipt_sha256,ordered_pair_lineage_commitment_sha256=value.ordered_pair_lineage_commitment_sha256,selected_member_commitment_sha256=value.selected_member_commitment_sha256,selected_member_count=144,ordered_sampling_receipt_count=144)

_MEDIA_CACHE=[]; _MEDIA_BYTES={}
def media_lineages(count:int):
    if not _MEDIA_CACHE:
        folder=Path(tempfile.mkdtemp(prefix="p6-v4-media-cache-")); base=folder/"base.mp4"
        subprocess.run(["/usr/bin/ffmpeg","-v","error","-f","lavfi","-i","color=c=gray:s=160x120:r=15:d=2","-pix_fmt","yuv420p","-color_range","tv","-colorspace","bt709","-color_trc","bt709","-color_primaries","bt709",str(base)],check=True)
        original=base.read_bytes(); adapter=FfmpegRgbColorimetryAdapter()
        for index in range(count):
            path=folder/f"{index}.mp4"; path.write_bytes(original+b"P6V4"+index.to_bytes(4,"big")); fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
            try:
                result=adapter.probe_and_convert_fd(video_fd=fd); _MEDIA_CACHE.append(result); _MEDIA_BYTES[result[3].container_sha256]=path.read_bytes()
            finally:os.close(fd)
    return _MEDIA_CACHE[:count]

def test_photometric_source_mux_creates_policy_sized_bt709_envelope(tmp_path):
    video=tmp_path/"long-source.mp4"; audio=tmp_path/"long-source.wav"; output=tmp_path/"derived.mp4"
    subprocess.run(["/usr/bin/ffmpeg","-v","error","-f","lavfi","-i","color=c=gray:s=1920x1080:r=30:d=3","-pix_fmt","yuv420p",str(video)],check=True)
    subprocess.run(["/usr/bin/ffmpeg","-v","error","-f","lavfi","-i","anullsrc=r=16000:cl=mono","-t","3",str(audio)],check=True)
    video_fd=os.open(video,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW); audio_fd=os.open(audio,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW); output_fd=os.open(output,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
    try:
        _run_mux(ffmpeg_binary="/usr/bin/ffmpeg",video_descriptor=video_fd,audio_descriptor=audio_fd,output_descriptor=output_fd,start_seconds=0.5,duration_seconds=1.5,repository_root=Path.cwd())
    finally: os.close(video_fd); os.close(audio_fd); os.close(output_fd)
    fd=os.open(output,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        bundle=Aihub538PhotometricV4ProductionFactory().color_sampler().sample(video_fd=fd)
    finally: os.close(fd)
    assert 1_400_000 <= bundle.sampling_receipt.duration_us <= 1_500_000
    assert bundle.source_probe_receipt.container_sha256==bundle.sample.container_sha256

def bound_input(*,reuse_first_media:bool=False,include_production:bool=False,consume_measurements:bool=True):
    receipt,fresh=cohort(); p=protocol()
    store=Aihub538PhotometricV4IssuanceStore(Path(tempfile.mkdtemp(prefix="p6-v4-test-")))
    cap=issue_aihub538_photometric_cohort_capability_v4(remediation_receipt=receipt,candidate_aggregates=(fresh,),source_manifest_sha256=d("manifest"),admission_receipt_sha256=d("admission"),issuance_store=store)
    measurements=[]; lineages=media_lineages(len(cap.members)); c=lineages[-1][0]
    validation=lambda archive,root:SimpleNamespace(archive_sha256=archive,content_root_sha256=root)
    binding=SimpleNamespace(label=SimpleNamespace(validation_receipt=validation(fresh.label_archive_sha256,fresh.label_content_root_sha256)),source=SimpleNamespace(validation_receipt=validation(d("source-archive"),d("source-root"))),admission_receipt_sha256=d("admission"),__post_init__=lambda:None)
    private={}; private_by_label={}; inventory=[]
    for member,lineage in zip(cap.members,lineages,strict=True):
        sample=lineage[3]; label_token=member.item.sample_sha256; public=SimpleNamespace(sample_sha256=d("paired:"+label_token),label_selection_token_sha256=label_token,angle=member.item.angle,actor_token_sha256=member.item.actor_token_sha256,attempt_token_sha256=member.item.attempt_token_sha256); private_item=SimpleNamespace(public=public,video_info=SimpleNamespace(filename=label_token+".mp4")); inventory.append(public); private[public.sample_sha256]=private_item; private_by_label[label_token]=private_item
    adapter=object.__new__(Aihub538ActualMediaCalibrationAdapter); adapter._closed=False; adapter._inventory=tuple(inventory); adapter._private_items=private; adapter._private_by_label_selection_token=private_by_label; adapter._archive_binding=binding; adapter._identity_hash_key=b"k"*32; adapter._assert_archive_descriptors_stable=lambda:None; adapter._source_zip=SimpleNamespace(read=lambda info:next(_MEDIA_BYTES[lineage[3].container_sha256] for member,lineage in zip(cap.members,lineages,strict=True) if info.filename==member.item.sample_sha256+".mp4")); attach_fake_photometric_clip_opener(adapter,lambda token:next(_MEDIA_BYTES[lineage[3].container_sha256] for member,lineage in zip(cap.members,lineages,strict=True) if member.item.sample_sha256==token))
    with patch("voice_coach.visual_feedback.adapters.aihub538_actual_media_calibration.assert_aihub538_live_actual_media_adapter"):
        authority=create_aihub538_photometric_source_admission_authority(adapter)
    if not consume_measurements:
        return None,p,c,authority,receipt,fresh,cap
    for index,(member,lineage) in enumerate(zip(cap.members,lineages,strict=True)):
        _,probe,conversion,sample=lineages[0] if reuse_first_media and index==1 else lineage
        qc=candidate(str(index),p,sample,conversion.decoded_rgb_evidence_sha256)
        mapping=issue_aihub538_member_source_mapping(authority=authority,member=member)
        begin_aihub538_member_source_open(mapping); source_admission_module._MAPPING_PROOFS[mapping].add("source_closed"); source_admission_module._MAPPING_STATE[mapping]="source_closed"; mark_aihub538_member_measurement_bound(mapping); finish_aihub538_member_source_open(mapping,succeeded=True)
        measurements.append(Aihub538PhotometricMeasurementV4(member,probe,conversion,qc,mapping))
    result=(bind_aihub538_photometric_measurements_v4(capability=cap,measurements=tuple(measurements)),p,c)
    return result+(authority,receipt,fresh) if include_production else result

def test_single_pair_production_facade_rejected_as_incomplete_run(tmp_path):
    _,p,_,authority,receipt,fresh,_=bound_input(include_production=True,consume_measurements=False); counter=iter(range(1000))
    class Opener:
        @contextmanager
        def open_member_fd(self,mapping):
            path=tmp_path/(mapping.container_sha256+".mp4"); path.write_bytes(_MEDIA_BYTES[mapping.container_sha256]); path.chmod(0o400); fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
            try: yield fd
            finally:
                os.close(fd); source_admission_module._MAPPING_PROOFS[mapping].add("source_closed"); source_admission_module._MAPPING_STATE[mapping]="source_closed"
    def qc(sample):
        return candidate(str(next(counter)),p,sample,decoded_sample_digest(sample))
    with pytest.raises(VisualFeedbackContractError,match="production run identity differs"):
        Aihub538PhotometricV4ProductionRunner(source_opener=Opener(),source_authority=authority,qc_measure=qc,production_factory=Aihub538PhotometricV4ProductionFactory(),issuance_store=Aihub538PhotometricV4IssuanceStore(tmp_path/"issuance")).run(remediation_receipt=receipt,candidate_aggregates=(fresh,),source_manifest_sha256=d("manifest"),admission_receipt_sha256=d("admission"),protocol=p)

def test_same_video_reuse_across_members_rejected():
    with pytest.raises((Aihub538PhotometricCalibrationV4Error,VisualFeedbackContractError),match="media_reuse_forbidden|lineage differs"):
        bound_input(reuse_first_media=True)

def test_object_new_actual_adapter_cannot_issue_production_authority():
    forged=object.__new__(Aihub538ActualMediaCalibrationAdapter); forged._closed=False
    with pytest.raises(Exception,match="adapter_capability_invalid"):
        create_aihub538_photometric_source_admission_authority(forged)

def test_production_runner_rejects_single_frame_adapter_and_wrong_factory(tmp_path):
    with pytest.raises(TypeError):
        Aihub538PhotometricV4ProductionRunner(source_opener=SimpleNamespace(),source_authority=SimpleNamespace(),qc_measure=lambda value:value,color_adapter=FfmpegRgbColorimetryAdapter(),issuance_store=Aihub538PhotometricV4IssuanceStore(tmp_path/"old"))
    with pytest.raises(ValueError,match="production_factory_required"):
        Aihub538PhotometricV4ProductionRunner(source_opener=SimpleNamespace(),source_authority=SimpleNamespace(),qc_measure=lambda value:value,production_factory=object(),issuance_store=Aihub538PhotometricV4IssuanceStore(tmp_path/"wrong"))

def test_mapping_lifecycle_is_atomic_and_failure_revokes():
    _,_,_,authority,_,_,capability=bound_input(include_production=True,consume_measurements=False)
    member=capability.members[0]
    mapping=issue_aihub538_member_source_mapping(authority=authority,member=member)
    with pytest.raises(ValueError,match="already_issued"):
        issue_aihub538_member_source_mapping(authority=authority,member=member)
    begin_aihub538_member_source_open(mapping)
    with pytest.raises(ValueError,match="capability_invalid"):
        begin_aihub538_member_source_open(mapping)
    with pytest.raises(ValueError,match="measurement_proof_required"):
        finish_aihub538_member_source_open(mapping,succeeded=True)
    with pytest.raises(ValueError,match="capability_invalid"):
        begin_aihub538_member_source_open(mapping)

def test_three_pair_ordered_lineage_rejects_route_forgery_before_issuance(tmp_path):
    receipt,aggregates=cohort3()
    pairs=tuple(Aihub538PhotometricPairLineageV4(item.label_archive_sha256,item.label_content_root_sha256,d(f"source-{i}"),d(f"source-root-{i}"),d(f"manifest-{i}"),d(f"admission-{i}"),aihub538_shard_admission_policy_sha256(aihub538_nonlocked_shard_by_id(f"ts{177+i}_tl{177+i}")),AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256) for i,item in enumerate(aggregates))
    kwargs=dict(remediation_receipt=receipt,candidate_aggregates=aggregates,source_manifest_sha256=sha256_document({"schema_version":"voice_coach.p6_v4_ordered_source_manifests.v1","length":3,"values":[pair.source_manifest_sha256 for pair in pairs]}),admission_receipt_sha256=sha256_document({"schema_version":"voice_coach.p6_v4_ordered_admission_receipts.v1","length":3,"values":[pair.admission_receipt_sha256 for pair in pairs]}))
    capability=issue_aihub538_photometric_cohort_capability_v4(**kwargs,pair_lineages=pairs,issuance_store=Aihub538PhotometricV4IssuanceStore(tmp_path/"ok"))
    assert {member.label_archive_sha256 for member in capability.members}=={item.label_archive_sha256 for item in aggregates}
    attacks=(pairs[:-1],(pairs[1],pairs[0],pairs[2]),(pairs[0],pairs[0],pairs[2]),(replace(pairs[0],label_content_root_sha256=d("foreign-root")),pairs[1],pairs[2]),(replace(pairs[0],admission_policy_sha256=d("wrong-policy")),pairs[1],pairs[2]))
    for index,forged in enumerate(attacks):
        store=Aihub538PhotometricV4IssuanceStore(tmp_path/f"attack-{index}")
        with pytest.raises(Aihub538PhotometricCalibrationV4Error): issue_aihub538_photometric_cohort_capability_v4(**kwargs,pair_lineages=forged,issuance_store=store)
        assert not list(store.directory.glob("capability-*.json"))

def test_three_pair_runner_routes_every_member_to_exact_authority(tmp_path):
    receipt,aggregates=cohort3(); lineages=media_lineages(sum(len(item.items) for item in aggregates)); offset=0; pairs=[]; routes={}; seen=[]
    for pair_index,aggregate_value in enumerate(aggregates):
        pair=Aihub538PhotometricPairLineageV4(aggregate_value.label_archive_sha256,aggregate_value.label_content_root_sha256,d(f"source-{pair_index}"),d(f"source-root-{pair_index}"),d(f"manifest-{pair_index}"),d(f"admission-{pair_index}"),aihub538_shard_admission_policy_sha256(aihub538_nonlocked_shard_by_id(f"ts{177+pair_index}_tl{177+pair_index}")),AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256); pairs.append(pair)
        private={}; private_by_label={}; inventory=[]; payloads={}
        for item,lineage in zip(aggregate_value.items,lineages[offset:offset+len(aggregate_value.items)],strict=True):
            label_token=item.sample_sha256; public=SimpleNamespace(sample_sha256=d("paired:"+label_token),label_selection_token_sha256=label_token,angle=item.angle,actor_token_sha256=item.actor_token_sha256,attempt_token_sha256=item.attempt_token_sha256); name=label_token+".mp4"; private_item=SimpleNamespace(public=public,video_info=SimpleNamespace(filename=name)); inventory.append(public); private[public.sample_sha256]=private_item; private_by_label[label_token]=private_item; payloads[name]=_MEDIA_BYTES[lineage[3].container_sha256]
        offset+=len(aggregate_value.items); validation=lambda archive,root:SimpleNamespace(archive_sha256=archive,content_root_sha256=root); binding=SimpleNamespace(label=SimpleNamespace(validation_receipt=validation(pair.label_archive_sha256,pair.label_content_root_sha256)),source=SimpleNamespace(validation_receipt=validation(pair.source_archive_sha256,pair.source_content_root_sha256)),admission_receipt_sha256=pair.admission_receipt_sha256,__post_init__=lambda:None)
        adapter=object.__new__(Aihub538ActualMediaCalibrationAdapter); adapter._closed=False; adapter._inventory=tuple(inventory); adapter._private_items=private; adapter._private_by_label_selection_token=private_by_label; adapter._archive_binding=binding; adapter._identity_hash_key=b"k"*32; adapter._assert_archive_descriptors_stable=lambda:None; adapter._source_zip=SimpleNamespace(read=lambda info,payloads=payloads:payloads[info.filename]); attach_fake_photometric_clip_opener(adapter,lambda token,payloads=payloads:payloads[token+".mp4"])
        with patch("voice_coach.visual_feedback.adapters.aihub538_actual_media_calibration.assert_aihub538_live_actual_media_adapter"):
            authority=create_aihub538_photometric_source_admission_authority(adapter)
        class Opener:
            def __init__(self,expected): self.expected=expected
            @contextmanager
            def open_member_fd(self,mapping):
                assert mapping.source_archive_sha256==self.expected; seen.append(self.expected); path=tmp_path/(mapping.container_sha256+".mp4"); path.write_bytes(_MEDIA_BYTES[mapping.container_sha256]); path.chmod(0o400); fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
                try: yield fd
                finally:
                    os.close(fd); source_admission_module._MAPPING_PROOFS[mapping].add("source_closed"); source_admission_module._MAPPING_STATE[mapping]="source_closed"
        routes[pair.label_archive_sha256]=(authority,Opener(pair.source_archive_sha256))
    p=frozen_aihub538_photometric_v4_protocol(); counter=iter(range(1000))
    def qc(sample):
        return candidate(str(next(counter)),p,sample,decoded_sample_digest(sample))
    production_store=Aihub538PhotometricV4IssuanceStore(tmp_path/"multi-store"); runner=Aihub538PhotometricV4MultiPairRunner(routes=routes,qc_measure=qc,production_factory=Aihub538PhotometricV4ProductionFactory(),issuance_store=production_store)
    evidence=runner.run(remediation_receipt=receipt,candidate_aggregates=aggregates,pair_lineages=tuple(pairs),protocol=p)
    assert evidence.to_json_object()["promotion_authorized"] is False and len(seen)==144 and set(seen)=={pair.source_archive_sha256 for pair in pairs}
    assert evidence.production_execution_policy_sha256==AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256
    assert evidence.ordered_sampling_receipt_commitment_sha256!=sha256_document({"schema_version":"voice_coach.p6_v4_ordered_sampling_receipts.v1","values":[]})
    from voice_coach.visual_feedback.services.aihub538_photometric_v4_runner import consume_aihub538_photometric_v4_production_run,finalize_aihub538_photometric_v4_production_run
    with pytest.raises(ValueError,match="production_run_capability_invalid"):
        consume_aihub538_photometric_v4_production_run(evidence,runner=runner,store=Aihub538PhotometricV4IssuanceStore(tmp_path/"foreign-store"),remediation_receipt=receipt)
    actual=consume_aihub538_photometric_v4_production_run(evidence,runner=runner,store=production_store,remediation_receipt=receipt)
    read_fd,write_fd=os.pipe(); child=os.fork()
    if child==0:
        os.close(read_fd)
        try:
            finalize_aihub538_photometric_v4_production_run(evidence,runner=runner,store=production_store,remediation_receipt=receipt,succeeded=True)
        except ValueError as error:
            os.write(write_fd,str(error).encode("ascii"))
        else:
            os.write(write_fd,b"accepted")
        finally:
            os.close(write_fd)
        os._exit(0)
    os.close(write_fd); child_result=os.read(read_fd,4096).decode("ascii"); os.close(read_fd); _,child_status=os.waitpid(child,0)
    assert os.waitstatus_to_exitcode(child_status)==0 and "production_run_capability_invalid" in child_result
    artifact=tmp_path/"capability-artifact.json"; artifact.write_text(json.dumps(actual.to_json_object(),sort_keys=True,separators=(",",":"))+"\n"); artifact.chmod(0o400)
    reload_receipt=production_store.launch_fresh_process_reload(artifact_path=artifact,artifact_sha256=actual.artifact_sha256); production_store.validate_fresh_process_reload_receipt(reload_receipt,artifact_path=artifact); finalize_aihub538_photometric_v4_production_run(evidence,runner=runner,store=production_store,remediation_receipt=receipt,succeeded=True)
    assert stat.S_IMODE(artifact.stat().st_mode)==0o400
    with pytest.raises(ValueError,match="production_run_capability_invalid"):
        consume_aihub538_photometric_v4_production_run(evidence,runner=runner,store=production_store,remediation_receipt=receipt)
    with pytest.raises(ValueError,match="production_run_capability_invalid"):
        finalize_aihub538_photometric_v4_production_run(evidence,runner=runner,store=production_store,remediation_receipt=receipt,succeeded=True)

def test_orchestrator_rejects_custom_runner_evidence_before_artifact(tmp_path):
    value,p,c=bound_input(); evidence=as_production_evidence(Aihub538PhotometricCalibrationV4Service(protocol=p,colorimetry_receipt=c).calibrate(calibration_input=value)); closed=[]; pairs=typed_prepared_pairs3(closed)
    class Runner:
        def run(self,**_kwargs): return evidence
    output=tmp_path/"output"; output.mkdir(mode=0o700); sentinel=output/"keep"; sentinel.write_text("preserve")
    store=Aihub538PhotometricV4IssuanceStore(tmp_path/"orchestrator-store")
    with pytest.raises(ValueError,match="exact_multi_pair_runner_required"):
        Aihub538PhotometricV4ProductionOrchestrator(runner_factory=lambda **_kwargs:Runner(),issuance_store=store,output_directory=output).run(remediation_receipt=cohort3()[0],pairs=pairs,protocol=frozen_aihub538_photometric_v4_protocol())
    assert sorted(closed)==[0,1,2] and sentinel.read_text()=="preserve" and not (output/"p6-photometric-v4-artifact.json").exists()

def test_orchestrator_failure_is_atomic_and_closes_all_authorities(tmp_path,monkeypatch):
    value,p,c=bound_input(); evidence=as_production_evidence(Aihub538PhotometricCalibrationV4Service(protocol=p,colorimetry_receipt=c).calibrate(calibration_input=value)); closed=[]; pairs=typed_prepared_pairs3(closed); output=tmp_path/"output"; output.mkdir(mode=0o700); sentinel=output/"keep"; sentinel.write_text("preserve")
    class Runner:
        def run(self,**_kwargs): return evidence
    real_write=orchestrator_module.os.write; calls=0
    def interrupted(fd,payload):
        nonlocal calls; calls+=1
        if calls==1:return real_write(fd,payload[:max(1,len(payload)//2)])
        raise OSError("synthetic interruption")
    monkeypatch.setattr(orchestrator_module.os,"write",interrupted)
    with pytest.raises(ValueError,match="exact_multi_pair_runner_required"):
        Aihub538PhotometricV4ProductionOrchestrator(runner_factory=lambda **_kwargs:Runner(),issuance_store=Aihub538PhotometricV4IssuanceStore(tmp_path/"failure-store"),output_directory=output).run(remediation_receipt=cohort3()[0],pairs=pairs,protocol=frozen_aihub538_photometric_v4_protocol())
    assert sorted(closed)==[0,1,2] and sentinel.read_text()=="preserve"
    assert not (output/"p6-photometric-v4-artifact.json").exists() and not (output/".p6-photometric-v4-artifact.tmp").exists()

def test_orchestrator_prepared_label_mismatch_closes_before_artifact(tmp_path):
    closed=[]; pairs=list(typed_prepared_pairs3(closed)); pairs[0]=replace(pairs[0],preparation_receipt=replace(pairs[0].preparation_receipt,label_zip_sha256=d("foreign-prepared-label")))
    output=tmp_path/"output"; output.mkdir(mode=0o700)
    with pytest.raises(ValueError,match="prepared_pair_lineage_invalid|label_preparation_invalid"):
        Aihub538PhotometricV4ProductionOrchestrator(runner_factory=lambda **_kwargs:pytest.fail("runner must not start"),issuance_store=Aihub538PhotometricV4IssuanceStore(tmp_path/"mismatch-store"),output_directory=output).run(remediation_receipt=cohort3()[0],pairs=tuple(pairs),protocol=frozen_aihub538_photometric_v4_protocol())
    assert sorted(closed)==[0,1,2] and not list(output.iterdir())

def test_prepared_pair_requires_exact_frozen_execution_policy():
    pair=typed_prepared_pairs3([])[0]
    assert pair.validate(expected_shard_id="ts177_tl177")
    for invalid in ("",d("foreign-execution-policy")):
        with pytest.raises((ValueError,VisualFeedbackContractError)):
            replace(pair,production_execution_policy_sha256=invalid).validate(expected_shard_id="ts177_tl177")

def test_remediation_typed_input_artifact_reload_and_duplicate_release():
    value,p,c=bound_input(); service=Aihub538PhotometricCalibrationV4Service(protocol=p,colorimetry_receipt=c)
    evidence=service.calibrate(calibration_input=value)
    assert evidence.actor_clustered_one_sided_ci.passed
    assert evidence.calibration_aggregate.actor_outcome_distribution.passed_actor_count==4
    assert Aihub538PhotometricCalibrationEvidenceV4.from_json_object(evidence.to_json_object())==evidence
    with pytest.raises(Aihub538PhotometricCalibrationV4Error,match="duplicate_release"): service.calibrate(calibration_input=value)

def test_distribution_and_artifact_forgery_rejected():
    value,p,c=bound_input(); evidence=Aihub538PhotometricCalibrationV4Service(protocol=p,colorimetry_receipt=c).calibrate(calibration_input=value)
    doc=evidence.to_json_object(); doc["calibration_aggregate"]["actor_outcome_distribution"]["passed_actor_count"]=3
    with pytest.raises(VisualFeedbackContractError): Aihub538PhotometricCalibrationEvidenceV4.from_json_object(doc)
    with pytest.raises(VisualFeedbackContractError,match="impossible"): Aihub538ActorOutcomeDistributionV4(4,16,16,4,(),3,16,16,4)
    with pytest.raises(VisualFeedbackContractError,match="impossible"): Aihub538ActorOutcomeDistributionV4(4,16,0,1,(),4,16,0,1)
    with pytest.raises(VisualFeedbackContractError,match="impossible"): Aihub538ActorOutcomeDistributionV4(4,16,16,0,(),4,16,16,0)

def test_namespace_selection_root_and_unissued_capability_rejected():
    receipt,fresh=cohort()
    store=Aihub538PhotometricV4IssuanceStore(Path(tempfile.mkdtemp(prefix="p6-v4-forge-")))
    with pytest.raises(Aihub538PhotometricCalibrationV4Error): issue_aihub538_photometric_cohort_capability_v4(remediation_receipt=receipt,candidate_aggregates=(replace(fresh,identity_namespace_commitment_sha256=d("wrong")),),source_manifest_sha256=d("m"),admission_receipt_sha256=d("a"),issuance_store=store)
    items=list(fresh.items); items[0]=replace(items[0],sample_sha256=d("forged"))
    with pytest.raises(Aihub538PhotometricCalibrationV4Error): issue_aihub538_photometric_cohort_capability_v4(remediation_receipt=receipt,candidate_aggregates=(replace(fresh,items=tuple(items)),),source_manifest_sha256=d("m"),admission_receipt_sha256=d("a"),issuance_store=store)
    changed=list(fresh.items); changed[0]=replace(changed[0],attempt_token_sha256=d("changed-attempt"))
    with pytest.raises(Aihub538PhotometricCalibrationV4Error): issue_aihub538_photometric_cohort_capability_v4(remediation_receipt=receipt,candidate_aggregates=(replace(fresh,items=tuple(changed)),),source_manifest_sha256=d("m"),admission_receipt_sha256=d("a"),issuance_store=store)
    swapped=list(fresh.items); swapped[0]=replace(swapped[0],angle="B" if swapped[0].angle!="B" else "C")
    with pytest.raises(Aihub538PhotometricCalibrationV4Error): issue_aihub538_photometric_cohort_capability_v4(remediation_receipt=receipt,candidate_aggregates=(replace(fresh,items=tuple(swapped)),),source_manifest_sha256=d("m"),admission_receipt_sha256=d("a"),issuance_store=store)
    forged=Aihub538PhotometricCohortCapabilityV4(receipt,receipt.receipt_sha256,receipt.identity_namespace_commitment_sha256,receipt.member_namespace_commitment_sha256,d("m"),d("a"),(),store,d("fake-receipt"),1,d("pairs"),d("members"))
    with pytest.raises(Aihub538PhotometricCalibrationV4Error,match="capability_invalid"): bind_aihub538_photometric_measurements_v4(capability=forged,measurements=())

def test_durable_store_rejects_duplicate_capability_across_instances(tmp_path):
    receipt,fresh=cohort(); store=Aihub538PhotometricV4IssuanceStore(tmp_path)
    kwargs=dict(remediation_receipt=receipt,candidate_aggregates=(fresh,),source_manifest_sha256=d("m"),admission_receipt_sha256=d("a"))
    issue_aihub538_photometric_cohort_capability_v4(**kwargs,issuance_store=store)
    with pytest.raises(Aihub538PhotometricV4IssuanceError,match="duplicate_capability"):
        issue_aihub538_photometric_cohort_capability_v4(**kwargs,issuance_store=Aihub538PhotometricV4IssuanceStore(tmp_path))

def test_store_rejects_rename_recreate_and_same_process_reload_fake(tmp_path):
    store=Aihub538PhotometricV4IssuanceStore(tmp_path); artifact=d("artifact")
    nonce=store.create_reload_worker_challenge(artifact,expected_worker_pid=os.getpid(),artifact_file_sha256=d("file"),artifact_identity_sha256=d("identity"))
    with pytest.raises(Aihub538PhotometricV4IssuanceError,match="challenge_invalid"):
        store.claim_reload_from_worker(artifact_sha256=artifact,nonce=nonce)
    moved=tmp_path.with_name(tmp_path.name+"-moved"); os.rename(tmp_path,moved); tmp_path.mkdir(mode=0o700)
    with pytest.raises(Aihub538PhotometricV4IssuanceError,match="directory_replaced"):
        store.claim(kind="artifact",key_sha256=d("k"),payload_sha256=d("p"))

def test_legacy_loose_api_and_pass_all_protocol_rejected():
    assert sha256_document(AIHUB538_PHOTOMETRIC_V4_FROZEN_SAFETY_AND_STATISTICS)==AIHUB538_PHOTOMETRIC_V4_FROZEN_SAFETY_AND_STATISTICS_SHA256
    _,p,c=bound_input(); service=Aihub538PhotometricCalibrationV4Service(protocol=p,colorimetry_receipt=c)
    with pytest.raises(TypeError,match="unexpected keyword"): service.calibrate(observations=())  # type: ignore[call-arg]
    with pytest.raises(VisualFeedbackContractError,match="frozen protocol differs"): replace(protocol(),hard_safety_envelope=replace(protocol().hard_safety_envelope,minimum_single_face_coverage=0.0))

def test_color_contract_semantics_and_path_restriction():
    assert FfmpegRgbColorimetryReceipt.from_json_object(color().to_json_object())==color()
    with pytest.raises(ValueError): FfmpegRgbColorimetryAdapter(ffmpeg_binary="/usr/bin/ffmpeg")
    with pytest.raises(VisualFeedbackContractError): FfmpegSourceColorProbeReceiptV4.issue(container_sha256=d("c"),video_stream_index=0,ffprobe_revision="ffprobe",probe_adapter_code_sha256=d("p"),pixel_format="yuv420p",color_range="pc",color_matrix="bt709",color_transfer="bt709",color_primaries="bt709",metadata_stdout_sha256=d("meta"),stderr_sha256=hashlib.sha256(b"").hexdigest())

def test_probe_conversion_candidate_lineage_forgery_rejected():
    value,_,c=bound_input(); original=value.measurements[0]
    with pytest.raises(VisualFeedbackContractError,match="source admission authority differs"):
        replace(original,source_mapping=replace(original.source_mapping,source_archive_sha256=d("forged-source")))
    wrong_probe=FfmpegSourceColorProbeReceiptV4.issue(container_sha256=d("wrong-container"),video_stream_index=0,ffprobe_revision="ffprobe_test",probe_adapter_code_sha256=d("probe"),pixel_format="yuv420p",color_range="tv",color_matrix="bt709",color_transfer="bt709",color_primaries="bt709",metadata_stdout_sha256=d("meta"),stderr_sha256=hashlib.sha256(b"").hexdigest())
    wrong_conversion=FfmpegRgbSourceConversionReceiptV4.issue(source_probe_receipt=wrong_probe,adapter_colorimetry_receipt=c,decoded_rgb_evidence_sha256=original.candidate.decoded_rgb_evidence_sha256,ffmpeg_revision=c.ffmpeg_revision)
    with pytest.raises(VisualFeedbackContractError,match="lineage differs"):
        Aihub538PhotometricMeasurementV4(original.member,wrong_probe,wrong_conversion,original.candidate,original.source_mapping)

def test_real_ffmpeg_range_matrix_golden():
    assert FfmpegRgbColorimetryAdapter().verify_conversion_contract().observed_output_sha256==FFMPEG_RGB_COLORIMETRY_GOLDEN_OUTPUT_SHA256

def test_actual_fd_probe_and_rgb_conversion_subprocess_e2e(tmp_path):
    video=tmp_path/"tiny.mp4"
    subprocess.run(["/usr/bin/ffmpeg","-v","error","-f","lavfi","-i","color=c=gray:s=256x256:r=1","-frames:v","1","-pix_fmt","yuv420p","-color_range","tv","-colorspace","bt709","-color_trc","bt709","-color_primaries","bt709",str(video)],check=True)
    fd=os.open(video,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        _,probe,conversion,sample=FfmpegRgbColorimetryAdapter().probe_and_convert_fd(video_fd=fd)
    finally:
        os.close(fd)
    assert probe.container_sha256==sample.container_sha256
    assert conversion.decoded_rgb_evidence_sha256
    assert len(sample.frames)==1
    assert len(sample.frames[0].rgb24)==256*256*3
    writable=os.open(video,os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        with pytest.raises(Exception,match="source_fd_unreadable"):
            FfmpegRgbColorimetryAdapter().probe_and_convert_fd(video_fd=writable)
    finally:os.close(writable)
    assert _validate_rgb_geometry(1920,1080)==1920*1080*3
    with pytest.raises(Exception,match="geometry_out_of_bounds"):_validate_rgb_geometry(1920,1081)
    with pytest.raises(Exception,match="geometry_out_of_bounds"):_validate_rgb_geometry(3840,2160)
    with pytest.raises(Exception,match="geometry_out_of_bounds"):_validate_rgb_geometry(0,1080)

def test_fresh_process_strict_reload_receipt_and_duplicate_rejected(tmp_path):
    value,p,c=bound_input(); evidence=Aihub538PhotometricCalibrationV4Service(protocol=p,colorimetry_receipt=c).calibrate(calibration_input=value)
    artifact=tmp_path/"artifact.json"; artifact.write_text(json.dumps(evidence.to_json_object()),encoding="utf-8")
    receipt=value.capability.issuance_store.launch_fresh_process_reload(artifact_path=artifact,artifact_sha256=evidence.artifact_sha256)
    assert receipt["fresh_process_reload_required"] is True and receipt["fresh_process_reload_completed"] is True
    foreign_store=Aihub538PhotometricV4IssuanceStore(value.capability.issuance_store.directory)
    with pytest.raises(Aihub538PhotometricV4IssuanceError,match="parent_launch_capability_invalid"):
        foreign_store.validate_fresh_process_reload_receipt(receipt,artifact_path=artifact)
    durable_path=value.capability.issuance_store.directory/f"reload-{evidence.artifact_sha256}.json"; durable_raw=durable_path.read_bytes(); durable=json.loads(durable_raw); durable["manual_forgery"]=True; durable_path.write_text(json.dumps(durable),encoding="utf-8")
    with pytest.raises(Aihub538PhotometricV4IssuanceError,match="durable_record_invalid"):
        value.capability.issuance_store.validate_fresh_process_reload_receipt(receipt,artifact_path=artifact)
    durable_path.write_bytes(durable_raw)
    assert value.capability.issuance_store.validate_fresh_process_reload_receipt(receipt,artifact_path=artifact)==receipt["receipt_sha256"]
    with pytest.raises(Aihub538PhotometricV4IssuanceError,match="parent_launch_capability_invalid|duplicate_issuance"):
        value.capability.issuance_store.validate_fresh_process_reload_receipt(receipt,artifact_path=artifact)
    copied=tmp_path/"copied-artifact.json"; copied.write_bytes(artifact.read_bytes())
    with pytest.raises(Aihub538PhotometricV4IssuanceError,match="durable_record_invalid|parent_launch_capability_invalid"):
        value.capability.issuance_store.validate_fresh_process_reload_receipt(receipt,artifact_path=copied)
    with pytest.raises(Aihub538PhotometricV4IssuanceError):
        value.capability.issuance_store.validate_fresh_process_reload_receipt({**receipt,"fresh_process_reload_completed":False},artifact_path=artifact)
    with pytest.raises(Aihub538PhotometricV4IssuanceError): value.capability.issuance_store.launch_fresh_process_reload(artifact_path=artifact,artifact_sha256=evidence.artifact_sha256)
