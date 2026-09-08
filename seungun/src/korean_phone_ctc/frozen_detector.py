"""Offline inference for the frozen Stage-1 + layer-18 pronunciation detector.

This module exposes the actual phone-level result that a deterministic policy
may consume.  The detector score is deliberately named ``error_score`` rather
than confidence: the frozen artifact supplies an operating threshold, but no
probability-calibration claim.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
from numpy.typing import NDArray

from .evaluation.phone_alignment import align_phones

SCHEMA_VERSION = "korean_phone_ctc.pronunciation_analysis.v1"
SCORE_SEMANTICS = "detector_ranking_score_not_calibrated_correctness_confidence"
TARGET_SAMPLE_RATE = 16_000
SOFT_FEATURE_NAMES = (
    "hard_error",
    "has_predicted_span",
    "expected_prob",
    "competitor_prob",
    "expected_margin",
    "predicted_prob",
    "entropy",
    "blank_prob",
    "span_seconds",
)
EXPECTED_ARTIFACT_HASHES = {
    "detector_config.json": (
        "bba5b1928e3f3bd8980f6a747a86a64758b33dbba021775efdf96d12987f9640"
    ),
    "detector_state.pt": (
        "693a87d1df1bc637810ee5b2e01815192dc35387862fb497cbc7a0a1e96fc398"
    ),
    "normalization.npz": (
        "5117d4069ac935cd4f27f258217d61e23b7b3b66f2a7d9202552dbfd35e7e419"
    ),
}

AlignmentOperation = Literal["correct", "substitution", "deletion"]


class FrozenDetectorError(RuntimeError):
    """The frozen artifact or inference input cannot be used safely."""


@dataclass(frozen=True, slots=True)
class PhoneEvidence:
    expected_index: int
    expected_phone: str
    observed_candidate: str | None
    alignment_operation: AlignmentOperation
    start_ms: int | None
    end_ms: int | None
    error_score: float
    operating_threshold: float
    is_error: bool
    score_semantics: str
    soft_features: dict[str, float]


@dataclass(frozen=True, slots=True)
class PronunciationAnalysis:
    schema_version: str
    detector_version: str
    detector_config_sha256: str
    backbone_name: str
    backbone_tree_sha256: str
    expected_phones: tuple[str, ...]
    ctc_hypothesis: tuple[str, ...]
    insertions: tuple[str, ...]
    phones: tuple[PhoneEvidence, ...]
    notices: tuple[str, ...]

    def to_json_object(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DetectorRow:
    expected_index: int
    expected_phone: str
    observed_candidate: str | None
    alignment_operation: AlignmentOperation
    start_ms: int | None
    end_ms: int | None
    soft: NDArray[np.float32]
    hidden: NDArray[np.float32]
    expected_id: int


def raw_ctc_spans(raw_ids: Sequence[int], blank_id: int) -> list[tuple[int, int, int]]:
    """Return non-blank ``(token_id, start, end_exclusive)`` CTC runs."""

    if not raw_ids:
        return []
    spans: list[tuple[int, int, int]] = []
    previous: int | None = raw_ids[0]
    start = 0
    for index in range(1, len(raw_ids) + 1):
        current = raw_ids[index] if index < len(raw_ids) else None
        if current != previous:
            if previous is not None and previous != blank_id:
                spans.append((previous, start, index))
            previous = current
            start = index
    return spans


def _softmax(values: NDArray[np.float32]) -> NDArray[np.float32]:
    shifted = values - values.max(axis=-1, keepdims=True)
    exponential = np.exp(shifted, dtype=np.float32)
    return np.asarray(
        exponential / exponential.sum(axis=-1, keepdims=True), dtype=np.float32
    )


def build_detector_rows(
    *,
    logits: NDArray[np.float32],
    hidden_layer_18: NDArray[np.float32],
    expected_phones: Sequence[str],
    vocabulary: dict[str, int],
    id_to_token: dict[int, str],
    blank_id: int,
    waveform_samples: int,
) -> tuple[list[DetectorRow], tuple[str, ...], tuple[str, ...]]:
    """Reproduce the frozen training feature extraction for one utterance."""

    if logits.ndim != 2 or logits.shape[1] != len(vocabulary):
        raise FrozenDetectorError("CTC logits have an unexpected shape")
    if hidden_layer_18.shape != (logits.shape[0], 1024):
        raise FrozenDetectorError("layer-18 hidden state has an unexpected shape")
    if logits.shape[0] == 0 or waveform_samples <= 0:
        raise FrozenDetectorError("CTC output or waveform is empty")
    if not expected_phones:
        raise FrozenDetectorError("expected phone sequence is empty")
    if not np.isfinite(logits).all() or not np.isfinite(hidden_layer_18).all():
        raise FrozenDetectorError("CTC output contains NaN or infinity")

    raw_ids = logits.argmax(axis=-1).astype(np.int64).tolist()
    spans = raw_ctc_spans(raw_ids, blank_id)
    try:
        hypothesis = tuple(id_to_token[token_id] for token_id, _, _ in spans)
    except KeyError as error:
        raise FrozenDetectorError(
            "CTC hypothesis contains an unknown token id"
        ) from error
    alignment = align_phones(expected_phones, hypothesis)
    frame_seconds = waveform_samples / TARGET_SAMPLE_RATE / logits.shape[0]
    frame_probs = _softmax(logits)

    rows: list[DetectorRow] = []
    insertions: list[str] = []
    expected_index = 0
    predicted_index = 0
    for item in alignment:
        if item.operation == "insertion":
            if item.hypothesis is not None:
                insertions.append(item.hypothesis)
            predicted_index += 1
            continue
        expected_phone = expected_phones[expected_index]
        expected_id = vocabulary.get(expected_phone)
        if expected_id is None:
            raise FrozenDetectorError(f"unknown expected phone: {expected_phone!r}")

        if item.operation == "deletion":
            soft = np.asarray(
                [1.0, 0.0, 0.0, 1.0, -1.0, 0.0, 0.0, 1.0, 0.0],
                dtype=np.float32,
            )
            hidden = np.zeros(1024, dtype=np.float32)
            observed = None
            start_ms = end_ms = None
            operation: AlignmentOperation = "deletion"
        else:
            predicted_id, start, end = spans[predicted_index]
            probabilities = frame_probs[start:end].mean(axis=0, dtype=np.float64)
            expected_prob = float(probabilities[expected_id])
            predicted_prob = float(probabilities[predicted_id])
            blank_prob = float(probabilities[blank_id])
            competitor = probabilities.copy()
            competitor[expected_id] = -1.0
            competitor_prob = float(competitor.max())
            safe = np.maximum(probabilities, 1e-12)
            entropy = float(-(safe * np.log(safe)).sum())
            operation = item.operation
            soft = np.asarray(
                [
                    float(operation == "substitution"),
                    1.0,
                    expected_prob,
                    competitor_prob,
                    expected_prob - competitor_prob,
                    predicted_prob,
                    entropy,
                    blank_prob,
                    (end - start) * frame_seconds,
                ],
                dtype=np.float32,
            )
            hidden = hidden_layer_18[start:end].mean(axis=0, dtype=np.float32)
            observed = item.hypothesis
            start_ms = round(start * frame_seconds * 1000)
            end_ms = round(end * frame_seconds * 1000)
            predicted_index += 1
        rows.append(
            DetectorRow(
                expected_index=expected_index,
                expected_phone=expected_phone,
                observed_candidate=observed,
                alignment_operation=operation,
                start_ms=start_ms,
                end_ms=end_ms,
                soft=soft,
                hidden=np.asarray(hidden, dtype=np.float32),
                expected_id=expected_id,
            )
        )
        expected_index += 1

    if expected_index != len(expected_phones) or predicted_index != len(spans):
        raise FrozenDetectorError("phone alignment cursor mismatch")
    return rows, hypothesis, tuple(insertions)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FrozenPronunciationDetector:
    """Load the frozen artifacts and score expected phones for one WAV."""

    def __init__(
        self,
        *,
        repository_root: str | Path,
        device: str = "cpu",
    ) -> None:
        self.root = Path(repository_root).resolve()
        self.device = device
        self.bundle = (
            self.root
            / "seungun/artifacts/error_detector/final_pronunciation_detector_v1"
        )
        self.model_dir = (
            self.root
            / "seungun/checkpoints/xlsr300m_full_internal_v1/best_model"
        )
        self.processor_dir = self.root / "seungun/processor"
        self.config = self._verify_and_read_config()
        with np.load(self.bundle / "normalization.npz", allow_pickle=False) as data:
            self.soft_mean = np.asarray(data["soft_mean"], dtype=np.float32)
            self.soft_std = np.asarray(data["soft_std"], dtype=np.float32)
        if self.soft_mean.shape != (9,) or self.soft_std.shape != (9,):
            raise FrozenDetectorError("normalization shape differs")
        self._processor: Any | None = None
        self._model: Any | None = None
        self._state: dict[str, Any] | None = None

    def _verify_and_read_config(self) -> dict[str, Any]:
        hashes = json.loads(
            (self.bundle / "artifact_hashes.json").read_text(encoding="utf-8")
        )
        for name, pinned in EXPECTED_ARTIFACT_HASHES.items():
            path = self.bundle / name
            expected = hashes.get(name, {}).get("sha256")
            if expected != pinned or not path.is_file() or _sha256(path) != pinned:
                raise FrozenDetectorError(f"frozen artifact hash differs: {name}")
        freeze = (self.bundle / "FREEZE_SHA256.txt").read_text(encoding="utf-8").strip()
        if freeze != EXPECTED_ARTIFACT_HASHES["detector_config.json"]:
            raise FrozenDetectorError("detector freeze receipt differs")
        config = json.loads(
            (self.bundle / "detector_config.json").read_text(encoding="utf-8")
        )
        detector = config.get("detector", {})
        if (
            detector.get("architecture") != "soft_expected_hidden_mlp"
            or detector.get("hidden_layers") != [18]
            or tuple(detector.get("soft_feature_names", ())) != SOFT_FEATURE_NAMES
        ):
            raise FrozenDetectorError("frozen detector architecture differs")
        return cast(dict[str, Any], config)

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoModelForCTC, Wav2Vec2Processor
        except ImportError as error:
            raise FrozenDetectorError(
                "inference dependencies are unavailable"
            ) from error
        try:
            processor = Wav2Vec2Processor.from_pretrained(
                self.processor_dir, local_files_only=True
            )
            model = AutoModelForCTC.from_pretrained(
                self.model_dir, local_files_only=True
            ).to(self.device)
            state = torch.load(
                self.bundle / "detector_state.pt",
                map_location=self.device,
                weights_only=True,
            )
        except Exception as error:
            raise FrozenDetectorError(
                f"frozen detector failed to load: {type(error).__name__}"
            ) from error
        self._processor = processor
        self._model = model.eval()
        self._state = state

    def preload(self) -> None:
        """Materialize all frozen inference state before runtime readiness."""

        self._load()

    def _score_rows(self, rows: Sequence[DetectorRow]) -> NDArray[np.float32]:
        self._load()
        assert self._state is not None
        import torch
        import torch.nn.functional as functional

        state = self._state
        soft = np.stack([row.soft for row in rows])
        normalized = (soft - self.soft_mean) / self.soft_std
        hidden = np.stack([row.hidden for row in rows])
        expected_ids = np.asarray([row.expected_id for row in rows], dtype=np.int64)
        with torch.inference_mode():
            soft_tensor = torch.from_numpy(normalized).to(self.device)
            hidden_tensor = torch.from_numpy(hidden).to(self.device)
            expected_tensor = torch.from_numpy(expected_ids).to(self.device)
            embedding = functional.embedding(
                expected_tensor, state["expected_embedding.weight"]
            )
            projected = functional.layer_norm(
                hidden_tensor,
                (1024,),
                state["hidden_norms.0.weight"],
                state["hidden_norms.0.bias"],
            )
            projected = functional.gelu(
                functional.linear(
                    projected,
                    state["hidden_projs.0.0.weight"],
                    state["hidden_projs.0.0.bias"],
                )
            )
            values = torch.cat((soft_tensor, embedding, projected), dim=1)
            values = functional.gelu(
                functional.linear(values, state["head.0.weight"], state["head.0.bias"])
            )
            values = functional.gelu(
                functional.linear(values, state["head.3.weight"], state["head.3.bias"])
            )
            logits = functional.linear(
                values, state["head.6.weight"], state["head.6.bias"]
            ).squeeze(1)
            scores = torch.sigmoid(logits).float().cpu().numpy()
        return np.asarray(scores, dtype=np.float32)

    @staticmethod
    def _read_audio(path: Path) -> NDArray[np.float32]:
        try:
            import soundfile as soundfile  # type: ignore[import-untyped]
            from scipy.signal import resample_poly  # type: ignore[import-untyped]
        except ImportError as error:
            raise FrozenDetectorError("audio dependencies are unavailable") from error
        samples, sample_rate = soundfile.read(path, dtype="float32")
        if samples.ndim == 2:
            samples = samples.mean(axis=1)
        if sample_rate != TARGET_SAMPLE_RATE:
            from math import gcd

            divisor = gcd(int(sample_rate), TARGET_SAMPLE_RATE)
            samples = resample_poly(
                samples, TARGET_SAMPLE_RATE // divisor, int(sample_rate) // divisor
            )
        result = np.asarray(samples, dtype=np.float32)
        if result.size < TARGET_SAMPLE_RATE // 10 or not np.isfinite(result).all():
            raise FrozenDetectorError("audio is too short or non-finite")
        return result

    def analyze(
        self, *, audio_path: str | Path, expected_phones: Sequence[str]
    ) -> PronunciationAnalysis:
        path = Path(audio_path)
        if not path.is_file():
            raise FrozenDetectorError("audio file does not exist")
        self._load()
        assert self._processor is not None and self._model is not None
        import torch

        samples = self._read_audio(path)
        inputs = self._processor(
            samples,
            sampling_rate=TARGET_SAMPLE_RATE,
            return_tensors="pt",
        )
        inputs = {name: value.to(self.device) for name, value in inputs.items()}
        with torch.inference_mode():
            output = self._model(
                **inputs, output_hidden_states=True, return_dict=True
            )
        logits = output.logits[0].float().cpu().numpy().astype(np.float32)
        hidden = output.hidden_states[18][0].float().cpu().numpy().astype(np.float32)
        vocabulary = self._processor.tokenizer.get_vocab()
        id_to_token = {int(value): str(key) for key, value in vocabulary.items()}
        blank_id = int(self._model.config.pad_token_id)
        rows, hypothesis, insertions = build_detector_rows(
            logits=logits,
            hidden_layer_18=hidden,
            expected_phones=expected_phones,
            vocabulary={str(key): int(value) for key, value in vocabulary.items()},
            id_to_token=id_to_token,
            blank_id=blank_id,
            waveform_samples=len(samples),
        )
        scores = self._score_rows(rows)
        threshold = float(self.config["operating_point"]["threshold"])
        phones = tuple(
            PhoneEvidence(
                expected_index=row.expected_index,
                expected_phone=row.expected_phone,
                observed_candidate=row.observed_candidate,
                alignment_operation=row.alignment_operation,
                start_ms=row.start_ms,
                end_ms=row.end_ms,
                error_score=float(score),
                operating_threshold=threshold,
                is_error=bool(score >= threshold),
                score_semantics=SCORE_SEMANTICS,
                soft_features={
                    name: float(value)
                    for name, value in zip(SOFT_FEATURE_NAMES, row.soft, strict=True)
                },
            )
            for row, score in zip(rows, scores, strict=True)
        )
        return PronunciationAnalysis(
            schema_version=SCHEMA_VERSION,
            detector_version=str(self.config["version"]),
            detector_config_sha256=_sha256(self.bundle / "detector_config.json"),
            backbone_name=str(self.config["backbone"]["name"]),
            backbone_tree_sha256=str(self.config["backbone"]["tree_sha256"]),
            expected_phones=tuple(expected_phones),
            ctc_hypothesis=hypothesis,
            insertions=insertions,
            phones=phones,
            notices=(
                "error_score is not calibrated correctness confidence",
                "observed_candidate is a CTC alignment candidate, not ground truth",
                "locked-test performance does not validate an individual decision",
            ),
        )


__all__ = [
    "SCHEMA_VERSION",
    "SCORE_SEMANTICS",
    "SOFT_FEATURE_NAMES",
    "EXPECTED_ARTIFACT_HASHES",
    "DetectorRow",
    "FrozenDetectorError",
    "FrozenPronunciationDetector",
    "PhoneEvidence",
    "PronunciationAnalysis",
    "build_detector_rows",
    "raw_ctc_spans",
]
