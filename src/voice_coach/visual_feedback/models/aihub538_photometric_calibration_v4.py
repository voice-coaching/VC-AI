"""Non-promotional P6 v4 contracts for photometric calibration redesign.

The failed v3 cohort may inform this design, but it can never contribute a
measurement to the v4 policy or its evaluation.  Only privacy-safe aggregates
and identity-set commitments are serializable.  Actor and attempt tokens stay
inside the calibration service process.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math

from ..errors import VisualFeedbackContractError
from .integrity import (
    require_boolean,
    require_exact_keys,
    require_finite_number,
    require_integer,
    require_mapping,
    require_safe_identifier,
    require_sha256,
    sha256_document,
)


FFMPEG_RGB_COLORIMETRY_RECEIPT_SCHEMA_VERSION = (
    "voice_coach.ffmpeg_rgb_colorimetry_receipt.v1"
)
FFMPEG_SOURCE_COLOR_PROBE_RECEIPT_V4_SCHEMA_VERSION = (
    "voice_coach.ffmpeg_source_color_probe_receipt.v4"
)
FFMPEG_RGB_SOURCE_CONVERSION_RECEIPT_V4_SCHEMA_VERSION = (
    "voice_coach.ffmpeg_rgb_source_conversion_receipt.v4"
)
DECODED_RGB_DERIVATION_MANIFEST_V4 = {
    "schema_version": "voice_coach.decoded_rgb_derivation_manifest.v4",
    "input_boundary": "inherited_read_only_fd",
    "source_probe": "ffprobe_exact_stream_color_metadata_v1",
    "conversion": "ffmpeg_scale_bt709_tv_to_rgb24_pc_v1",
    "evidence_digest": "voice_coach.decoded_rgb_sample_evidence.v1",
    "implicit_color_conversion_allowed": False,
}
DECODED_RGB_DERIVATION_MANIFEST_V4_SHA256 = sha256_document(DECODED_RGB_DERIVATION_MANIFEST_V4)
FFMPEG_FD_ADAPTER_EXECUTION_MANIFEST_V4 = {
    "schema_version": "voice_coach.ffmpeg_fd_adapter_execution_manifest.v4",
    "allowed_binary_roots": ["/bin", "/usr/bin"],
    "absolute_binary_configuration_allowed": False,
    "network_isolation_launcher_sha256": "d6b3f083756bbf0e4d223af991e88eab028481ae0bdb50be72fc0d94ab441b74",
    "source_boundary": "inherited_read_only_fd",
    "shell": False,
}
FFMPEG_FD_ADAPTER_EXECUTION_MANIFEST_V4_SHA256 = sha256_document(FFMPEG_FD_ADAPTER_EXECUTION_MANIFEST_V4)
AIHUB538_PHOTOMETRIC_PROTOCOL_V4_SCHEMA_VERSION = (
    "voice_coach.aihub538_photometric_protocol.v4"
)
AIHUB538_PHOTOMETRIC_POLICY_V4_SCHEMA_VERSION = (
    "voice_coach.aihub538_photometric_policy.v4"
)
AIHUB538_FRESH_COHORT_SEPARATION_V4_SCHEMA_VERSION = (
    "voice_coach.aihub538_fresh_cohort_separation.v4"
)
AIHUB538_PHOTOMETRIC_EVIDENCE_V4_SCHEMA_VERSION = (
    "voice_coach.aihub538_photometric_calibration_evidence.v4"
)

AIHUB538_V3_FAILURE_ARTIFACT_SHA256 = (
    "75577cdb92e9f82ec92e1fbfe2d12d5b2abd09a36a6b3b3e626a0fb0e367a2bb"
)
AIHUB538_V3_COHORT_ROLE = "design_only_non_promotional"

AIHUB538_PHOTOMETRIC_V4_FROZEN_SAFETY_AND_STATISTICS = {
    "schema_version": "voice_coach.aihub538_photometric_v4_frozen_constants.v1",
    "policy_state": "frozen_pre_observation",
    "minimum_single_face_coverage": 0.75,
    "maximum_multiple_face_frame_fraction": 0.125,
    "maximum_catastrophic_underexposed_pixel_fraction": 0.90,
    "maximum_catastrophic_overexposed_pixel_fraction": 0.90,
    "minimum_lip_roi_visibility_fraction": 0.75,
    "minimum_track_transition_coverage": 0.70,
    "minimum_longest_single_face_run_fraction": 0.70,
    "maximum_lip_center_step_normalized": 0.20,
    "minimum_development_actor_count": 4,
    "minimum_calibration_actor_count": 4,
    "minimum_attempts_per_actor": 4,
    "soft_score_upper_quantile": 0.90,
    "soft_score_margin": 0.25,
    "soft_metric_contribution_cap": 6.0,
    "full_frame_group_weight": 0.50,
    "lip_roi_group_weight": 0.50,
    "minimum_actor_attempt_pass_fraction": 0.75,
    "actor_ci_confidence_level": 0.95,
    "actor_ci_z_score": 1.6448536269514722,
    "minimum_actor_ci_lower_bound": 0.50,
    "minimum_publishable_cell_actor_count": 4,
}
AIHUB538_PHOTOMETRIC_V4_FROZEN_SAFETY_AND_STATISTICS_SHA256 = sha256_document(
    AIHUB538_PHOTOMETRIC_V4_FROZEN_SAFETY_AND_STATISTICS
)

FFMPEG_RGB_COLORIMETRY_CONVERSION_DOCUMENT = {
    "schema_version": "voice_coach.ffmpeg_rgb_conversion_contract.v1",
    "input_pixel_format": "yuv420p",
    "input_color_range": "tv",
    "input_color_matrix": "bt709",
    "input_color_transfer": "bt709",
    "input_color_primaries": "bt709",
    "output_pixel_format": "rgb24",
    "output_color_range": "pc",
    "output_encoder_threads": 1,
    "conversion_filter": (
        "scale=in_range=tv:out_range=pc:in_color_matrix=bt709:"
        "out_color_matrix=bt709:flags=accurate_rnd+full_chroma_int,"
        "format=rgb24"
    ),
    "implicit_colorimetry_allowed": False,
    "source_color_metadata_required": True,
    "transfer_metadata_binding": "bt709_metadata_required_not_numerically_tested",
    "primaries_metadata_binding": "bt709_metadata_required_not_numerically_tested",
}
FFMPEG_RGB_COLORIMETRY_CONVERSION_CONTRACT_SHA256 = sha256_document(
    FFMPEG_RGB_COLORIMETRY_CONVERSION_DOCUMENT
)

# Four 2x2 limited-range BT.709 YUV420P frames: black, white, mid-gray, red.
# The serialized receipt contains only the set digest, never the raw vectors.
FFMPEG_RGB_COLORIMETRY_GOLDEN_INPUT = bytes.fromhex(
    "101010108080"
    "ebebebeb8080"
    "7e7e7e7e8080"
    "3f3f3f3f66f0"
)
FFMPEG_RGB_COLORIMETRY_GOLDEN_OUTPUT = bytes.fromhex(
    "000000000000000000000000"
    "ffffffffffffffffffffffff"
    "808080808080808080808080"
    "ff0100ff0100ff0100ff0100"
)
FFMPEG_RGB_COLORIMETRY_GOLDEN_VECTOR_SET_SHA256 = sha256_document(
    {
        "schema_version": "voice_coach.ffmpeg_rgb_golden_vectors.v1",
        "frame_geometry": "2x2",
        "frame_count": 4,
        "input_sha256": hashlib.sha256(
            FFMPEG_RGB_COLORIMETRY_GOLDEN_INPUT
        ).hexdigest(),
        "expected_output_sha256": hashlib.sha256(
            FFMPEG_RGB_COLORIMETRY_GOLDEN_OUTPUT
        ).hexdigest(),
        "conversion_contract_sha256": (
            FFMPEG_RGB_COLORIMETRY_CONVERSION_CONTRACT_SHA256
        ),
    }
)
FFMPEG_RGB_COLORIMETRY_GOLDEN_OUTPUT_SHA256 = hashlib.sha256(
    FFMPEG_RGB_COLORIMETRY_GOLDEN_OUTPUT
).hexdigest()


def _fraction(value: object, name: str) -> float:
    number = require_finite_number(value, name)
    if not 0.0 <= number <= 1.0:
        raise VisualFeedbackContractError(f"{name} must be in [0, 1]")
    return number


def _optional_number(value: object, name: str) -> float | None:
    return None if value is None else require_finite_number(value, name)


@dataclass(frozen=True, slots=True)
class FfmpegRgbColorimetryReceipt:
    """Adapter-level proof that the explicit RGB conversion passed goldens."""

    ffmpeg_revision: str
    adapter_code_sha256: str
    observed_output_sha256: str
    observed_output_byte_count: int
    receipt_sha256: str
    conversion_contract_sha256: str = (
        FFMPEG_RGB_COLORIMETRY_CONVERSION_CONTRACT_SHA256
    )
    golden_vector_set_sha256: str = (
        FFMPEG_RGB_COLORIMETRY_GOLDEN_VECTOR_SET_SHA256
    )
    schema_version: str = FFMPEG_RGB_COLORIMETRY_RECEIPT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            self.schema_version != FFMPEG_RGB_COLORIMETRY_RECEIPT_SCHEMA_VERSION
            or self.conversion_contract_sha256
            != FFMPEG_RGB_COLORIMETRY_CONVERSION_CONTRACT_SHA256
            or self.golden_vector_set_sha256
            != FFMPEG_RGB_COLORIMETRY_GOLDEN_VECTOR_SET_SHA256
        ):
            raise VisualFeedbackContractError(
                "ffmpeg RGB colorimetry contract differs"
            )
        require_safe_identifier(self.ffmpeg_revision, "ffmpeg_revision")
        revision_parts = self.ffmpeg_revision.split(":")
        if (
            len(revision_parts) != 3
            or revision_parts[0] != "ffmpeg"
            or not revision_parts[1]
        ):
            raise VisualFeedbackContractError("ffmpeg revision identity differs")
        require_sha256(revision_parts[2], "ffmpeg_revision_digest")
        require_sha256(self.adapter_code_sha256, "adapter_code_sha256")
        require_sha256(
            self.observed_output_sha256, "observed_output_sha256"
        )
        if (
            self.observed_output_sha256
            != FFMPEG_RGB_COLORIMETRY_GOLDEN_OUTPUT_SHA256
            or require_integer(
                self.observed_output_byte_count,
                "observed_output_byte_count",
                minimum=0,
            )
            != len(FFMPEG_RGB_COLORIMETRY_GOLDEN_OUTPUT)
        ):
            raise VisualFeedbackContractError(
                "ffmpeg RGB colorimetry golden vectors differ"
            )
        require_sha256(self.receipt_sha256, "receipt_sha256")
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError(
                "ffmpeg RGB colorimetry receipt digest differs"
            )

    @classmethod
    def issue(
        cls,
        *,
        ffmpeg_revision: str,
        adapter_code_sha256: str,
        observed_output_sha256: str,
        observed_output_byte_count: int,
    ) -> FfmpegRgbColorimetryReceipt:
        unsigned = cls._unsigned_document(
            ffmpeg_revision=ffmpeg_revision,
            adapter_code_sha256=adapter_code_sha256,
            observed_output_sha256=observed_output_sha256,
            observed_output_byte_count=observed_output_byte_count,
        )
        return cls(
            ffmpeg_revision=ffmpeg_revision,
            adapter_code_sha256=adapter_code_sha256,
            observed_output_sha256=observed_output_sha256,
            observed_output_byte_count=observed_output_byte_count,
            receipt_sha256=sha256_document(unsigned),
        )

    @staticmethod
    def _unsigned_document(
        *,
        ffmpeg_revision: str,
        adapter_code_sha256: str,
        observed_output_sha256: str,
        observed_output_byte_count: int,
    ) -> dict[str, object]:
        return {
            "schema_version": FFMPEG_RGB_COLORIMETRY_RECEIPT_SCHEMA_VERSION,
            "status": "range_matrix_golden_vectors_verified",
            "scope": (
                "adapter_range_matrix_contract_not_transfer_numeric_proof_"
                "or_source_stream_claim"
            ),
            "ffmpeg_revision": ffmpeg_revision,
            "adapter_code_sha256": adapter_code_sha256,
            "conversion_contract_sha256": (
                FFMPEG_RGB_COLORIMETRY_CONVERSION_CONTRACT_SHA256
            ),
            "decoded_rgb_derivation_manifest_sha256": DECODED_RGB_DERIVATION_MANIFEST_V4_SHA256,
            "golden_vector_set_sha256": (
                FFMPEG_RGB_COLORIMETRY_GOLDEN_VECTOR_SET_SHA256
            ),
            "observed_output_sha256": observed_output_sha256,
            "observed_output_byte_count": observed_output_byte_count,
            "input_color_range": "tv",
            "input_color_matrix": "bt709",
            "input_color_transfer": "bt709",
            "input_color_primaries": "bt709",
            "output_pixel_format": "rgb24",
            "output_color_range": "pc",
            "source_color_metadata_required": True,
            "implicit_colorimetry_allowed": False,
            "transfer_metadata_bound": True,
            "transfer_numerical_effect_verified": False,
            "primaries_metadata_bound": True,
            "primaries_numerical_effect_verified": False,
            "raw_vectors_serialized": False,
        }

    def unsigned_json_object(self) -> dict[str, object]:
        return self._unsigned_document(
            ffmpeg_revision=self.ffmpeg_revision,
            adapter_code_sha256=self.adapter_code_sha256,
            observed_output_sha256=self.observed_output_sha256,
            observed_output_byte_count=self.observed_output_byte_count,
        )

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> FfmpegRgbColorimetryReceipt:
        document = require_mapping(value, "ffmpeg_rgb_colorimetry_receipt")
        require_exact_keys(
            document,
            {
                "schema_version",
                "status",
                "scope",
                "ffmpeg_revision",
                "adapter_code_sha256",
                "conversion_contract_sha256",
                "decoded_rgb_derivation_manifest_sha256",
                "golden_vector_set_sha256",
                "observed_output_sha256",
                "observed_output_byte_count",
                "input_color_range",
                "input_color_matrix",
                "input_color_transfer",
                "input_color_primaries",
                "output_pixel_format",
                "output_color_range",
                "source_color_metadata_required",
                "implicit_colorimetry_allowed",
                "transfer_metadata_bound",
                "transfer_numerical_effect_verified",
                "primaries_metadata_bound",
                "primaries_numerical_effect_verified",
                "raw_vectors_serialized",
                "receipt_sha256",
            },
            "ffmpeg_rgb_colorimetry_receipt",
        )
        if (
            document["status"] != "range_matrix_golden_vectors_verified"
            or document["scope"]
            != (
                "adapter_range_matrix_contract_not_transfer_numeric_proof_"
                "or_source_stream_claim"
            )
            or document["decoded_rgb_derivation_manifest_sha256"]
            != DECODED_RGB_DERIVATION_MANIFEST_V4_SHA256
            or document["input_color_range"] != "tv"
            or document["input_color_matrix"] != "bt709"
            or document["input_color_transfer"] != "bt709"
            or document["input_color_primaries"] != "bt709"
            or document["output_pixel_format"] != "rgb24"
            or document["output_color_range"] != "pc"
            or require_boolean(
                document["source_color_metadata_required"],
                "source_color_metadata_required",
            )
            is not True
            or require_boolean(
                document["implicit_colorimetry_allowed"],
                "implicit_colorimetry_allowed",
            )
            is not False
            or require_boolean(
                document["raw_vectors_serialized"],
                "raw_vectors_serialized",
            )
            is not False
            or require_boolean(
                document["transfer_metadata_bound"],
                "transfer_metadata_bound",
            )
            is not True
            or require_boolean(
                document["transfer_numerical_effect_verified"],
                "transfer_numerical_effect_verified",
            )
            is not False
            or require_boolean(
                document["primaries_metadata_bound"],
                "primaries_metadata_bound",
            )
            is not True
            or require_boolean(
                document["primaries_numerical_effect_verified"],
                "primaries_numerical_effect_verified",
            )
            is not False
        ):
            raise VisualFeedbackContractError(
                "ffmpeg RGB colorimetry receipt semantics differ"
            )
        return cls(
            ffmpeg_revision=require_safe_identifier(
                document["ffmpeg_revision"], "ffmpeg_revision"
            ),
            adapter_code_sha256=require_sha256(
                document["adapter_code_sha256"], "adapter_code_sha256"
            ),
            conversion_contract_sha256=require_sha256(
                document["conversion_contract_sha256"],
                "conversion_contract_sha256",
            ),
            golden_vector_set_sha256=require_sha256(
                document["golden_vector_set_sha256"],
                "golden_vector_set_sha256",
            ),
            observed_output_sha256=require_sha256(
                document["observed_output_sha256"],
                "observed_output_sha256",
            ),
            observed_output_byte_count=require_integer(
                document["observed_output_byte_count"],
                "observed_output_byte_count",
                minimum=0,
            ),
            receipt_sha256=require_sha256(
                document["receipt_sha256"], "receipt_sha256"
            ),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
        )


@dataclass(frozen=True, slots=True)
class FfmpegSourceColorProbeReceiptV4:
    """Exact source metadata assertion from a dedicated v4 ffprobe boundary."""

    container_sha256: str
    video_stream_index: int
    ffprobe_revision: str
    probe_adapter_code_sha256: str
    metadata_stdout_sha256: str
    stderr_sha256: str
    receipt_sha256: str
    schema_version: str = FFMPEG_SOURCE_COLOR_PROBE_RECEIPT_V4_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != FFMPEG_SOURCE_COLOR_PROBE_RECEIPT_V4_SCHEMA_VERSION:
            raise VisualFeedbackContractError("source color probe schema differs")
        require_sha256(self.container_sha256, "container_sha256")
        require_integer(self.video_stream_index, "video_stream_index", minimum=0)
        require_safe_identifier(self.ffprobe_revision, "ffprobe_revision")
        require_sha256(
            self.probe_adapter_code_sha256, "probe_adapter_code_sha256"
        )
        require_sha256(self.metadata_stdout_sha256,"metadata_stdout_sha256")
        if self.stderr_sha256 != hashlib.sha256(b"").hexdigest():
            raise VisualFeedbackContractError("source color probe stderr is not empty")
        require_sha256(self.receipt_sha256, "receipt_sha256")
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("source color probe digest differs")

    @classmethod
    def issue(
        cls,
        *,
        container_sha256: str,
        video_stream_index: int,
        ffprobe_revision: str,
        probe_adapter_code_sha256: str,
        pixel_format: str,
        color_range: str,
        color_matrix: str,
        color_transfer: str,
        color_primaries: str,
        metadata_stdout_sha256: str,
        stderr_sha256: str,
    ) -> FfmpegSourceColorProbeReceiptV4:
        if (
            pixel_format != "yuv420p"
            or color_range != "tv"
            or color_matrix != "bt709"
            or color_transfer != "bt709"
            or color_primaries != "bt709"
        ):
            raise VisualFeedbackContractError(
                "source color metadata is missing or unsupported"
            )
        values = {
            "container_sha256": container_sha256,
            "video_stream_index": video_stream_index,
            "ffprobe_revision": ffprobe_revision,
            "probe_adapter_code_sha256": probe_adapter_code_sha256,
            "metadata_stdout_sha256": metadata_stdout_sha256,
            "stderr_sha256": stderr_sha256,
        }
        unsigned = cls._unsigned_document(**values)
        return cls(**values, receipt_sha256=sha256_document(unsigned))

    @staticmethod
    def _unsigned_document(**values: object) -> dict[str, object]:
        return {
            "schema_version": FFMPEG_SOURCE_COLOR_PROBE_RECEIPT_V4_SCHEMA_VERSION,
            **values,
            "adapter_execution_manifest_sha256": FFMPEG_FD_ADAPTER_EXECUTION_MANIFEST_V4_SHA256,
            "pixel_format": "yuv420p",
            "color_range": "tv",
            "color_matrix": "bt709",
            "color_transfer": "bt709",
            "color_primaries": "bt709",
            "metadata_complete": True,
            "transfer_numerical_effect_verified": False,
            "primaries_numerical_effect_verified": False,
        }

    def unsigned_json_object(self) -> dict[str, object]:
        return self._unsigned_document(
            container_sha256=self.container_sha256,
            video_stream_index=self.video_stream_index,
            ffprobe_revision=self.ffprobe_revision,
            probe_adapter_code_sha256=self.probe_adapter_code_sha256,
            metadata_stdout_sha256=self.metadata_stdout_sha256,
            stderr_sha256=self.stderr_sha256,
        )

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> FfmpegSourceColorProbeReceiptV4:
        document = require_mapping(value, "source_color_probe_receipt")
        expected = {
            "schema_version",
            "container_sha256",
            "video_stream_index",
            "ffprobe_revision",
            "probe_adapter_code_sha256",
            "metadata_stdout_sha256",
            "stderr_sha256",
            "adapter_execution_manifest_sha256",
            "pixel_format",
            "color_range",
            "color_matrix",
            "color_transfer",
            "color_primaries",
            "metadata_complete",
            "transfer_numerical_effect_verified",
            "primaries_numerical_effect_verified",
            "receipt_sha256",
        }
        require_exact_keys(document, expected, "source_color_probe_receipt")
        if (
            document["pixel_format"] != "yuv420p"
            or document["adapter_execution_manifest_sha256"] != FFMPEG_FD_ADAPTER_EXECUTION_MANIFEST_V4_SHA256
            or document["color_range"] != "tv"
            or document["color_matrix"] != "bt709"
            or document["color_transfer"] != "bt709"
            or document["color_primaries"] != "bt709"
            or require_boolean(document["metadata_complete"], "metadata_complete")
            is not True
            or require_boolean(
                document["transfer_numerical_effect_verified"],
                "transfer_numerical_effect_verified",
            )
            is not False
            or require_boolean(
                document["primaries_numerical_effect_verified"],
                "primaries_numerical_effect_verified",
            )
            is not False
        ):
            raise VisualFeedbackContractError("source color probe semantics differ")
        return cls(
            container_sha256=require_sha256(
                document["container_sha256"], "container_sha256"
            ),
            video_stream_index=require_integer(
                document["video_stream_index"], "video_stream_index", minimum=0
            ),
            ffprobe_revision=require_safe_identifier(
                document["ffprobe_revision"], "ffprobe_revision"
            ),
            probe_adapter_code_sha256=require_sha256(
                document["probe_adapter_code_sha256"],
                "probe_adapter_code_sha256",
            ),
            metadata_stdout_sha256=require_sha256(document["metadata_stdout_sha256"],"metadata_stdout_sha256"),
            stderr_sha256=require_sha256(document["stderr_sha256"],"stderr_sha256"),
            receipt_sha256=require_sha256(
                document["receipt_sha256"], "receipt_sha256"
            ),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
        )


@dataclass(frozen=True, slots=True)
class FfmpegRgbSourceConversionReceiptV4:
    """Bind one decoded RGB digest to exact source metadata and goldens."""

    source_probe_receipt_sha256: str
    adapter_colorimetry_receipt_sha256: str
    container_sha256: str
    video_stream_index: int
    decoded_rgb_evidence_sha256: str
    ffmpeg_revision: str
    receipt_sha256: str
    schema_version: str = FFMPEG_RGB_SOURCE_CONVERSION_RECEIPT_V4_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != FFMPEG_RGB_SOURCE_CONVERSION_RECEIPT_V4_SCHEMA_VERSION:
            raise VisualFeedbackContractError("source RGB conversion schema differs")
        for name in (
            "source_probe_receipt_sha256",
            "adapter_colorimetry_receipt_sha256",
            "container_sha256",
            "decoded_rgb_evidence_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        require_integer(self.video_stream_index, "video_stream_index", minimum=0)
        require_safe_identifier(self.ffmpeg_revision, "ffmpeg_revision")
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("source RGB conversion digest differs")

    @classmethod
    def issue(
        cls,
        *,
        source_probe_receipt: FfmpegSourceColorProbeReceiptV4,
        adapter_colorimetry_receipt: FfmpegRgbColorimetryReceipt,
        decoded_rgb_evidence_sha256: str,
        ffmpeg_revision: str,
    ) -> FfmpegRgbSourceConversionReceiptV4:
        if (
            type(source_probe_receipt) is not FfmpegSourceColorProbeReceiptV4
            or type(adapter_colorimetry_receipt) is not FfmpegRgbColorimetryReceipt
            or ffmpeg_revision != adapter_colorimetry_receipt.ffmpeg_revision
        ):
            raise VisualFeedbackContractError("source RGB conversion lineage differs")
        values = {
            "source_probe_receipt_sha256": source_probe_receipt.receipt_sha256,
            "adapter_colorimetry_receipt_sha256": (
                adapter_colorimetry_receipt.receipt_sha256
            ),
            "container_sha256": source_probe_receipt.container_sha256,
            "video_stream_index": source_probe_receipt.video_stream_index,
            "decoded_rgb_evidence_sha256": decoded_rgb_evidence_sha256,
            "ffmpeg_revision": ffmpeg_revision,
        }
        return cls(
            **values,
            receipt_sha256=sha256_document(cls._unsigned_document(**values)),
        )

    @staticmethod
    def _unsigned_document(**values: object) -> dict[str, object]:
        return {
            "schema_version": FFMPEG_RGB_SOURCE_CONVERSION_RECEIPT_V4_SCHEMA_VERSION,
            **values,
            "conversion_contract_sha256": (
                FFMPEG_RGB_COLORIMETRY_CONVERSION_CONTRACT_SHA256
            ),
            "decoded_rgb_derivation_manifest_sha256": DECODED_RGB_DERIVATION_MANIFEST_V4_SHA256,
            "range_matrix_golden_verified": True,
            "source_metadata_exact_bound": True,
            "transfer_numerical_effect_verified": False,
            "primaries_numerical_effect_verified": False,
            "implicit_conversion_allowed": False,
        }

    def unsigned_json_object(self) -> dict[str, object]:
        return self._unsigned_document(
            source_probe_receipt_sha256=self.source_probe_receipt_sha256,
            adapter_colorimetry_receipt_sha256=(
                self.adapter_colorimetry_receipt_sha256
            ),
            container_sha256=self.container_sha256,
            video_stream_index=self.video_stream_index,
            decoded_rgb_evidence_sha256=self.decoded_rgb_evidence_sha256,
            ffmpeg_revision=self.ffmpeg_revision,
        )

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> FfmpegRgbSourceConversionReceiptV4:
        document = require_mapping(value, "source_rgb_conversion_receipt")
        fields = {
            "schema_version", "source_probe_receipt_sha256",
            "adapter_colorimetry_receipt_sha256", "container_sha256",
            "video_stream_index", "decoded_rgb_evidence_sha256",
            "ffmpeg_revision", "conversion_contract_sha256",
            "decoded_rgb_derivation_manifest_sha256",
            "range_matrix_golden_verified", "source_metadata_exact_bound",
            "transfer_numerical_effect_verified",
            "primaries_numerical_effect_verified", "implicit_conversion_allowed",
            "receipt_sha256",
        }
        require_exact_keys(document, fields, "source_rgb_conversion_receipt")
        if (
            document["conversion_contract_sha256"]
            != FFMPEG_RGB_COLORIMETRY_CONVERSION_CONTRACT_SHA256
            or document["decoded_rgb_derivation_manifest_sha256"] != DECODED_RGB_DERIVATION_MANIFEST_V4_SHA256
            or require_boolean(document["range_matrix_golden_verified"], "range_matrix_golden_verified") is not True
            or require_boolean(document["source_metadata_exact_bound"], "source_metadata_exact_bound") is not True
            or require_boolean(document["transfer_numerical_effect_verified"], "transfer_numerical_effect_verified") is not False
            or require_boolean(document["primaries_numerical_effect_verified"], "primaries_numerical_effect_verified") is not False
            or require_boolean(document["implicit_conversion_allowed"], "implicit_conversion_allowed") is not False
        ):
            raise VisualFeedbackContractError("source RGB conversion semantics differ")
        return cls(
            source_probe_receipt_sha256=require_sha256(document["source_probe_receipt_sha256"], "source_probe_receipt_sha256"),
            adapter_colorimetry_receipt_sha256=require_sha256(document["adapter_colorimetry_receipt_sha256"], "adapter_colorimetry_receipt_sha256"),
            container_sha256=require_sha256(document["container_sha256"], "container_sha256"),
            video_stream_index=require_integer(document["video_stream_index"], "video_stream_index", minimum=0),
            decoded_rgb_evidence_sha256=require_sha256(document["decoded_rgb_evidence_sha256"], "decoded_rgb_evidence_sha256"),
            ffmpeg_revision=require_safe_identifier(document["ffmpeg_revision"], "ffmpeg_revision"),
            receipt_sha256=require_sha256(document["receipt_sha256"], "receipt_sha256"),
            schema_version=require_safe_identifier(document["schema_version"], "schema_version"),
        )


@dataclass(frozen=True, slots=True)
class Aihub538HardSafetyEnvelopeV4:
    """Outcome-independent hard failures; photometric fit cannot waive them."""

    minimum_single_face_coverage: float
    maximum_multiple_face_frame_fraction: float
    maximum_catastrophic_underexposed_pixel_fraction: float
    maximum_catastrophic_overexposed_pixel_fraction: float
    minimum_lip_roi_visibility_fraction: float
    minimum_track_transition_coverage: float
    minimum_longest_single_face_run_fraction: float
    maximum_lip_center_step_normalized: float

    def __post_init__(self) -> None:
        for name in (
            "minimum_single_face_coverage",
            "maximum_multiple_face_frame_fraction",
            "maximum_catastrophic_underexposed_pixel_fraction",
            "maximum_catastrophic_overexposed_pixel_fraction",
            "minimum_lip_roi_visibility_fraction",
            "minimum_track_transition_coverage",
            "minimum_longest_single_face_run_fraction",
        ):
            _fraction(getattr(self, name), name)
        maximum_step = require_finite_number(
            self.maximum_lip_center_step_normalized,
            "maximum_lip_center_step_normalized",
        )
        if not 0.0 < maximum_step <= math.sqrt(2.0):
            raise VisualFeedbackContractError(
                "maximum lip center step is outside hard safety envelope"
            )

    def to_json_object(self) -> dict[str, object]:
        return {
            "minimum_single_face_coverage": self.minimum_single_face_coverage,
            "maximum_multiple_face_frame_fraction": (
                self.maximum_multiple_face_frame_fraction
            ),
            "maximum_catastrophic_underexposed_pixel_fraction": (
                self.maximum_catastrophic_underexposed_pixel_fraction
            ),
            "maximum_catastrophic_overexposed_pixel_fraction": (
                self.maximum_catastrophic_overexposed_pixel_fraction
            ),
            "minimum_lip_roi_visibility_fraction": (
                self.minimum_lip_roi_visibility_fraction
            ),
            "minimum_track_transition_coverage": (
                self.minimum_track_transition_coverage
            ),
            "minimum_longest_single_face_run_fraction": (
                self.minimum_longest_single_face_run_fraction
            ),
            "maximum_lip_center_step_normalized": (
                self.maximum_lip_center_step_normalized
            ),
            "availability_required": True,
            "lip_roi_exposure_required": True,
            "derived_from_photometric_fit": False,
            "soft_score_can_override": False,
        }

    @classmethod
    def from_json_object(cls, value: object) -> Aihub538HardSafetyEnvelopeV4:
        document = require_mapping(value, "hard_safety_envelope")
        fields = {
            "minimum_single_face_coverage",
            "maximum_multiple_face_frame_fraction",
            "maximum_catastrophic_underexposed_pixel_fraction",
            "maximum_catastrophic_overexposed_pixel_fraction",
            "minimum_lip_roi_visibility_fraction",
            "minimum_track_transition_coverage",
            "minimum_longest_single_face_run_fraction",
            "maximum_lip_center_step_normalized",
            "availability_required",
            "lip_roi_exposure_required",
            "derived_from_photometric_fit",
            "soft_score_can_override",
        }
        require_exact_keys(document, fields, "hard_safety_envelope")
        for name, expected in (
            ("availability_required", True),
            ("lip_roi_exposure_required", True),
            ("derived_from_photometric_fit", False),
            ("soft_score_can_override", False),
        ):
            if require_boolean(document[name], name) is not expected:
                raise VisualFeedbackContractError(
                    "hard safety envelope semantics differ"
                )
        return cls(
            **{
                name: require_finite_number(document[name], name)
                for name in fields
                if name
                not in {
                    "availability_required",
                    "lip_roi_exposure_required",
                    "derived_from_photometric_fit",
                    "soft_score_can_override",
                }
            }
        )


@dataclass(frozen=True, slots=True)
class Aihub538PhotometricCalibrationProtocolV4:
    """Frozen inputs for a new cohort, never a rewrite of the v3 protocol."""

    protocol_revision: str
    video_qc_measurement_policy_sha256: str
    video_qc_measurement_revision: str
    video_qc_measurement_adapter_code_sha256: str
    hard_safety_envelope: Aihub538HardSafetyEnvelopeV4
    minimum_development_actor_count: int
    minimum_calibration_actor_count: int
    minimum_attempts_per_actor: int
    soft_score_upper_quantile: float
    soft_score_margin: float
    soft_metric_contribution_cap: float
    full_frame_group_weight: float
    lip_roi_group_weight: float
    minimum_actor_attempt_pass_fraction: float
    actor_ci_confidence_level: float
    actor_ci_z_score: float
    minimum_actor_ci_lower_bound: float
    prior_v3_failure_artifact_sha256: str = (
        AIHUB538_V3_FAILURE_ARTIFACT_SHA256
    )
    schema_version: str = AIHUB538_PHOTOMETRIC_PROTOCOL_V4_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != AIHUB538_PHOTOMETRIC_PROTOCOL_V4_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "AI Hub 538 photometric v4 protocol schema differs"
            )
        require_safe_identifier(self.protocol_revision, "protocol_revision")
        require_sha256(
            self.video_qc_measurement_policy_sha256,
            "video_qc_measurement_policy_sha256",
        )
        require_safe_identifier(
            self.video_qc_measurement_revision,
            "video_qc_measurement_revision",
        )
        require_sha256(
            self.video_qc_measurement_adapter_code_sha256,
            "video_qc_measurement_adapter_code_sha256",
        )
        if type(self.hard_safety_envelope) is not Aihub538HardSafetyEnvelopeV4:
            raise VisualFeedbackContractError("hard safety envelope type differs")
        if (
            require_sha256(
                self.prior_v3_failure_artifact_sha256,
                "prior_v3_failure_artifact_sha256",
            )
            != AIHUB538_V3_FAILURE_ARTIFACT_SHA256
        ):
            raise VisualFeedbackContractError("prior v3 evidence identity differs")
        for name in (
            "minimum_development_actor_count",
            "minimum_calibration_actor_count",
        ):
            require_integer(getattr(self, name), name, minimum=2)
        require_integer(
            self.minimum_attempts_per_actor,
            "minimum_attempts_per_actor",
            minimum=1,
        )
        quantile = _fraction(
            self.soft_score_upper_quantile, "soft_score_upper_quantile"
        )
        if not 0.5 <= quantile < 1.0:
            raise VisualFeedbackContractError(
                "soft score upper quantile is outside policy"
            )
        for name in (
            "soft_score_margin",
            "soft_metric_contribution_cap",
        ):
            if require_finite_number(getattr(self, name), name) <= 0.0:
                raise VisualFeedbackContractError(f"{name} must be positive")
        full_weight = _fraction(
            self.full_frame_group_weight, "full_frame_group_weight"
        )
        lip_weight = _fraction(self.lip_roi_group_weight, "lip_roi_group_weight")
        if not math.isclose(full_weight + lip_weight, 1.0, abs_tol=1e-12):
            raise VisualFeedbackContractError(
                "photometric group weights must sum to one"
            )
        if _fraction(
            self.minimum_actor_attempt_pass_fraction,
            "minimum_actor_attempt_pass_fraction",
        ) <= 0.0:
            raise VisualFeedbackContractError(
                "minimum actor attempt pass fraction must be positive"
            )
        confidence = _fraction(
            self.actor_ci_confidence_level, "actor_ci_confidence_level"
        )
        if not math.isclose(
            confidence, 0.95, rel_tol=0.0, abs_tol=1e-15
        ):
            raise VisualFeedbackContractError(
                "actor CI confidence level differs"
            )
        if not math.isclose(
            require_finite_number(self.actor_ci_z_score, "actor_ci_z_score"),
            1.6448536269514722,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise VisualFeedbackContractError("actor CI z score differs")
        if _fraction(
            self.minimum_actor_ci_lower_bound,
            "minimum_actor_ci_lower_bound",
        ) <= 0.0:
            raise VisualFeedbackContractError(
                "minimum actor CI lower bound must be positive"
            )
        frozen_envelope = {
            "minimum_single_face_coverage": 0.75,
            "maximum_multiple_face_frame_fraction": 0.125,
            "maximum_catastrophic_underexposed_pixel_fraction": 0.90,
            "maximum_catastrophic_overexposed_pixel_fraction": 0.90,
            "minimum_lip_roi_visibility_fraction": 0.75,
            "minimum_track_transition_coverage": 0.70,
            "minimum_longest_single_face_run_fraction": 0.70,
            "maximum_lip_center_step_normalized": 0.20,
        }
        frozen_protocol = {
            "minimum_development_actor_count": 4,
            "minimum_calibration_actor_count": 4,
            "minimum_attempts_per_actor": 4,
            "soft_score_upper_quantile": 0.90,
            "soft_score_margin": 0.25,
            "soft_metric_contribution_cap": 6.0,
            "full_frame_group_weight": 0.50,
            "lip_roi_group_weight": 0.50,
            "minimum_actor_attempt_pass_fraction": 0.75,
            "actor_ci_confidence_level": 0.95,
            "actor_ci_z_score": 1.6448536269514722,
            "minimum_actor_ci_lower_bound": 0.50,
        }
        if any(
            getattr(self.hard_safety_envelope, name) != value
            for name, value in frozen_envelope.items()
        ) or any(getattr(self, name) != value for name, value in frozen_protocol.items()):
            raise VisualFeedbackContractError(
                "AI Hub 538 photometric v4 frozen protocol differs"
            )
        if AIHUB538_PHOTOMETRIC_V4_FROZEN_SAFETY_AND_STATISTICS_SHA256 != sha256_document(
            AIHUB538_PHOTOMETRIC_V4_FROZEN_SAFETY_AND_STATISTICS
        ):
            raise VisualFeedbackContractError("AI Hub 538 photometric v4 frozen constants digest differs")

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "protocol_revision": self.protocol_revision,
            "source_dataset_id": "aihub_538",
            "prior_v3_failure_artifact_sha256": (
                self.prior_v3_failure_artifact_sha256
            ),
            "prior_v3_cohort_role": AIHUB538_V3_COHORT_ROLE,
            "prior_v3_measurements_used_for_policy": False,
            "prior_v3_measurements_used_for_evaluation": False,
            "fresh_development_policy_derivation_only": True,
            "fresh_calibration_policy_derivation_allowed": False,
            "actor_disjoint_required": True,
            "attempt_disjoint_required": True,
            "video_qc_measurement_policy_sha256": (
                self.video_qc_measurement_policy_sha256
            ),
            "video_qc_measurement_revision": (
                self.video_qc_measurement_revision
            ),
            "video_qc_measurement_adapter_code_sha256": (
                self.video_qc_measurement_adapter_code_sha256
            ),
            "colorimetry_conversion_contract_sha256": (
                FFMPEG_RGB_COLORIMETRY_CONVERSION_CONTRACT_SHA256
            ),
            "hard_safety_envelope": self.hard_safety_envelope.to_json_object(),
            "minimum_development_actor_count": (
                self.minimum_development_actor_count
            ),
            "minimum_calibration_actor_count": self.minimum_calibration_actor_count,
            "minimum_attempts_per_actor": self.minimum_attempts_per_actor,
            "soft_metric_specification": [
                dict(item) for item in AIHUB538_PHOTOMETRIC_SOFT_METRIC_SPECS
            ],
            "soft_normalization_method": "median_scaled_mad_v1",
            "mad_consistency_factor": 1.4826,
            "quantile_method": "linear_type7",
            "joint_score_group_reduction": "mean_then_weighted_sum_v1",
            "soft_score_upper_quantile": self.soft_score_upper_quantile,
            "soft_score_margin": self.soft_score_margin,
            "soft_metric_contribution_cap": self.soft_metric_contribution_cap,
            "full_frame_group_weight": self.full_frame_group_weight,
            "lip_roi_group_weight": self.lip_roi_group_weight,
            "minimum_actor_attempt_pass_fraction": (
                self.minimum_actor_attempt_pass_fraction
            ),
            "actor_ci_method": "wilson_actor_binary_one_sided_v1",
            "actor_ci_confidence_level": self.actor_ci_confidence_level,
            "actor_ci_z_score": self.actor_ci_z_score,
            "minimum_actor_ci_lower_bound": self.minimum_actor_ci_lower_bound,
            "post_observation_policy_edits_allowed": False,
            "promotion_authorized": False,
        }

    @property
    def protocol_sha256(self) -> str:
        return sha256_document(self.to_json_object())

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> Aihub538PhotometricCalibrationProtocolV4:
        document = require_mapping(value, "photometric_v4_protocol")
        expected = {
            "schema_version",
            "protocol_revision",
            "source_dataset_id",
            "prior_v3_failure_artifact_sha256",
            "prior_v3_cohort_role",
            "prior_v3_measurements_used_for_policy",
            "prior_v3_measurements_used_for_evaluation",
            "fresh_development_policy_derivation_only",
            "fresh_calibration_policy_derivation_allowed",
            "actor_disjoint_required",
            "attempt_disjoint_required",
            "video_qc_measurement_policy_sha256",
            "video_qc_measurement_revision",
            "video_qc_measurement_adapter_code_sha256",
            "colorimetry_conversion_contract_sha256",
            "hard_safety_envelope",
            "minimum_development_actor_count",
            "minimum_calibration_actor_count",
            "minimum_attempts_per_actor",
            "soft_metric_specification",
            "soft_normalization_method",
            "mad_consistency_factor",
            "quantile_method",
            "joint_score_group_reduction",
            "soft_score_upper_quantile",
            "soft_score_margin",
            "soft_metric_contribution_cap",
            "full_frame_group_weight",
            "lip_roi_group_weight",
            "minimum_actor_attempt_pass_fraction",
            "actor_ci_method",
            "actor_ci_confidence_level",
            "actor_ci_z_score",
            "minimum_actor_ci_lower_bound",
            "post_observation_policy_edits_allowed",
            "promotion_authorized",
        }
        require_exact_keys(document, expected, "photometric_v4_protocol")
        if (
            document["source_dataset_id"] != "aihub_538"
            or document["prior_v3_cohort_role"] != AIHUB538_V3_COHORT_ROLE
            or document["actor_ci_method"]
            != "wilson_actor_binary_one_sided_v1"
            or document["colorimetry_conversion_contract_sha256"]
            != FFMPEG_RGB_COLORIMETRY_CONVERSION_CONTRACT_SHA256
            or document["soft_metric_specification"]
            != [dict(item) for item in AIHUB538_PHOTOMETRIC_SOFT_METRIC_SPECS]
            or document["soft_normalization_method"]
            != "median_scaled_mad_v1"
            or require_finite_number(
                document["mad_consistency_factor"], "mad_consistency_factor"
            )
            != 1.4826
            or document["quantile_method"] != "linear_type7"
            or document["joint_score_group_reduction"]
            != "mean_then_weighted_sum_v1"
        ):
            raise VisualFeedbackContractError(
                "AI Hub 538 photometric v4 protocol semantics differ"
            )
        for name, expected_value in (
            ("prior_v3_measurements_used_for_policy", False),
            ("prior_v3_measurements_used_for_evaluation", False),
            ("fresh_development_policy_derivation_only", True),
            ("fresh_calibration_policy_derivation_allowed", False),
            ("actor_disjoint_required", True),
            ("attempt_disjoint_required", True),
            ("post_observation_policy_edits_allowed", False),
            ("promotion_authorized", False),
        ):
            if require_boolean(document[name], name) is not expected_value:
                raise VisualFeedbackContractError(
                    "AI Hub 538 photometric v4 protocol gate differs"
                )
        return cls(
            protocol_revision=require_safe_identifier(
                document["protocol_revision"], "protocol_revision"
            ),
            video_qc_measurement_policy_sha256=require_sha256(
                document["video_qc_measurement_policy_sha256"],
                "video_qc_measurement_policy_sha256",
            ),
            video_qc_measurement_revision=require_safe_identifier(
                document["video_qc_measurement_revision"],
                "video_qc_measurement_revision",
            ),
            video_qc_measurement_adapter_code_sha256=require_sha256(
                document["video_qc_measurement_adapter_code_sha256"],
                "video_qc_measurement_adapter_code_sha256",
            ),
            hard_safety_envelope=Aihub538HardSafetyEnvelopeV4.from_json_object(
                document["hard_safety_envelope"]
            ),
            minimum_development_actor_count=require_integer(
                document["minimum_development_actor_count"],
                "minimum_development_actor_count",
                minimum=2,
            ),
            minimum_calibration_actor_count=require_integer(
                document["minimum_calibration_actor_count"],
                "minimum_calibration_actor_count",
                minimum=2,
            ),
            minimum_attempts_per_actor=require_integer(
                document["minimum_attempts_per_actor"],
                "minimum_attempts_per_actor",
                minimum=1,
            ),
            soft_score_upper_quantile=require_finite_number(
                document["soft_score_upper_quantile"],
                "soft_score_upper_quantile",
            ),
            soft_score_margin=require_finite_number(
                document["soft_score_margin"], "soft_score_margin"
            ),
            soft_metric_contribution_cap=require_finite_number(
                document["soft_metric_contribution_cap"],
                "soft_metric_contribution_cap",
            ),
            full_frame_group_weight=require_finite_number(
                document["full_frame_group_weight"],
                "full_frame_group_weight",
            ),
            lip_roi_group_weight=require_finite_number(
                document["lip_roi_group_weight"], "lip_roi_group_weight"
            ),
            minimum_actor_attempt_pass_fraction=require_finite_number(
                document["minimum_actor_attempt_pass_fraction"],
                "minimum_actor_attempt_pass_fraction",
            ),
            actor_ci_confidence_level=require_finite_number(
                document["actor_ci_confidence_level"],
                "actor_ci_confidence_level",
            ),
            actor_ci_z_score=require_finite_number(
                document["actor_ci_z_score"], "actor_ci_z_score"
            ),
            minimum_actor_ci_lower_bound=require_finite_number(
                document["minimum_actor_ci_lower_bound"],
                "minimum_actor_ci_lower_bound",
            ),
            prior_v3_failure_artifact_sha256=require_sha256(
                document["prior_v3_failure_artifact_sha256"],
                "prior_v3_failure_artifact_sha256",
            ),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
        )


AIHUB538_PHOTOMETRIC_SOFT_METRIC_SPECS = (
    {
        "metric_name": "full_frame_mean_luma_8bit",
        "group": "full_frame",
        "direction": "two_sided",
        "scale_floor": 1.0,
    },
    {
        "metric_name": "full_frame_underexposed_pixel_fraction",
        "group": "full_frame",
        "direction": "upper_only",
        "scale_floor": 0.01,
    },
    {
        "metric_name": "full_frame_overexposed_pixel_fraction",
        "group": "full_frame",
        "direction": "upper_only",
        "scale_floor": 0.01,
    },
    {
        "metric_name": "lip_roi_visibility_fraction",
        "group": "lip_roi",
        "direction": "lower_only",
        "scale_floor": 0.01,
    },
    {
        "metric_name": "lip_roi_mean_luma_8bit",
        "group": "lip_roi",
        "direction": "two_sided",
        "scale_floor": 1.0,
    },
    {
        "metric_name": "lip_roi_underexposed_pixel_fraction",
        "group": "lip_roi",
        "direction": "upper_only",
        "scale_floor": 0.01,
    },
    {
        "metric_name": "lip_roi_overexposed_pixel_fraction",
        "group": "lip_roi",
        "direction": "upper_only",
        "scale_floor": 0.01,
    },
)
_SOFT_METRIC_SPEC_BY_NAME = {
    item["metric_name"]: item for item in AIHUB538_PHOTOMETRIC_SOFT_METRIC_SPECS
}


@dataclass(frozen=True, slots=True)
class Aihub538PhotometricMetricNormalizerV4:
    metric_name: str
    group: str
    direction: str
    development_median: float
    development_mad_scale: float

    def __post_init__(self) -> None:
        spec = _SOFT_METRIC_SPEC_BY_NAME.get(self.metric_name)
        if (
            spec is None
            or self.group != spec["group"]
            or self.direction != spec["direction"]
        ):
            raise VisualFeedbackContractError(
                "photometric metric normalizer specification differs"
            )
        median = require_finite_number(
            self.development_median, "development_median"
        )
        scale = require_finite_number(
            self.development_mad_scale, "development_mad_scale"
        )
        if scale < float(spec["scale_floor"]):
            raise VisualFeedbackContractError(
                "photometric metric normalizer scale is below floor"
            )
        if "luma_8bit" in self.metric_name and not 0.0 <= median <= 255.0:
            raise VisualFeedbackContractError(
                "photometric luma median is outside range"
            )
        if "fraction" in self.metric_name and not 0.0 <= median <= 1.0:
            raise VisualFeedbackContractError(
                "photometric fraction median is outside range"
            )

    def to_json_object(self) -> dict[str, object]:
        return {
            "metric_name": self.metric_name,
            "group": self.group,
            "direction": self.direction,
            "development_median": self.development_median,
            "development_mad_scale": self.development_mad_scale,
        }

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> Aihub538PhotometricMetricNormalizerV4:
        document = require_mapping(value, "photometric_metric_normalizer")
        require_exact_keys(
            document,
            {
                "metric_name",
                "group",
                "direction",
                "development_median",
                "development_mad_scale",
            },
            "photometric_metric_normalizer",
        )
        return cls(
            metric_name=require_safe_identifier(
                document["metric_name"], "metric_name"
            ),
            group=require_safe_identifier(document["group"], "group"),
            direction=require_safe_identifier(
                document["direction"], "direction"
            ),
            development_median=require_finite_number(
                document["development_median"], "development_median"
            ),
            development_mad_scale=require_finite_number(
                document["development_mad_scale"], "development_mad_scale"
            ),
        )


@dataclass(frozen=True, slots=True)
class Aihub538PhotometricPolicyV4:
    """Fresh-development-only soft policy plus the immutable hard envelope."""

    protocol_sha256: str
    colorimetry_receipt_sha256: str
    video_qc_measurement_policy_sha256: str
    video_qc_measurement_revision: str
    video_qc_measurement_adapter_code_sha256: str
    development_actor_set_commitment_sha256: str
    development_actor_count: int
    development_observation_count: int
    hard_safety_envelope: Aihub538HardSafetyEnvelopeV4
    metric_normalizers: tuple[Aihub538PhotometricMetricNormalizerV4, ...]
    soft_score_threshold: float
    soft_score_upper_quantile: float
    soft_score_margin: float
    soft_metric_contribution_cap: float
    full_frame_group_weight: float
    lip_roi_group_weight: float
    policy_sha256: str
    schema_version: str = AIHUB538_PHOTOMETRIC_POLICY_V4_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != AIHUB538_PHOTOMETRIC_POLICY_V4_SCHEMA_VERSION:
            raise VisualFeedbackContractError("photometric v4 policy schema differs")
        for name in (
            "protocol_sha256",
            "colorimetry_receipt_sha256",
            "video_qc_measurement_policy_sha256",
            "video_qc_measurement_adapter_code_sha256",
            "development_actor_set_commitment_sha256",
            "policy_sha256",
        ):
            require_sha256(getattr(self, name), name)
        require_safe_identifier(
            self.video_qc_measurement_revision,
            "video_qc_measurement_revision",
        )
        require_integer(
            self.development_actor_count,
            "development_actor_count",
            minimum=2,
        )
        require_integer(
            self.development_observation_count,
            "development_observation_count",
            minimum=self.development_actor_count,
        )
        if type(self.hard_safety_envelope) is not Aihub538HardSafetyEnvelopeV4:
            raise VisualFeedbackContractError("photometric hard envelope differs")
        expected_names = tuple(
            item["metric_name"] for item in AIHUB538_PHOTOMETRIC_SOFT_METRIC_SPECS
        )
        if (
            tuple(item.metric_name for item in self.metric_normalizers)
            != expected_names
            or any(
                type(item) is not Aihub538PhotometricMetricNormalizerV4
                for item in self.metric_normalizers
            )
        ):
            raise VisualFeedbackContractError(
                "photometric metric normalizer coverage differs"
            )
        if require_finite_number(
            self.soft_score_threshold, "soft_score_threshold"
        ) < 0.0:
            raise VisualFeedbackContractError(
                "soft score threshold must be non-negative"
            )
        _fraction(self.soft_score_upper_quantile, "soft_score_upper_quantile")
        for name in ("soft_score_margin", "soft_metric_contribution_cap"):
            if require_finite_number(getattr(self, name), name) <= 0.0:
                raise VisualFeedbackContractError(f"{name} must be positive")
        if not math.isclose(
            _fraction(self.full_frame_group_weight, "full_frame_group_weight")
            + _fraction(self.lip_roi_group_weight, "lip_roi_group_weight"),
            1.0,
            abs_tol=1e-12,
        ):
            raise VisualFeedbackContractError(
                "photometric policy group weights differ"
            )
        if self.policy_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError("photometric v4 policy digest differs")

    @classmethod
    def issue(
        cls,
        *,
        protocol_sha256: str,
        colorimetry_receipt_sha256: str,
        video_qc_measurement_policy_sha256: str,
        video_qc_measurement_revision: str,
        video_qc_measurement_adapter_code_sha256: str,
        development_actor_set_commitment_sha256: str,
        development_actor_count: int,
        development_observation_count: int,
        hard_safety_envelope: Aihub538HardSafetyEnvelopeV4,
        metric_normalizers: tuple[Aihub538PhotometricMetricNormalizerV4, ...],
        soft_score_threshold: float,
        soft_score_upper_quantile: float,
        soft_score_margin: float,
        soft_metric_contribution_cap: float,
        full_frame_group_weight: float,
        lip_roi_group_weight: float,
    ) -> Aihub538PhotometricPolicyV4:
        values = {
            "protocol_sha256": protocol_sha256,
            "colorimetry_receipt_sha256": colorimetry_receipt_sha256,
            "video_qc_measurement_policy_sha256": (
                video_qc_measurement_policy_sha256
            ),
            "video_qc_measurement_revision": video_qc_measurement_revision,
            "video_qc_measurement_adapter_code_sha256": (
                video_qc_measurement_adapter_code_sha256
            ),
            "development_actor_set_commitment_sha256": (
                development_actor_set_commitment_sha256
            ),
            "development_actor_count": development_actor_count,
            "development_observation_count": development_observation_count,
            "hard_safety_envelope": hard_safety_envelope,
            "metric_normalizers": metric_normalizers,
            "soft_score_threshold": soft_score_threshold,
            "soft_score_upper_quantile": soft_score_upper_quantile,
            "soft_score_margin": soft_score_margin,
            "soft_metric_contribution_cap": soft_metric_contribution_cap,
            "full_frame_group_weight": full_frame_group_weight,
            "lip_roi_group_weight": lip_roi_group_weight,
        }
        draft = cls._unsigned_document(**values)
        return cls(**values, policy_sha256=sha256_document(draft))

    @staticmethod
    def _unsigned_document(**values: object) -> dict[str, object]:
        hard = values["hard_safety_envelope"]
        normalizers = values["metric_normalizers"]
        assert isinstance(hard, Aihub538HardSafetyEnvelopeV4)
        assert isinstance(normalizers, tuple)
        return {
            "schema_version": AIHUB538_PHOTOMETRIC_POLICY_V4_SCHEMA_VERSION,
            "status": "derived_from_fresh_development_only",
            "protocol_sha256": values["protocol_sha256"],
            "colorimetry_receipt_sha256": values["colorimetry_receipt_sha256"],
            "video_qc_measurement_policy_sha256": values[
                "video_qc_measurement_policy_sha256"
            ],
            "video_qc_measurement_revision": values[
                "video_qc_measurement_revision"
            ],
            "video_qc_measurement_adapter_code_sha256": values[
                "video_qc_measurement_adapter_code_sha256"
            ],
            "development_actor_set_commitment_sha256": values[
                "development_actor_set_commitment_sha256"
            ],
            "development_actor_count": values["development_actor_count"],
            "development_observation_count": values[
                "development_observation_count"
            ],
            "hard_safety_envelope": hard.to_json_object(),
            "metric_normalizers": [item.to_json_object() for item in normalizers],
            "soft_score_threshold": values["soft_score_threshold"],
            "soft_score_upper_quantile": values["soft_score_upper_quantile"],
            "soft_score_margin": values["soft_score_margin"],
            "soft_metric_contribution_cap": values[
                "soft_metric_contribution_cap"
            ],
            "full_frame_group_weight": values["full_frame_group_weight"],
            "lip_roi_group_weight": values["lip_roi_group_weight"],
            "prior_v3_measurements_used": False,
            "calibration_measurements_used_for_derivation": False,
            "raw_observations_serialized": False,
            "promotion_authorized": False,
        }

    def unsigned_json_object(self) -> dict[str, object]:
        return self._unsigned_document(
            protocol_sha256=self.protocol_sha256,
            colorimetry_receipt_sha256=self.colorimetry_receipt_sha256,
            video_qc_measurement_policy_sha256=(
                self.video_qc_measurement_policy_sha256
            ),
            video_qc_measurement_revision=self.video_qc_measurement_revision,
            video_qc_measurement_adapter_code_sha256=(
                self.video_qc_measurement_adapter_code_sha256
            ),
            development_actor_set_commitment_sha256=(
                self.development_actor_set_commitment_sha256
            ),
            development_actor_count=self.development_actor_count,
            development_observation_count=self.development_observation_count,
            hard_safety_envelope=self.hard_safety_envelope,
            metric_normalizers=self.metric_normalizers,
            soft_score_threshold=self.soft_score_threshold,
            soft_score_upper_quantile=self.soft_score_upper_quantile,
            soft_score_margin=self.soft_score_margin,
            soft_metric_contribution_cap=self.soft_metric_contribution_cap,
            full_frame_group_weight=self.full_frame_group_weight,
            lip_roi_group_weight=self.lip_roi_group_weight,
        )

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "policy_sha256": self.policy_sha256}

    @classmethod
    def from_json_object(cls, value: object) -> Aihub538PhotometricPolicyV4:
        document = require_mapping(value, "photometric_v4_policy")
        expected = {
            "schema_version",
            "status",
            "protocol_sha256",
            "colorimetry_receipt_sha256",
            "video_qc_measurement_policy_sha256",
            "video_qc_measurement_revision",
            "video_qc_measurement_adapter_code_sha256",
            "development_actor_set_commitment_sha256",
            "development_actor_count",
            "development_observation_count",
            "hard_safety_envelope",
            "metric_normalizers",
            "soft_score_threshold",
            "soft_score_upper_quantile",
            "soft_score_margin",
            "soft_metric_contribution_cap",
            "full_frame_group_weight",
            "lip_roi_group_weight",
            "prior_v3_measurements_used",
            "calibration_measurements_used_for_derivation",
            "raw_observations_serialized",
            "promotion_authorized",
            "policy_sha256",
        }
        require_exact_keys(document, expected, "photometric_v4_policy")
        if document["status"] != "derived_from_fresh_development_only":
            raise VisualFeedbackContractError("photometric v4 policy status differs")
        for name in (
            "prior_v3_measurements_used",
            "calibration_measurements_used_for_derivation",
            "raw_observations_serialized",
            "promotion_authorized",
        ):
            if require_boolean(document[name], name) is not False:
                raise VisualFeedbackContractError(
                    "photometric v4 policy provenance differs"
                )
        normalizer_values = document["metric_normalizers"]
        if not isinstance(normalizer_values, list):
            raise VisualFeedbackContractError(
                "metric_normalizers must be an array"
            )
        return cls(
            protocol_sha256=require_sha256(
                document["protocol_sha256"], "protocol_sha256"
            ),
            colorimetry_receipt_sha256=require_sha256(
                document["colorimetry_receipt_sha256"],
                "colorimetry_receipt_sha256",
            ),
            video_qc_measurement_policy_sha256=require_sha256(
                document["video_qc_measurement_policy_sha256"],
                "video_qc_measurement_policy_sha256",
            ),
            video_qc_measurement_revision=require_safe_identifier(
                document["video_qc_measurement_revision"],
                "video_qc_measurement_revision",
            ),
            video_qc_measurement_adapter_code_sha256=require_sha256(
                document["video_qc_measurement_adapter_code_sha256"],
                "video_qc_measurement_adapter_code_sha256",
            ),
            development_actor_set_commitment_sha256=require_sha256(
                document["development_actor_set_commitment_sha256"],
                "development_actor_set_commitment_sha256",
            ),
            development_actor_count=require_integer(
                document["development_actor_count"],
                "development_actor_count",
                minimum=2,
            ),
            development_observation_count=require_integer(
                document["development_observation_count"],
                "development_observation_count",
                minimum=2,
            ),
            hard_safety_envelope=Aihub538HardSafetyEnvelopeV4.from_json_object(
                document["hard_safety_envelope"]
            ),
            metric_normalizers=tuple(
                Aihub538PhotometricMetricNormalizerV4.from_json_object(item)
                for item in normalizer_values
            ),
            soft_score_threshold=require_finite_number(
                document["soft_score_threshold"], "soft_score_threshold"
            ),
            soft_score_upper_quantile=require_finite_number(
                document["soft_score_upper_quantile"],
                "soft_score_upper_quantile",
            ),
            soft_score_margin=require_finite_number(
                document["soft_score_margin"], "soft_score_margin"
            ),
            soft_metric_contribution_cap=require_finite_number(
                document["soft_metric_contribution_cap"],
                "soft_metric_contribution_cap",
            ),
            full_frame_group_weight=require_finite_number(
                document["full_frame_group_weight"],
                "full_frame_group_weight",
            ),
            lip_roi_group_weight=require_finite_number(
                document["lip_roi_group_weight"], "lip_roi_group_weight"
            ),
            policy_sha256=require_sha256(
                document["policy_sha256"], "policy_sha256"
            ),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
        )


@dataclass(frozen=True, slots=True)
class Aihub538FullFrameExposureAggregateV4:
    measurement_count: int
    mean_luma_8bit: float | None
    mean_underexposed_pixel_fraction: float | None
    mean_overexposed_pixel_fraction: float | None

    def __post_init__(self) -> None:
        count = require_integer(self.measurement_count, "measurement_count", minimum=0)
        values = (
            self.mean_luma_8bit,
            self.mean_underexposed_pixel_fraction,
            self.mean_overexposed_pixel_fraction,
        )
        if count == 0:
            if any(value is not None for value in values):
                raise VisualFeedbackContractError(
                    "empty full-frame aggregate must not contain means"
                )
            return
        if any(value is None for value in values):
            raise VisualFeedbackContractError(
                "full-frame aggregate requires complete means"
            )
        assert self.mean_luma_8bit is not None
        if not 0.0 <= require_finite_number(
            self.mean_luma_8bit, "mean_luma_8bit"
        ) <= 255.0:
            raise VisualFeedbackContractError("full-frame luma mean differs")
        assert self.mean_underexposed_pixel_fraction is not None
        assert self.mean_overexposed_pixel_fraction is not None
        _fraction(
            self.mean_underexposed_pixel_fraction,
            "mean_underexposed_pixel_fraction",
        )
        _fraction(
            self.mean_overexposed_pixel_fraction,
            "mean_overexposed_pixel_fraction",
        )
        if (
            self.mean_luma_8bit != round(self.mean_luma_8bit)
            or self.mean_underexposed_pixel_fraction
            != round(self.mean_underexposed_pixel_fraction, 2)
            or self.mean_overexposed_pixel_fraction
            != round(self.mean_overexposed_pixel_fraction, 2)
        ):
            raise VisualFeedbackContractError("full-frame published means are not rounded")

    def to_json_object(self) -> dict[str, object]:
        return {
            "measurement_count": self.measurement_count,
            "mean_luma_8bit": self.mean_luma_8bit,
            "mean_underexposed_pixel_fraction": (
                self.mean_underexposed_pixel_fraction
            ),
            "mean_overexposed_pixel_fraction": (
                self.mean_overexposed_pixel_fraction
            ),
            "scope": "full_decoded_frame",
        }

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> Aihub538FullFrameExposureAggregateV4:
        document = require_mapping(value, "full_frame_exposure_aggregate")
        require_exact_keys(
            document,
            {
                "measurement_count",
                "mean_luma_8bit",
                "mean_underexposed_pixel_fraction",
                "mean_overexposed_pixel_fraction",
                "scope",
            },
            "full_frame_exposure_aggregate",
        )
        if document["scope"] != "full_decoded_frame":
            raise VisualFeedbackContractError("full-frame exposure scope differs")
        return cls(
            measurement_count=require_integer(
                document["measurement_count"], "measurement_count", minimum=0
            ),
            mean_luma_8bit=_optional_number(
                document["mean_luma_8bit"], "mean_luma_8bit"
            ),
            mean_underexposed_pixel_fraction=_optional_number(
                document["mean_underexposed_pixel_fraction"],
                "mean_underexposed_pixel_fraction",
            ),
            mean_overexposed_pixel_fraction=_optional_number(
                document["mean_overexposed_pixel_fraction"],
                "mean_overexposed_pixel_fraction",
            ),
        )


@dataclass(frozen=True, slots=True)
class Aihub538LipRoiExposureVisibilityAggregateV4:
    visibility_measurement_count: int
    exposure_measurement_count: int
    mean_visibility_fraction: float | None
    mean_luma_8bit: float | None
    mean_underexposed_pixel_fraction: float | None
    mean_overexposed_pixel_fraction: float | None

    def __post_init__(self) -> None:
        visibility_count = require_integer(
            self.visibility_measurement_count,
            "visibility_measurement_count",
            minimum=0,
        )
        exposure_count = require_integer(
            self.exposure_measurement_count,
            "exposure_measurement_count",
            minimum=0,
        )
        if exposure_count > visibility_count:
            raise VisualFeedbackContractError(
                "lip ROI exposure count exceeds visibility count"
            )
        if visibility_count == 0:
            if self.mean_visibility_fraction is not None:
                raise VisualFeedbackContractError(
                    "empty lip ROI visibility aggregate contains a mean"
                )
        elif self.mean_visibility_fraction is None:
            raise VisualFeedbackContractError(
                "lip ROI visibility aggregate requires a mean"
            )
        else:
            _fraction(self.mean_visibility_fraction, "mean_visibility_fraction")
        exposure_values = (
            self.mean_luma_8bit,
            self.mean_underexposed_pixel_fraction,
            self.mean_overexposed_pixel_fraction,
        )
        if exposure_count == 0:
            if any(value is not None for value in exposure_values):
                raise VisualFeedbackContractError(
                    "empty lip ROI exposure aggregate contains means"
                )
            return
        if any(value is None for value in exposure_values):
            raise VisualFeedbackContractError(
                "lip ROI exposure aggregate requires complete means"
            )
        assert self.mean_luma_8bit is not None
        if not 0.0 <= require_finite_number(
            self.mean_luma_8bit, "mean_luma_8bit"
        ) <= 255.0:
            raise VisualFeedbackContractError("lip ROI luma mean differs")
        assert self.mean_underexposed_pixel_fraction is not None
        assert self.mean_overexposed_pixel_fraction is not None
        _fraction(
            self.mean_underexposed_pixel_fraction,
            "mean_underexposed_pixel_fraction",
        )
        _fraction(
            self.mean_overexposed_pixel_fraction,
            "mean_overexposed_pixel_fraction",
        )
        assert self.mean_visibility_fraction is not None
        if (
            self.mean_luma_8bit != round(self.mean_luma_8bit)
            or self.mean_visibility_fraction != round(self.mean_visibility_fraction, 2)
            or self.mean_underexposed_pixel_fraction
            != round(self.mean_underexposed_pixel_fraction, 2)
            or self.mean_overexposed_pixel_fraction
            != round(self.mean_overexposed_pixel_fraction, 2)
        ):
            raise VisualFeedbackContractError("lip ROI published means are not rounded")

    def to_json_object(self) -> dict[str, object]:
        return {
            "visibility_measurement_count": self.visibility_measurement_count,
            "exposure_measurement_count": self.exposure_measurement_count,
            "mean_visibility_fraction": self.mean_visibility_fraction,
            "mean_luma_8bit": self.mean_luma_8bit,
            "mean_underexposed_pixel_fraction": (
                self.mean_underexposed_pixel_fraction
            ),
            "mean_overexposed_pixel_fraction": (
                self.mean_overexposed_pixel_fraction
            ),
            "scope": "tracked_lip_roi_only",
        }

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> Aihub538LipRoiExposureVisibilityAggregateV4:
        document = require_mapping(value, "lip_roi_exposure_visibility_aggregate")
        require_exact_keys(
            document,
            {
                "visibility_measurement_count",
                "exposure_measurement_count",
                "mean_visibility_fraction",
                "mean_luma_8bit",
                "mean_underexposed_pixel_fraction",
                "mean_overexposed_pixel_fraction",
                "scope",
            },
            "lip_roi_exposure_visibility_aggregate",
        )
        if document["scope"] != "tracked_lip_roi_only":
            raise VisualFeedbackContractError("lip ROI exposure scope differs")
        return cls(
            visibility_measurement_count=require_integer(
                document["visibility_measurement_count"],
                "visibility_measurement_count",
                minimum=0,
            ),
            exposure_measurement_count=require_integer(
                document["exposure_measurement_count"],
                "exposure_measurement_count",
                minimum=0,
            ),
            mean_visibility_fraction=_optional_number(
                document["mean_visibility_fraction"],
                "mean_visibility_fraction",
            ),
            mean_luma_8bit=_optional_number(
                document["mean_luma_8bit"], "mean_luma_8bit"
            ),
            mean_underexposed_pixel_fraction=_optional_number(
                document["mean_underexposed_pixel_fraction"],
                "mean_underexposed_pixel_fraction",
            ),
            mean_overexposed_pixel_fraction=_optional_number(
                document["mean_overexposed_pixel_fraction"],
                "mean_overexposed_pixel_fraction",
            ),
        )


@dataclass(frozen=True, slots=True)
class Aihub538ActorOutcomeCellV4:
    attempt_count: int
    passed_attempt_count: int
    actor_count: int

    def __post_init__(self) -> None:
        require_integer(self.attempt_count, "attempt_count", minimum=4)
        require_integer(self.passed_attempt_count, "passed_attempt_count", minimum=0)
        require_integer(self.actor_count, "actor_count", minimum=4)
        if self.passed_attempt_count > self.attempt_count:
            raise VisualFeedbackContractError("actor outcome cell is impossible")

    def to_json_object(self) -> dict[str, int]:
        return {
            "attempt_count": self.attempt_count,
            "passed_attempt_count": self.passed_attempt_count,
            "actor_count": self.actor_count,
        }

    @classmethod
    def from_json_object(cls, value: object) -> Aihub538ActorOutcomeCellV4:
        document = require_mapping(value, "actor_outcome_cell")
        require_exact_keys(document, {"attempt_count", "passed_attempt_count", "actor_count"}, "actor_outcome_cell")
        return cls(
            attempt_count=require_integer(document["attempt_count"], "attempt_count", minimum=4),
            passed_attempt_count=require_integer(document["passed_attempt_count"], "passed_attempt_count", minimum=0),
            actor_count=require_integer(document["actor_count"], "actor_count", minimum=4),
        )


def _suppressed_actor_outcomes_feasible(
    actor_count: int, attempt_count: int, passed_attempt_count: int, passed_actor_count: int
) -> bool:
    if actor_count == 0:
        return attempt_count == passed_attempt_count == passed_actor_count == 0
    states={(0,0,0)}
    for index in range(actor_count):
        remaining=actor_count-index-1; next_states=set()
        for used_attempts,used_passes,used_passed_actors in states:
            max_attempts=attempt_count-used_attempts-4*remaining
            for attempts in range(4,max_attempts+1):
                threshold=math.ceil(0.75*attempts)
                for is_passed in (0,1):
                    low=threshold if is_passed else 0
                    high=attempts if is_passed else threshold-1
                    for passes in range(low,high+1):
                        candidate=(used_attempts+attempts,used_passes+passes,used_passed_actors+is_passed)
                        if candidate[0]<=attempt_count and candidate[1]<=passed_attempt_count and candidate[2]<=passed_actor_count:
                            next_states.add(candidate)
        states=next_states
    return (attempt_count,passed_attempt_count,passed_actor_count) in states


@dataclass(frozen=True, slots=True)
class Aihub538ActorOutcomeDistributionV4:
    actor_count: int
    attempt_count: int
    passed_attempt_count: int
    passed_actor_count: int
    published_cells: tuple[Aihub538ActorOutcomeCellV4, ...]
    suppressed_actor_count: int
    suppressed_attempt_count: int
    suppressed_passed_attempt_count: int
    suppressed_passed_actor_count: int

    def __post_init__(self) -> None:
        actor_count = require_integer(self.actor_count, "actor_count", minimum=4)
        attempt_count = require_integer(self.attempt_count, "attempt_count", minimum=16)
        passed_attempt_count = require_integer(self.passed_attempt_count, "passed_attempt_count", minimum=0)
        passed_actor_count = require_integer(self.passed_actor_count, "passed_actor_count", minimum=0)
        suppressed_actor_count = require_integer(self.suppressed_actor_count, "suppressed_actor_count", minimum=0)
        suppressed_attempt_count = require_integer(self.suppressed_attempt_count, "suppressed_attempt_count", minimum=0)
        suppressed_passed_attempt_count = require_integer(self.suppressed_passed_attempt_count, "suppressed_passed_attempt_count", minimum=0)
        suppressed_passed_actor_count = require_integer(self.suppressed_passed_actor_count, "suppressed_passed_actor_count", minimum=0)
        if any(type(item) is not Aihub538ActorOutcomeCellV4 for item in self.published_cells):
            raise VisualFeedbackContractError("actor outcome cell type differs")
        keys = [(item.attempt_count, item.passed_attempt_count) for item in self.published_cells]
        visible_actors = sum(item.actor_count for item in self.published_cells)
        visible_attempts = sum(item.actor_count * item.attempt_count for item in self.published_cells)
        visible_passed_attempts = sum(item.actor_count * item.passed_attempt_count for item in self.published_cells)
        threshold_passed_visible = sum(
            item.actor_count for item in self.published_cells
            if item.passed_attempt_count / item.attempt_count >= 0.75
        )
        if (
            keys != sorted(set(keys))
            or actor_count != visible_actors + suppressed_actor_count
            or attempt_count != visible_attempts + suppressed_attempt_count
            or passed_attempt_count != visible_passed_attempts + suppressed_passed_attempt_count
            or passed_actor_count != threshold_passed_visible + suppressed_passed_actor_count
            or passed_attempt_count > attempt_count
            or passed_attempt_count < 3 * passed_actor_count
            or passed_actor_count > actor_count
            or suppressed_passed_attempt_count > suppressed_attempt_count
            or suppressed_passed_attempt_count < 3 * suppressed_passed_actor_count
            or suppressed_passed_actor_count > suppressed_actor_count
            or (suppressed_actor_count == 0) != (suppressed_attempt_count == 0)
            or suppressed_attempt_count < 4 * suppressed_actor_count
            or not _suppressed_actor_outcomes_feasible(
                suppressed_actor_count,
                suppressed_attempt_count,
                suppressed_passed_attempt_count,
                suppressed_passed_actor_count,
            )
        ):
            raise VisualFeedbackContractError("actor outcome distribution is impossible")

    def to_json_object(self) -> dict[str, object]:
        return {
            "actor_count": self.actor_count,
            "attempt_count": self.attempt_count,
            "passed_attempt_count": self.passed_attempt_count,
            "passed_actor_count": self.passed_actor_count,
            "published_cells": [item.to_json_object() for item in self.published_cells],
            "suppressed_actor_count": self.suppressed_actor_count,
            "suppressed_attempt_count": self.suppressed_attempt_count,
            "suppressed_passed_attempt_count": self.suppressed_passed_attempt_count,
            "suppressed_passed_actor_count": self.suppressed_passed_actor_count,
            "minimum_publishable_cell_actor_count": 4,
            "release_granularity": "one_partition_once",
        }

    @classmethod
    def from_json_object(cls, value: object) -> Aihub538ActorOutcomeDistributionV4:
        document = require_mapping(value, "actor_outcome_distribution")
        fields = set(cls.__dataclass_fields__) | {"minimum_publishable_cell_actor_count", "release_granularity"}
        require_exact_keys(document, fields, "actor_outcome_distribution")
        if document["minimum_publishable_cell_actor_count"] != 4 or document["release_granularity"] != "one_partition_once" or not isinstance(document["published_cells"], list):
            raise VisualFeedbackContractError("actor outcome privacy policy differs")
        return cls(
            **{name: require_integer(document[name], name, minimum=0) for name in cls.__dataclass_fields__ if name != "published_cells"},
            published_cells=tuple(Aihub538ActorOutcomeCellV4.from_json_object(item) for item in document["published_cells"]),
        )


@dataclass(frozen=True, slots=True)
class Aihub538PhotometricPartitionAggregateV4:
    partition_role: str
    selected_count: int
    actor_count: int
    colorimetry_verified_count: int
    hard_safety_available_count: int
    hard_safety_passed_count: int
    soft_score_evaluated_count: int
    soft_score_passed_count: int
    full_frame_exposure: Aihub538FullFrameExposureAggregateV4
    lip_roi_exposure_visibility: Aihub538LipRoiExposureVisibilityAggregateV4
    actor_outcome_distribution: Aihub538ActorOutcomeDistributionV4

    def __post_init__(self) -> None:
        if self.partition_role not in {"fresh_development", "fresh_calibration"}:
            raise VisualFeedbackContractError(
                "photometric partition role differs"
            )
        selected = require_integer(self.selected_count, "selected_count", minimum=1)
        actor_count = require_integer(self.actor_count, "actor_count", minimum=1)
        if actor_count > selected:
            raise VisualFeedbackContractError(
                "photometric actor count exceeds selected count"
            )
        for name in (
            "colorimetry_verified_count",
            "hard_safety_available_count",
            "hard_safety_passed_count",
            "soft_score_evaluated_count",
            "soft_score_passed_count",
        ):
            count = require_integer(getattr(self, name), name, minimum=0)
            if count > selected:
                raise VisualFeedbackContractError(
                    "photometric partition count exceeds selected count"
                )
        if (
            self.colorimetry_verified_count != selected
            or self.hard_safety_passed_count > self.hard_safety_available_count
            or self.soft_score_evaluated_count != self.hard_safety_passed_count
            or self.soft_score_passed_count > self.soft_score_evaluated_count
        ):
            raise VisualFeedbackContractError(
                "photometric hard/soft gate counts differ"
            )
        if (
            type(self.full_frame_exposure)
            is not Aihub538FullFrameExposureAggregateV4
            or type(self.lip_roi_exposure_visibility)
            is not Aihub538LipRoiExposureVisibilityAggregateV4
            or self.full_frame_exposure.measurement_count != selected
            or self.lip_roi_exposure_visibility.visibility_measurement_count
            != selected
        ):
            raise VisualFeedbackContractError(
                "photometric exposure aggregate counts differ"
            )
        if (
            type(self.actor_outcome_distribution) is not Aihub538ActorOutcomeDistributionV4
            or self.actor_outcome_distribution.actor_count != actor_count
            or self.actor_outcome_distribution.attempt_count != selected
            or self.actor_outcome_distribution.passed_attempt_count != self.soft_score_passed_count
        ):
            raise VisualFeedbackContractError("photometric actor distribution differs")

    def to_json_object(self) -> dict[str, object]:
        return {
            "partition_role": self.partition_role,
            "selected_count": self.selected_count,
            "actor_count": self.actor_count,
            "colorimetry_verified_count": self.colorimetry_verified_count,
            "hard_safety_available_count": self.hard_safety_available_count,
            "hard_safety_passed_count": self.hard_safety_passed_count,
            "soft_score_evaluated_count": self.soft_score_evaluated_count,
            "soft_score_passed_count": self.soft_score_passed_count,
            "full_frame_exposure": self.full_frame_exposure.to_json_object(),
            "lip_roi_exposure_visibility": (
                self.lip_roi_exposure_visibility.to_json_object()
            ),
            "actor_outcome_distribution": self.actor_outcome_distribution.to_json_object(),
            "sample_level_values_serialized": False,
        }

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> Aihub538PhotometricPartitionAggregateV4:
        document = require_mapping(value, "photometric_partition_aggregate")
        require_exact_keys(
            document,
            {
                "partition_role",
                "selected_count",
                "actor_count",
                "colorimetry_verified_count",
                "hard_safety_available_count",
                "hard_safety_passed_count",
                "soft_score_evaluated_count",
                "soft_score_passed_count",
                "full_frame_exposure",
                "lip_roi_exposure_visibility",
                "actor_outcome_distribution",
                "sample_level_values_serialized",
            },
            "photometric_partition_aggregate",
        )
        if require_boolean(
            document["sample_level_values_serialized"],
            "sample_level_values_serialized",
        ) is not False:
            raise VisualFeedbackContractError(
                "photometric aggregate privacy boundary differs"
            )
        return cls(
            partition_role=require_safe_identifier(
                document["partition_role"], "partition_role"
            ),
            selected_count=require_integer(
                document["selected_count"], "selected_count", minimum=1
            ),
            actor_count=require_integer(
                document["actor_count"], "actor_count", minimum=1
            ),
            colorimetry_verified_count=require_integer(
                document["colorimetry_verified_count"],
                "colorimetry_verified_count",
                minimum=0,
            ),
            hard_safety_available_count=require_integer(
                document["hard_safety_available_count"],
                "hard_safety_available_count",
                minimum=0,
            ),
            hard_safety_passed_count=require_integer(
                document["hard_safety_passed_count"],
                "hard_safety_passed_count",
                minimum=0,
            ),
            soft_score_evaluated_count=require_integer(
                document["soft_score_evaluated_count"],
                "soft_score_evaluated_count",
                minimum=0,
            ),
            soft_score_passed_count=require_integer(
                document["soft_score_passed_count"],
                "soft_score_passed_count",
                minimum=0,
            ),
            full_frame_exposure=(
                Aihub538FullFrameExposureAggregateV4.from_json_object(
                    document["full_frame_exposure"]
                )
            ),
            lip_roi_exposure_visibility=(
                Aihub538LipRoiExposureVisibilityAggregateV4.from_json_object(
                    document["lip_roi_exposure_visibility"]
                )
            ),
            actor_outcome_distribution=Aihub538ActorOutcomeDistributionV4.from_json_object(
                document["actor_outcome_distribution"]
            ),
        )


@dataclass(frozen=True, slots=True)
class Aihub538ActorClusteredOneSidedCiV4:
    actor_count: int
    passed_actor_count: int
    minimum_actor_attempt_pass_fraction: float
    confidence_level: float
    z_score: float
    observed_actor_pass_fraction: float
    lower_bound: float
    minimum_required_lower_bound: float
    passed: bool

    def __post_init__(self) -> None:
        actor_count = require_integer(self.actor_count, "actor_count", minimum=1)
        passed_count = require_integer(
            self.passed_actor_count, "passed_actor_count", minimum=0
        )
        if passed_count > actor_count:
            raise VisualFeedbackContractError(
                "passed actor count exceeds actor count"
            )
        _fraction(
            self.minimum_actor_attempt_pass_fraction,
            "minimum_actor_attempt_pass_fraction",
        )
        if not math.isclose(
            _fraction(self.confidence_level, "confidence_level"),
            0.95,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise VisualFeedbackContractError("actor CI confidence level differs")
        z_score = require_finite_number(self.z_score, "z_score")
        if not math.isclose(
            z_score, 1.6448536269514722, rel_tol=0.0, abs_tol=1e-15
        ):
            raise VisualFeedbackContractError("actor CI z score differs")
        observed = passed_count / actor_count
        if not math.isclose(
            self.observed_actor_pass_fraction,
            observed,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise VisualFeedbackContractError(
                "actor CI observed fraction differs"
            )
        denominator = 1.0 + z_score * z_score / actor_count
        center = observed + z_score * z_score / (2.0 * actor_count)
        radius = z_score * math.sqrt(
            observed * (1.0 - observed) / actor_count
            + z_score * z_score / (4.0 * actor_count * actor_count)
        )
        expected_lower = max(0.0, (center - radius) / denominator)
        if not math.isclose(
            self.lower_bound,
            expected_lower,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise VisualFeedbackContractError("actor CI lower bound differs")
        minimum = _fraction(
            self.minimum_required_lower_bound,
            "minimum_required_lower_bound",
        )
        if self.passed is not (expected_lower >= minimum):
            raise VisualFeedbackContractError("actor CI decision differs")

    def to_json_object(self) -> dict[str, object]:
        return {
            "method": "wilson_actor_binary_one_sided_v1",
            "actor_count": self.actor_count,
            "passed_actor_count": self.passed_actor_count,
            "minimum_actor_attempt_pass_fraction": (
                self.minimum_actor_attempt_pass_fraction
            ),
            "confidence_level": self.confidence_level,
            "z_score": self.z_score,
            "observed_actor_pass_fraction": self.observed_actor_pass_fraction,
            "lower_bound": self.lower_bound,
            "minimum_required_lower_bound": self.minimum_required_lower_bound,
            "passed": self.passed,
            "cluster_unit": "actor",
            "attempts_treated_as_independent_clusters": False,
        }

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> Aihub538ActorClusteredOneSidedCiV4:
        document = require_mapping(value, "actor_clustered_one_sided_ci")
        require_exact_keys(
            document,
            {
                "method",
                "actor_count",
                "passed_actor_count",
                "minimum_actor_attempt_pass_fraction",
                "confidence_level",
                "z_score",
                "observed_actor_pass_fraction",
                "lower_bound",
                "minimum_required_lower_bound",
                "passed",
                "cluster_unit",
                "attempts_treated_as_independent_clusters",
            },
            "actor_clustered_one_sided_ci",
        )
        if (
            document["method"] != "wilson_actor_binary_one_sided_v1"
            or document["cluster_unit"] != "actor"
            or require_boolean(
                document["attempts_treated_as_independent_clusters"],
                "attempts_treated_as_independent_clusters",
            )
            is not False
        ):
            raise VisualFeedbackContractError("actor CI semantics differ")
        return cls(
            actor_count=require_integer(
                document["actor_count"], "actor_count", minimum=1
            ),
            passed_actor_count=require_integer(
                document["passed_actor_count"],
                "passed_actor_count",
                minimum=0,
            ),
            minimum_actor_attempt_pass_fraction=require_finite_number(
                document["minimum_actor_attempt_pass_fraction"],
                "minimum_actor_attempt_pass_fraction",
            ),
            confidence_level=require_finite_number(
                document["confidence_level"], "confidence_level"
            ),
            z_score=require_finite_number(document["z_score"], "z_score"),
            observed_actor_pass_fraction=require_finite_number(
                document["observed_actor_pass_fraction"],
                "observed_actor_pass_fraction",
            ),
            lower_bound=require_finite_number(
                document["lower_bound"], "lower_bound"
            ),
            minimum_required_lower_bound=require_finite_number(
                document["minimum_required_lower_bound"],
                "minimum_required_lower_bound",
            ),
            passed=require_boolean(document["passed"], "passed"),
        )


@dataclass(frozen=True, slots=True)
class Aihub538FreshCohortSeparationReceiptV4:
    identity_namespace_commitment_sha256: str
    prior_v3_actor_set_commitment_sha256: str
    prior_v3_attempt_set_commitment_sha256: str
    fresh_development_actor_set_commitment_sha256: str
    fresh_development_attempt_set_commitment_sha256: str
    fresh_calibration_actor_set_commitment_sha256: str
    fresh_calibration_attempt_set_commitment_sha256: str
    prior_v3_actor_count: int
    prior_v3_attempt_count: int
    development_actor_count: int
    development_attempt_count: int
    calibration_actor_count: int
    calibration_attempt_count: int
    receipt_sha256: str
    schema_version: str = AIHUB538_FRESH_COHORT_SEPARATION_V4_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != AIHUB538_FRESH_COHORT_SEPARATION_V4_SCHEMA_VERSION:
            raise VisualFeedbackContractError("fresh cohort separation schema differs")
        for name in (
            "identity_namespace_commitment_sha256",
            "prior_v3_actor_set_commitment_sha256",
            "prior_v3_attempt_set_commitment_sha256",
            "fresh_development_actor_set_commitment_sha256",
            "fresh_development_attempt_set_commitment_sha256",
            "fresh_calibration_actor_set_commitment_sha256",
            "fresh_calibration_attempt_set_commitment_sha256",
            "receipt_sha256",
        ):
            require_sha256(getattr(self, name), name)
        for name in (
            "prior_v3_actor_count",
            "prior_v3_attempt_count",
            "development_actor_count",
            "development_attempt_count",
            "calibration_actor_count",
            "calibration_attempt_count",
        ):
            require_integer(getattr(self, name), name, minimum=1)
        if (
            self.development_attempt_count < self.development_actor_count
            or self.calibration_attempt_count < self.calibration_actor_count
            or len(
                {
                    self.identity_namespace_commitment_sha256,
                    self.prior_v3_actor_set_commitment_sha256,
                    self.prior_v3_attempt_set_commitment_sha256,
                    self.fresh_development_actor_set_commitment_sha256,
                    self.fresh_development_attempt_set_commitment_sha256,
                    self.fresh_calibration_actor_set_commitment_sha256,
                    self.fresh_calibration_attempt_set_commitment_sha256,
                }
            )
            != 7
        ):
            raise VisualFeedbackContractError(
                "fresh cohort separation counts or commitments differ"
            )
        if self.receipt_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError(
                "fresh cohort separation receipt digest differs"
            )

    @classmethod
    def issue(cls, **values: object) -> Aihub538FreshCohortSeparationReceiptV4:
        unsigned = cls._unsigned_document(**values)
        return cls(**values, receipt_sha256=sha256_document(unsigned))

    @staticmethod
    def _unsigned_document(**values: object) -> dict[str, object]:
        return {
            "schema_version": AIHUB538_FRESH_COHORT_SEPARATION_V4_SCHEMA_VERSION,
            **values,
            "prior_v3_cohort_role": AIHUB538_V3_COHORT_ROLE,
            "development_calibration_actor_overlap_count": 0,
            "development_calibration_attempt_overlap_count": 0,
            "prior_v3_fresh_actor_overlap_count": 0,
            "prior_v3_fresh_attempt_overlap_count": 0,
            "raw_actor_tokens_serialized": False,
            "raw_attempt_tokens_serialized": False,
            "promotion_authorized": False,
        }

    def unsigned_json_object(self) -> dict[str, object]:
        values = {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
            if name not in {"receipt_sha256", "schema_version"}
        }
        return self._unsigned_document(**values)

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "receipt_sha256": self.receipt_sha256}

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> Aihub538FreshCohortSeparationReceiptV4:
        document = require_mapping(value, "fresh_cohort_separation_receipt")
        data_fields = {
            name
            for name in cls.__dataclass_fields__
            if name not in {"schema_version", "receipt_sha256"}
        }
        expected = data_fields | {
            "schema_version",
            "receipt_sha256",
            "prior_v3_cohort_role",
            "development_calibration_actor_overlap_count",
            "development_calibration_attempt_overlap_count",
            "prior_v3_fresh_actor_overlap_count",
            "prior_v3_fresh_attempt_overlap_count",
            "raw_actor_tokens_serialized",
            "raw_attempt_tokens_serialized",
            "promotion_authorized",
        }
        require_exact_keys(document, expected, "fresh_cohort_separation_receipt")
        if document["prior_v3_cohort_role"] != AIHUB538_V3_COHORT_ROLE:
            raise VisualFeedbackContractError("prior v3 cohort role differs")
        for name in (
            "development_calibration_actor_overlap_count",
            "development_calibration_attempt_overlap_count",
            "prior_v3_fresh_actor_overlap_count",
            "prior_v3_fresh_attempt_overlap_count",
        ):
            if require_integer(document[name], name, minimum=0) != 0:
                raise VisualFeedbackContractError("fresh cohort overlap is nonzero")
        for name in (
            "raw_actor_tokens_serialized",
            "raw_attempt_tokens_serialized",
            "promotion_authorized",
        ):
            if require_boolean(document[name], name) is not False:
                raise VisualFeedbackContractError(
                    "fresh cohort privacy/promotion boundary differs"
                )
        values: dict[str, object] = {}
        for name in data_fields:
            if name.endswith("sha256"):
                values[name] = require_sha256(document[name], name)
            else:
                values[name] = require_integer(document[name], name, minimum=1)
        return cls(
            **values,
            receipt_sha256=require_sha256(
                document["receipt_sha256"], "receipt_sha256"
            ),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
        )


@dataclass(frozen=True, slots=True)
class Aihub538PhotometricCalibrationEvidenceV4:
    protocol: Aihub538PhotometricCalibrationProtocolV4
    colorimetry_receipt: FfmpegRgbColorimetryReceipt
    policy: Aihub538PhotometricPolicyV4
    cohort_separation_receipt: Aihub538FreshCohortSeparationReceiptV4
    source_manifest_sha256: str
    admission_receipt_sha256: str
    development_aggregate: Aihub538PhotometricPartitionAggregateV4
    calibration_aggregate: Aihub538PhotometricPartitionAggregateV4
    actor_clustered_one_sided_ci: Aihub538ActorClusteredOneSidedCiV4
    production_execution_policy_sha256: str
    ordered_sampling_receipt_commitment_sha256: str
    pair_count: int
    remediation_receipt_sha256: str
    ordered_pair_lineage_commitment_sha256: str
    selected_member_commitment_sha256: str
    selected_member_count: int
    ordered_sampling_receipt_count: int
    artifact_sha256: str
    schema_version: str = AIHUB538_PHOTOMETRIC_EVIDENCE_V4_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != AIHUB538_PHOTOMETRIC_EVIDENCE_V4_SCHEMA_VERSION:
            raise VisualFeedbackContractError(
                "photometric v4 evidence schema differs"
            )
        if (
            type(self.protocol) is not Aihub538PhotometricCalibrationProtocolV4
            or type(self.colorimetry_receipt) is not FfmpegRgbColorimetryReceipt
            or type(self.policy) is not Aihub538PhotometricPolicyV4
            or type(self.cohort_separation_receipt)
            is not Aihub538FreshCohortSeparationReceiptV4
            or type(self.development_aggregate)
            is not Aihub538PhotometricPartitionAggregateV4
            or type(self.calibration_aggregate)
            is not Aihub538PhotometricPartitionAggregateV4
            or type(self.actor_clustered_one_sided_ci)
            is not Aihub538ActorClusteredOneSidedCiV4
        ):
            raise VisualFeedbackContractError(
                "photometric v4 evidence nested type differs"
            )
        for name in (
            "source_manifest_sha256",
            "admission_receipt_sha256",
            "production_execution_policy_sha256",
            "ordered_sampling_receipt_commitment_sha256",
            "remediation_receipt_sha256","ordered_pair_lineage_commitment_sha256","selected_member_commitment_sha256",
            "artifact_sha256",
        ):
            require_sha256(getattr(self, name), name)
        if (
            self.policy.protocol_sha256 != self.protocol.protocol_sha256
            or self.policy.colorimetry_receipt_sha256
            != self.colorimetry_receipt.receipt_sha256
            or self.policy.video_qc_measurement_policy_sha256
            != self.protocol.video_qc_measurement_policy_sha256
            or self.policy.video_qc_measurement_revision
            != self.protocol.video_qc_measurement_revision
            or self.policy.video_qc_measurement_adapter_code_sha256
            != self.protocol.video_qc_measurement_adapter_code_sha256
            or self.policy.hard_safety_envelope
            != self.protocol.hard_safety_envelope
            or self.policy.soft_score_upper_quantile
            != self.protocol.soft_score_upper_quantile
            or self.policy.soft_score_margin != self.protocol.soft_score_margin
            or self.policy.soft_metric_contribution_cap
            != self.protocol.soft_metric_contribution_cap
            or self.policy.full_frame_group_weight
            != self.protocol.full_frame_group_weight
            or self.policy.lip_roi_group_weight
            != self.protocol.lip_roi_group_weight
            or self.policy.development_actor_set_commitment_sha256
            != self.cohort_separation_receipt.fresh_development_actor_set_commitment_sha256
            or self.development_aggregate.partition_role != "fresh_development"
            or self.calibration_aggregate.partition_role != "fresh_calibration"
            or self.development_aggregate.actor_count
            != self.cohort_separation_receipt.development_actor_count
            or self.calibration_aggregate.actor_count
            != self.cohort_separation_receipt.calibration_actor_count
            or self.development_aggregate.selected_count
            != self.cohort_separation_receipt.development_attempt_count
            or self.calibration_aggregate.selected_count
            != self.cohort_separation_receipt.calibration_attempt_count
            or self.policy.development_actor_count
            != self.development_aggregate.actor_count
            or self.policy.development_observation_count
            != self.development_aggregate.hard_safety_passed_count
            or self.development_aggregate.actor_count
            < self.protocol.minimum_development_actor_count
            or self.calibration_aggregate.actor_count
            < self.protocol.minimum_calibration_actor_count
            or self.actor_clustered_one_sided_ci.actor_count
            != self.calibration_aggregate.actor_count
            or self.actor_clustered_one_sided_ci.passed_actor_count
            != self.calibration_aggregate.actor_outcome_distribution.passed_actor_count
            or self.actor_clustered_one_sided_ci.minimum_actor_attempt_pass_fraction
            != self.protocol.minimum_actor_attempt_pass_fraction
            or self.actor_clustered_one_sided_ci.confidence_level
            != self.protocol.actor_ci_confidence_level
            or self.actor_clustered_one_sided_ci.z_score
            != self.protocol.actor_ci_z_score
            or self.actor_clustered_one_sided_ci.minimum_required_lower_bound
            != self.protocol.minimum_actor_ci_lower_bound
        ):
            raise VisualFeedbackContractError(
                "photometric v4 evidence lineage differs"
            )
        if self.artifact_sha256 != sha256_document(self.unsigned_json_object()):
            raise VisualFeedbackContractError(
                "photometric v4 evidence digest differs"
            )
        if self.production_execution_policy_sha256 == "e8da8270b167610e3ba70b04e02fedba59b03f5800179dd9b77582b7a6a83fd4" and (self.pair_count!=3 or self.selected_member_count!=144 or self.ordered_sampling_receipt_count!=144):
            raise VisualFeedbackContractError("photometric v4 production run identity differs")

    @classmethod
    def issue(
        cls,
        *,
        protocol: Aihub538PhotometricCalibrationProtocolV4,
        colorimetry_receipt: FfmpegRgbColorimetryReceipt,
        policy: Aihub538PhotometricPolicyV4,
        cohort_separation_receipt: Aihub538FreshCohortSeparationReceiptV4,
        source_manifest_sha256: str,
        admission_receipt_sha256: str,
        development_aggregate: Aihub538PhotometricPartitionAggregateV4,
        calibration_aggregate: Aihub538PhotometricPartitionAggregateV4,
        actor_clustered_one_sided_ci: Aihub538ActorClusteredOneSidedCiV4,
        production_execution_policy_sha256: str,
        ordered_sampling_receipt_commitment_sha256: str,
        pair_count:int,remediation_receipt_sha256:str,ordered_pair_lineage_commitment_sha256:str,selected_member_commitment_sha256:str,selected_member_count:int,ordered_sampling_receipt_count:int,
    ) -> Aihub538PhotometricCalibrationEvidenceV4:
        values = {
            "protocol": protocol,
            "colorimetry_receipt": colorimetry_receipt,
            "policy": policy,
            "cohort_separation_receipt": cohort_separation_receipt,
            "source_manifest_sha256": source_manifest_sha256,
            "admission_receipt_sha256": admission_receipt_sha256,
            "development_aggregate": development_aggregate,
            "calibration_aggregate": calibration_aggregate,
            "actor_clustered_one_sided_ci": actor_clustered_one_sided_ci,
            "production_execution_policy_sha256": production_execution_policy_sha256,
            "ordered_sampling_receipt_commitment_sha256": ordered_sampling_receipt_commitment_sha256,
            "pair_count":pair_count,"remediation_receipt_sha256":remediation_receipt_sha256,"ordered_pair_lineage_commitment_sha256":ordered_pair_lineage_commitment_sha256,"selected_member_commitment_sha256":selected_member_commitment_sha256,"selected_member_count":selected_member_count,"ordered_sampling_receipt_count":ordered_sampling_receipt_count,
        }
        return cls(
            **values,
            artifact_sha256=sha256_document(cls._unsigned_document(**values)),
        )

    @staticmethod
    def _unsigned_document(**values: object) -> dict[str, object]:
        protocol = values["protocol"]
        colorimetry = values["colorimetry_receipt"]
        policy = values["policy"]
        separation = values["cohort_separation_receipt"]
        development = values["development_aggregate"]
        calibration = values["calibration_aggregate"]
        ci = values["actor_clustered_one_sided_ci"]
        assert isinstance(protocol, Aihub538PhotometricCalibrationProtocolV4)
        assert isinstance(colorimetry, FfmpegRgbColorimetryReceipt)
        assert isinstance(policy, Aihub538PhotometricPolicyV4)
        assert isinstance(separation, Aihub538FreshCohortSeparationReceiptV4)
        assert isinstance(development, Aihub538PhotometricPartitionAggregateV4)
        assert isinstance(calibration, Aihub538PhotometricPartitionAggregateV4)
        assert isinstance(ci, Aihub538ActorClusteredOneSidedCiV4)
        return {
            "schema_version": AIHUB538_PHOTOMETRIC_EVIDENCE_V4_SCHEMA_VERSION,
            "status": (
                "evaluated_non_promotional" if ci.passed else "failed_closed"
            ),
            "source_dataset_id": "aihub_538",
            "protocol": protocol.to_json_object(),
            "colorimetry_receipt": colorimetry.to_json_object(),
            "policy": policy.to_json_object(),
            "cohort_separation_receipt": separation.to_json_object(),
            "source_manifest_sha256": values["source_manifest_sha256"],
            "admission_receipt_sha256": values["admission_receipt_sha256"],
            "development_aggregate": development.to_json_object(),
            "calibration_aggregate": calibration.to_json_object(),
            "actor_clustered_one_sided_ci": ci.to_json_object(),
            "production_execution_policy_sha256": values["production_execution_policy_sha256"],
            "ordered_sampling_receipt_commitment_sha256": values["ordered_sampling_receipt_commitment_sha256"],
            "pair_count":values["pair_count"],"remediation_receipt_sha256":values["remediation_receipt_sha256"],"ordered_pair_lineage_commitment_sha256":values["ordered_pair_lineage_commitment_sha256"],"selected_member_commitment_sha256":values["selected_member_commitment_sha256"],"selected_member_count":values["selected_member_count"],"ordered_sampling_receipt_count":values["ordered_sampling_receipt_count"],
            "aggregate_only": True,
            "prior_v3_cohort_role": AIHUB538_V3_COHORT_ROLE,
            "prior_v3_measurements_used": False,
            "fresh_process_reload_required": True,
            "fresh_process_reload_completed": False,
            "runtime_policy_replaced": False,
            "runtime_publisher_invoked": False,
            "promotion_authorized": False,
            "raw_identifiers_serialized": False,
            "raw_paths_serialized": False,
            "sample_level_values_serialized": False,
        }

    def unsigned_json_object(self) -> dict[str, object]:
        return self._unsigned_document(
            protocol=self.protocol,
            colorimetry_receipt=self.colorimetry_receipt,
            policy=self.policy,
            cohort_separation_receipt=self.cohort_separation_receipt,
            source_manifest_sha256=self.source_manifest_sha256,
            admission_receipt_sha256=self.admission_receipt_sha256,
            development_aggregate=self.development_aggregate,
            calibration_aggregate=self.calibration_aggregate,
            actor_clustered_one_sided_ci=self.actor_clustered_one_sided_ci,
            production_execution_policy_sha256=self.production_execution_policy_sha256,
            ordered_sampling_receipt_commitment_sha256=self.ordered_sampling_receipt_commitment_sha256,
            pair_count=self.pair_count,remediation_receipt_sha256=self.remediation_receipt_sha256,ordered_pair_lineage_commitment_sha256=self.ordered_pair_lineage_commitment_sha256,selected_member_commitment_sha256=self.selected_member_commitment_sha256,selected_member_count=self.selected_member_count,ordered_sampling_receipt_count=self.ordered_sampling_receipt_count,
        )

    def to_json_object(self) -> dict[str, object]:
        return {**self.unsigned_json_object(), "artifact_sha256": self.artifact_sha256}

    @classmethod
    def from_json_object(
        cls, value: object
    ) -> Aihub538PhotometricCalibrationEvidenceV4:
        document = require_mapping(value, "photometric_v4_evidence")
        expected = {
            "schema_version",
            "status",
            "source_dataset_id",
            "protocol",
            "colorimetry_receipt",
            "policy",
            "cohort_separation_receipt",
            "source_manifest_sha256",
            "admission_receipt_sha256",
            "development_aggregate",
            "calibration_aggregate",
            "actor_clustered_one_sided_ci",
            "production_execution_policy_sha256",
            "ordered_sampling_receipt_commitment_sha256",
            "pair_count","remediation_receipt_sha256","ordered_pair_lineage_commitment_sha256","selected_member_commitment_sha256","selected_member_count","ordered_sampling_receipt_count",
            "aggregate_only",
            "prior_v3_cohort_role",
            "prior_v3_measurements_used",
            "fresh_process_reload_required",
            "fresh_process_reload_completed",
            "runtime_policy_replaced",
            "runtime_publisher_invoked",
            "promotion_authorized",
            "raw_identifiers_serialized",
            "raw_paths_serialized",
            "sample_level_values_serialized",
            "artifact_sha256",
        }
        require_exact_keys(document, expected, "photometric_v4_evidence")
        if (
            document["status"]
            not in {"evaluated_non_promotional", "failed_closed"}
            or document["source_dataset_id"] != "aihub_538"
            or document["prior_v3_cohort_role"] != AIHUB538_V3_COHORT_ROLE
        ):
            raise VisualFeedbackContractError(
                "photometric v4 evidence semantics differ"
            )
        for name, expected_value in (
            ("aggregate_only", True),
            ("prior_v3_measurements_used", False),
            ("fresh_process_reload_required", True),
            ("fresh_process_reload_completed", False),
            ("runtime_policy_replaced", False),
            ("runtime_publisher_invoked", False),
            ("promotion_authorized", False),
            ("raw_identifiers_serialized", False),
            ("raw_paths_serialized", False),
            ("sample_level_values_serialized", False),
        ):
            if require_boolean(document[name], name) is not expected_value:
                raise VisualFeedbackContractError(
                    "photometric v4 evidence gate differs"
                )
        result = cls(
            protocol=Aihub538PhotometricCalibrationProtocolV4.from_json_object(
                document["protocol"]
            ),
            colorimetry_receipt=FfmpegRgbColorimetryReceipt.from_json_object(
                document["colorimetry_receipt"]
            ),
            policy=Aihub538PhotometricPolicyV4.from_json_object(
                document["policy"]
            ),
            cohort_separation_receipt=(
                Aihub538FreshCohortSeparationReceiptV4.from_json_object(
                    document["cohort_separation_receipt"]
                )
            ),
            source_manifest_sha256=require_sha256(
                document["source_manifest_sha256"], "source_manifest_sha256"
            ),
            admission_receipt_sha256=require_sha256(
                document["admission_receipt_sha256"],
                "admission_receipt_sha256",
            ),
            development_aggregate=(
                Aihub538PhotometricPartitionAggregateV4.from_json_object(
                    document["development_aggregate"]
                )
            ),
            calibration_aggregate=(
                Aihub538PhotometricPartitionAggregateV4.from_json_object(
                    document["calibration_aggregate"]
                )
            ),
            actor_clustered_one_sided_ci=(
                Aihub538ActorClusteredOneSidedCiV4.from_json_object(
                    document["actor_clustered_one_sided_ci"]
                )
            ),
            production_execution_policy_sha256=require_sha256(document["production_execution_policy_sha256"],"production_execution_policy_sha256"),
            ordered_sampling_receipt_commitment_sha256=require_sha256(document["ordered_sampling_receipt_commitment_sha256"],"ordered_sampling_receipt_commitment_sha256"),
            pair_count=require_integer(document["pair_count"],"pair_count",minimum=1),remediation_receipt_sha256=require_sha256(document["remediation_receipt_sha256"],"remediation_receipt_sha256"),ordered_pair_lineage_commitment_sha256=require_sha256(document["ordered_pair_lineage_commitment_sha256"],"ordered_pair_lineage_commitment_sha256"),selected_member_commitment_sha256=require_sha256(document["selected_member_commitment_sha256"],"selected_member_commitment_sha256"),selected_member_count=require_integer(document["selected_member_count"],"selected_member_count",minimum=1),ordered_sampling_receipt_count=require_integer(document["ordered_sampling_receipt_count"],"ordered_sampling_receipt_count",minimum=0),
            artifact_sha256=require_sha256(
                document["artifact_sha256"], "artifact_sha256"
            ),
            schema_version=require_safe_identifier(
                document["schema_version"], "schema_version"
            ),
        )
        expected_status = (
            "evaluated_non_promotional"
            if result.actor_clustered_one_sided_ci.passed
            else "failed_closed"
        )
        if document["status"] != expected_status:
            raise VisualFeedbackContractError(
                "photometric v4 evidence status decision differs"
            )
        return result


__all__ = [
    "AIHUB538_PHOTOMETRIC_EVIDENCE_V4_SCHEMA_VERSION",
    "AIHUB538_PHOTOMETRIC_POLICY_V4_SCHEMA_VERSION",
    "AIHUB538_PHOTOMETRIC_PROTOCOL_V4_SCHEMA_VERSION",
    "AIHUB538_PHOTOMETRIC_SOFT_METRIC_SPECS",
    "AIHUB538_PHOTOMETRIC_V4_FROZEN_SAFETY_AND_STATISTICS",
    "AIHUB538_PHOTOMETRIC_V4_FROZEN_SAFETY_AND_STATISTICS_SHA256",
    "AIHUB538_V3_COHORT_ROLE",
    "AIHUB538_V3_FAILURE_ARTIFACT_SHA256",
    "FFMPEG_RGB_COLORIMETRY_CONVERSION_CONTRACT_SHA256",
    "FFMPEG_RGB_COLORIMETRY_CONVERSION_DOCUMENT",
    "FFMPEG_RGB_COLORIMETRY_GOLDEN_INPUT",
    "FFMPEG_RGB_COLORIMETRY_GOLDEN_OUTPUT",
    "FFMPEG_RGB_COLORIMETRY_GOLDEN_OUTPUT_SHA256",
    "FFMPEG_RGB_COLORIMETRY_GOLDEN_VECTOR_SET_SHA256",
    "DECODED_RGB_DERIVATION_MANIFEST_V4",
    "DECODED_RGB_DERIVATION_MANIFEST_V4_SHA256",
    "FFMPEG_FD_ADAPTER_EXECUTION_MANIFEST_V4",
    "FFMPEG_FD_ADAPTER_EXECUTION_MANIFEST_V4_SHA256",
    "Aihub538ActorClusteredOneSidedCiV4",
    "Aihub538ActorOutcomeCellV4",
    "Aihub538ActorOutcomeDistributionV4",
    "Aihub538FreshCohortSeparationReceiptV4",
    "Aihub538FullFrameExposureAggregateV4",
    "Aihub538HardSafetyEnvelopeV4",
    "Aihub538LipRoiExposureVisibilityAggregateV4",
    "Aihub538PhotometricCalibrationEvidenceV4",
    "Aihub538PhotometricCalibrationProtocolV4",
    "Aihub538PhotometricMetricNormalizerV4",
    "Aihub538PhotometricPartitionAggregateV4",
    "Aihub538PhotometricPolicyV4",
    "FfmpegRgbColorimetryReceipt",
    "FfmpegSourceColorProbeReceiptV4",
    "FfmpegRgbSourceConversionReceiptV4",
]
