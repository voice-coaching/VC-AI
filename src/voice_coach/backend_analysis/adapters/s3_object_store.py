"""Restricted, streaming materialisation from one private S3-compatible bucket."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import BinaryIO, Iterator, Mapping, Protocol
from urllib.parse import urlparse

from ..domain import AnalysisRequest
from ..ports import AnalysisAuthorization, BackendAnalysisError, MaterializedAudio


class _S3Client(Protocol):
    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class S3ObjectStoreSettings:
    bucket: str
    recordings_prefix: str
    workspace_root: Path
    maximum_audio_bytes: int = 20 * 1024 * 1024

    def __post_init__(self) -> None:
        forbidden_roots = {Path("/"), Path("/tmp"), Path.home()}
        if (
            not self.bucket
            or not self.recordings_prefix
            or self.recordings_prefix.startswith("/")
            or not self.recordings_prefix.endswith("/")
            or "\\" in self.recordings_prefix
            or any(
                segment in {"", ".", ".."}
                for segment in self.recordings_prefix.removesuffix("/").split("/")
            )
            or not self.workspace_root.is_absolute()
            or self.workspace_root in forbidden_roots
            or self.maximum_audio_bytes <= 0
        ):
            raise ValueError("analysis_object_storage_configuration_invalid")


class S3CompatibleRestrictedObjectStore:
    """Download one authorized object and erase the local copy on every exit."""

    def __init__(self, client: _S3Client, settings: S3ObjectStoreSettings) -> None:
        self._client = client
        self._settings = settings
        self._prepare_workspace_root()

    @contextmanager
    def materialize(
        self, request: AnalysisRequest, authorization: AnalysisAuthorization
    ) -> Iterator[MaterializedAudio]:
        del authorization
        self._require_owned_key(request.audio_object_key)
        if (
            request.file_size_bytes is not None
            and request.file_size_bytes > self._settings.maximum_audio_bytes
        ):
            raise BackendAnalysisError(
                "audio_metadata_mismatch",
                "등록된 녹음 메타데이터와 일치하지 않습니다.",
            )
        workspace = Path(tempfile.mkdtemp(prefix="analysis-", dir=self._settings.workspace_root))
        os.chmod(workspace, 0o700)
        destination = workspace / "source.audio"
        body: object | None = None
        try:
            response = self._client.get_object(
                Bucket=self._settings.bucket,
                Key=request.audio_object_key,
            )
            body = response.get("Body")
            if body is None or not callable(getattr(body, "read", None)):
                raise BackendAnalysisError(
                    "restricted_audio_unavailable",
                    "승인된 녹음에 접근할 수 없습니다.",
                )
            self._validate_response_metadata(response, request)
            byte_count, digest = self._stream_to_restricted_file(body, destination)
            if request.file_size_bytes is not None and byte_count != request.file_size_bytes:
                raise BackendAnalysisError(
                    "audio_metadata_mismatch",
                    "등록된 녹음 메타데이터와 일치하지 않습니다.",
                )
            if not hmac.compare_digest(digest, request.audio_sha256):
                raise BackendAnalysisError(
                    "audio_integrity_mismatch",
                    "등록된 녹음의 무결성을 확인할 수 없습니다.",
                )
            yield MaterializedAudio(
                path=destination,
                byte_count=byte_count,
                sha256=digest,
            )
        except BackendAnalysisError:
            raise
        except Exception:
            raise BackendAnalysisError(
                "restricted_audio_unavailable",
                "승인된 녹음에 접근할 수 없습니다.",
            ) from None
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            shutil.rmtree(workspace, ignore_errors=True)

    def _prepare_workspace_root(self) -> None:
        root = self._settings.workspace_root
        try:
            root.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError:
            raise ValueError("analysis_object_storage_configuration_invalid") from None
        try:
            metadata = os.lstat(root)
        except OSError:
            raise ValueError("analysis_object_storage_configuration_invalid") from None
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise ValueError("analysis_object_storage_configuration_invalid")

    def _require_owned_key(self, key: str) -> None:
        if (
            not key.startswith(self._settings.recordings_prefix)
            or key.startswith("/")
            or "\\" in key
            or any(segment in {"", ".", ".."} for segment in key.split("/"))
        ):
            raise BackendAnalysisError(
                "restricted_audio_unavailable",
                "승인된 녹음에 접근할 수 없습니다.",
            )

    def _validate_response_metadata(
        self, response: Mapping[str, object], request: AnalysisRequest
    ) -> None:
        content_length = response.get("ContentLength")
        if (
            isinstance(content_length, bool)
            or not isinstance(content_length, int)
            or content_length < 0
            or content_length > self._settings.maximum_audio_bytes
            or (
                request.file_size_bytes is not None
                and content_length != request.file_size_bytes
            )
        ):
            raise BackendAnalysisError(
                "audio_metadata_mismatch",
                "등록된 녹음 메타데이터와 일치하지 않습니다.",
            )
        content_type = response.get("ContentType")
        if request.mime_type is not None and (
            not isinstance(content_type, str)
            or content_type.partition(";")[0].strip().lower() != request.mime_type.lower()
        ):
            raise BackendAnalysisError(
                "audio_metadata_mismatch",
                "등록된 녹음 메타데이터와 일치하지 않습니다.",
            )

    def _stream_to_restricted_file(
        self, body: BinaryIO, destination: Path
    ) -> tuple[int, str]:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        digest = hashlib.sha256()
        total = 0
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                while True:
                    chunk = body.read(64 * 1024)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise BackendAnalysisError(
                            "restricted_audio_unavailable",
                            "승인된 녹음에 접근할 수 없습니다.",
                        )
                    total += len(chunk)
                    if total > self._settings.maximum_audio_bytes:
                        raise BackendAnalysisError(
                            "audio_metadata_mismatch",
                            "등록된 녹음 메타데이터와 일치하지 않습니다.",
                        )
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(destination, 0o400)
            return total, digest.hexdigest()
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise


def create_s3_client_from_environment(environment: Mapping[str, str] | None = None) -> object:
    """Create a boto3 client using the SDK credential chain and an HTTPS endpoint."""

    env = os.environ if environment is None else environment
    try:
        region = env["AI_ANALYSIS_OBJECT_STORAGE_REGION"]
        endpoint = env.get("AI_ANALYSIS_OBJECT_STORAGE_ENDPOINT") or None
        path_style = env.get("AI_ANALYSIS_OBJECT_STORAGE_PATH_STYLE", "false").lower() == "true"
        if not region:
            raise ValueError
        if endpoint:
            parsed = urlparse(endpoint)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if path_style else "virtual"},
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
    except (ImportError, KeyError, TypeError, ValueError):
        raise ValueError("analysis_object_storage_configuration_invalid") from None


__all__ = [
    "S3CompatibleRestrictedObjectStore",
    "S3ObjectStoreSettings",
    "create_s3_client_from_environment",
]
