"""Frame posteriors from a pinned Korean syllable-CTC acoustic model.

One model load serves both S1 stages.  Content alignment needs the posteriors to
locate syllables, and the S1 scorer needs the same posteriors to score them; a
second forward pass would double the cost and, worse, let the two stages drift
onto different frame grids so a span and the score attached to it would no
longer describe the same audio.  So the posteriors are computed once per
recording and cached by content digest.

The vocabulary is Korean *syllables*, not phones or characters, which is why
this backend can align a prompt directly: the units the model emits are the
units the prompt catalog already anchors feedback to.

The model is loaded from a local snapshot only.  A backend that could reach the
network would make the revision that produced a score unreproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any

import numpy as np

from ..domain import FailureCode
from ..errors import BackendUnavailableError

#: Pinned snapshot of ``kresnik/wav2vec2-large-xlsr-korean``.  Recorded as a
#: revision rather than a tag so a moved tag cannot change what was scored.
DEFAULT_MODEL_REVISION = "629c9a3501c10ba128bf3fa1eebb12af3be03f61"
DEFAULT_MODEL_ROOT = Path(
    "data/cache/huggingface/hub/models--kresnik--wav2vec2-large-xlsr-korean/snapshots"
)
BACKEND_ID = "wav2vec2-large-xlsr-korean-ctc"
TARGET_SAMPLE_RATE = 16_000
#: The word delimiter the model was trained with; a space in the prompt maps to
#: it, which lets the aligner spend frames on inter-word silence instead of
#: stretching the neighbouring syllable over it.
WORD_DELIMITER = "|"
PAD_TOKEN = "[PAD]"
UNKNOWN_TOKEN = "[UNK]"


def _fail(code: FailureCode, detail: str) -> BackendUnavailableError:
    return BackendUnavailableError(code, detail)


@dataclass(frozen=True, slots=True)
class CtcPosteriors:
    """Log-posteriors for one recording, with the grid they sit on."""

    log_posteriors: np.ndarray
    audio_sha256: str
    sample_rate: int
    duration_seconds: float
    #: Seconds between the starts of consecutive frames.
    hop_seconds: float
    #: Seconds each frame's receptive field spans.
    window_seconds: float

    def frame_start_ms(self, frame: int) -> int:
        return int(round(frame * self.hop_seconds * 1000.0))

    def frame_end_ms(self, frame: int) -> int:
        return int(round((frame * self.hop_seconds + self.window_seconds) * 1000.0))


class CtcAcousticBackend:
    """Loads the pinned CTC model once and answers posterior requests."""

    def __init__(
        self,
        *,
        repository_root: str | Path,
        model_dir: str | Path | None = None,
        device: str = "cpu",
        cache_size: int = 4,
    ) -> None:
        root = Path(repository_root)
        if not root.is_absolute():
            raise ValueError("repository_root must be absolute")
        self._root = root
        self._model_dir = Path(model_dir) if model_dir is not None else None
        self._device = device
        self._cache_size = max(1, cache_size)
        self._cache: dict[str, CtcPosteriors] = {}
        self._cache_order: list[str] = []
        self._lock = threading.Lock()
        self._model: Any | None = None
        self._vocabulary: dict[str, int] | None = None
        self._blank_id: int | None = None
        self._hop_seconds: float | None = None
        self._window_seconds: float | None = None

    # ------------------------------------------------------------- provenance

    @property
    def backend_id(self) -> str:
        return BACKEND_ID

    @property
    def revision(self) -> str:
        return DEFAULT_MODEL_REVISION

    def resolve_model_dir(self) -> Path:
        if self._model_dir is not None:
            candidate = self._model_dir
            if not candidate.is_absolute():
                candidate = self._root / candidate
            if not (candidate / "config.json").is_file():
                raise _fail(
                    FailureCode.CONTENT_BACKEND_UNAVAILABLE,
                    "configured CTC model directory has no config.json",
                )
            return candidate
        candidate = self._root / DEFAULT_MODEL_ROOT / DEFAULT_MODEL_REVISION
        if not (candidate / "config.json").is_file():
            raise _fail(
                FailureCode.CONTENT_BACKEND_UNAVAILABLE,
                "pinned Korean CTC snapshot is not present in the local cache",
            )
        return candidate

    # ------------------------------------------------------------ vocabulary

    def vocabulary(self) -> dict[str, int]:
        if self._vocabulary is None:
            path = self.resolve_model_dir() / "vocab.json"
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise _fail(
                    FailureCode.CONTENT_BACKEND_UNAVAILABLE,
                    f"CTC vocabulary unreadable: {exc}",
                ) from exc
            if not isinstance(record, dict) or PAD_TOKEN not in record:
                raise _fail(
                    FailureCode.CONTENT_BACKEND_UNAVAILABLE,
                    "CTC vocabulary is not a token->id map with a pad token",
                )
            self._vocabulary = {str(key): int(value) for key, value in record.items()}
            self._blank_id = self._vocabulary[PAD_TOKEN]
        return self._vocabulary

    @property
    def blank_id(self) -> int:
        self.vocabulary()
        assert self._blank_id is not None
        return self._blank_id

    def token_id(self, token: str) -> int | None:
        """Vocabulary id for one token, or ``None`` when it is not covered.

        ``None`` is a real answer, not an error: the vocabulary holds the 1,203
        syllables the model was trained on, and a prompt syllable outside it
        cannot be aligned or scored.  Callers report that syllable as unmeasured
        instead of substituting ``[UNK]``, whose posterior means "some syllable
        I do not know", not "this syllable".
        """

        return self.vocabulary().get(token)

    # ----------------------------------------------------------------- audio

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        model_dir = self.resolve_model_dir()
        # Keep the load offline: a reachable hub would let the effective
        # revision change without the pin changing.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        try:
            import torch
            from transformers import Wav2Vec2ForCTC
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise _fail(
                FailureCode.CONTENT_BACKEND_UNAVAILABLE,
                f"CTC backend needs torch and transformers: {exc}",
            ) from exc
        try:
            model = Wav2Vec2ForCTC.from_pretrained(str(model_dir), local_files_only=True)
        except Exception as exc:  # pragma: no cover - load-time failure
            raise _fail(
                FailureCode.CONTENT_BACKEND_UNAVAILABLE,
                f"pinned CTC model failed to load: {type(exc).__name__}",
            ) from exc
        model = model.to(self._device).eval()
        strides = getattr(model.config, "conv_stride", None)
        stride = 1
        for value in strides or ():
            stride *= int(value)
        self._hop_seconds = (stride or 320) / TARGET_SAMPLE_RATE
        #: The first conv reads 400 samples, so a frame's receptive field is
        #: 25 ms wide even though frames start every 20 ms.
        self._window_seconds = 400 / TARGET_SAMPLE_RATE
        self._model = model
        torch.set_grad_enabled(False)
        return model

    def _decode_audio(self, audio_path: Path) -> tuple[np.ndarray, str]:
        try:
            import soundfile as sf
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise _fail(
                FailureCode.CONTENT_BACKEND_UNAVAILABLE,
                f"CTC backend needs soundfile: {exc}",
            ) from exc
        try:
            samples, sample_rate = sf.read(str(audio_path), dtype="float32")
        except Exception as exc:
            raise _fail(
                FailureCode.AUDIO_INVALID, f"recording could not be decoded: {exc}"
            ) from exc
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        if sample_rate != TARGET_SAMPLE_RATE:
            from math import gcd

            from scipy.signal import resample_poly

            divisor = gcd(int(sample_rate), TARGET_SAMPLE_RATE)
            samples = resample_poly(
                samples, TARGET_SAMPLE_RATE // divisor, int(sample_rate) // divisor
            )
        digest = hashlib.sha256(
            np.ascontiguousarray(samples, dtype=np.float32).tobytes()
        ).hexdigest()
        return np.asarray(samples, dtype=np.float32), digest

    def posteriors(self, audio_path: str | Path) -> CtcPosteriors:
        """Log-posteriors for one recording, computed once and cached."""

        path = Path(audio_path)
        if not path.is_file():
            raise _fail(FailureCode.AUDIO_INVALID, "recording does not exist")
        samples, digest = self._decode_audio(path)
        with self._lock:
            cached = self._cache.get(digest)
            if cached is not None:
                return cached
        if samples.size < 400:
            raise _fail(
                FailureCode.AUDIO_INVALID,
                "recording is shorter than one acoustic frame",
            )

        import torch

        model = self._load_model()
        # Zero-mean unit-variance, matching the processor's do_normalize.
        normalized = samples - samples.mean()
        deviation = float(np.sqrt((normalized**2).mean()) + 1e-7)
        normalized = normalized / deviation
        with torch.no_grad():
            logits = model(
                torch.from_numpy(normalized[None, :]).to(self._device)
            ).logits[0]
            log_posteriors = torch.log_softmax(logits.float(), dim=-1).cpu().numpy()
        assert self._hop_seconds is not None and self._window_seconds is not None
        result = CtcPosteriors(
            log_posteriors=np.asarray(log_posteriors, dtype=np.float64),
            audio_sha256=digest,
            sample_rate=TARGET_SAMPLE_RATE,
            duration_seconds=float(samples.size) / TARGET_SAMPLE_RATE,
            hop_seconds=self._hop_seconds,
            window_seconds=self._window_seconds,
        )
        with self._lock:
            self._cache[digest] = result
            self._cache_order.append(digest)
            while len(self._cache_order) > self._cache_size:
                self._cache.pop(self._cache_order.pop(0), None)
        return result
