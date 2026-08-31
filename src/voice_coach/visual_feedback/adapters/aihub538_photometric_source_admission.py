"""Opaque source capabilities derived only from admitted private inventory."""
from __future__ import annotations
from dataclasses import dataclass,field
import os,hashlib,hmac,zipfile,fcntl
from contextlib import contextmanager
from weakref import WeakKeyDictionary,ref
from .aihub538_admitted_archive_binding import Aihub538AdmittedArchiveBinding

@dataclass(frozen=True,slots=True,eq=False,weakref_slot=True)
class Aihub538MemberSourceMappingCapabilityV4:
    sample_sha256:str; angle:str; actor_token_sha256:str=field(repr=False); attempt_token_sha256:str=field(repr=False)
    label_archive_sha256:str; label_content_root_sha256:str; source_archive_sha256:str; source_content_root_sha256:str
    source_member_hmac_sha256:str=field(repr=False); container_sha256:str; admission_receipt_sha256:str

class Aihub538PhotometricSourceAdmissionAuthority:
    __slots__=("_adapter","_capability","__weakref__")
    def __init__(self,*_args,**_kwargs)->None: raise TypeError("p6_v4_source_authority_factory_required")
class _AuthorityToken: __slots__=("__weakref__",)
_AUTHORITIES=WeakKeyDictionary(); _ADAPTER_AUTHORITIES=WeakKeyDictionary(); _ISSUED=WeakKeyDictionary(); _MEMBER_NAMES=WeakKeyDictionary(); _MAPPING_STATE=WeakKeyDictionary(); _MAPPING_PROOFS=WeakKeyDictionary(); _MAPPING_FDS=WeakKeyDictionary(); _AUTHORITY_SAMPLES=WeakKeyDictionary()

def create_aihub538_photometric_source_admission_authority(actual_media_adapter:object)->Aihub538PhotometricSourceAdmissionAuthority:
    from .aihub538_actual_media_calibration import Aihub538ActualMediaCalibrationAdapter,assert_aihub538_live_actual_media_adapter
    if type(actual_media_adapter) is not Aihub538ActualMediaCalibrationAdapter: raise ValueError("p6_v4_actual_media_authority_required")
    assert_aihub538_live_actual_media_adapter(actual_media_adapter)
    if actual_media_adapter in _ADAPTER_AUTHORITIES: raise ValueError("p6_v4_source_authority_already_issued")
    actual_media_adapter._assert_archive_descriptors_stable(); binding=actual_media_adapter._archive_binding; binding.__post_init__(); inventory=actual_media_adapter.inventory
    if (
        not inventory
        or set(actual_media_adapter._private_items)
        != {item.sample_sha256 for item in inventory}
        or set(actual_media_adapter._private_by_label_selection_token)
        != {item.label_selection_token_sha256 for item in inventory}
        or any(
            actual_media_adapter._private_items.get(item.sample_sha256)
            is not actual_media_adapter._private_by_label_selection_token.get(
                item.label_selection_token_sha256
            )
            for item in inventory
        )
    ):
        raise ValueError("p6_v4_actual_media_inventory_invalid")
    token=_AuthorityToken(); authority=object.__new__(Aihub538PhotometricSourceAdmissionAuthority); authority._adapter=actual_media_adapter; authority._capability=token
    _AUTHORITIES[authority]=(os.getpid(),token,actual_media_adapter,ref(authority)); _ADAPTER_AUTHORITIES[actual_media_adapter]=ref(authority); _AUTHORITY_SAMPLES[authority]=set(); return authority

def issue_aihub538_member_source_mapping(*,authority:Aihub538PhotometricSourceAdmissionAuthority,member:object)->Aihub538MemberSourceMappingCapabilityV4:
    issued=_AUTHORITIES.get(authority) if type(authority) is Aihub538PhotometricSourceAdmissionAuthority else None
    if issued is None or issued[:3]!=(os.getpid(),authority._capability,authority._adapter) or issued[3]() is not authority: raise ValueError("p6_v4_source_authority_invalid")
    adapter=authority._adapter; adapter._assert_archive_descriptors_stable(); binding=adapter._archive_binding; binding.__post_init__(); item=getattr(member,"item",None)
    # A label-capacity cohort stores its label-only selection token in
    # ``sample_sha256``. Resolve that token through the private paired-media
    # index; the public inventory keeps the distinct full-media binding token.
    selection_token=getattr(item,"sample_sha256",None)
    private=adapter._private_by_label_selection_token.get(selection_token)
    public=getattr(private,"public",None)
    exact=(getattr(item,"sample_sha256",None),getattr(item,"angle",None),getattr(item,"actor_token_sha256",None),getattr(item,"attempt_token_sha256",None))
    if (
        public is None
        or selection_token != public.label_selection_token_sha256
        or adapter._private_items.get(public.sample_sha256) is not private
        or exact[1:]
        != (public.angle, public.actor_token_sha256, public.attempt_token_sha256)
    ):
        raise ValueError("p6_v4_source_member_not_admitted")
    if exact[0] in _AUTHORITY_SAMPLES[authority]: raise ValueError("p6_v4_source_mapping_already_issued")
    if getattr(member,"label_archive_sha256",None)!=binding.label.validation_receipt.archive_sha256 or getattr(member,"label_content_root_sha256",None)!=binding.label.validation_receipt.content_root_sha256 or getattr(member,"admission_receipt_sha256",None)!=binding.admission_receipt_sha256: raise ValueError("p6_v4_source_member_lineage_invalid")
    name=private.video_info.filename; memory_fd=os.memfd_create("p6-v4-derived-source",os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING); readonly_fd=None
    try:
        digest=hashlib.sha256()
        with adapter._open_photometric_source_clip(label_selection_token_sha256=selection_token) as clip_fd:
            offset=0
            while chunk:=os.pread(clip_fd,1024*1024,offset):
                digest.update(chunk); offset+=len(chunk); remaining=memoryview(chunk)
                while remaining:
                    written=os.write(memory_fd,remaining)
                    if written<=0: raise OSError("p6_v4_source_write_failed")
                    remaining=remaining[written:]
        fcntl.fcntl(memory_fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_WRITE|fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
        readonly_fd=os.open(f"/proc/self/fd/{memory_fd}",os.O_RDONLY|os.O_CLOEXEC); os.close(memory_fd); memory_fd=-1
        container=digest.hexdigest()
    except BaseException:
        if readonly_fd is not None: os.close(readonly_fd)
        if memory_fd>=0: os.close(memory_fd)
        raise
    member_hmac=hmac.new(adapter._identity_hash_key,b"voice_coach.aihub538.source_member.v1\x00"+name.encode(),hashlib.sha256).hexdigest()
    values=exact+(binding.label.validation_receipt.archive_sha256,binding.label.validation_receipt.content_root_sha256,binding.source.validation_receipt.archive_sha256,binding.source.validation_receipt.content_root_sha256,member_hmac,container,binding.admission_receipt_sha256)
    result=Aihub538MemberSourceMappingCapabilityV4(*values); _ISSUED[result]=(os.getpid(),authority)+values; _MEMBER_NAMES[result]=name; _MAPPING_STATE[result]="issued"; _MAPPING_PROOFS[result]=set(); _MAPPING_FDS[result]=readonly_fd; _AUTHORITY_SAMPLES[authority].add(exact[0]); return result

def assert_aihub538_member_source_mapping(value:Aihub538MemberSourceMappingCapabilityV4)->None:
    issued=_ISSUED.get(value); authority=issued[1] if issued else None
    expected=(os.getpid(),authority,value.sample_sha256,value.angle,value.actor_token_sha256,value.attempt_token_sha256,value.label_archive_sha256,value.label_content_root_sha256,value.source_archive_sha256,value.source_content_root_sha256,value.source_member_hmac_sha256,value.container_sha256,value.admission_receipt_sha256)
    if issued!=expected or _AUTHORITIES.get(authority) is None or _MAPPING_STATE.get(value)!="measurement_ready": raise ValueError("p6_v4_source_mapping_capability_invalid")
    _MAPPING_STATE[value]="consumed"

def begin_aihub538_member_source_open(value:Aihub538MemberSourceMappingCapabilityV4)->None:
    if _MAPPING_STATE.get(value)!="issued" or _ISSUED.get(value) is None: raise ValueError("p6_v4_source_mapping_capability_invalid")
    _MAPPING_STATE[value]="opened"

def finish_aihub538_member_source_open(value:Aihub538MemberSourceMappingCapabilityV4,*,succeeded:bool)->None:
    state=_MAPPING_STATE.get(value)
    if type(succeeded) is not bool or state not in {"opened","reading","source_closed"}: raise ValueError("p6_v4_source_mapping_capability_invalid")
    if succeeded and (state!="source_closed" or _MAPPING_PROOFS.get(value)!= {"source_closed","measurement_bound"}):
        _MAPPING_STATE[value]="revoked"; raise ValueError("p6_v4_source_measurement_proof_required")
    descriptor=_MAPPING_FDS.pop(value,None)
    if descriptor is not None: os.close(descriptor)
    _MAPPING_STATE[value]="measurement_ready" if succeeded else "revoked"

def mark_aihub538_member_measurement_bound(value:Aihub538MemberSourceMappingCapabilityV4)->None:
    if _MAPPING_STATE.get(value)!="source_closed": raise ValueError("p6_v4_source_mapping_capability_invalid")
    _MAPPING_PROOFS[value].add("measurement_bound")

def close_aihub538_photometric_source_admission_authority(authority:Aihub538PhotometricSourceAdmissionAuthority)->None:
    issued=_AUTHORITIES.pop(authority,None)
    if issued is None: raise ValueError("p6_v4_source_authority_invalid")
    for mapping,row in tuple(_ISSUED.items()):
        if row[1] is authority:
            descriptor=_MAPPING_FDS.pop(mapping,None)
            if descriptor is not None: os.close(descriptor)
    _AUTHORITY_SAMPLES.pop(authority,None); _ADAPTER_AUTHORITIES.pop(issued[2],None); issued[2].close()

class Aihub538AdmittedNonlockedSourceReader:
    def __init__(self,binding:Aihub538AdmittedArchiveBinding)->None:
        if type(binding) is not Aihub538AdmittedArchiveBinding: raise ValueError("p6_v4_admitted_source_authority_required")
        binding.__post_init__(); self._binding=binding
    @contextmanager
    def open_member_fd(self,mapping:Aihub538MemberSourceMappingCapabilityV4):
        if _MAPPING_STATE.get(mapping)!="opened" or _ISSUED.get(mapping) is None: raise ValueError("p6_v4_source_mapping_capability_invalid")
        _MAPPING_STATE[mapping]="reading"
        if mapping.source_archive_sha256!=self._binding.source.validation_receipt.archive_sha256 or mapping.source_content_root_sha256!=self._binding.source.validation_receipt.content_root_sha256: raise ValueError("p6_v4_source_archive_binding_invalid")
        source_fd=_MAPPING_FDS.get(mapping)
        if source_fd is None: raise ValueError("p6_v4_source_mapping_capability_invalid")
        readonly_fd=os.dup(source_fd)
        try:
            if hashlib.sha256(os.pread(readonly_fd,os.fstat(readonly_fd).st_size,0)).hexdigest()!=mapping.container_sha256: raise ValueError("p6_v4_source_container_digest_mismatch")
            yield readonly_fd
            if hashlib.sha256(os.pread(readonly_fd,os.fstat(readonly_fd).st_size,0)).hexdigest()!=mapping.container_sha256: raise ValueError("p6_v4_source_container_digest_mismatch")
            os.close(readonly_fd); readonly_fd=None
            _MAPPING_PROOFS[mapping].add("source_closed"); _MAPPING_STATE[mapping]="source_closed"
        finally:
            if readonly_fd is not None: os.close(readonly_fd)

__all__=["Aihub538AdmittedNonlockedSourceReader","Aihub538MemberSourceMappingCapabilityV4","Aihub538PhotometricSourceAdmissionAuthority","assert_aihub538_member_source_mapping","begin_aihub538_member_source_open","close_aihub538_photometric_source_admission_authority","create_aihub538_photometric_source_admission_authority","finish_aihub538_member_source_open","issue_aihub538_member_source_mapping","mark_aihub538_member_measurement_bound"]
