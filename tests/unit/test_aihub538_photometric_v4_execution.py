from __future__ import annotations
from contextlib import ExitStack
import copy,json,os
import importlib.util
from pathlib import Path
import pytest
import subprocess,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"helpers"))
from aihub538_photometric_v4_public_fixture import build_public_photometric_v4_fixture
from voice_coach.visual_feedback.adapters.aihub538_photometric_v4_production_factory import Aihub538PhotometricV4ProductionFactory
from voice_coach.pronunciation_attempts.adapters.mediapipe_lip_landmarks import MediaPipeLipLandmarkAdapter
from voice_coach.pronunciation_attempts.adapters.video_qc import VideoQcCandidateAdapter
from voice_coach.visual_feedback.models.integrity import sha256_document
from voice_coach.visual_feedback.models.aihub538_photometric_v4_production_policy import AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256
from voice_coach.visual_feedback.services.aihub538_photometric_v4_execution import *
from voice_coach.visual_feedback.services.aihub538_decision_calibration import Aihub538DecisionCalibrationError

def write0400(path:Path,value):
    path.write_text(json.dumps(value,sort_keys=True,separators=(",",":"))+"\n"); path.chmod(0o400); return os.open(path,os.O_RDONLY|os.O_NOFOLLOW)

def test_protocol_document_strict_fd_reload_and_mutation_rejected(tmp_path):
    fd=write0400(tmp_path/"protocol.json",AIHUB538_PHOTOMETRIC_V4_PROTOCOL_DOCUMENT)
    try: assert load_aihub538_photometric_v4_protocol_descriptor(fd)==frozen_aihub538_photometric_v4_protocol()
    finally: os.close(fd)
    path=tmp_path/"bad.json"; bad=dict(AIHUB538_PHOTOMETRIC_V4_PROTOCOL_DOCUMENT); bad["minimum_development_actor_count"]=5; fd=write0400(path,bad)
    try:
        with pytest.raises(Aihub538PhotometricV4ExecutionError): load_aihub538_photometric_v4_protocol_descriptor(fd)
    finally:os.close(fd)

def test_prefix_and_handoff_strict_exact_lineage(tmp_path):
    handoff={"schema_version":"voice_coach.aihub538_future_pair_handoff.v1","dataset_id":538,"shard_id":"ts177_tl177","ordered_file_keys":["54728","54947"],"label_candidate_index":0,"source_candidate_index":1,"admission_receipt_sha256":"a"*64,"admission_policy_sha256":"b"*64,"label_archive_sha256":"c"*64,"label_content_root_sha256":"d"*64,"source_archive_sha256":"e"*64,"source_content_root_sha256":"f"*64,"prepared_label_receipt_sha256":"1"*64,"terminal_frame_tail_policy_sha256":"2"*64,"production_execution_policy_sha256":AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256,"member_namespace_commitment_sha256":"3"*64,"pipeline_completion_receipt_sha256":"4"*64,"candidate_files":["candidate-0.zip","candidate-1.zip"],"admission_receipt_file":"50-dataset-admission.json","raw_identifiers_serialized":False,"member_names_serialized":False,"source_paths_serialized":False,"locked_partition_accessed":False,"p8_accessed":False,"promotion_authorized":False}
    handoff={**handoff,"receipt_sha256":sha256_document(handoff)}; fd=write0400(tmp_path/"handoff.json",handoff)
    try: assert load_aihub538_future_handoff_descriptor(fd,expected_shard_id="ts177_tl177",expected_receipt_sha256=handoff["receipt_sha256"])["promotion_authorized"] is False
    finally:os.close(fd)
    prefix={"schema_version":"voice_coach.aihub538_future_pair_prefix.v1","status":"completed_future_remediation_pair_preparation","dataset_id":538,"remediation_receipt_sha256":"5"*64,"pair_handoff_receipt_sha256":[handoff["receipt_sha256"],"7"*64,"8"*64],"ordered_shard_ids":["ts177_tl177","ts178_tl178","ts179_tl179"],"identity_namespace_commitment_sha256":"6"*64,"production_execution_policy_sha256":AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256,"raw_identifiers_serialized":False,"member_names_serialized":False,"source_paths_serialized":False,"locked_partition_accessed":False,"p8_accessed":False,"promotion_authorized":False}
    prefix={**prefix,"receipt_sha256":sha256_document(prefix)}; fd=write0400(tmp_path/"prefix.json",prefix)
    try: assert load_aihub538_future_prefix_completion_descriptor(fd)["ordered_shard_ids"][-1]=="ts179_tl179"
    finally:os.close(fd)

def test_receipt_loader_rejects_mode_link_and_extra_key(tmp_path):
    value={key:False for key in ()}; path=tmp_path/"x"; path.write_text("{}"); path.chmod(0o600); fd=os.open(path,os.O_RDONLY)
    try:
        with pytest.raises(Aihub538PhotometricV4ExecutionError): load_aihub538_future_prefix_completion_descriptor(fd)
    finally:os.close(fd)

def test_internal_cli_help_has_descriptor_first_boundary():
    result=subprocess.run([sys.executable,"scripts/run_aihub538_p6_photometric_v4.py","--help"],capture_output=True,text=True,check=False)
    assert result.returncode==0 and "--remediation-fd" in result.stdout and "--identity-key-fd" in result.stdout and "--landmark-model-fd" in result.stdout

def test_internal_cli_rejects_stdio_descriptors_without_echoing_paths(tmp_path):
    secret=tmp_path/"raw-speaker-C123"
    result=subprocess.run([sys.executable,"scripts/run_aihub538_p6_photometric_v4.py","--remediation-fd","0","--actual-v3-failure-fd","0","--identity-key-fd","0","--landmark-model-fd","0","--prepared-prefix",str(secret),"--prepared-label-dir",str(secret),"--restricted-workspace",str(tmp_path/"restricted"),"--issuance-dir",str(tmp_path/"issuance"),"--output-dir",str(tmp_path/"output")],capture_output=True,text=True,check=False)
    assert result.returncode!=0 and str(secret) not in result.stderr and "C123" not in result.stderr

def test_internal_cli_preserves_only_privacy_safe_gate_reason_codes(tmp_path):
    script=Path("scripts/run_aihub538_p6_photometric_v4.py").resolve()
    spec=importlib.util.spec_from_file_location("p6_v4_cli_reason_test",script)
    assert spec is not None and spec.loader is not None
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    assert module._privacy_safe_reason_code(ValueError("p6_v4_sampling_failed"))=="p6_v4_sampling_failed"
    secret=tmp_path/"raw-speaker-C123"
    assert module._privacy_safe_reason_code(ValueError(str(secret)))=="ValueError"

def test_landmark_qc_facade_requires_live_factory_registered_adapters(tmp_path):
    factory=Aihub538PhotometricV4ProductionFactory(); landmark=MediaPipeLipLandmarkAdapter(model_path=tmp_path/"unused",expected_model_sha256=factory.landmark_model_sha256,policy=factory.landmark_policy()); qc=VideoQcCandidateAdapter(policy=factory.video_qc_policy())
    facade=create_aihub538_photometric_v4_landmark_qc_facade(landmark_adapter=landmark,qc_adapter=qc)
    assert facade.landmark_adapter is landmark
    with pytest.raises(TypeError): Aihub538PhotometricV4LandmarkQcFacade(landmark,qc)  # type: ignore[call-arg]
    forged=object.__new__(MediaPipeLipLandmarkAdapter); forged._expected_model_sha256=factory.landmark_model_sha256; forged._policy=factory.landmark_policy()
    with pytest.raises(Exception,match="capability_invalid"):
        create_aihub538_photometric_v4_landmark_qc_facade(landmark_adapter=forged,qc_adapter=qc)

    copied_landmark=copy.copy(landmark)
    copied_qc=copy.copy(qc)
    with pytest.raises(Exception,match="capability_invalid"):
        create_aihub538_photometric_v4_landmark_qc_facade(landmark_adapter=copied_landmark,qc_adapter=qc)
    with pytest.raises(Exception,match="capability_invalid"):
        create_aihub538_photometric_v4_landmark_qc_facade(landmark_adapter=landmark,qc_adapter=copied_qc)

    read_fd,write_fd=os.pipe()
    child=os.fork()
    if child==0:
        os.close(read_fd)
        try:
            create_aihub538_photometric_v4_landmark_qc_facade(landmark_adapter=landmark,qc_adapter=qc)
        except Exception as exc:
            os.write(write_fd,str(exc).encode())
        else:
            os.write(write_fd,b"accepted")
        finally:
            os.close(write_fd)
        os._exit(0)
    os.close(write_fd)
    child_result=os.read(read_fd,4096).decode()
    os.close(read_fd)
    _,status=os.waitpid(child,0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status)==0
    assert "capability_invalid" in child_result

    forged_facade=object.__new__(Aihub538PhotometricV4LandmarkQcFacade)
    object.__setattr__(forged_facade,"landmark_adapter",landmark)
    object.__setattr__(forged_facade,"qc_adapter",qc)
    object.__setattr__(forged_facade,"_token",object())
    with pytest.raises(Aihub538PhotometricV4ExecutionError,match="eight_frame_sample_required"):
        forged_facade(None)  # type: ignore[arg-type]


def test_transactional_factory_opens_exact_three_public_pairs_and_revokes(tmp_path):
    fixture=build_public_photometric_v4_fixture(tmp_path/"fixture")
    completion=json.loads(fixture.prefix_completion_path.read_text())
    model=Path(__file__).resolve().parents[2]/"models/mediapipe/face_landmarker.task"
    assert model.is_file()
    with ExitStack() as stack:
        key_fd=os.open(fixture.identity_key_path,os.O_RDONLY|os.O_NOFOLLOW); stack.callback(os.close,key_fd)
        model_fd=os.open(model,os.O_RDONLY|os.O_NOFOLLOW); stack.callback(os.close,model_fd)
        with open_aihub538_photometric_v4_prepared_pairs(prefix_directory=fixture.root,prepared_label_directories=tuple(row.prepared_label_directory for row in fixture.pairs),identity_key_descriptor=key_fd,landmark_model_descriptor=model_fd,restricted_workspace_root=tmp_path/"restricted",expected_remediation_receipt_sha256=completion["remediation_receipt_sha256"]) as (pairs,loaded):
            assert len(pairs)==3 and loaded==completion
            assert tuple(pair.admission_receipt.shard_id for pair in pairs)==("ts177_tl177","ts178_tl178","ts179_tl179")
            for index,pair in enumerate(pairs): pair.validate(expected_shard_id=f"ts{177+index}_tl{177+index}")
        for pair in pairs:
            from voice_coach.visual_feedback.adapters.aihub538_photometric_source_admission import close_aihub538_photometric_source_admission_authority
            with pytest.raises(ValueError,match="source_authority_invalid"):
                close_aihub538_photometric_source_admission_authority(pair.authority)


def test_transactional_factory_rejects_role_swap_and_preserves_existing_workspace(tmp_path):
    fixture=build_public_photometric_v4_fixture(tmp_path/"fixture")
    completion=json.loads(fixture.prefix_completion_path.read_text()); handoff_path=fixture.pairs[0].handoff_receipt_path
    handoff=json.loads(handoff_path.read_text()); handoff["label_candidate_index"],handoff["source_candidate_index"]=1,0; handoff["receipt_sha256"]=sha256_document({k:v for k,v in handoff.items() if k!="receipt_sha256"}); handoff_path.chmod(0o600); handoff_path.write_text(json.dumps(handoff,sort_keys=True,separators=(",",":"))+"\n"); handoff_path.chmod(0o400)
    completion["pair_handoff_receipt_sha256"][0]=handoff["receipt_sha256"]; completion["receipt_sha256"]=sha256_document({k:v for k,v in completion.items() if k!="receipt_sha256"}); fixture.prefix_completion_path.chmod(0o600); fixture.prefix_completion_path.write_text(json.dumps(completion,sort_keys=True,separators=(",",":"))+"\n"); fixture.prefix_completion_path.chmod(0o400)
    restricted=tmp_path/"restricted"; restricted.mkdir(mode=0o700); sentinel=restricted/"keep"; sentinel.write_text("preserve")
    model=Path(__file__).resolve().parents[2]/"models/mediapipe/face_landmarker.task"
    with ExitStack() as stack:
        key_fd=os.open(fixture.identity_key_path,os.O_RDONLY|os.O_NOFOLLOW); stack.callback(os.close,key_fd); model_fd=os.open(model,os.O_RDONLY|os.O_NOFOLLOW); stack.callback(os.close,model_fd)
        with pytest.raises(Aihub538PhotometricV4ExecutionError,match="candidate_role_index_invalid"):
            with open_aihub538_photometric_v4_prepared_pairs(prefix_directory=fixture.root,prepared_label_directories=tuple(row.prepared_label_directory for row in fixture.pairs),identity_key_descriptor=key_fd,landmark_model_descriptor=model_fd,restricted_workspace_root=restricted,expected_remediation_receipt_sha256=completion["remediation_receipt_sha256"]): pass
    assert sentinel.read_text()=="preserve"


@pytest.mark.parametrize("attack",("truncate","rename","symlink"))
def test_transactional_factory_rejects_candidate_path_races(tmp_path,attack):
    fixture=build_public_photometric_v4_fixture(tmp_path/"fixture"); completion=json.loads(fixture.prefix_completion_path.read_text()); target=fixture.pairs[0].source_zip
    if attack=="truncate":
        target.chmod(0o600); target.write_bytes(b"truncated"); target.chmod(0o400)
    else:
        moved=target.with_suffix(".moved"); target.rename(moved)
        if attack=="symlink": target.symlink_to(moved)
    model=Path(__file__).resolve().parents[2]/"models/mediapipe/face_landmarker.task"
    with ExitStack() as stack:
        key_fd=os.open(fixture.identity_key_path,os.O_RDONLY|os.O_NOFOLLOW); stack.callback(os.close,key_fd); model_fd=os.open(model,os.O_RDONLY|os.O_NOFOLLOW); stack.callback(os.close,model_fd)
        with pytest.raises((OSError,ValueError,Aihub538PhotometricV4ExecutionError,Aihub538DecisionCalibrationError)):
            with open_aihub538_photometric_v4_prepared_pairs(prefix_directory=fixture.root,prepared_label_directories=tuple(row.prepared_label_directory for row in fixture.pairs),identity_key_descriptor=key_fd,landmark_model_descriptor=model_fd,restricted_workspace_root=tmp_path/"restricted",expected_remediation_receipt_sha256=completion["remediation_receipt_sha256"]): pass


def test_transactional_factory_baseexception_preserves_existing_workspace(tmp_path,monkeypatch):
    fixture=build_public_photometric_v4_fixture(tmp_path/"fixture"); completion=json.loads(fixture.prefix_completion_path.read_text()); restricted=tmp_path/"restricted"; restricted.mkdir(mode=0o700); sentinel=restricted/"keep"; sentinel.write_text("preserve")
    import voice_coach.visual_feedback.adapters.aihub538_admitted_archive_binding as binding_module
    monkeypatch.setattr(binding_module,"validate_aihub538_immutable_zip_descriptor",lambda *_args,**_kwargs:(_ for _ in ()).throw(KeyboardInterrupt()))
    model=Path(__file__).resolve().parents[2]/"models/mediapipe/face_landmarker.task"
    with ExitStack() as stack:
        key_fd=os.open(fixture.identity_key_path,os.O_RDONLY|os.O_NOFOLLOW); stack.callback(os.close,key_fd); model_fd=os.open(model,os.O_RDONLY|os.O_NOFOLLOW); stack.callback(os.close,model_fd)
        with pytest.raises(KeyboardInterrupt):
            with open_aihub538_photometric_v4_prepared_pairs(prefix_directory=fixture.root,prepared_label_directories=tuple(row.prepared_label_directory for row in fixture.pairs),identity_key_descriptor=key_fd,landmark_model_descriptor=model_fd,restricted_workspace_root=restricted,expected_remediation_receipt_sha256=completion["remediation_receipt_sha256"]): pass
    assert sentinel.read_text()=="preserve"
