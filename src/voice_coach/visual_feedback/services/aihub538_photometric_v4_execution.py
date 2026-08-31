"""Strict, path-safe public execution boundary for future P6 v4 pairs."""
from __future__ import annotations
from dataclasses import dataclass,field
from weakref import WeakKeyDictionary
import fcntl,hashlib,json,os,stat
from pathlib import Path
from typing import Callable
from contextlib import ExitStack,contextmanager

from ...pronunciation_attempts.adapters.mediapipe_lip_landmarks import MediaPipeLipLandmarkAdapter
from ...pronunciation_attempts.adapters.video_qc import MEASUREMENT_REVISION,VideoQcCandidateAdapter,video_qc_measurement_code_sha256
from ...pronunciation_attempts.models import Rgb24Sample
from ..adapters.aihub538_photometric_v4_production_factory import Aihub538PhotometricV4ProductionFactory
from ..models.aihub538_photometric_calibration_v4 import Aihub538HardSafetyEnvelopeV4,Aihub538PhotometricCalibrationProtocolV4
from ..models.aihub538_photometric_v4_production_policy import AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256
from ..models.integrity import sha256_document

_VIDEO_QC_SOURCE_SHA256="6e4bba93ffc4186eee4f0047a774fcadadf3110425cc38d558bf105a4259d5da"
_PREFIX_KEYS={"schema_version","status","dataset_id","remediation_receipt_sha256","pair_handoff_receipt_sha256","ordered_shard_ids","identity_namespace_commitment_sha256","production_execution_policy_sha256","raw_identifiers_serialized","member_names_serialized","source_paths_serialized","locked_partition_accessed","p8_accessed","promotion_authorized","receipt_sha256"}
_HANDOFF_KEYS={"schema_version","dataset_id","shard_id","ordered_file_keys","label_candidate_index","source_candidate_index","admission_receipt_sha256","admission_policy_sha256","label_archive_sha256","label_content_root_sha256","source_archive_sha256","source_content_root_sha256","prepared_label_receipt_sha256","terminal_frame_tail_policy_sha256","production_execution_policy_sha256","member_namespace_commitment_sha256","pipeline_completion_receipt_sha256","candidate_files","admission_receipt_file","raw_identifiers_serialized","member_names_serialized","source_paths_serialized","locked_partition_accessed","p8_accessed","promotion_authorized","receipt_sha256"}

class Aihub538PhotometricV4ExecutionError(RuntimeError): pass
def _sha(value): return type(value) is str and len(value)==64 and all(c in "0123456789abcdef" for c in value)

def _strict_json_fd(descriptor:int,*,keys:set[str],maximum:int=1024*1024)->dict[str,object]:
    try:
        flags=fcntl.fcntl(descriptor,fcntl.F_GETFL); before=os.fstat(descriptor)
        if flags&os.O_ACCMODE!=os.O_RDONLY or not stat.S_ISREG(before.st_mode) or before.st_uid!=os.geteuid() or stat.S_IMODE(before.st_mode)!=0o400 or before.st_nlink!=1 or not 0<before.st_size<=maximum: raise ValueError
        identity=lambda item:(item.st_dev,item.st_ino,item.st_mode,item.st_nlink,item.st_uid,item.st_size,item.st_mtime_ns,item.st_ctime_ns)
        raw=os.pread(descriptor,before.st_size,0)
        if len(raw)!=before.st_size: raise ValueError
        def pairs(rows):
            if len({key for key,_ in rows})!=len(rows): raise ValueError
            return dict(rows)
        value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _value:(_ for _ in ()).throw(ValueError()))
        if type(value) is not dict or set(value)!=keys: raise ValueError
        receipt=value.get("receipt_sha256"); unsigned={k:v for k,v in value.items() if k!="receipt_sha256"}
        if receipt!=sha256_document(unsigned) or identity(os.fstat(descriptor))!=identity(before) or raw!=(json.dumps(value,sort_keys=True,separators=(",",":"))+"\n").encode(): raise ValueError
        return value
    except (OSError,ValueError,TypeError,json.JSONDecodeError): raise Aihub538PhotometricV4ExecutionError("p6_v4_strict_receipt_fd_invalid") from None

def load_aihub538_future_prefix_completion_descriptor(descriptor:int)->dict[str,object]:
    value=_strict_json_fd(descriptor,keys=_PREFIX_KEYS)
    receipts=value["pair_handoff_receipt_sha256"]
    if value["schema_version"]!="voice_coach.aihub538_future_pair_prefix.v1" or value["status"]!="completed_future_remediation_pair_preparation" or type(value["dataset_id"]) is not int or value["dataset_id"]!=538 or not _sha(value["remediation_receipt_sha256"]) or not _sha(value["identity_namespace_commitment_sha256"]) or type(receipts) is not list or len(receipts)!=3 or len(set(receipts))!=3 or not all(_sha(item) for item in receipts) or value["ordered_shard_ids"]!=["ts177_tl177","ts178_tl178","ts179_tl179"] or value["production_execution_policy_sha256"]!=AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256 or any(value[name] is not False for name in ("raw_identifiers_serialized","member_names_serialized","source_paths_serialized","locked_partition_accessed","p8_accessed","promotion_authorized")): raise Aihub538PhotometricV4ExecutionError("p6_v4_prefix_completion_invalid")
    return value

def load_aihub538_future_handoff_descriptor(descriptor:int,*,expected_shard_id:str,expected_receipt_sha256:str)->dict[str,object]:
    value=_strict_json_fd(descriptor,keys=_HANDOFF_KEYS)
    from ..models.aihub538_nonlocked_shards import aihub538_nonlocked_shard_by_id
    try: shard=aihub538_nonlocked_shard_by_id(expected_shard_id)
    except ValueError: raise Aihub538PhotometricV4ExecutionError("p6_v4_handoff_invalid") from None
    digests=("admission_receipt_sha256","admission_policy_sha256","label_archive_sha256","label_content_root_sha256","source_archive_sha256","source_content_root_sha256","prepared_label_receipt_sha256","terminal_frame_tail_policy_sha256","member_namespace_commitment_sha256","pipeline_completion_receipt_sha256")
    if not _sha(expected_receipt_sha256) or value["schema_version"]!="voice_coach.aihub538_future_pair_handoff.v1" or value["dataset_id"]!=538 or value["shard_id"]!=expected_shard_id or value["ordered_file_keys"]!=list(shard.ordered_file_keys) or (value["label_candidate_index"],value["source_candidate_index"]) not in {(0,1),(1,0)} or not all(_sha(value[name]) for name in digests) or value["receipt_sha256"]!=expected_receipt_sha256 or value["production_execution_policy_sha256"]!=AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256 or value["candidate_files"]!=["candidate-0.zip","candidate-1.zip"] or value["admission_receipt_file"]!="50-dataset-admission.json" or any(value[name] is not False for name in ("raw_identifiers_serialized","member_names_serialized","source_paths_serialized","locked_partition_accessed","p8_accessed","promotion_authorized")): raise Aihub538PhotometricV4ExecutionError("p6_v4_handoff_invalid")
    return value

def frozen_aihub538_photometric_v4_protocol()->Aihub538PhotometricCalibrationProtocolV4:
    source=Path(__file__).resolve().parents[2]/"pronunciation_attempts/adapters/video_qc.py"
    if hashlib.sha256(source.read_bytes()).hexdigest()!=_VIDEO_QC_SOURCE_SHA256: raise Aihub538PhotometricV4ExecutionError("p6_v4_video_qc_source_pin_invalid")
    factory=Aihub538PhotometricV4ProductionFactory(); qc=factory.video_qc_policy()
    return Aihub538PhotometricCalibrationProtocolV4("p6_v4_frozen",qc.receipt_sha256,MEASUREMENT_REVISION,video_qc_measurement_code_sha256(),Aihub538HardSafetyEnvelopeV4(0.75,0.125,0.9,0.9,0.75,0.7,0.7,0.2),4,4,4,0.9,0.25,6.0,0.5,0.5,0.75,0.95,1.6448536269514722,0.5)

AIHUB538_PHOTOMETRIC_V4_PROTOCOL_DOCUMENT=frozen_aihub538_photometric_v4_protocol().to_json_object()
AIHUB538_PHOTOMETRIC_V4_PROTOCOL_DOCUMENT_SHA256=sha256_document(AIHUB538_PHOTOMETRIC_V4_PROTOCOL_DOCUMENT)

def load_aihub538_photometric_v4_protocol_descriptor(descriptor:int)->Aihub538PhotometricCalibrationProtocolV4:
    try:
        before=os.fstat(descriptor)
        if fcntl.fcntl(descriptor,fcntl.F_GETFL)&os.O_ACCMODE!=os.O_RDONLY or not stat.S_ISREG(before.st_mode) or before.st_uid!=os.geteuid() or stat.S_IMODE(before.st_mode)!=0o400 or before.st_nlink!=1 or not 0<before.st_size<=65536: raise ValueError
        raw=os.pread(descriptor,before.st_size,0); value=json.loads(raw,parse_constant=lambda _value:(_ for _ in ()).throw(ValueError()))
        identity=lambda item:(item.st_dev,item.st_ino,item.st_mode,item.st_nlink,item.st_uid,item.st_size,item.st_mtime_ns,item.st_ctime_ns)
        if value!=AIHUB538_PHOTOMETRIC_V4_PROTOCOL_DOCUMENT or identity(os.fstat(descriptor))!=identity(before) or raw!=(json.dumps(value,sort_keys=True,separators=(",",":"))+"\n").encode(): raise ValueError
        loaded=Aihub538PhotometricCalibrationProtocolV4.from_json_object(value)
        if loaded!=frozen_aihub538_photometric_v4_protocol(): raise ValueError
        return loaded
    except (OSError,ValueError,TypeError,json.JSONDecodeError): raise Aihub538PhotometricV4ExecutionError("p6_v4_protocol_document_invalid") from None

def load_aihub538_actual_v3_failure_descriptor(descriptor:int):
    from ..models.aihub538_decision_calibration import Aihub538DecisionCalibrationFailureArtifactV2
    from ..models.aihub538_p6_remediation import AIHUB538_P6_ACTUAL_V3_FAILURE_ARTIFACT_SHA256
    try:
        before=os.fstat(descriptor); raw=os.pread(descriptor,before.st_size,0)
        if fcntl.fcntl(descriptor,fcntl.F_GETFL)&os.O_ACCMODE!=os.O_RDONLY or not stat.S_ISREG(before.st_mode) or before.st_uid!=os.geteuid() or stat.S_IMODE(before.st_mode)!=0o400 or before.st_nlink!=1 or not 0<before.st_size<=4*1024*1024 or len(raw)!=before.st_size: raise ValueError
        def pairs(rows):
            if len({key for key,_ in rows})!=len(rows): raise ValueError
            return dict(rows)
        value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _value:(_ for _ in ()).throw(ValueError()))
        if raw!=(json.dumps(value,sort_keys=True,separators=(",",":"))+"\n").encode(): raise ValueError
        artifact=Aihub538DecisionCalibrationFailureArtifactV2.from_json_object(value)
        if artifact.artifact_sha256!=AIHUB538_P6_ACTUAL_V3_FAILURE_ARTIFACT_SHA256: raise ValueError
        after=os.fstat(descriptor)
        if (before.st_dev,before.st_ino,before.st_mode,before.st_nlink,before.st_uid,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_mode,after.st_nlink,after.st_uid,after.st_size,after.st_mtime_ns,after.st_ctime_ns): raise ValueError
        return artifact
    except Exception: raise Aihub538PhotometricV4ExecutionError("p6_v4_actual_v3_failure_invalid") from None

_FACADE_TOKEN=object(); _FACADES=WeakKeyDictionary()
@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class Aihub538PhotometricV4LandmarkQcFacade:
    landmark_adapter:MediaPipeLipLandmarkAdapter
    qc_adapter:VideoQcCandidateAdapter
    _token:object=field(repr=False,compare=False)
    def __post_init__(self):
        factory=Aihub538PhotometricV4ProductionFactory()
        if self._token is not _FACADE_TOKEN or type(self.landmark_adapter) is not MediaPipeLipLandmarkAdapter or type(self.qc_adapter) is not VideoQcCandidateAdapter or self.landmark_adapter.production_identity!=(factory.landmark_model_sha256,factory.landmark_policy()) or self.qc_adapter.production_identity!=factory.video_qc_policy(): raise Aihub538PhotometricV4ExecutionError("p6_v4_frozen_landmark_qc_facade_required")
        _FACADES[self]=(os.getpid(),self.landmark_adapter,self.qc_adapter)
    def __call__(self,sample:Rgb24Sample):
        if _FACADES.get(self)!=(os.getpid(),self.landmark_adapter,self.qc_adapter) or type(sample) is not Rgb24Sample or len(sample.frames)!=8: raise Aihub538PhotometricV4ExecutionError("p6_v4_eight_frame_sample_required")
        return self.qc_adapter.measure(sample=sample,landmarks=self.landmark_adapter.detect(sample))

def create_aihub538_photometric_v4_landmark_qc_facade(*,landmark_adapter:MediaPipeLipLandmarkAdapter,qc_adapter:VideoQcCandidateAdapter)->Aihub538PhotometricV4LandmarkQcFacade:
    return Aihub538PhotometricV4LandmarkQcFacade(landmark_adapter,qc_adapter,_FACADE_TOKEN)

@contextmanager
def open_aihub538_photometric_v4_prepared_pairs(*,prefix_directory:Path,prepared_label_directories:tuple[Path,...],identity_key_descriptor:int,landmark_model_descriptor:int,restricted_workspace_root:Path,expected_remediation_receipt_sha256:str):
    """Atomically construct the exact three live admitted pair routes."""
    from ..adapters.aihub538_identity_key_store import load_aihub538_identity_key_descriptor
    from ..adapters.aihub538_admitted_archive_binding import bind_aihub538_prevalidated_immutable_archive_descriptors,load_aihub538_training_admission_receipt_descriptor,resolve_aihub538_prevalidated_candidate_role_descriptors,revoke_aihub538_admitted_archive_binding,validate_aihub538_immutable_zip_descriptor
    from ..adapters.aihub538_label_capacity import Aihub538LabelCapacityAdapter
    from ..adapters.aihub538_p6_label_preparation import open_aihub538_p6_prepared_label_directory
    from ..adapters.aihub538_actual_media_calibration import Aihub538ActualMediaCalibrationAdapter
    from ..adapters.aihub538_photometric_source_admission import Aihub538AdmittedNonlockedSourceReader,close_aihub538_photometric_source_admission_authority,create_aihub538_photometric_source_admission_authority
    from ..adapters.aihub538_training_archive import aihub538_archive_security_policy_for_shard
    from ..models.aihub538_nonlocked_shards import aihub538_nonlocked_shard_by_id
    from .aihub538_photometric_v4_orchestrator import Aihub538PhotometricPreparedPairV4
    if not prefix_directory.is_absolute() or len(prepared_label_directories)!=3 or not restricted_workspace_root.is_absolute() or not _sha(expected_remediation_receipt_sha256): raise Aihub538PhotometricV4ExecutionError("p6_v4_prepared_pair_input_invalid")
    restricted_workspace_root.mkdir(mode=0o700,parents=True,exist_ok=True)
    workspace_stat=restricted_workspace_root.stat()
    if restricted_workspace_root.is_symlink() or workspace_stat.st_uid!=os.geteuid() or stat.S_IMODE(workspace_stat.st_mode)!=0o700: raise Aihub538PhotometricV4ExecutionError("p6_v4_restricted_workspace_invalid")
    model_stat=os.fstat(landmark_model_descriptor); model_identity=lambda item:(item.st_dev,item.st_ino,item.st_mode,item.st_nlink,item.st_uid,item.st_size,item.st_mtime_ns,item.st_ctime_ns)
    model_raw=os.pread(landmark_model_descriptor,model_stat.st_size,0)
    if fcntl.fcntl(landmark_model_descriptor,fcntl.F_GETFL)&os.O_ACCMODE!=os.O_RDONLY or not stat.S_ISREG(model_stat.st_mode) or model_stat.st_uid!=os.geteuid() or stat.S_IMODE(model_stat.st_mode)!=0o400 or model_stat.st_nlink!=1 or not 0<model_stat.st_size<=64*1024*1024 or len(model_raw)!=model_stat.st_size or hashlib.sha256(model_raw).hexdigest()!=Aihub538PhotometricV4ProductionFactory().landmark_model_sha256 or model_identity(os.fstat(landmark_model_descriptor))!=model_identity(model_stat): raise Aihub538PhotometricV4ExecutionError("p6_v4_landmark_model_descriptor_invalid")
    identity=load_aihub538_identity_key_descriptor(identity_key_descriptor); factory=Aihub538PhotometricV4ProductionFactory(); authorities=[]
    with ExitStack() as stack:
        rootfd=os.open(prefix_directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW); stack.callback(os.close,rootfd)
        completion_fd=os.open("99-completion.json",os.O_RDONLY|os.O_NOFOLLOW,dir_fd=rootfd); stack.callback(os.close,completion_fd); completion=load_aihub538_future_prefix_completion_descriptor(completion_fd)
        if completion["remediation_receipt_sha256"]!=expected_remediation_receipt_sha256 or completion["identity_namespace_commitment_sha256"]!=identity.namespace_commitment_sha256: raise Aihub538PhotometricV4ExecutionError("p6_v4_prefix_identity_lineage_invalid")
        pairs=[]
        try:
            for index,shard_id in enumerate(("ts177_tl177","ts178_tl178","ts179_tl179")):
                shard=aihub538_nonlocked_shard_by_id(shard_id); shardfd=os.open(shard_id,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=rootfd); stack.callback(os.close,shardfd)
                handoff_fd=os.open("70-photometric-v4-handoff.json",os.O_RDONLY|os.O_NOFOLLOW,dir_fd=shardfd); stack.callback(os.close,handoff_fd); handoff=load_aihub538_future_handoff_descriptor(handoff_fd,expected_shard_id=shard_id,expected_receipt_sha256=completion["pair_handoff_receipt_sha256"][index])
                candidate_fds=[os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=shardfd) for name in handoff["candidate_files"]]
                for fd in candidate_fds: stack.callback(os.close,fd)
                admission_fd=os.open(handoff["admission_receipt_file"],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=shardfd); stack.callback(os.close,admission_fd); admission=load_aihub538_training_admission_receipt_descriptor(admission_fd)
                policy=aihub538_archive_security_policy_for_shard(shard)
                candidates=tuple(validate_aihub538_immutable_zip_descriptor(fd,policy=policy) for fd in candidate_fds)
                roles=resolve_aihub538_prevalidated_candidate_role_descriptors(candidates=candidates,admission_receipt=admission,policy=policy)
                if (roles.label_candidate_index,roles.source_candidate_index)!=(handoff["label_candidate_index"],handoff["source_candidate_index"]): raise Aihub538PhotometricV4ExecutionError("p6_v4_candidate_role_index_invalid")
                label_fd=roles.label_zip_descriptor; source_fd=roles.source_zip_descriptor
                binding=bind_aihub538_prevalidated_immutable_archive_descriptors(label=candidates[roles.label_candidate_index],source=candidates[roles.source_candidate_index],admission_receipt=admission,policy=policy)
                stack.callback(revoke_aihub538_admitted_archive_binding,binding)
                aggregate=Aihub538LabelCapacityAdapter(shard_spec=shard,archive_policy=policy).audit(label_zip_descriptor=label_fd,identity_hash_key=identity.key)
                _,prepared=stack.enter_context(open_aihub538_p6_prepared_label_directory(prepared_label_directories[index].absolute(),identity_hash_key=identity.key))
                adapter=Aihub538ActualMediaCalibrationAdapter(label_zip_descriptor=label_fd,source_zip_descriptor=source_fd,identity_hash_key=identity.key,admission_receipt=admission,shard_spec=shard,archive_binding=binding,restricted_workspace_root=restricted_workspace_root/str(index),landmark_model_path=Path(f"/proc/self/fd/{landmark_model_descriptor}"),landmark_model_sha256=factory.landmark_model_sha256,video_qc_policy=factory.video_qc_policy(),av_sync_policy=factory.av_sync_policy(),container_policy=factory.container_policy(),landmark_policy=factory.landmark_policy())
                stack.callback(adapter.close); authority=create_aihub538_photometric_source_admission_authority(adapter); authorities.append(authority)
                if handoff["prepared_label_receipt_sha256"]!=prepared.receipt_sha256 or handoff["admission_receipt_sha256"]!=admission.receipt_sha256 or handoff["label_archive_sha256"]!=admission.label_archive_sha256 or handoff["label_content_root_sha256"]!=admission.label_zip_content_root_sha256 or handoff["source_archive_sha256"]!=admission.source_archive_sha256 or handoff["source_content_root_sha256"]!=admission.source_zip_content_root_sha256 or handoff["admission_policy_sha256"]!=admission.admission_policy_sha256 or handoff["member_namespace_commitment_sha256"]!=prepared.member_namespace_commitment_sha256 or handoff["terminal_frame_tail_policy_sha256"]!=prepared.terminal_frame_tail_policy_sha256: raise Aihub538PhotometricV4ExecutionError("p6_v4_prepared_handoff_lineage_invalid")
                pair=Aihub538PhotometricPreparedPairV4(prepared,aggregate,admission,admission.admission_manifest_sha256,AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256,authority,Aihub538AdmittedNonlockedSourceReader(binding)); pair.validate(expected_shard_id=shard_id); pairs.append(pair)
            yield tuple(pairs),completion
        finally:
            for authority in authorities:
                try: close_aihub538_photometric_source_admission_authority(authority)
                except ValueError: pass

__all__=["Aihub538PhotometricV4ExecutionError","Aihub538PhotometricV4LandmarkQcFacade","create_aihub538_photometric_v4_landmark_qc_facade","AIHUB538_PHOTOMETRIC_V4_PROTOCOL_DOCUMENT","AIHUB538_PHOTOMETRIC_V4_PROTOCOL_DOCUMENT_SHA256","frozen_aihub538_photometric_v4_protocol","load_aihub538_future_handoff_descriptor","load_aihub538_future_prefix_completion_descriptor","load_aihub538_photometric_v4_protocol_descriptor","open_aihub538_photometric_v4_prepared_pairs"]
