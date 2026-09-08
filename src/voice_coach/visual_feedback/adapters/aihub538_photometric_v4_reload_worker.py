"""Fresh-process strict reload worker for a non-promotional P6 v4 artifact."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path

from .aihub538_photometric_v4_store import Aihub538PhotometricV4IssuanceStore
from ..models.aihub538_photometric_calibration_v4 import Aihub538PhotometricCalibrationEvidenceV4
from ..models.integrity import sha256_document

def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("--artifact",type=Path,required=True)
    parser.add_argument("--issuance-directory",type=Path,required=True)
    parser.add_argument("--challenge-fd",type=int,required=True)
    args=parser.parse_args()
    artifact=Aihub538PhotometricCalibrationEvidenceV4.from_json_object(json.loads(args.artifact.read_text(encoding="utf-8")))
    store=Aihub538PhotometricV4IssuanceStore(args.issuance_directory)
    nonce=os.read(args.challenge_fd,256).decode("ascii"); os.close(args.challenge_fd)
    durable=store.claim_reload_from_worker(artifact_sha256=artifact.artifact_sha256,nonce=nonce)
    unsigned={"schema_version":"voice_coach.p6_v4_fresh_process_reload_receipt.v1","artifact_sha256":artifact.artifact_sha256,"durable_reload_receipt_sha256":durable,"fresh_process_reload_required":True,"fresh_process_reload_completed":True,"promotion_authorized":False}
    print(json.dumps({**unsigned,"receipt_sha256":sha256_document(unsigned)},sort_keys=True,separators=(",",":")))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
