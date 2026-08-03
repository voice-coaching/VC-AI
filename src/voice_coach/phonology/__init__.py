"""Korean phone-label normalization and pronunciation-error ontology."""

from .error_tags import (
    ERROR_TAG_ID_TO_RAW_LABEL,
    RAW_ERROR_TAG_TO_ID,
    ErrorTagId,
    normalize_error_tag,
)
from .error_mapping import (
    PHONE_LOCALIZABLE_ERROR_TAGS,
    ErrorPhoneAssignment,
    ErrorPhoneMatchStatus,
    ErrorTagInterval,
    TargetPhoneInterval,
    assign_error_tags_to_target_phones,
)
from .expected_phone_agreement import (
    AIHUB_EXPECTED_SOURCE_ROLE,
    EXPECTED_PHONE_AGREEMENT_HARD_MAX_CELLS,
    EXPECTED_PHONE_AGREEMENT_POLICY_ID,
    G2PK_EXPECTED_SOURCE_ROLE,
    ExpectedPhoneAgreementError,
    ExpectedPhoneAgreementResult,
    evaluate_expected_phone_agreement,
)
from .phones import (
    EMPTY_REALIZED_PHONE_LABELS,
    KOREAN_COMPATIBILITY_JAMO,
    SPEECH_NOISE_TOKEN,
    UNKNOWN_PHONE_TOKEN,
    normalize_realized_phone_label,
    normalize_realized_phone_labels,
)
from .g2pk_expected import (
    EXPECTED_CMUDICT_FILES,
    EXPECTED_G2PK_DEPENDENCIES,
    EXPECTED_G2PK_VERSION,
    G2PKExpectedPhoneError,
    G2PKExpectedPhones,
    WordBoundaryPolicy,
    generate_g2pk_expected_phones,
)

__all__ = [
    "AIHUB_EXPECTED_SOURCE_ROLE",
    "EMPTY_REALIZED_PHONE_LABELS",
    "ERROR_TAG_ID_TO_RAW_LABEL",
    "EXPECTED_PHONE_AGREEMENT_HARD_MAX_CELLS",
    "EXPECTED_PHONE_AGREEMENT_POLICY_ID",
    "EXPECTED_CMUDICT_FILES",
    "EXPECTED_G2PK_DEPENDENCIES",
    "EXPECTED_G2PK_VERSION",
    "G2PKExpectedPhoneError",
    "G2PKExpectedPhones",
    "G2PK_EXPECTED_SOURCE_ROLE",
    "PHONE_LOCALIZABLE_ERROR_TAGS",
    "KOREAN_COMPATIBILITY_JAMO",
    "RAW_ERROR_TAG_TO_ID",
    "SPEECH_NOISE_TOKEN",
    "UNKNOWN_PHONE_TOKEN",
    "ErrorTagId",
    "ExpectedPhoneAgreementError",
    "ExpectedPhoneAgreementResult",
    "ErrorPhoneAssignment",
    "ErrorPhoneMatchStatus",
    "ErrorTagInterval",
    "TargetPhoneInterval",
    "WordBoundaryPolicy",
    "assign_error_tags_to_target_phones",
    "evaluate_expected_phone_agreement",
    "generate_g2pk_expected_phones",
    "normalize_error_tag",
    "normalize_realized_phone_label",
    "normalize_realized_phone_labels",
]
