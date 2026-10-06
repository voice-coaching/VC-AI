"""Restore the pinned GOP source overlay after the base B2 runtime assets."""
import hashlib
import json
import os
from pathlib import Path
import sys
from b2_backup import B2
from install_runtime_overlay import install

FILE_ID = '4_ze5cc71977ef1880ca60b0c17_f1089f3b207a34f11_d20261006_m162439_c005_v0501017_t0024_u01791303879438'
SIZE = 14689470
SHA256 = '906df1334b8482a6c1fade401534dd8ea977edec8130da61acca9818b29f344c'


def main():
    auth = json.loads(sys.stdin.buffer.read(65537))
    with B2(auth).read(FILE_ID) as response:
        raw = response.read(SIZE + 1)
    if len(raw) != SIZE or hashlib.sha256(raw).hexdigest() != SHA256:
        raise ValueError('GOP_RECOVERY_DIGEST_MISMATCH')
    install(raw, SHA256)


if __name__ == '__main__':
    main()
