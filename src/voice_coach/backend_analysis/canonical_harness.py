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
    model: str = "gpt-6-astra"
    reasoning_effort: str = "low"
    service_tier: str | None = None
    explicit_prompt_cache: bool = False
    selection_view: bool = True


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
DLPC_CONCISE = HarnessIdentity(
    'canonical-dlpc-gop-announcer-20261007-v2',
    '3e430635a4d714ead5d8e08352e41ff9a2b06c893d73af7f018fb43b4c37f61c',
    '58c26086546abb8b94eb6998671101d67633af426adf373cceb42862ccaf0ec4',
    'ad59ba9124fbe26b9451bcb1a04a5eb1afcb3c819c539d5a25d3ac09a1dc281d',
    DLPC_GOP.core_sha256, True, True, True, True,
)
DLPC_CONCISE_LLM_DIRECTORY = 'canonical_dlpc_gop_announcer_20261007_v2_h5'
DLPC_LUNA = HarnessIdentity(
    'canonical-dlpc-gop-luna-20261024-v1',
    '1c62cd5f42625d80c2ca772d631ec3a2f9b6286d7e2a4b48e1b0b4f5a290899e',
    '8da3410fba23a1f0c802b436edca181a7fa0c3a14f2b455f4a26b9f7a0330c73',
    DLPC_CONCISE.prompt_sha256,
    DLPC_GOP.core_sha256, True, True, True, True, model='gpt-6-luna',
)
DLPC_LUNA_LLM_DIRECTORY = 'canonical_dlpc_gop_luna_20261024_v1_h5'
DLPC_LUNA_NONE = HarnessIdentity(
    'canonical-dlpc-gop-luna-none-20261024-v1',
    'a0be7ab1bd5738c14798f15e6c72f82558106f717798ad7e5da90f52e2dc5691',
    'ed9bab85e9fbbda03196c77ebc7d191f382dfb108670a6fb296f15f83f061869',
    DLPC_LUNA.prompt_sha256, DLPC_GOP.core_sha256, True, True, True, True,
    model='gpt-6-luna', reasoning_effort='none', service_tier='default',
)
DLPC_LUNA_NONE_LLM_DIRECTORY = 'canonical_dlpc_gop_luna_none_20261024_v1_h5'
DLPC_MFA_TRACE = HarnessIdentity(
    'canonical-dlpc-mfa-trace-20261025-v1',
    '1d227a6654060357c04fb1a592826bba2bfc464cc536837e2f142cc612573211',
    '06907765cad8d14300e41fa6cf8709e32e11edf89c9bf021b945e534348e9579',
    DLPC_LUNA_NONE.prompt_sha256,
    'c9c7d893ebbf6e5a2fa7762c3fa2e72b33b4ada391c945ce564c59d4d4a5bf82',
    True, True, True, True,
    model='gpt-6-luna', reasoning_effort='none', service_tier='default',
)
DLPC_MFA_PREPARED = HarnessIdentity(
    'canonical-dlpc-mfa-prepared-20261025-t1-v2',
    '11d920cd07d3ed583885d6ea79d1bc2ee0a65903a42c98448cca87a37b4a5e94',
    'fb322f24a263d385accb6b48f9f841bbaa8dd866b68b7129c5189b854ede553b',
    DLPC_LUNA_NONE.prompt_sha256,
    '755667629b5d603910ff2646daa807bf7757e2d3f3ca29845e265e2e4dc79984',
    True, True, True, True,
    model='gpt-6-luna', reasoning_effort='none', service_tier='default',
)
DLPC_MFA_DISAMBIGUATION = HarnessIdentity(
    'canonical-dlpc-mfa-disambiguation-20261025-v1',
    '0bc2d14c5229a932f56d0b984f357ccfcc3833775860a9857e986a36a55d2806',
    'eb73ac8878e3154b6fc4c5b0609adb30bb0361e49048ad9b4bfbf5bde92e5e00',
    DLPC_LUNA_NONE.prompt_sha256,
    '55cdf30b40dd463080c2aeab8832821a2f6874a9994cc30657d8c5ef3eb6b714',
    True, True, True, True,
    model='gpt-6-luna', reasoning_effort='none', service_tier='default',
)
DLPC_SEUNGUN_SERVER = HarnessIdentity(
    'canonical-dlpc-seungun-server-20261025-v3',
    'c41b820326cd6a95a9ef6ff502b400be0a078ee9d51c5cce99fdabca5c60ee1d',
    'ecc76bcc2ca505f4b717d7a49add8b556fef427c9a1bc61f84204f11ce572639',
    DLPC_LUNA_NONE.prompt_sha256,
    'fb597319347d106e4a8e59d7bb6a254f031a7e85a21b87d8c349763829b0152d',
    True, True, True, True,
    model='gpt-6-luna', reasoning_effort='none', service_tier='default',
)
DLPC_PIPELINE_LATENCY = HarnessIdentity(
    'canonical-dlpc-pipeline-latency-20261025-v4',
    'c3d78433cdb0d9ca999ab7b407464084a95cbf8f6d931a80ffd9610656ea3b68', '2864be8b43b850136c7b41ffc9242265b5faa5890c48c59847f807051fc44114', 'fdd98e37b076c3b36058a09bc40667bcd2cd1d9d3a9d31a7106b5caa7df8ca7a', '9e29fbc9a7fcc039a6d4fa36b323cdcca61431100f3786cc9a55e41b53c4a4ba',
    True, True, True, True, model='gpt-6-luna', reasoning_effort='none', service_tier='default',
    selection_view=False,
)
DLPC_PIPELINE_SOL61_LIGHT = HarnessIdentity(
    'canonical-dlpc-pipeline-sol61-light-20261026-v1',
    '8abd31b94aa245617c8a1caf1218e2dae62fdd19058f83a2876abd0a27e135a9',
    '1903c5339773f301a1a26ba814e970987b3649a88fb3579c11ec41de30a16893',
    DLPC_PIPELINE_LATENCY.prompt_sha256, DLPC_PIPELINE_LATENCY.core_sha256,
    True, True, True, True, model='gpt-6.1-sol', reasoning_effort='low',
    service_tier='default', selection_view=False,
)
DLPC_INTERNAL_BOUNDED = HarnessIdentity(
    'canonical-dlpc-internal-bounded-20261025-v1',
    '25531c9e52c1088c9cdceee82a5e2d57d52c46da4b4cd93cac75eea87d6ade60', '570f92d23aa19f241e4ac73b35fccc406761bdfd2c097d1c83f3bb66c821d742', DLPC_CONCISE.prompt_sha256, 'f562484d436e42626114e16afa2bcaa782c1abbfc3cc3cdbd5c3b3cf030b920a', True, True, True, True,
)
# Read/verification candidates; promoting them requires the preceding stage's observations.
DLPC_LUNA_COMPACT = HarnessIdentity(
    'canonical-dlpc-gop-luna-compact-20261024-v1',
    '50701f1b8a64ddb1b6c5c9d440a7e1beb04ce12a0b423cc1e5f3277647d69db0',
    '1044117f1c7d300984210263a9afebabaa2ee0501f56aa5ad83922479a08e20b',
    '99a7270f45558a6a0c7cf5f4f169ebd7e6e74767e961c959152e9198b183eb58',
    DLPC_GOP.core_sha256, True, True, True, True,
    model='gpt-6-luna', reasoning_effort='none', service_tier='default',
)
DLPC_LUNA_CACHED = HarnessIdentity(
    'canonical-dlpc-gop-luna-cached-20261024-v1',
    '9cf431b34f7a5216236da8598ba4f127bbd89c56c920147a2d25d66b9b662c2d',
    '432222eb78ed2ccba797cef2515dd1e1be3527572343abe7c67bf1e2981a84f2',
    DLPC_LUNA_COMPACT.prompt_sha256, DLPC_GOP.core_sha256, True, True, True, True,
    model='gpt-6-luna', reasoning_effort='none', service_tier='default', explicit_prompt_cache=True,
)
# Read/verification identity only; this is not a developer execution approval.
AUDIOVISUAL = HarnessIdentity(
    'canonical-dlpc-audiovisual-20261007-v2',
    'd70372926d3406842b95c370b5ceb152480e7f0af83ba840028ec8d9ace2dc50',
    '934729ee467a2eb030c244584c6fa5c3575ca9ed0a43348a59a486199c0f55e9',
    'c727910b9e30c1648adb120cbb5f8ef2178c806285331dfd095fcc39c5aedfee',
    '4cc0cbacd41b5c6f2ac20e6e4840bc5406f4c7ab79ecf53702586eeb5874ecbc', True, True, True, True,
)
AUDIOVISUAL_LLM_DIRECTORY = 'canonical_dlpc_audiovisual_20261007_v2_h5'
AUDIOVISUAL_CORE_DIRECTORY = "canonical_dlpc_audiovisual_core_20261007_v2"
HARNESSES = {value.manifest_sha256: value for value in (S7, H5, SCORED_V1, SCORED_V2, SCORED, RESIDENT, NATIVE_V1, NATIVE, SEUNGUN_GPT_V1, SEUNGUN_GPT, DLPC_GOP, DLPC_ANNOUNCER, DLPC_CONCISE, DLPC_LUNA, DLPC_LUNA_NONE, DLPC_MFA_TRACE, DLPC_MFA_PREPARED, DLPC_MFA_DISAMBIGUATION, DLPC_SEUNGUN_SERVER, DLPC_PIPELINE_LATENCY, DLPC_PIPELINE_SOL61_LIGHT, DLPC_INTERNAL_BOUNDED, DLPC_LUNA_COMPACT, DLPC_LUNA_CACHED, AUDIOVISUAL)}


def harness_identity(manifest_sha256: str) -> HarnessIdentity:
    try:
        return HARNESSES[manifest_sha256]
    except (KeyError, TypeError):
        raise ValueError("CANONICAL_HARNESS_UNKNOWN") from None


def check_schema_registration(identity: HarnessIdentity) -> None:
    """Check release pins and v6 result classification before admitting inference."""
    from jsonschema import Draft202012Validator, ValidationError
    from .canonical_codec import _schema

    values = dict(parentManifestSha256=identity.core_sha256,
                  llmManifestSha256=identity.manifest_sha256,
                  llmDependencyLockSha256=identity.lock_sha256,
                  promptSha256=identity.prompt_sha256)
    try:
        for version, names in (("v4", ("association", "sourceIdentity")),
                               ("v6", ("association", "audioAssociation", "sourceIdentity"))):
            schema = _schema(version)
            Draft202012Validator(schema["$defs"]["binding"]["properties"][
                "parent_manifest_sha256"]).validate(identity.core_sha256)
            for name in names:
                definition = schema["$defs"][name]
                # Check configured identities without a fabricated recording/result.
                fragment = dict(properties={k: definition["properties"][k]
                                            for k in values if k in definition["properties"]},
                                oneOf=definition["oneOf"])
                Draft202012Validator(fragment).validate(values)
        _check_result_classification(_schema("v6"), identity)
    except ValidationError:
        raise ValueError("CANONICAL_HARNESS_SCHEMA_MISMATCH") from None


def _check_result_classification(schema: dict, identity: HarnessIdentity) -> None:
    """Registration alone does not select the scoring/GPT result branches.

    Inspect the actual serializer contract without inventing a recording, score
    or model response. Both the audiovisual result and its audio intermediate
    must classify the configured H5 consistently with its server-owned flags.
    """
    from jsonschema import Draft202012Validator

    for name in ("result", "audioResult"):
        classifications = {"scoring": [], "gpt_grading": []}
        for rule in schema["$defs"][name]["allOf"]:
            fields = rule.get("then", {}).get("properties", {})
            score_options = fields.get("score", {}).get("oneOf", [])
            if {"$ref": "#/$defs/computedScore"} in score_options:
                classifications["scoring"].append(rule)
            if (fields.get("selection") == {"const": None}
                    and "feedback" in fields.get("coaching", {}).get("required", [])):
                classifications["gpt_grading"].append(rule)
        for flag, rules in classifications.items():
            # A changed/missing contract branch needs an explicit reader update.
            if len(rules) != 1:
                raise ValueError("CANONICAL_HARNESS_SCHEMA_MISMATCH")
            manifest = rules[0]["if"]["properties"]["sourceIdentity"]["properties"]["llmManifestSha256"]
            selected = Draft202012Validator(manifest).is_valid(identity.manifest_sha256)
            if selected != getattr(identity, flag):
                raise ValueError("CANONICAL_HARNESS_SCHEMA_MISMATCH")
