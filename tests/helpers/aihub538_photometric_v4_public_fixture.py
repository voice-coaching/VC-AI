"""Public-API synthetic TS177--179 fixture builder (no restricted data)."""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from voice_coach.visual_feedback.adapters.aihub538_label_capacity import Aihub538LabelCapacityAdapter
from voice_coach.visual_feedback.adapters.aihub538_training_archive import aihub538_archive_security_policy_for_shard, validate_zip_descriptor
from voice_coach.visual_feedback.adapters.aihub538_training_dataset import Aihub538TrainingDatasetAdapter
from voice_coach.visual_feedback.models.aihub538_label_capacity import AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256
from voice_coach.visual_feedback.models.aihub538_nonlocked_shards import AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS, AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS
from voice_coach.visual_feedback.models.aihub538_training_evidence import Aihub538TrainingAdmissionReceipt
from voice_coach.visual_feedback.models.aihub538_photometric_v4_production_policy import AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256
from voice_coach.visual_feedback.models.integrity import canonical_json_bytes, sha256_document
from voice_coach.visual_feedback.services.aihub538_p6_label_preparation import Aihub538P6LabelPreparationService


@dataclass(frozen=True, slots=True)
class PublicPhotometricPairFixture:
    shard_id: str
    directory: Path
    prepared_label_directory: Path
    label_zip: Path
    source_zip: Path
    runtime_label_zip: Path
    runtime_source_zip: Path
    admission_receipt_path: Path
    handoff_receipt_path: Path
    admission_receipt: Aihub538TrainingAdmissionReceipt
    preparation_receipt: object


@dataclass(frozen=True, slots=True)
class PublicPhotometricV4Fixture:
    root: Path
    identity_key_path: Path
    prefix_completion_path: Path
    pairs: tuple[PublicPhotometricPairFixture, ...]


def build_public_photometric_v4_fixture(root: Path) -> PublicPhotometricV4Fixture:
    """Build three deterministic real-schema pairs solely through public APIs."""

    root = root.absolute()
    root.mkdir(mode=0o700, parents=True)
    root.chmod(0o700)
    identity_key = root / "identity.key"
    identity_key.write_bytes(b"public-fixture-identity-key-v1!!")
    identity_key.chmod(0o400)
    key = identity_key.read_bytes()
    rows: list[PublicPhotometricPairFixture] = []
    handoff_digests: list[str] = []
    identity_namespace: str | None = None
    for ordinal, shard in enumerate(AIHUB538_LABEL_CAPACITY_LADDER_PAIR_SHARDS[2:5], start=177):
        audit_shard = AIHUB538_LABEL_CAPACITY_LADDER_AUDIT_SHARDS[ordinal - 175]
        label_members, source_members = _members(ordinal, shard.label_role_component, shard.source_role_component)
        label_bytes, source_bytes = _zip_bytes(label_members), _zip_bytes(source_members)
        label_input = _completed_label_download(root / f"label-input-{ordinal}", shard.ordered_file_keys[0], label_bytes)
        prepared = root / f"prepared-{ordinal}"
        with ExitStack() as stack:
            key_fd = os.open(identity_key, os.O_RDONLY | os.O_NOFOLLOW)
            stack.callback(os.close, key_fd)
            preparation = Aihub538P6LabelPreparationService(shard_spec=audit_shard).prepare_from_completed_directory(
                input_directory=label_input, identity_key_descriptor=key_fd,
                output_directory=prepared, dataset_access_permission_confirmed=True,
            )
        pair_dir = root / shard.shard_id
        pair_dir.mkdir(mode=0o700)
        label_path, source_path = pair_dir / "candidate-0.zip", pair_dir / "candidate-1.zip"
        label_path.write_bytes(label_bytes); source_path.write_bytes(source_bytes)
        label_path.chmod(0o400); source_path.chmod(0o400)
        runtime = root / f"runtime-{ordinal}"; runtime.mkdir(mode=0o700)
        runtime_label, runtime_source = runtime / "label.zip", runtime / "source.zip"
        runtime_label.write_bytes(label_bytes); runtime_source.write_bytes(source_bytes)
        runtime_label.chmod(0o600); runtime_source.chmod(0o600)
        with ExitStack() as stack:
            label_fd = os.open(label_path, os.O_RDONLY | os.O_NOFOLLOW); stack.callback(os.close, label_fd)
            source_fd = os.open(source_path, os.O_RDONLY | os.O_NOFOLLOW); stack.callback(os.close, source_fd)
            aggregate = Aihub538TrainingDatasetAdapter(
                shard_spec=shard,
                nonlocked_label_schema_policy_sha256=AIHUB538_LABEL_CAPACITY_TERMINAL_FRAME_TAIL_POLICY_SHA256,
            ).admit(label_zip_descriptor=label_fd, source_zip_descriptor=source_fd, identity_hash_key=key)
            admission = Aihub538TrainingAdmissionReceipt.issue(aggregate)
            label_validation = validate_zip_descriptor(label_fd, policy=aihub538_archive_security_policy_for_shard(shard))
            source_validation = validate_zip_descriptor(source_fd, policy=aihub538_archive_security_policy_for_shard(shard))
            label_audit = Aihub538LabelCapacityAdapter(shard_spec=shard).audit(label_zip_descriptor=label_fd, identity_hash_key=key)
        admission_path = pair_dir / "50-dataset-admission.json"
        _write0400(admission_path, admission.to_json_object())
        handoff_unsigned = {
            "schema_version": "voice_coach.aihub538_future_pair_handoff.v1", "dataset_id": 538,
            "shard_id": shard.shard_id, "ordered_file_keys": list(shard.ordered_file_keys),
            "label_candidate_index": 0, "source_candidate_index": 1,
            "admission_receipt_sha256": admission.receipt_sha256,
            "admission_policy_sha256": admission.admission_policy_sha256,
            "label_archive_sha256": label_validation.archive_sha256,
            "label_content_root_sha256": label_validation.content_root_sha256,
            "source_archive_sha256": source_validation.archive_sha256,
            "source_content_root_sha256": source_validation.content_root_sha256,
            "prepared_label_receipt_sha256": preparation.receipt_sha256,
            "terminal_frame_tail_policy_sha256": preparation.terminal_frame_tail_policy_sha256,
            "production_execution_policy_sha256": AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256,
            "member_namespace_commitment_sha256": preparation.member_namespace_commitment_sha256,
            "pipeline_completion_receipt_sha256": sha256_document({"fixture_pipeline": shard.shard_id}),
            "candidate_files": ["candidate-0.zip", "candidate-1.zip"],
            "admission_receipt_file": "50-dataset-admission.json",
            "raw_identifiers_serialized": False, "member_names_serialized": False,
            "source_paths_serialized": False, "locked_partition_accessed": False,
            "p8_accessed": False, "promotion_authorized": False,
        }
        handoff = {**handoff_unsigned, "receipt_sha256": sha256_document(handoff_unsigned)}
        handoff_path = pair_dir / "70-photometric-v4-handoff.json"; _write0400(handoff_path, handoff)
        handoff_digests.append(handoff["receipt_sha256"])
        identity_namespace = admission.identity_namespace_commitment_sha256 if identity_namespace is None else identity_namespace
        rows.append(PublicPhotometricPairFixture(shard.shard_id, pair_dir, prepared, label_path, source_path, runtime_label, runtime_source, admission_path, handoff_path, admission, preparation))
        assert label_audit.label_archive_sha256 == preparation.label_zip_sha256
    prefix_unsigned = {
        "schema_version": "voice_coach.aihub538_future_pair_prefix.v1",
        "status": "completed_future_remediation_pair_preparation", "dataset_id": 538,
        "remediation_receipt_sha256": sha256_document({"fixture": "remediation"}),
        "pair_handoff_receipt_sha256": handoff_digests,
        "ordered_shard_ids": [row.shard_id for row in rows],
        "identity_namespace_commitment_sha256": identity_namespace,
        "production_execution_policy_sha256": AIHUB538_PHOTOMETRIC_V4_PRODUCTION_EXECUTION_POLICY_SHA256,
        "raw_identifiers_serialized": False, "member_names_serialized": False,
        "source_paths_serialized": False, "locked_partition_accessed": False,
        "p8_accessed": False, "promotion_authorized": False,
    }
    prefix = root / "99-completion.json"
    _write0400(prefix, {**prefix_unsigned, "receipt_sha256": sha256_document(prefix_unsigned)})
    return PublicPhotometricV4Fixture(root, identity_key, prefix, tuple(rows))


def _members(ordinal: int, label_role: str, source_role: str) -> tuple[dict[str, bytes], dict[str, bytes]]:
    labels: dict[str, bytes] = {}; sources: dict[str, bytes] = {}
    relative = "fixture/C/F/public"
    for speaker_index in range(4):
        speaker = f"{ordinal}{speaker_index}"
        for angle in "ABCDEFGHI":
            for attempt in range(4):
                stem = f"lip_J_1_F_02_C{speaker}_{angle}_{attempt:03d}"
                record = _record(stem, speaker, angle)
                labels[f"fixture/Label/{label_role}/{relative}/{stem}.json"] = json.dumps([record], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                sources[f"fixture/Source/{source_role}/{relative}/{stem}.mp4"] = b"\x00\x00\x00\x18ftypisompublic-fixture"
                a_stem = f"lip_J_1_F_02_C{speaker}_A_{attempt:03d}"
                sources.setdefault(f"fixture/Source/{source_role}/{relative}/{a_stem}.wav", b"RIFF\x08\x00\x00\x00WAVEpublic")
    return labels, sources


def _record(stem: str, speaker: str, angle: str) -> dict[str, object]:
    return {"dataSet":{"description":"fixture","url":"https://www.aihub.or.kr/","version":1.0,"year":"2022"},
        "Video_info":{"video_Name":stem+".mp4","video_Format":"mp4","video_Duration":"1.0","FPS":30.0,"Resolution":"640x480"},
        "Audio_info":{"Audio_Name":stem+".wav","Audio_Format":"wav","Audio_Duration":"1.0","Sampling_rate":"48kHz","Channel(s)":1},
        "Audio_env":{"Noise":1},"Video_env":{"env":"indoor_light","Angle":angle},
        "Sentence_info":[{"ID":"fixture","topic":"fixture","sentence_text":"합성 테스트 문장","start_time":0.0,"end_time":0.8}],
        "speaker_info":{"speaker_ID":"C"+speaker,"Specificity":"C","Gender":"F","Age":2,"Accent":"N"},
        "Bounding_box_info":{"Face_bounding_box":{"xtl_ytl_xbr_ybr":[[100,100,200,180]]*30},"Lip_bounding_box":{"xtl_ytl_xbr_ybr":[[120,130,180,165]]*30}}}


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with ZipFile(stream, "w", ZIP_STORED) as archive:
        for name, payload in sorted(members.items()):
            info = ZipInfo(name, (1980,1,1,0,0,0)); info.compress_type=ZIP_STORED; info.external_attr=0o600<<16
            archive.writestr(info, payload)
    return stream.getvalue()


def _completed_label_download(path: Path, file_key: str, label_zip: bytes) -> Path:
    path.mkdir(mode=0o700); outer=path/"download.tar"; split=len(label_zip)//2
    with tarfile.open(outer,"w") as archive:
        for offset,payload in ((0,label_zip[:split]),(split,label_zip[split:])):
            info=tarfile.TarInfo(f"opaque/label/label.zip.part{offset}"); info.size=len(payload); info.mode=0o600; archive.addfile(info,io.BytesIO(payload))
    outer.chmod(0o600); raw=outer.read_bytes()
    _write0400(path/"download.receipt.json", {"schema_version":"voice_coach.aihub_secure_download_receipt.v1","dataset_key":"538","file_keys":[file_key],"archive_size_bytes":len(raw),"archive_sha256":hashlib.sha256(raw).hexdigest(),"download_completed_unix_seconds":1,"credential_persisted":False,"archive_extracted":False})
    return path


def _write0400(path: Path, document: dict[str, object]) -> None:
    path.write_bytes(canonical_json_bytes(document)+b"\n"); path.chmod(0o400)


__all__ = ["PublicPhotometricPairFixture", "PublicPhotometricV4Fixture", "build_public_photometric_v4_fixture"]
