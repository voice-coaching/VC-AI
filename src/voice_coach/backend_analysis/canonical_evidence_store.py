"""Private, version-pinned B2 evidence I/O; not a Backend verification receipt.

No default bucket, retention policy, credentials, or production wiring. A caller
must persist the returned version reference before acknowledging a result. This
adapter never deletes objects or enables callback-v4 admission.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from typing import Callable, Mapping
from uuid import UUID


MAX_ARTIFACT_BYTES = 16 * 1024 * 1024


class EvidenceStoreError(ValueError):
    """Only stable service codes; no SDK errors, keys, or user content."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise EvidenceStoreError(code)


@dataclass(frozen=True, slots=True)
class EvidenceStoreSettings:
    endpoint: str = field(repr=False)
    bucket: str = field(repr=False)
    prefix: str = field(repr=False)
    region: str
    # None is an explicitly configured INDEFINITE policy, never a missing value.
    retention_days: int | None
    deletion_grace_hours: int | None
    orphan_grace_hours: int | None

    def __post_init__(self):
        # No alternate URL, credentials in URL, or arbitrary callback endpoint.
        expected = f"https://s3.{self.region}.backblazeb2.com"
        _require(bool(re.fullmatch(r"[a-z]{2}-[a-z]+-[0-9]{3}", self.region))
                 and self.endpoint == expected, "EVIDENCE_ENDPOINT_INVALID")
        _require(bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{4,48}[A-Za-z0-9]", self.bucket)),
                 "EVIDENCE_BUCKET_INVALID")
        _require(len(self.prefix) <= 800 and bool(re.fullmatch(r"(?:[A-Za-z0-9_-]+/)+", self.prefix))
                 and self.prefix[0].isalnum(),
                 "EVIDENCE_PREFIX_INVALID")
        _require((self.retention_days is None or
                  type(self.retention_days) is int and 1 <= self.retention_days <= 3650)
                 and (self.deletion_grace_hours is None or
                      type(self.deletion_grace_hours) is int and 0 <= self.deletion_grace_hours <= 8760)
                 and (self.orphan_grace_hours is None or
                      type(self.orphan_grace_hours) is int and 1 <= self.orphan_grace_hours <= 8760),
                 "EVIDENCE_RETENTION_UNCONFIGURED")

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]):
        prefix = "AI_CANONICAL_EVIDENCE_"
        def duration(name: str) -> int | None:
            value = environment[prefix + name]
            return None if value == "INDEFINITE" else int(value)

        try:
            return cls(
                environment[prefix + "B2_ENDPOINT"], environment[prefix + "B2_BUCKET"],
                environment[prefix + "B2_PREFIX"], environment[prefix + "B2_REGION"],
                duration("RETENTION_DAYS"),
                duration("DELETION_GRACE_HOURS"),
                duration("ORPHAN_GRACE_HOURS"),
            )
        except (KeyError, TypeError, ValueError):
            raise EvidenceStoreError("EVIDENCE_SETTINGS_INVALID") from None


class PrivateB2EvidenceStore:
    """Use a dedicated limited application key, never the recordings client.

    The SDK client must have bounded timeouts, no automatic upload retries and a
    fixed HTTPS B2 endpoint. Object keys are derived, not accepted from clients.
    Upload verification here does not replace Backend receipt verification.
    """

    def __init__(self, client, settings: EvidenceStoreSettings):
        self._client = client
        self._settings = settings
        _require(getattr(getattr(client, "meta", None), "endpoint_url", None) == settings.endpoint,
                 "EVIDENCE_CLIENT_ENDPOINT_MISMATCH")

    def _key(self, kind: str, sha: str, analysis_id: int, execution_id: str) -> str:
        _require(type(analysis_id) is int and 0 < analysis_id <= 9007199254740991,
                 "EVIDENCE_IDENTITY_INVALID")
        try:
            canonical_execution = str(UUID(execution_id))
        except (ValueError, TypeError, AttributeError):
            raise EvidenceStoreError("EVIDENCE_IDENTITY_INVALID") from None
        _require(canonical_execution == execution_id, "EVIDENCE_IDENTITY_INVALID")
        _require(isinstance(kind, str) and bool(re.fullmatch(r"[A-Z][A-Z0-9_]{0,39}", kind)),
                 "EVIDENCE_KIND_INVALID")
        _require(isinstance(sha, str) and bool(re.fullmatch(r"[0-9a-f]{64}", sha)),
                 "EVIDENCE_DIGEST_INVALID")
        return f"{self._settings.prefix}{analysis_id}/{execution_id}/{kind}/{sha}.json"

    def close(self) -> None:
        self._client.close()

    def assert_private(self) -> None:
        try:
            acl = self._client.get_bucket_acl(Bucket=self._settings.bucket)
            owner = acl.get("Owner", {}).get("ID")
            grants = acl.get("Grants")
            _require(isinstance(owner, str) and bool(owner) and isinstance(grants, list)
                     and bool(grants), "EVIDENCE_BUCKET_NOT_PRIVATE")
            for grant in grants:
                grantee = grant.get("Grantee", {})
                _require(grantee.get("Type") == "CanonicalUser" and grantee.get("ID") == owner
                         and grant.get("Permission") == "FULL_CONTROL", "EVIDENCE_BUCKET_NOT_PRIVATE")
        except EvidenceStoreError:
            raise
        except Exception:
            raise EvidenceStoreError("EVIDENCE_STORAGE_UNAVAILABLE") from None

    def put(self, *, kind: str, schema_version: str, raw: bytes,
            analysis_id: int, execution_id: str,
            on_uploaded: Callable[[dict], None]) -> dict:
        _require(callable(on_uploaded), "EVIDENCE_JOURNAL_CALLBACK_REQUIRED")
        _require(type(raw) is bytes and 0 < len(raw) <= MAX_ARTIFACT_BYTES,
                 "EVIDENCE_SIZE_INVALID")
        _require(isinstance(schema_version, str)
                 and bool(re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,119}", schema_version)),
                 "EVIDENCE_SCHEMA_INVALID")
        sha = hashlib.sha256(raw).hexdigest()
        key = self._key(kind, sha, analysis_id, execution_id)
        self.assert_private()
        try:
            reply = self._client.put_object(
                Bucket=self._settings.bucket, Key=key, Body=raw, ContentLength=len(raw),
                ContentType="application/json", CacheControl="no-store", ACL="private",
                ServerSideEncryption="AES256", Metadata={"sha256": sha},
            )
            version = reply.get("VersionId")
            _require(isinstance(version, str) and 0 < len(version) <= 1024 and version != "null"
                     and not any(ord(c) < 32 or ord(c) == 127 for c in version),
                     "EVIDENCE_VERSION_MISSING")
            reference = dict(kind=kind, schemaVersion=schema_version, sha256=sha,
                             byteSize=len(raw), objectKey=key, versionId=version)
            # Persist the returned immutable version BEFORE any further network
            # call. If readback then fails, recovery reads that version, not PUT.
            on_uploaded(dict(reference))
            # Never use ETag as a SHA-256 or read back an unpinned 'latest' object.
            self.read_verified(reference, analysis_id=analysis_id, execution_id=execution_id)
            return reference
        except EvidenceStoreError:
            raise
        except Exception:
            # A lost upload response may leave an orphan version. Do not blindly
            # retry or delete it here. Reconciliation must respect the explicit
            # retention policy, including INDEFINITE (no automatic deletion).
            raise EvidenceStoreError("EVIDENCE_UPLOAD_UNCONFIRMED") from None

    def read_verified(self, reference: dict, *, analysis_id: int, execution_id: str) -> bytes:
        body = None
        try:
            _require(type(reference) is dict and set(reference) == {
                "kind", "schemaVersion", "sha256", "byteSize", "objectKey", "versionId"
            }, "EVIDENCE_REFERENCE_INVALID")
            expected_size = reference["byteSize"]
            schema = reference["schemaVersion"]
            _require(isinstance(schema, str)
                     and bool(re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,119}", schema)),
                     "EVIDENCE_SCHEMA_INVALID")
            _require(type(expected_size) is int and 0 < expected_size <= MAX_ARTIFACT_BYTES,
                     "EVIDENCE_SIZE_INVALID")
            key = self._key(reference["kind"], reference["sha256"], analysis_id, execution_id)
            _require(reference["objectKey"] == key, "EVIDENCE_CROSS_EXECUTION_REFERENCE")
            version = reference["versionId"]
            _require(isinstance(version, str) and 0 < len(version) <= 1024 and version != "null"
                     and not any(ord(c) < 32 or ord(c) == 127 for c in version),
                     "EVIDENCE_VERSION_MISSING")
            self.assert_private()
            reply = self._client.get_object(Bucket=self._settings.bucket, Key=key, VersionId=version)
            body = reply.get("Body")
            _require(reply.get("VersionId") == version and reply.get("ContentLength") == expected_size
                     and reply.get("ContentType") == "application/json"
                     and reply.get("ServerSideEncryption") == "AES256"
                     and body is not None, "EVIDENCE_OBJECT_METADATA_MISMATCH")
            chunks = bytearray()
            while True:
                chunk = body.read(min(65536, expected_size + 1 - len(chunks)))
                if not chunk:
                    break
                chunks.extend(chunk)
                _require(len(chunks) <= expected_size, "EVIDENCE_SIZE_MISMATCH")
            raw = bytes(chunks)
            _require(len(raw) == expected_size and hashlib.sha256(raw).hexdigest() == reference["sha256"],
                     "EVIDENCE_BYTES_MISMATCH")
            return raw
        except EvidenceStoreError:
            raise
        except Exception:
            raise EvidenceStoreError("EVIDENCE_STORAGE_UNAVAILABLE") from None
        finally:
            if body is not None:
                try:
                    body.close()
                except Exception:
                    pass


def evidence_store_from_environment(environment: Mapping[str, str]) -> PrivateB2EvidenceStore:
    """Construct only; caller must check private ACL and lifecycle readiness.

    Retention values are configuration, not an implemented deletion scheduler.
    B2 bucket lifecycle/object lock is never changed by this function.
    """
    settings = EvidenceStoreSettings.from_environment(environment)
    try:
        key_id = environment["AI_CANONICAL_EVIDENCE_B2_KEY_ID"]
        app_key = environment["AI_CANONICAL_EVIDENCE_B2_APPLICATION_KEY"]
        _require(bool(key_id) and bool(app_key), "EVIDENCE_CREDENTIALS_MISSING")
        import boto3
        from botocore.config import Config
        client = boto3.client(
            "s3", endpoint_url=settings.endpoint, region_name=settings.region,
            aws_access_key_id=key_id, aws_secret_access_key=app_key,
            config=Config(signature_version="s3v4", connect_timeout=3, read_timeout=15,
                          max_pool_connections=2,
                          retries={"max_attempts": 0}, proxies={},
                          s3={"addressing_style": "path"}),
        )
        return PrivateB2EvidenceStore(client, settings)
    except EvidenceStoreError:
        raise
    except Exception:
        raise EvidenceStoreError("EVIDENCE_CLIENT_UNAVAILABLE") from None
