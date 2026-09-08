#!/usr/bin/env python3
"""Internal, descriptor-first AI Hub 538 P6 photometric v4 runner."""
from __future__ import annotations
import argparse,json,os,re,stat,sys
from pathlib import Path
_ROOT=Path(__file__).resolve().parents[1]
if str(_ROOT/"src") not in sys.path: sys.path.insert(0,str(_ROOT/"src"))
from voice_coach.pronunciation_attempts.adapters.mediapipe_lip_landmarks import MediaPipeLipLandmarkAdapter
from voice_coach.pronunciation_attempts.adapters.video_qc import VideoQcCandidateAdapter
from voice_coach.visual_feedback.adapters.aihub538_p6_remediation_publish import load_aihub538_p6_remediation_receipt_descriptor
from voice_coach.visual_feedback.adapters.aihub538_photometric_v4_production_factory import Aihub538PhotometricV4ProductionFactory
from voice_coach.visual_feedback.adapters.aihub538_photometric_v4_store import Aihub538PhotometricV4IssuanceStore
from voice_coach.visual_feedback.services.aihub538_photometric_v4_execution import create_aihub538_photometric_v4_landmark_qc_facade,load_aihub538_actual_v3_failure_descriptor,open_aihub538_photometric_v4_prepared_pairs,frozen_aihub538_photometric_v4_protocol
from voice_coach.visual_feedback.services.aihub538_photometric_v4_orchestrator import Aihub538PhotometricV4ProductionOrchestrator
from voice_coach.visual_feedback.services.aihub538_photometric_v4_runner import Aihub538PhotometricV4MultiPairRunner

def _fd(value):
    parsed=int(value)
    if parsed<3: raise argparse.ArgumentTypeError("descriptor must be >= 3")
    return parsed
_SAFE_REASON_CODE=re.compile(r"^[a-z0-9_]{1,160}$")
def _privacy_safe_reason_code(error:BaseException)->str:
    """Preserve a stable gate code without echoing paths or source details."""
    candidates=(getattr(error,"reason_code",None),str(error))
    for candidate in candidates:
        if isinstance(candidate,str) and _SAFE_REASON_CODE.fullmatch(candidate):
            return candidate
    return type(error).__name__
def parser():
    p=argparse.ArgumentParser(); p.add_argument("--remediation-fd",type=_fd,required=True); p.add_argument("--actual-v3-failure-fd",type=_fd,required=True); p.add_argument("--identity-key-fd",type=_fd,required=True); p.add_argument("--landmark-model-fd",type=_fd,required=True); p.add_argument("--prepared-prefix",type=Path,required=True); p.add_argument("--prepared-label-dir",type=Path,action="append",required=True); p.add_argument("--restricted-workspace",type=Path,required=True); p.add_argument("--issuance-dir",type=Path,required=True); p.add_argument("--output-dir",type=Path,required=True); return p
def main(argv=None):
    try:
        args=parser().parse_args(argv); remediation=load_aihub538_p6_remediation_receipt_descriptor(args.remediation_fd); failure=load_aihub538_actual_v3_failure_descriptor(args.actual_v3_failure_fd)
        if remediation.design_failure_artifact_receipt_sha256!=failure.artifact_sha256: raise ValueError("p6_v4_actual_v3_remediation_lineage_invalid")
        for directory in (args.restricted_workspace,args.issuance_dir,args.output_dir):
            if not directory.is_absolute(): raise ValueError("p6_v4_absolute_directory_required")
            directory.mkdir(mode=0o700,parents=True,exist_ok=True)
            if directory.is_symlink() or stat.S_IMODE(directory.stat().st_mode)!=0o700: raise ValueError("p6_v4_private_directory_invalid")
        factory=Aihub538PhotometricV4ProductionFactory(); landmark=MediaPipeLipLandmarkAdapter(model_path=Path(f"/proc/self/fd/{args.landmark_model_fd}"),model_descriptor=args.landmark_model_fd,expected_model_sha256=factory.landmark_model_sha256,policy=factory.landmark_policy()); qc=create_aihub538_photometric_v4_landmark_qc_facade(landmark_adapter=landmark,qc_adapter=VideoQcCandidateAdapter(policy=factory.video_qc_policy())); store=Aihub538PhotometricV4IssuanceStore(args.issuance_dir)
        with open_aihub538_photometric_v4_prepared_pairs(prefix_directory=args.prepared_prefix.absolute(),prepared_label_directories=tuple(path.absolute() for path in args.prepared_label_dir),identity_key_descriptor=args.identity_key_fd,landmark_model_descriptor=args.landmark_model_fd,restricted_workspace_root=args.restricted_workspace,expected_remediation_receipt_sha256=remediation.receipt_sha256) as (pairs,_completion):
            orchestrator=Aihub538PhotometricV4ProductionOrchestrator(runner_factory=lambda **kw:Aihub538PhotometricV4MultiPairRunner(qc_measure=qc,production_factory=factory,**kw),issuance_store=store,output_directory=args.output_dir)
            completion=orchestrator.run(remediation_receipt=remediation,pairs=pairs,protocol=frozen_aihub538_photometric_v4_protocol())
        print(json.dumps(completion,sort_keys=True,separators=(",",":"))); return 0
    except SystemExit as error:
        if error.code==0:return 0
        raise
    except BaseException as error:
        print(json.dumps({"status":"failed_closed","reason_code":_privacy_safe_reason_code(error),"promotion_authorized":False,"locked_partition_accessed":False,"p8_accessed":False},sort_keys=True,separators=(",",":")),file=sys.stderr); return 2
if __name__=="__main__": raise SystemExit(main())
