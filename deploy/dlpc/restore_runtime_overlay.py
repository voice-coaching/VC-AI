"""Restore the pinned GOP source overlay after the base B2 runtime assets."""
import hashlib
import json
import os
from pathlib import Path
import sys
from b2_backup import B2
from install_runtime_overlay import install
import host_identity

FILE_ID = '4_ze5cc71977ef1880ca60b0c17_f114952e6b08b9921_d20261008_m083112_c005_v0501033_t0033_u01791448272034'
SIZE = 17308951
SHA256 = 'd5ccbc765737499261c1ec9e4cac515caf430c30d3c43cfec96d88fa98e6a443'


def main():
    auth = json.loads(sys.stdin.buffer.read(65537))
    with B2(auth).read(FILE_ID) as response:
        raw = response.read(SIZE + 1)
    if len(raw) != SIZE or hashlib.sha256(raw).hexdigest() != SHA256:
        raise ValueError('GOP_RECOVERY_DIGEST_MISMATCH')
    install(raw, SHA256)


if __name__ == '__main__':
    main()
