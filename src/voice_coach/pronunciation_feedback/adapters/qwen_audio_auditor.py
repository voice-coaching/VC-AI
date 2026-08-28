"""``S2AuditorPort`` over Qwen2-Audio — a different model family from S1.

The independence this port claims has to be architectural, not a renamed
backend id.  S1 here is ``wav2vec2-large-xlsr-korean``: a wav2vec2 SSL encoder
with a CTC head over Korean syllables, trained on Korean read and free speech.
S2 is ``Qwen2-Audio-7B-Instruct``: a Whisper-large-v3 audio encoder feeding a
Qwen2 language model, instruction-tuned on multilingual audio understanding.
Different pretraining corpora, different architectures, different objectives —
so a sentence S1 mis-hears is not a sentence S2 is predisposed to mis-hear the
same way, which is the only property that makes an audit worth running.

What the auditor is asked is deliberately narrow: *what does this recording
say?*  It is not asked to grade pronunciation.  An auditor that re-scored
quality would drift into agreeing with whatever S1 already decided, and its
disagreement would stop being informative; asking only for content keeps the
veto about the thing a wrong answer would make unsafe — coaching someone on a
sentence they did not read.

Confidence is the mean per-token probability of the reply the model actually
produced, not a number the model was asked to state about itself.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import threading
from typing import Any

from ..domain import (
    AuditVerdict,
    FailureCode,
    IndependenceReceipt,
    PromptSpec,
    S2AuditResult,
)
from ..errors import BackendUnavailableError
from .ctc_content_alignment import jamo_similarity

BACKEND_ID = "qwen2-audio-7b-instruct-auditor"
#: Read off the resolved snapshot directory rather than hard-coded, so the
#: revision in a receipt is the revision that answered, not one copied here.
DEFAULT_MODEL_ROOT = Path(
    "data/cache/huggingface/hub/models--Qwen--Qwen2-Audio-7B-Instruct/snapshots"
)
#: Asked in Korean because the audio is Korean; an English instruction makes the
#: reply language a second variable in every answer.
TRANSCRIPTION_QUESTION = (
    "이 음성에서 들리는 한국어 문장을 그대로 받아써 주세요. "
    "설명이나 따옴표 없이 문장만 출력하세요."
)
#: Above this jamo agreement the recording is the prompt sentence; below the
#: lower bound it is a different sentence.  Between them the auditor says so
#: rather than casting a vote it cannot support, and the gate stops the attempt.
AUDIT_AGREEMENT_APPROVE = 0.70
AUDIT_AGREEMENT_REJECT = 0.45
MAXIMUM_REPLY_TOKENS = 96
TARGET_SAMPLE_RATE = 16_000


def _fail(detail: str) -> BackendUnavailableError:
    return BackendUnavailableError(FailureCode.S2_BACKEND_UNAVAILABLE, detail)


@dataclass(frozen=True, slots=True)
class AuditorReply:
    text: str
    mean_token_probability: float


class QwenAudioAuditor:
    """``S2AuditorPort`` implementation over a locally cached Qwen2-Audio."""

    def __init__(
        self,
        *,
        repository_root: str | Path,
        model_dir: str | Path | None = None,
        device: str = "cpu",
        dtype: str = "float32",
    ) -> None:
        root = Path(repository_root)
        if not root.is_absolute():
            raise ValueError("repository_root must be absolute")
        self._root = root
        self._model_dir = Path(model_dir) if model_dir is not None else None
        self._device = device
        self._dtype = dtype
        self._lock = threading.Lock()
        self._model: Any | None = None
        self._processor: Any | None = None
        self._revision: str | None = None

    # ------------------------------------------------------------- provenance

    @property
    def backend_id(self) -> str:
        return BACKEND_ID

    @property
    def revision(self) -> str:
        if self._revision is None:
            self._revision = self.resolve_model_dir().name
        return self._revision

    def resolve_model_dir(self) -> Path:
        if self._model_dir is not None:
            candidate = self._model_dir
            if not candidate.is_absolute():
                candidate = self._root / candidate
        else:
            snapshots = self._root / DEFAULT_MODEL_ROOT
            if not snapshots.is_dir():
                raise _fail("Qwen2-Audio snapshot is not present in the local cache")
            children = sorted(
                child for child in snapshots.iterdir() if child.is_dir()
            )
            if len(children) != 1:
                raise _fail(
                    "expected exactly one cached Qwen2-Audio snapshot, "
                    f"found {len(children)}"
                )
            candidate = children[0]
        if not (candidate / "config.json").is_file():
            raise _fail("Qwen2-Audio model directory has no config.json")
        return candidate

    # ------------------------------------------------------------------ model

    def _load(self) -> tuple[Any, Any]:
        with self._lock:
            if self._model is not None and self._processor is not None:
                return self._model, self._processor
            model_dir = self.resolve_model_dir()
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            try:
                import torch
                from transformers import (
                    AutoProcessor,
                    Qwen2AudioForConditionalGeneration,
                )
            except ImportError as exc:  # pragma: no cover - environment dependent
                raise _fail(f"S2 auditor needs torch and transformers: {exc}") from exc
            try:
                processor = AutoProcessor.from_pretrained(
                    str(model_dir), local_files_only=True
                )
                model = Qwen2AudioForConditionalGeneration.from_pretrained(
                    str(model_dir),
                    local_files_only=True,
                    dtype=getattr(torch, self._dtype),
                )
            except Exception as exc:  # pragma: no cover - load-time failure
                raise _fail(
                    f"Qwen2-Audio failed to load: {type(exc).__name__}"
                ) from exc
            model = model.to(self._device).eval()
            self._model, self._processor = model, processor
            return model, processor

    def _read_audio(self, audio_path: Path, sample_rate: int):
        import numpy as np
        import soundfile as sf

        try:
            samples, source_rate = sf.read(str(audio_path), dtype="float32")
        except Exception as exc:
            raise BackendUnavailableError(
                FailureCode.AUDIO_INVALID, f"recording could not be decoded: {exc}"
            ) from exc
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        if source_rate != sample_rate:
            from math import gcd

            from scipy.signal import resample_poly

            divisor = gcd(int(source_rate), int(sample_rate))
            samples = resample_poly(
                samples, sample_rate // divisor, int(source_rate) // divisor
            )
        return np.asarray(samples, dtype=np.float32)

    def transcribe(self, audio_path: Path) -> AuditorReply:
        """Ask the auditor what it hears, with the reply's own token confidence."""

        import torch

        model, processor = self._load()
        sample_rate = processor.feature_extractor.sampling_rate
        audio = self._read_audio(audio_path, sample_rate)
        conversation = [
            {
                "role": "user",
                "content": [
                    {"type": "audio", "audio_url": "attempt"},
                    {"type": "text", "text": TRANSCRIPTION_QUESTION},
                ],
            }
        ]
        text = processor.apply_chat_template(
            conversation, add_generation_prompt=True, tokenize=False
        )
        inputs = processor(
            text=text,
            audio=[audio],
            sampling_rate=sample_rate,
            return_tensors="pt",
            padding=True,
        ).to(self._device)
        with torch.no_grad():
            generated = model.generate(
                **inputs,
                max_new_tokens=MAXIMUM_REPLY_TOKENS,
                do_sample=False,
                output_scores=True,
                return_dict_in_generate=True,
            )
        prompt_length = inputs["input_ids"].shape[1]
        reply_ids = generated.sequences[:, prompt_length:]
        reply = processor.batch_decode(reply_ids, skip_special_tokens=True)[0].strip()

        # Confidence covers the answer, not the template that ends it.  An
        # end-of-sequence token is near-certain by construction, so counting it
        # would raise every confidence toward 1 by the same uninformative amount
        # — and raise short answers more than long ones.
        terminators = {
            token
            for token in (
                getattr(model.generation_config, "eos_token_id", None),
                getattr(processor.tokenizer, "eos_token_id", None),
                getattr(processor.tokenizer, "pad_token_id", None),
            )
            if isinstance(token, int)
        }
        probabilities: list[float] = []
        for step, scores in enumerate(generated.scores):
            if step >= reply_ids.shape[1]:
                break
            token_id = int(reply_ids[0, step])
            if token_id in terminators:
                break
            distribution = torch.softmax(scores[0].float(), dim=-1)
            probabilities.append(float(distribution[token_id]))
        mean_probability = (
            sum(probabilities) / len(probabilities) if probabilities else 0.0
        )
        return AuditorReply(text=reply, mean_token_probability=mean_probability)

    # --------------------------------------------------------------- the port

    def audit(
        self,
        *,
        audio_path: Path,
        prompt_spec: PromptSpec,
        s1_backend_ids: tuple[str, ...],
    ) -> S2AuditResult:
        receipt = build_independence_receipt(s1_backend_ids)
        if not receipt.independent:
            # Running the model would only produce a score the gate must
            # discard; report the receipt and stop.
            return S2AuditResult(
                verdict=AuditVerdict.NOT_RUN,
                backend_id=BACKEND_ID,
                revision=self.revision,
                content_score=None,
                confidence=None,
                independence=receipt,
            )
        reply = self.transcribe(Path(audio_path))
        agreement = jamo_similarity(reply.text, prompt_spec.normalized_text)
        if agreement >= AUDIT_AGREEMENT_APPROVE:
            verdict = AuditVerdict.APPROVED
        elif agreement <= AUDIT_AGREEMENT_REJECT:
            verdict = AuditVerdict.DISAGREEMENT
        else:
            verdict = AuditVerdict.UNCERTAIN
        return S2AuditResult(
            verdict=verdict,
            backend_id=BACKEND_ID,
            revision=self.revision,
            content_score=agreement,
            confidence=reply.mean_token_probability,
            independence=receipt,
        )


def build_independence_receipt(
    s1_backend_ids: tuple[str, ...], *, s2_backend_id: str = BACKEND_ID
) -> IndependenceReceipt:
    """State what S2 shares with S1, and refuse the claim when it shares weights."""

    if s2_backend_id in s1_backend_ids:
        return IndependenceReceipt(
            s1_backend_ids=tuple(s1_backend_ids),
            s2_backend_id=s2_backend_id,
            independent=False,
            reason="the auditor backend id is also an S1 backend id",
        )
    return IndependenceReceipt(
        s1_backend_ids=tuple(s1_backend_ids),
        s2_backend_id=s2_backend_id,
        independent=True,
        reason=(
            "S1 is a wav2vec2 SSL encoder with a Korean syllable CTC head; "
            "S2 is a Whisper-large-v3 encoder feeding an instruction-tuned "
            "Qwen2 language model. No weights, pretraining corpus, or training "
            "objective is shared."
        ),
    )
