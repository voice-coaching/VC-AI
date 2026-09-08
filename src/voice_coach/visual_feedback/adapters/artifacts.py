"""Versioned in-memory and JSON adapters for visual contract artifacts."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path

from ..errors import VisualArtifactUnavailableError, VisualFeedbackContractError
from ..models.artifacts import VisualCalibrationArtifact, VisualClaimCatalog
from ..models.integrity import require_sha256


class InMemoryVisualCalibrationAdapter:
    """Resolve already-validated calibration artifacts by capture contract."""

    def __init__(
        self,
        artifacts: Mapping[str, VisualCalibrationArtifact],
    ) -> None:
        self._artifacts = dict(artifacts)
        for capture_contract_id, artifact in self._artifacts.items():
            if capture_contract_id != artifact.capture_contract_id:
                raise VisualFeedbackContractError(
                    "calibration map key differs from capture contract"
                )

    def load(self, *, capture_contract_id: str) -> VisualCalibrationArtifact:
        try:
            return self._artifacts[capture_contract_id]
        except KeyError as error:
            raise VisualArtifactUnavailableError(
                "calibration artifact is unavailable"
            ) from error


class JsonVisualCalibrationAdapter:
    """Load a strict calibration document behind the filesystem boundary."""

    def __init__(
        self,
        paths: Mapping[str, Path],
        *,
        expected_artifact_sha256: Mapping[str, str] | None = None,
    ) -> None:
        self._paths = {key: Path(value) for key, value in paths.items()}
        self._expected = dict(expected_artifact_sha256 or {})
        for capture_contract_id, digest in self._expected.items():
            if capture_contract_id not in self._paths:
                raise VisualFeedbackContractError(
                    "expected calibration digest has no configured document"
                )
            require_sha256(digest, "expected_artifact_sha256")

    def load(self, *, capture_contract_id: str) -> VisualCalibrationArtifact:
        path = self._paths.get(capture_contract_id)
        if path is None:
            raise VisualArtifactUnavailableError(
                "calibration artifact is unavailable"
            )
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            artifact = VisualCalibrationArtifact.from_json_object(document)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            VisualFeedbackContractError,
        ) as error:
            raise VisualArtifactUnavailableError(
                "calibration artifact could not be validated"
            ) from error
        if artifact.capture_contract_id != capture_contract_id:
            raise VisualArtifactUnavailableError(
                "calibration capture contract differs"
            )
        expected = self._expected.get(capture_contract_id)
        if expected is not None and artifact.artifact_sha256 != expected:
            raise VisualArtifactUnavailableError(
                "calibration artifact does not match the configured digest"
            )
        return artifact


class InMemoryVisualClaimCatalogAdapter:
    """Return one already-validated claim catalog."""

    def __init__(self, catalog: VisualClaimCatalog) -> None:
        self._catalog = catalog

    def load(self) -> VisualClaimCatalog:
        return self._catalog


class JsonVisualClaimCatalogAdapter:
    """Load the strict allowlisted claim catalog from a configured document."""

    def __init__(
        self,
        path: Path,
        *,
        expected_catalog_sha256: str | None = None,
    ) -> None:
        self._path = Path(path)
        if expected_catalog_sha256 is not None:
            require_sha256(expected_catalog_sha256, "expected_catalog_sha256")
        self._expected_catalog_sha256 = expected_catalog_sha256

    def load(self) -> VisualClaimCatalog:
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
            catalog = VisualClaimCatalog.from_json_object(document)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            VisualFeedbackContractError,
        ) as error:
            raise VisualArtifactUnavailableError(
                "visual claim catalog could not be validated"
            ) from error
        if (
            self._expected_catalog_sha256 is not None
            and catalog.catalog_sha256 != self._expected_catalog_sha256
        ):
            raise VisualArtifactUnavailableError(
                "visual claim catalog does not match the configured digest"
            )
        return catalog


__all__ = [
    "InMemoryVisualCalibrationAdapter",
    "InMemoryVisualClaimCatalogAdapter",
    "JsonVisualCalibrationAdapter",
    "JsonVisualClaimCatalogAdapter",
]
