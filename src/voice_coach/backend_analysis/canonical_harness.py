"""Closed, server-owned identities for S7 and its H5 expression derivative."""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HarnessIdentity:
    revision: str
    manifest_sha256: str
    lock_sha256: str
    prompt_sha256: str


S7 = HarnessIdentity(
    "canonical-s7-20260928",
    "1413b196616c919aeaab243fdd57771e70221ffc53a37f9b018d1881499b8c73",
    "d8356763579dcfee347073a3f0ff72f4a0f67e33a29fa3b4c71012afcfd4ba01",
    "362ccc1f15d2e166110cca52d90fd3d6e81ee85646e96eb7018d8b8278898714",
)
H5 = HarnessIdentity(
    "canonical-h5-20261003-v1",
    "9f10296b6944249ded9f5ce2ccfdfa7c53b6e4af670999fe68e9e477e97eaf45",
    "93636f0c4befe7f1e34358e74b1077cc94cb2b82064781485b0183c53357d2f5",
    "6f9e8515ae083a62e0500a03e3c2cef073ae78eb696bed8b5857a14b551ff1fd",
)
H5_DIRECTORY = "canonical_h5_20261003"
HARNESSES = {value.manifest_sha256: value for value in (S7, H5)}


def harness_identity(manifest_sha256: str) -> HarnessIdentity:
    try:
        return HARNESSES[manifest_sha256]
    except (KeyError, TypeError):
        raise ValueError("CANONICAL_HARNESS_UNKNOWN") from None
