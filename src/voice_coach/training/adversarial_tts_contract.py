"""Torch-free preflight contract for the adversarial TTS prototype."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
from pathlib import Path
import re
from typing import Any


class AdversarialTTSConfigError(ValueError):
    """Raised before Torch, audio, a model, or a GPU may be accessed."""


SCHEMA_VERSION = "voice_coach.adversarial_tts.v1"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PROTECTED_SPLITS = {"stack", "calibration", "locked_test"}

TOP_LEVEL_KEYS = {
    "schema_version",
    "experiment",
    "data",
    "model",
    "training",
    "safety",
    "outputs",
}
EXPERIMENT_KEYS = {
    "id",
    "status",
    "production_ready",
    "research_only",
    "description",
}
DATA_KEYS = {"learner", "native", "sampling"}
LEARNER_KEYS = {
    "manifest",
    "sha256",
    "allowed_splits",
    "qc_clean_only",
    "exclude_reserved_prompts",
    "target_mode",
    "treat_all_as_incorrect",
    "text_condition",
}
NATIVE_KEYS = {
    "manifest",
    "sha256",
    "allowed_source_ids",
    "required_intended_role",
    "noncommercial_only",
    "paired_reconstruction_allowed",
    "multi_speaker_required_before_promotion",
}
SAMPLING_KEYS = {
    "strategy",
    "duration_bucketed",
    "shared_audio_preprocessing",
    "nuisance_balanced",
}
MODEL_KEYS = {
    "vocabulary_size",
    "aspect_count",
    "feedback_dim",
    "max_meta_adjustment",
    "generator",
    "evaluator1",
    "evaluator2_meta_critic",
}
GENERATOR_KEYS = {
    "hidden_dim",
    "acoustic_dim",
    "hop_length",
    "residual_blocks",
    "dropout",
    "max_edit_amplitude",
}
EVALUATOR_KEYS = {"hidden_dim", "embedding_dim"}
META_EVALUATOR_KEYS = {
    "hidden_dim",
    "embedding_dim",
    "consumes_evaluator1_outputs",
    "direct_gradient_to_generator",
    "production_s2",
}
TRAINING_KEYS = {
    "seed",
    "update_order",
    "learning_rates",
    "loss_weights",
    "monotonic_margin",
    "max_gradient_norm",
}
LEARNING_RATE_KEYS = {
    "generator",
    "evaluator1_core",
    "evaluator2_meta_critic",
    "evaluator1_meta_reward",
}
LOSS_WEIGHT_KEYS = {
    "primary_adversarial",
    "primary_content",
    "primary_aspects",
    "primary_meta",
    "meta_domain",
    "meta_progress",
    "meta_adjustment_regularization",
    "generator_adversarial",
    "generator_content",
    "generator_reward",
    "generator_monotonic",
    "generator_identity",
    "generator_minimal_edit",
    "generator_native_identity",
}
SAFETY_KEYS = {
    "preserve_speaker_identity",
    "identity_mode_requires_consent",
    "preserve_content",
    "bounded_edits",
    "generated_audio_label_required",
    "production_s2_remains_independent",
    "forbid_nationality_as_model_input",
}
OUTPUT_KEYS = {
    "artifact_root",
    "checkpoint_format",
    "store_raw_audio_in_report",
    "store_transcript_in_report",
    "store_source_paths_in_report",
}


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AdversarialTTSConfigError(f"{field_name} must be a mapping")
    return value


def _exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    field_name: str,
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise AdversarialTTSConfigError(
            f"{field_name} keys differ: missing={missing}, extra={extra}"
        )


def _expect(value: object, expected: object, field_name: str) -> None:
    if value != expected:
        raise AdversarialTTSConfigError(f"{field_name} must be {expected!r}")


def _positive_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdversarialTTSConfigError(f"{field_name} must be numeric")
    parsed = float(value)
    if not 0 < parsed < float("inf"):
        raise AdversarialTTSConfigError(f"{field_name} must be finite and positive")
    return parsed


def _nonnegative_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdversarialTTSConfigError(f"{field_name} must be numeric")
    parsed = float(value)
    if not 0 <= parsed < float("inf"):
        raise AdversarialTTSConfigError(f"{field_name} must be finite and non-negative")
    return parsed


def _positive_integer(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AdversarialTTSConfigError(f"{field_name} must be a positive integer")
    return int(value)


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise AdversarialTTSConfigError(f"{field_name} must be a lowercase SHA-256")
    return value


def _string_sequence(value: object, field_name: str) -> tuple[str, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise AdversarialTTSConfigError(
            f"{field_name} must be a non-empty string sequence"
        )
    return tuple(value)


def _relative_path(value: object, field_name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise AdversarialTTSConfigError(f"{field_name} must be a relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise AdversarialTTSConfigError(f"{field_name} must stay inside the workspace")
    if any(part == ".env" or part.startswith(".env.") for part in path.parts):
        raise AdversarialTTSConfigError(
            f"{field_name} may not reference an environment file"
        )
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_adversarial_tts_config(
    document: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate policy, independence, split, and architecture declarations."""

    _exact_keys(document, TOP_LEVEL_KEYS, "config")
    _expect(document["schema_version"], SCHEMA_VERSION, "schema_version")

    experiment = _mapping(document["experiment"], "experiment")
    _exact_keys(experiment, EXPERIMENT_KEYS, "experiment")
    _expect(experiment["status"], "prototype", "experiment.status")
    _expect(
        experiment["production_ready"],
        False,
        "experiment.production_ready",
    )
    _expect(experiment["research_only"], True, "experiment.research_only")
    if not isinstance(experiment["id"], str) or not experiment["id"]:
        raise AdversarialTTSConfigError("experiment.id must be non-empty")
    if not isinstance(experiment["description"], str):
        raise AdversarialTTSConfigError("experiment.description must be a string")

    data = _mapping(document["data"], "data")
    _exact_keys(data, DATA_KEYS, "data")
    learner = _mapping(data["learner"], "data.learner")
    native = _mapping(data["native"], "data.native")
    sampling = _mapping(data["sampling"], "data.sampling")
    _exact_keys(learner, LEARNER_KEYS, "data.learner")
    _exact_keys(native, NATIVE_KEYS, "data.native")
    _exact_keys(sampling, SAMPLING_KEYS, "data.sampling")
    learner_manifest = _relative_path(
        learner["manifest"],
        "data.learner.manifest",
    )
    native_manifest = _relative_path(
        native["manifest"],
        "data.native.manifest",
    )
    learner_sha = _sha256(learner["sha256"], "data.learner.sha256")
    native_sha = _sha256(native["sha256"], "data.native.sha256")
    allowed_splits = _string_sequence(
        learner["allowed_splits"],
        "data.learner.allowed_splits",
    )
    if set(allowed_splits) & PROTECTED_SPLITS:
        raise AdversarialTTSConfigError(
            "protected learner splits may not enter GAN training"
        )
    _expect(allowed_splits, ("train",), "data.learner.allowed_splits")
    _expect(learner["qc_clean_only"], True, "learner QC policy")
    _expect(
        learner["exclude_reserved_prompts"],
        True,
        "learner reserved-prompt policy",
    )
    _expect(
        learner["target_mode"],
        "phone_labels_and_soft_human_scores",
        "learner target mode",
    )
    _expect(
        learner["treat_all_as_incorrect"],
        False,
        "learner correctness policy",
    )
    _expect(
        learner["text_condition"],
        "canonical_phone_ids",
        "learner text condition",
    )
    allowed_native = _string_sequence(
        native["allowed_source_ids"],
        "data.native.allowed_source_ids",
    )
    _expect(
        allowed_native,
        ("kss",),
        "currently licensed native training sources",
    )
    _expect(
        native["required_intended_role"],
        "research_only_native_korean_tts_baseline",
        "native intended role",
    )
    _expect(native["noncommercial_only"], True, "native license boundary")
    _expect(
        native["paired_reconstruction_allowed"],
        False,
        "cross-speaker reconstruction policy",
    )
    _expect(
        native["multi_speaker_required_before_promotion"],
        True,
        "native promotion policy",
    )
    _expect(
        sampling["strategy"],
        "unpaired_conditional",
        "sampling strategy",
    )
    for field in (
        "duration_bucketed",
        "shared_audio_preprocessing",
        "nuisance_balanced",
    ):
        _expect(sampling[field], True, f"sampling.{field}")

    model = _mapping(document["model"], "model")
    _exact_keys(model, MODEL_KEYS, "model")
    vocabulary_size = _positive_integer(
        model["vocabulary_size"],
        "model.vocabulary_size",
    )
    aspect_count = _positive_integer(
        model["aspect_count"],
        "model.aspect_count",
    )
    feedback_dim = _positive_integer(
        model["feedback_dim"],
        "model.feedback_dim",
    )
    if feedback_dim < aspect_count:
        raise AdversarialTTSConfigError(
            "feedback_dim must cover every evaluator aspect"
        )
    adjustment = _nonnegative_number(
        model["max_meta_adjustment"],
        "model.max_meta_adjustment",
    )
    if adjustment > 1:
        raise AdversarialTTSConfigError("model.max_meta_adjustment may not exceed 1")
    generator = _mapping(model["generator"], "model.generator")
    evaluator1 = _mapping(model["evaluator1"], "model.evaluator1")
    evaluator2 = _mapping(
        model["evaluator2_meta_critic"],
        "model.evaluator2_meta_critic",
    )
    _exact_keys(generator, GENERATOR_KEYS, "model.generator")
    _exact_keys(evaluator1, EVALUATOR_KEYS, "model.evaluator1")
    _exact_keys(
        evaluator2,
        META_EVALUATOR_KEYS,
        "model.evaluator2_meta_critic",
    )
    for field in (
        "hidden_dim",
        "acoustic_dim",
        "hop_length",
        "residual_blocks",
    ):
        _positive_integer(generator[field], f"generator.{field}")
    dropout = _nonnegative_number(generator["dropout"], "generator.dropout")
    if dropout >= 1:
        raise AdversarialTTSConfigError("generator.dropout must be below 1")
    edit = _positive_number(
        generator["max_edit_amplitude"],
        "generator.max_edit_amplitude",
    )
    if edit > 1:
        raise AdversarialTTSConfigError("generator.max_edit_amplitude may not exceed 1")
    for prefix, evaluator in (
        ("evaluator1", evaluator1),
        ("evaluator2", evaluator2),
    ):
        _positive_integer(evaluator["hidden_dim"], f"{prefix}.hidden_dim")
        _positive_integer(
            evaluator["embedding_dim"],
            f"{prefix}.embedding_dim",
        )
    _expect(
        evaluator2["consumes_evaluator1_outputs"],
        False,
        "evaluator2 independence",
    )
    _expect(
        evaluator2["direct_gradient_to_generator"],
        False,
        "evaluator2 gradient boundary",
    )
    _expect(
        evaluator2["production_s2"],
        False,
        "evaluator2 production role",
    )

    training = _mapping(document["training"], "training")
    _exact_keys(training, TRAINING_KEYS, "training")
    _positive_integer(training["seed"], "training.seed")
    order = _string_sequence(training["update_order"], "training.update_order")
    _expect(
        order,
        (
            "evaluator1_core",
            "evaluator2_meta_critic",
            "evaluator1_meta_reward",
            "generator",
        ),
        "training.update_order",
    )
    rates = _mapping(training["learning_rates"], "training.learning_rates")
    _exact_keys(rates, LEARNING_RATE_KEYS, "training.learning_rates")
    for name, value in rates.items():
        _positive_number(value, f"learning_rates.{name}")
    weights = _mapping(training["loss_weights"], "training.loss_weights")
    _exact_keys(weights, LOSS_WEIGHT_KEYS, "training.loss_weights")
    for name, value in weights.items():
        _nonnegative_number(value, f"loss_weights.{name}")
    _nonnegative_number(
        training["monotonic_margin"],
        "training.monotonic_margin",
    )
    _positive_number(
        training["max_gradient_norm"],
        "training.max_gradient_norm",
    )

    safety = _mapping(document["safety"], "safety")
    _exact_keys(safety, SAFETY_KEYS, "safety")
    for name in SAFETY_KEYS:
        _expect(safety[name], True, f"safety.{name}")

    outputs = _mapping(document["outputs"], "outputs")
    _exact_keys(outputs, OUTPUT_KEYS, "outputs")
    artifact_root = _relative_path(
        outputs["artifact_root"],
        "outputs.artifact_root",
    )
    _expect(
        outputs["checkpoint_format"],
        "safetensors",
        "outputs.checkpoint_format",
    )
    for name in (
        "store_raw_audio_in_report",
        "store_transcript_in_report",
        "store_source_paths_in_report",
    ):
        _expect(outputs[name], False, f"outputs.{name}")

    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": experiment["id"],
        "status": "validated_prototype_config",
        "production_ready": False,
        "learner_manifest": learner_manifest.as_posix(),
        "learner_manifest_sha256": learner_sha,
        "native_manifest": native_manifest.as_posix(),
        "native_manifest_sha256": native_sha,
        "native_source_ids": list(allowed_native),
        "sampling_strategy": sampling["strategy"],
        "vocabulary_size": vocabulary_size,
        "feedback_dim": feedback_dim,
        "artifact_root": artifact_root.as_posix(),
        "production_s2_remains_independent": True,
    }


def load_adversarial_tts_config(path: str | Path) -> dict[str, Any]:
    """Load YAML with duplicate-key rejection, then validate it."""

    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise AdversarialTTSConfigError(
            "PyYAML is required to load the experiment configuration"
        ) from exc

    class UniqueKeyLoader(yaml.SafeLoader):
        pass

    def construct_mapping(
        loader: Any,
        node: Any,
        deep: bool = False,
    ) -> dict[str, Any]:
        mapping: dict[str, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise AdversarialTTSConfigError("configuration keys must be strings")
            if key in mapping:
                raise AdversarialTTSConfigError(f"duplicate configuration key: {key}")
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    UniqueKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_mapping,
    )
    config_path = Path(path)
    try:
        document = yaml.load(
            config_path.read_text(encoding="utf-8"),
            Loader=UniqueKeyLoader,
        )
    except (OSError, yaml.YAMLError) as exc:
        raise AdversarialTTSConfigError(
            "failed to load adversarial TTS configuration"
        ) from exc
    config = dict(_mapping(document, "config"))
    validate_adversarial_tts_config(config)
    return config


def preflight_adversarial_tts(
    *,
    workspace_root: str | Path,
    config_path: str | Path,
) -> dict[str, Any]:
    """Verify config and pinned manifests without importing Torch."""

    workspace = Path(workspace_root).resolve(strict=True)
    candidate = Path(config_path)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved_config = candidate.resolve(strict=True)
    try:
        resolved_config.relative_to(workspace)
    except ValueError as exc:
        raise AdversarialTTSConfigError("config path escapes the workspace") from exc
    config = load_adversarial_tts_config(resolved_config)
    summary = validate_adversarial_tts_config(config)
    verified: dict[str, str] = {}
    for role in ("learner", "native"):
        declared = Path(str(config["data"][role]["manifest"]))
        resolved = (workspace / declared).resolve(strict=True)
        try:
            resolved.relative_to(workspace)
        except ValueError as exc:
            raise AdversarialTTSConfigError(
                f"{role} manifest escapes the workspace"
            ) from exc
        if not resolved.is_file():
            raise AdversarialTTSConfigError(f"{role} manifest is not a regular file")
        actual = sha256_file(resolved)
        expected = str(config["data"][role]["sha256"])
        if actual != expected:
            raise AdversarialTTSConfigError(
                f"{role} manifest SHA-256 differs from the pin"
            )
        verified[role] = actual
    return {
        **summary,
        "config_path": resolved_config.relative_to(workspace).as_posix(),
        "config_sha256": sha256_file(resolved_config),
        "verified_manifest_sha256": verified,
        "torch_imported": False,
        "audio_opened": False,
        "model_or_gpu_accessed": False,
    }
