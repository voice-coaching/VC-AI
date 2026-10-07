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
    native: bool = False
    gpt_grading: bool = False
    gop_grading: bool = False


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
SCORED_V2 = HarnessIdentity(
    "canonical-scored-h5-20261003-v2",
    "4afc1c2cdbf7b5440742cf154fe96ab2083beda69cd9e07c47a241fb0d784fcf",
    "b4ff1aa729d9c981a048d5b7df1293d9637df53269d0598d7ae6069f2b0cc989",
    "6f9e8515ae083a62e0500a03e3c2cef073ae78eb696bed8b5857a14b551ff1fd",
    "08fd8a19b6897d0c3890b0944986685830f24e6e676ea19422038b03a940ba80",
    True,
)
SCORED_CORE_DIRECTORY = "canonical_scored_core_20261003_v2"
SCORED_V2_LLM_DIRECTORY = "canonical_scored_h5_20261003_v2"
SCORED = HarnessIdentity(
    "canonical-scored-h5-20261003-v3",
    "e3d6d6be9d896ede8d75d5f35f5daf4520a8d42f484e5f84a6fc32f8a05fb2e3",
    "dc3db9cc03a3355ddeb5d5819cd6fd46726935c8ca9b830f513e37163c89f4d8",
    "6f9e8515ae083a62e0500a03e3c2cef073ae78eb696bed8b5857a14b551ff1fd",
    "08fd8a19b6897d0c3890b0944986685830f24e6e676ea19422038b03a940ba80",
    True,
)
SCORED_LLM_DIRECTORY = "canonical_scored_h5_20261003_v3"
RESIDENT = HarnessIdentity(
    'canonical-resident-h5-20261004-v1',
    '885a2f0a67494dc3bebed28962222bfe95a0143292fe2de04194b84ea8beadc5',
    '1945dcf2fad7fc97621830abed34353c2d372d8224cc9ad11a9c422eabfdf9f4',
    SCORED.prompt_sha256,
    'ce16f635da012237b5316b36161d5eeff9432279cd08dbbe7967f5ab7ace7f7e', True,
)
RESIDENT_CORE_DIRECTORY = 'canonical_resident_core_20261004_v1'
RESIDENT_LLM_DIRECTORY = 'canonical_resident_h5_20261004_v1'
# Numeric calibration remains separate from the pinned analysis execution permit.
NATIVE_V1 = HarnessIdentity(
    'canonical-native-h5-20261004-v1',
    '358365399f7445ab0a77797ce46619979955503c4faac8185724e2225aa5205c',
    '798b47b6f5355b120141a7fa730350739298082549036233ea5804226dbad80c',
    SCORED.prompt_sha256,
    '9e2a2107db7d30fe8910c8984687974d85b95120760e2bcce53d57640a5d5a32', True, True,
)
NATIVE_V1_CORE_DIRECTORY = 'canonical_native_core_20261004_v1'
NATIVE_V1_LLM_DIRECTORY = 'canonical_native_h5_20261004_v1'
NATIVE = HarnessIdentity(
    'canonical-native-h5-20261005-v4',
    'efc705b042cb1b18d4babff4f5949f37622154e979b1c07b33afdc36eeb89428',
    '1b8cb378b889817e14bf06be5065b04016589f6b74dcd5501776dde758f38259',
    SCORED.prompt_sha256,
    'f1009fcdbf75515ddc13e915dcbbf19dfac751ce39779e404749c4f51dd74841', True, True,
)
NATIVE_CORE_DIRECTORY = 'canonical_native_core_20261005_v4'
NATIVE_LLM_DIRECTORY = 'canonical_native_h5_20261005_v4'
SEUNGUN_GPT_V1 = HarnessIdentity(
    'canonical-seungun-gpt-20261006-v1',
    '6a3bf7c872e718086cb9add4d48412c825188310cdc9eb394089301dd784ff57',
    '3f4ce0a44ae75192fe7fea3b03b879c21ab0b2be7cc0a620522c60c2155b674b',
    '883e1d43faaf26541efbd37b897ead7f4fca556b3bd8cf03608f43870382f127',
    '20acb7275aa06d0be4aca1a567420ea603c0eec084e50c651c96501903034f1b',
    True, True, True,
)
SEUNGUN_V1_CORE_DIRECTORY = 'canonical_seungun_core_20261006_v1'
SEUNGUN_V1_LLM_DIRECTORY = 'canonical_seungun_gpt_20261006_v1'
SEUNGUN_GPT = HarnessIdentity(
    'canonical-seungun-gop-gpt-20261006-v1',
    'f90440b764f78f32c989dc0785f41262397cc22e1d1ca48c40361189b81f86d5',
    '74784bf0c7ba99628ed2ff8db19157c63d22a2a1cd66c58000ad2e811dc93160',
    '5f3ec5aa81f54fdb3afe28869db2c5791a2133e8a44a23ead52187e9be542ff8',
    '818a3c16cba39dfcfa9f1083865db7f122a3eba40bc332d85256da94d1d6fb1e',
    True, True, True, True,
)
SEUNGUN_CORE_DIRECTORY = 'canonical_seungun_gop_core_20261006_v1'
SEUNGUN_LLM_DIRECTORY = 'canonical_seungun_gop_gpt_20261006_v1'
DLPC_GOP = HarnessIdentity(
    'canonical-dlpc-gop-cuda-20261007-v2',
    '1612539f82a0b6e2c5c0b3551440b8094ca979bd8ab5d898386713bd0d1f54b7',
    '3e2e81b43b1c1298266df428bc01ef0761d37f413073824cae2000623ea2eb3c',
    SEUNGUN_GPT.prompt_sha256,
    'f6d56e1decc160f0ca38cc8d52303f3de20945f872d714bf6a2e743ed94c0391', True, True, True, True,
)
DLPC_GOP_CORE_DIRECTORY = 'canonical_dlpc_gop_cuda_20261007_v2'
DLPC_GOP_LLM_DIRECTORY = 'canonical_dlpc_gop_cuda_20261007_v2_h5'
DLPC_ANNOUNCER = HarnessIdentity(
    'canonical-dlpc-gop-announcer-20261007-v1',
    'def65f68a849a2452b0e4dde600f0ef85d99beaa97123d30d97262653ebf8e77',
    'b605c3b9ba06bbf18fc126770b18de3d2da0d3f70c08e04873f17bc735b948bc',
    '977c49744e7af9fc60fdc2eb13c23985692cb00dd71789d8b3c43c3813e3f1b5',
    DLPC_GOP.core_sha256, True, True, True, True,
)
DLPC_ANNOUNCER_LLM_DIRECTORY = 'canonical_dlpc_gop_announcer_20261007_v1_h5'
HARNESSES = {value.manifest_sha256: value for value in (S7, H5, SCORED_V1, SCORED_V2, SCORED, RESIDENT, NATIVE_V1, NATIVE, SEUNGUN_GPT_V1, SEUNGUN_GPT, DLPC_GOP, DLPC_ANNOUNCER)}


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
