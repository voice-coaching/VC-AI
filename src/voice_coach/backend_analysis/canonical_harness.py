"""Closed, server-owned identities for S7 and its H5 expression derivative."""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HarnessIdentity:
    revision: str
    manifest_sha256: str
    lock_sha256: str
    prompt_sha256: str
    core_sha256: str = "95f2347ad53ab53030f68a65795cfc5a84ca1c8c41bd03119c9a9fb15d0367c2"
    scoring: bool = False


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
SCORED_V1 = HarnessIdentity(
    "canonical-scored-h5-20261003-v1",
    "820d600fab4c49615050b9a038e7236f0429f68249dc631a39c0f5ab0be6f0f6",
    "17d327b5898b183274418996468b577f3c455d8c1a5868b9cf3e00a34d53114d",
    "6f9e8515ae083a62e0500a03e3c2cef073ae78eb696bed8b5857a14b551ff1fd",
    "6ca67fcdaed390cc3da634fbafaef153890e52b8775c431f3fefae7dc122c776",
    True,
)
SCORED_V1_CORE_DIRECTORY = "canonical_scored_core_20261003_v1"
SCORED_V1_LLM_DIRECTORY = "canonical_scored_h5_20261003_v1"
SCORED = HarnessIdentity(
    "canonical-scored-h5-20261003-v2",
    "4afc1c2cdbf7b5440742cf154fe96ab2083beda69cd9e07c47a241fb0d784fcf",
    "b4ff1aa729d9c981a048d5b7df1293d9637df53269d0598d7ae6069f2b0cc989",
    "6f9e8515ae083a62e0500a03e3c2cef073ae78eb696bed8b5857a14b551ff1fd",
    "08fd8a19b6897d0c3890b0944986685830f24e6e676ea19422038b03a940ba80",
    True,
)
SCORED_CORE_DIRECTORY = "canonical_scored_core_20261003_v2"
SCORED_LLM_DIRECTORY = "canonical_scored_h5_20261003_v2"
HARNESSES = {value.manifest_sha256: value for value in (S7, H5, SCORED_V1, SCORED)}


def harness_identity(manifest_sha256: str) -> HarnessIdentity:
    try:
        return HARNESSES[manifest_sha256]
    except (KeyError, TypeError):
        raise ValueError("CANONICAL_HARNESS_UNKNOWN") from None


def check_schema_registration(identity: HarnessIdentity) -> None:
    """Catch missing or mismatched release pins before admitting inference."""
    from jsonschema import Draft202012Validator, ValidationError
    from .canonical_codec import _schema

    schema = _schema("v4")
    values = dict(parentManifestSha256=identity.core_sha256,
                  llmManifestSha256=identity.manifest_sha256,
                  llmDependencyLockSha256=identity.lock_sha256,
                  promptSha256=identity.prompt_sha256)
    try:
        Draft202012Validator(schema["$defs"]["binding"]["properties"][
            "parent_manifest_sha256"]).validate(identity.core_sha256)
        for name in ("association", "sourceIdentity"):
            definition = schema["$defs"][name]
            # Validate the real configured identity and its closed tuple, not a
            # fabricated recording/result. Other required fields belong to requests.
            fragment = dict(properties={k: definition["properties"][k]
                                        for k in values if k in definition["properties"]},
                            oneOf=definition["oneOf"])
            Draft202012Validator(fragment).validate(values)
    except ValidationError:
        raise ValueError("CANONICAL_HARNESS_SCHEMA_MISMATCH") from None
