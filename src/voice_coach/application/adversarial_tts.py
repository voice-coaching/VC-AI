"""Serving-neutral adapters for generated pronunciation-reference audio."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Sequence

import numpy as np
import torch

from voice_coach.models.adversarial_tts import (
    AdversarialPronunciationSystem,
    FeedbackTrajectory,
)


class GeneratedAudioError(ValueError):
    """Raised when generated audio cannot be safely materialized."""


@dataclass(frozen=True, slots=True)
class GeneratedAudioArtifact:
    schema_version: str
    media_label: str
    relative_name: str
    sample_rate_hz: int
    sample_count: int
    sha256: str


class AdversarialTTSEngine:
    """Default application adapter for the source-conditioned TTS prototype.

    The engine owns inference and model-only checkpoint I/O. Corpus paths,
    prompt text, speaker identifiers, and evaluator labels stay outside this
    serving boundary. Checkpoints use safetensors and a small JSON sidecar so
    a saved model can be reloaded without executing an arbitrary pickle.
    """

    CHECKPOINT_SCHEMA = "voice_coach.adversarial_tts_checkpoint.v1"

    def __init__(
        self,
        system: AdversarialPronunciationSystem,
        config: Mapping[str, Any],
        *,
        device: str | torch.device = "cpu",
    ) -> None:
        self.system = system.to(device)
        self.config = config
        self.device = torch.device(device)

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        device: str | torch.device = "cpu",
        optimizers: Mapping[str, torch.optim.Optimizer] | None = None,
    ) -> "AdversarialTTSEngine":
        from voice_coach.training.adversarial_tts import build_adversarial_system

        return cls(build_adversarial_system(config), config, device=device)

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint_path: str | Path,
        config: Mapping[str, Any],
        *,
        device: str | torch.device = "cpu",
        optimizers: Mapping[str, torch.optim.Optimizer] | None = None,
    ) -> "AdversarialTTSEngine":
        from safetensors.torch import load_file  # type: ignore[import-untyped]

        checkpoint = Path(checkpoint_path)
        metadata_path = checkpoint.with_suffix(checkpoint.suffix + ".json")
        if checkpoint.suffix != ".safetensors":
            raise GeneratedAudioError("TTS checkpoint must end with .safetensors")
        if not checkpoint.is_file() or not metadata_path.is_file():
            raise GeneratedAudioError("TTS checkpoint and metadata sidecar are required")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GeneratedAudioError("TTS checkpoint metadata is unreadable") from exc
        if metadata.get("schema_version") != cls.CHECKPOINT_SCHEMA:
            raise GeneratedAudioError("unsupported TTS checkpoint schema")
        if metadata.get("config_sha256") != cls._config_hash(config):
            raise GeneratedAudioError("TTS checkpoint configuration does not match")
        engine = cls.from_config(config, device=device)
        try:
            state = load_file(str(checkpoint), device=str(engine.device))
            engine.system.load_state_dict(state, strict=True)
        except (RuntimeError, OSError, ValueError) as exc:
            raise GeneratedAudioError("TTS checkpoint weights are invalid") from exc
        if optimizers is not None:
            engine.load_optimizer_checkpoint(checkpoint, optimizers)
        return engine

    @staticmethod
    def _config_hash(config: Mapping[str, Any]) -> str:
        serialized = json.dumps(
            config,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()

    def generate(
        self,
        learner_waveform: torch.Tensor,
        learner_text_tokens: torch.Tensor,
        native_reference_waveform: torch.Tensor,
        *,
        learner_mask: torch.Tensor | None = None,
        native_reference_mask: torch.Tensor | None = None,
    ) -> FeedbackTrajectory:
        """Generate a bounded draft/refined pronunciation trajectory."""

        self.system.eval()
        with torch.inference_mode():
            return self.system(
                learner_waveform.to(self.device),
                learner_text_tokens.to(self.device),
                native_reference_waveform.to(self.device),
                source_mask=(
                    learner_mask.to(self.device) if learner_mask is not None else None
                ),
                native_reference_mask=(
                    native_reference_mask.to(self.device)
                    if native_reference_mask is not None
                    else None
                ),
            )

    @staticmethod
    def _optimizer_checkpoint_path(checkpoint: Path) -> Path:
        return checkpoint.with_suffix(".optim.safetensors")

    @staticmethod
    def _json_safe(value: object) -> object:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, tuple):
            return [AdversarialTTSEngine._json_safe(item) for item in value]
        if isinstance(value, list):
            return [AdversarialTTSEngine._json_safe(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): AdversarialTTSEngine._json_safe(item)
                for key, item in value.items()
            }
        raise GeneratedAudioError("optimizer metadata contains an unsupported value")

    @staticmethod
    def _serialize_optimizer_states(
        optimizers: Mapping[str, torch.optim.Optimizer],
    ) -> tuple[dict[str, torch.Tensor], dict[str, object]]:
        tensors: dict[str, torch.Tensor] = {}
        roles: dict[str, object] = {}
        for role, optimizer in sorted(optimizers.items()):
            if not role or not role.replace("_", "").isalnum():
                raise GeneratedAudioError("optimizer role names must be simple identifiers")
            state = optimizer.state_dict()
            parameter_ordinals: list[int] = []
            ordinal = 0
            for group in state["param_groups"]:
                for parameter_id in group["params"]:
                    parameter_ordinals.append(int(parameter_id))
                    slots = state["state"].get(parameter_id, {})
                    slot_names: list[str] = []
                    for slot_name, value in slots.items():
                        key = f"{role}__p{ordinal}__{slot_name}"
                        if isinstance(value, torch.Tensor):
                            tensors[key] = value.detach().to("cpu").contiguous()
                        elif isinstance(value, (int, float)) and not isinstance(value, bool):
                            tensors[key] = torch.tensor(float(value), dtype=torch.float64)
                        else:
                            raise GeneratedAudioError(
                                "optimizer state contains an unsupported value"
                            )
                        slot_names.append(str(slot_name))
                    ordinal += 1
            roles[role] = {
                "parameter_count": len(parameter_ordinals),
                "state_parameter_ids": parameter_ordinals,
                "parameter_groups": [
                    {
                        key: AdversarialTTSEngine._json_safe(value)
                        for key, value in group.items()
                        if key != "params"
                    }
                    for group in state["param_groups"]
                ],
            }
        return tensors, roles

    def load_optimizer_checkpoint(
        self,
        checkpoint_path: str | Path,
        optimizers: Mapping[str, torch.optim.Optimizer],
    ) -> dict[str, object]:
        """Restore optimizer slots from the sibling safetensors file."""

        from safetensors.torch import load_file  # type: ignore[import-untyped]

        checkpoint = Path(checkpoint_path)
        metadata_path = checkpoint.with_suffix(checkpoint.suffix + ".json")
        optimizer_path = self._optimizer_checkpoint_path(checkpoint)
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            optimizer_roles = metadata["optimizer_roles"]
            state = load_file(str(optimizer_path), device=str(self.device))
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise GeneratedAudioError("optimizer checkpoint is incomplete") from exc
        if not isinstance(optimizer_roles, dict):
            raise GeneratedAudioError("optimizer checkpoint roles are invalid")
        for role, optimizer in optimizers.items():
            role_meta = optimizer_roles.get(role)
            if not isinstance(role_meta, dict):
                raise GeneratedAudioError(f"optimizer role is missing: {role}")
            saved_ids = role_meta.get("state_parameter_ids")
            if not isinstance(saved_ids, list):
                raise GeneratedAudioError(f"optimizer parameter map is invalid: {role}")
            current = optimizer.state_dict()
            current_ids = [
                int(parameter_id)
                for group in current["param_groups"]
                for parameter_id in group["params"]
            ]
            if len(current_ids) != len(saved_ids):
                raise GeneratedAudioError(f"optimizer parameter count differs: {role}")
            restored_state: dict[int, dict[str, torch.Tensor]] = {}
            for ordinal, current_id in enumerate(current_ids):
                prefix = f"{role}__p{ordinal}__"
                slots = {
                    key[len(prefix):]: value
                    for key, value in state.items()
                    if key.startswith(prefix)
                }
                if slots:
                    restored_state[current_id] = slots
            current["state"] = restored_state
            optimizer.load_state_dict(current)
        return {
            "optimizer_roles": sorted(optimizers),
            "optimizer_state_path": optimizer_path.name,
        }

    def save_checkpoint(
        self,
        checkpoint_path: str | Path,
        *,
        optimizer_step: int,
        optimizers: Mapping[str, torch.optim.Optimizer] | None = None,
        epoch: int = 0,
        batch_index: int = 0,
        metric_name: str | None = None,
        metric_value: float | None = None,
        best_metric_value: float | None = None,
        best_checkpoint_name: str | None = None,
        is_best: bool = False,
    ) -> dict[str, object]:
        """Publish a model-only, no-overwrite safetensors checkpoint."""

        from safetensors.torch import save_file  # type: ignore[import-untyped]

        if (
            isinstance(optimizer_step, bool)
            or not isinstance(optimizer_step, int)
            or optimizer_step < 0
        ):
            raise GeneratedAudioError("optimizer_step must be a non-negative integer")
        checkpoint = Path(checkpoint_path)
        if checkpoint.suffix != ".safetensors":
            raise GeneratedAudioError("TTS checkpoint must end with .safetensors")
        metadata_path = checkpoint.with_suffix(checkpoint.suffix + ".json")
        optimizer_path = self._optimizer_checkpoint_path(checkpoint)
        if checkpoint.exists() or metadata_path.exists() or (
            optimizers is not None and optimizer_path.exists()
        ):
            raise GeneratedAudioError("TTS checkpoint output already exists")
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        tensors = {
            name: value.detach().to(device="cpu").contiguous()
            for name, value in self.system.state_dict().items()
        }
        optimizer_tensors: dict[str, torch.Tensor] = {}
        optimizer_roles: dict[str, object] = {}
        if optimizers is not None:
            optimizer_tensors, optimizer_roles = self._serialize_optimizer_states(optimizers)
        metadata = {
            "schema_version": self.CHECKPOINT_SCHEMA,
            "optimizer_step": optimizer_step,
            "epoch": epoch,
            "batch_index": batch_index,
            "metric_name": metric_name,
            "metric_value": metric_value,
            "best_metric_value": best_metric_value,
            "best_checkpoint_name": best_checkpoint_name,
            "is_best": bool(is_best),
            "config_sha256": self._config_hash(self.config),
            "model_class": type(self.system).__name__,
            "optimizer_roles": optimizer_roles,
            "optimizer_state_name": optimizer_path.name if optimizers is not None else None,
        }
        temporary: Path | None = None
        metadata_temporary: Path | None = None
        optimizer_temporary: Path | None = None
        published_checkpoint = False
        published_optimizer = False
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{checkpoint.name}.",
                suffix=".part",
                dir=checkpoint.parent,
            )
            os.close(descriptor)
            temporary = Path(temporary_name)
            save_file(
                tensors,
                str(temporary),
                metadata={
                    "schema_version": self.CHECKPOINT_SCHEMA,
                    "optimizer_step": str(optimizer_step),
                },
            )
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            _publish_no_overwrite(temporary, checkpoint)
            published_checkpoint = True
            if optimizers is not None:
                descriptor, optimizer_name = tempfile.mkstemp(
                    prefix=f".{optimizer_path.name}.",
                    suffix=".part",
                    dir=optimizer_path.parent,
                )
                os.close(descriptor)
                optimizer_temporary = Path(optimizer_name)
                from safetensors.torch import save_file  # type: ignore[import-untyped]

                save_file(optimizer_tensors, str(optimizer_temporary))
                with optimizer_temporary.open("rb") as stream:
                    os.fsync(stream.fileno())
                _publish_no_overwrite(optimizer_temporary, optimizer_path)
                published_optimizer = True

            descriptor, metadata_name = tempfile.mkstemp(
                prefix=f".{metadata_path.name}.",
                suffix=".part",
                dir=metadata_path.parent,
            )
            metadata_temporary = Path(metadata_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(metadata, stream, ensure_ascii=False, sort_keys=True, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            _publish_no_overwrite(metadata_temporary, metadata_path)
            return {
                "schema_version": self.CHECKPOINT_SCHEMA,
                "relative_name": checkpoint.name,
                "metadata_name": metadata_path.name,
                "optimizer_step": optimizer_step,
                "sha256": _sha256_file(checkpoint),
                "optimizer_state_name": optimizer_path.name if optimizers is not None else None,
                "epoch": epoch,
                "batch_index": batch_index,
                "metric_name": metric_name,
                "metric_value": metric_value,
                "best_metric_value": best_metric_value,
                "best_checkpoint_name": best_checkpoint_name,
                "is_best": bool(is_best),
            }
        except Exception:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if metadata_temporary is not None:
                metadata_temporary.unlink(missing_ok=True)
            if optimizer_temporary is not None:
                optimizer_temporary.unlink(missing_ok=True)
            if published_optimizer and optimizer_path.exists():
                optimizer_path.unlink()
            if published_checkpoint and checkpoint.exists() and not metadata_path.exists():
                checkpoint.unlink()
            raise


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _publish_no_overwrite(temporary: Path, destination: Path) -> None:
    """Atomically publish a same-filesystem temporary without replacement."""

    os.link(temporary, destination)
    temporary.unlink()


def write_generated_waveform(
    path: str | Path,
    waveform: torch.Tensor,
    *,
    sample_rate_hz: int = 16_000,
) -> GeneratedAudioArtifact:
    """Atomically write one generated WAV plus a mandatory provenance sidecar.

    The sidecar intentionally excludes source paths, prompt text, speaker IDs,
    and evaluator feedback.  Existing files are never overwritten.
    """

    try:
        import soundfile as sf  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise GeneratedAudioError(
            "soundfile is required to materialize generated audio"
        ) from exc
    output = Path(path)
    if output.suffix.lower() != ".wav":
        raise GeneratedAudioError("generated audio path must end in .wav")
    if (
        isinstance(sample_rate_hz, bool)
        or not isinstance(sample_rate_hz, int)
        or sample_rate_hz <= 0
    ):
        raise GeneratedAudioError("sample_rate_hz must be a positive integer")
    if not isinstance(waveform, torch.Tensor):
        raise GeneratedAudioError("waveform must be a tensor")
    if waveform.ndim == 2 and waveform.shape[0] == 1:
        waveform = waveform.squeeze(0)
    if (
        waveform.ndim != 1
        or waveform.numel() < 16
        or not torch.is_floating_point(waveform)
        or not torch.isfinite(waveform).all()
    ):
        raise GeneratedAudioError(
            "waveform must be one finite floating-point audio item"
        )
    if float(waveform.detach().abs().max()) > 1:
        raise GeneratedAudioError("waveform must be normalized to [-1, 1]")
    sidecar = output.with_suffix(output.suffix + ".generated.json")
    if output.exists() or sidecar.exists():
        raise GeneratedAudioError("generated audio output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    samples = np.ascontiguousarray(
        waveform.detach().to(device="cpu", dtype=torch.float32).numpy()
    )
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".part",
        dir=output.parent,
    )
    os.close(file_descriptor)
    temporary = Path(temporary_name)
    sidecar_temporary: Path | None = None
    published_audio = False
    try:
        sf.write(
            temporary,
            samples,
            sample_rate_hz,
            format="WAV",
            subtype="PCM_16",
        )
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        _publish_no_overwrite(temporary, output)
        published_audio = True
        artifact = GeneratedAudioArtifact(
            schema_version="voice_coach.generated_audio.v1",
            media_label="AI-generated pronunciation reference",
            relative_name=output.name,
            sample_rate_hz=sample_rate_hz,
            sample_count=int(samples.size),
            sha256=_sha256_file(output),
        )
        sidecar_descriptor, sidecar_name = tempfile.mkstemp(
            prefix=f".{sidecar.name}.",
            suffix=".part",
            dir=sidecar.parent,
        )
        sidecar_temporary = Path(sidecar_name)
        serialized = (
            json.dumps(
                asdict(artifact),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
        with os.fdopen(
            sidecar_descriptor,
            "w",
            encoding="utf-8",
        ) as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        with sidecar_temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        _publish_no_overwrite(sidecar_temporary, sidecar)
        return artifact
    except Exception:
        temporary.unlink(missing_ok=True)
        if sidecar_temporary is not None:
            sidecar_temporary.unlink(missing_ok=True)
        if published_audio and output.exists() and not sidecar.exists():
            output.unlink()
        raise
