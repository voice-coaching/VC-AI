"""Read a deployment-pinned reference/calibration catalog, never request paths.

An empty catalog is valid for observation-only execution. It is not evidence
that a reference was extracted, reviewed, or independently calibrated.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .audiovisual_evidence import Calibration, LipReference
from .feature_protocol import MAPPING
from .reference_distance import DistancePolicy, Trajectory
from .parallel_analysis import MediaIntegrityFailure

SCHEMA = "voice-coaching.lip-reference-catalog.v1"
MAX_CATALOG_BYTES = 8 * 1024 * 1024


def require(ok):
    if not ok:
        raise MediaIntegrityFailure("REFERENCE_CATALOG_INVALID")


def sha(value):
    require(type(value) is str and re.fullmatch("[0-9a-f]{64}", value) is not None)
    return value


def text(value, limit=200):
    require(type(value) is str and 0 < len(value) <= limit)
    return value


@dataclass(frozen=True)
class ReferenceCatalog:
    sha256: str
    revision: str
    policy: DistancePolicy
    references: tuple[LipReference, ...]
    calibrations: tuple[Calibration, ...]
    minimum_coverage: float

    @classmethod
    def load(cls, path: Path, expected_sha256: str):
        sha(expected_sha256)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= MAX_CATALOG_BYTES)
            raw = stream.read(MAX_CATALOG_BYTES + 1)
        require(len(raw) == before.st_size and hashlib.sha256(raw).hexdigest() == expected_sha256)
        return cls.from_bytes(raw, expected_sha256)

    @classmethod
    def from_bytes(cls, raw: bytes, expected_sha256: str):
        """Also used by the offline verifier with the identical installed asset."""
        from .feature_protocol import strict_json
        require(0 < len(raw) <= MAX_CATALOG_BYTES and hashlib.sha256(raw).hexdigest() == sha(expected_sha256))
        data = strict_json(raw)
        require(type(data) is dict and set(data) == {"schemaVersion", "revision", "mappingRevision",
            "contextPolicy", "distancePolicy", "minimumCoverage", "references", "calibrations"})
        require(data["schemaVersion"] == SCHEMA and data["mappingRevision"] == MAPPING.revision
                and data["contextPolicy"] == "EXACT_WORD_AND_ROLE_V1")
        revision = text(data["revision"])
        policy = DistancePolicy(**data["distancePolicy"])
        policy.validate(3)
        require(type(data["minimumCoverage"]) in (int, float) and 0 < data["minimumCoverage"] <= 1)
        require(type(data["references"]) is list and len(data["references"]) <= 256)
        references = []
        for item in data["references"]:
            require(type(item) is dict and set(item) == {"referenceId", "videoSha256", "expectedPhone", "role",
                "context", "expertReviewId", "trajectory"})
            t = item["trajectory"]
            require(type(t) is dict and set(t) == {"times", "landmarks", "features"})
            trajectory = Trajectory(tuple(t["times"]), tuple(tuple(tuple(p) for p in row) for row in t["landmarks"]),
                                    tuple(tuple(row) for row in t["features"]))
            trajectory.validate(policy.max_frames)
            require(all(len(row) == len(MAPPING.lip_indices) for row in trajectory.landmarks)
                    and all(len(row) == 3 for row in trajectory.features))
            require(item["role"] in {"onset", "nucleus", "coda"})
            references.append(LipReference(text(item["referenceId"]), sha(item["videoSha256"]),
                text(item["expectedPhone"], 20), item["role"], text(item["context"]), MAPPING.revision,
                trajectory, text(item["expertReviewId"])))
        require(len({r.reference_id for r in references}) == len(references))
        require(type(data["calibrations"]) is list and len(data["calibrations"]) <= 256)
        calibrations = []
        for item in data["calibrations"]:
            require(type(item) is dict and set(item) == {"revision", "validationReceipt", "expectedPhone", "role",
                "context", "referenceIds", "tau", "knots"})
            ids = tuple(item["referenceIds"])
            require(ids and len(ids) == len(set(ids)) and len(ids) <= 32)
            matching = [r.reference_id for r in references if (r.expected_phone, r.role, r.context) ==
                        (item["expectedPhone"], item["role"], item["context"])]
            require(sorted(ids) == sorted(matching))
            calibration = Calibration(text(item["revision"]), text(item["validationReceipt"]),
                text(item["expectedPhone"], 20), text(item["role"]), text(item["context"]), MAPPING.revision,
                policy.revision, ids, item["tau"], tuple(tuple(k) for k in item["knots"]))
            # Validate the whole curve at load time; no lazy acceptance of a bad
            # calibration merely because this request never encounters its phone.
            from .reference_distance import calibrated_score, reference_similarity
            require((calibration.tau is None) != (not calibration.knots))
            if calibration.tau is not None:
                reference_similarity(0, tau=calibration.tau)
            else:
                require(2 <= len(calibration.knots) <= 128)
                calibrated_score(calibration.knots[0][0], knots=calibration.knots)
            calibrations.append(calibration)
        keys = [(c.expected_phone, c.role, c.context) for c in calibrations]
        require(len(keys) == len(set(keys)))
        return cls(expected_sha256, revision, policy, tuple(references), tuple(calibrations), data["minimumCoverage"])

    def calibration_for(self, anchor):
        return next((c for c in self.calibrations if (c.expected_phone, c.role, c.context) ==
                     (anchor.expected_phone, anchor.role, anchor.context)), None)
