"""Pinned public research corpus acquisition; never executes dataset scripts."""
from __future__ import annotations

import hashlib
import heapq
import json
import re
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

HUPD_REVISION = "f570a84b03663180b6034c1f7f4c15864f94385e"
HUPD_URL = f"https://huggingface.co/datasets/HUPD/hupd/resolve/{HUPD_REVISION}/data/sample-jan-2016.tar.gz"
HUPD_SHA256 = "c8db7a2e730f04a95946847d39a74b4a9ea53b0f4fd357bb8789abd77684e37f"
HUPD_BYTES = 387636489


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def atomic_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    part.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    part.replace(path)


def acquire_hupd(root: Path, limit: int = 512) -> dict:
    """Select smallest SHA256(application ID) values, independent of queries.

    HUPD's card has conflicting BY-SA metadata and NC-SA prose. This pilot
    conservatively marks all derived data research-only; no commercial release.
    Local files are rebuildable temporary working copies, not permanent storage.
    """
    if not 1 <= limit <= 10000:
        raise ValueError("limit must be 1..10000")
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "sample-jan-2016.tar.gz"
    if not archive.exists() or sha256_file(archive) != HUPD_SHA256:
        part = archive.with_suffix(".part")
        received = 0
        request = Request(HUPD_URL, headers={"User-Agent": "patent-search-research/0.1"})
        with urlopen(request, timeout=60) as response, part.open("wb") as out:
            while block := response.read(1024 * 1024):
                received += len(block)
                if received > HUPD_BYTES:
                    raise ValueError("source exceeds pinned size")
                out.write(block)
        if received != HUPD_BYTES or sha256_file(part) != HUPD_SHA256:
            raise ValueError("source integrity mismatch")
        part.replace(archive)
    selected: list = []
    seen = set()
    scanned = rejected = 0
    with tarfile.open(archive, "r|gz") as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith(".json"):
                continue
            scanned += 1
            if member.size > 16 * 1024 * 1024:
                rejected += 1
                continue
            with tar.extractfile(member) as stream:
                record = json.load(stream)
            app = str(record.get("application_number", ""))
            pub_raw = str(record.get("publication_number", "")).upper()
            # HUPD appends '-YYYYMMDD' to the actual publication identifier.
            pub = re.sub(r"-\d{8}$", "", pub_raw)
            pub = re.sub(r"[^A-Za-z0-9]", "", pub)
            if not pub.startswith("US") and re.fullmatch(r"\d{11}[A-Z]\d", pub):
                pub = "US" + pub
            if not re.fullmatch(r"US\d{11}[A-Z]\d", pub):
                rejected += 1
                continue
            if pub in seen:
                continue
            seen.add(pub)
            title, abstract = record.get("title"), record.get("abstract")
            if not isinstance(title, str) or not isinstance(abstract, str) or not abstract.strip():
                rejected += 1
                continue
            date_raw = str(record.get("date_published", ""))
            date_digits = re.sub(r"\D", "", date_raw)
            date = None
            if len(date_digits) == 8:
                try:
                    date = datetime.strptime(date_digits, "%Y%m%d").date().isoformat()
                except ValueError:
                    pass
            item = {
                "id": pub, "publicationNumberRaw": pub_raw, "applicationId": app, "country": "US", "language": "en",
                "title": title, "abstract": abstract, "claims": record.get("claims") or "",
                "publicationDate": date, "publicationDateRaw": date_raw,
                "classifications": record.get("cpc_labels") or [],
                "sourceUrl": f"https://patents.google.com/patent/{pub}/en",
                "sourceMember": member.name, "sourceRevision": HUPD_REVISION,
            }
            item["textSha256"] = hashlib.sha256((title + "\n" + abstract + "\n" + str(item["claims"])).encode()).hexdigest()
            key = int(hashlib.sha256(app.encode()).hexdigest(), 16)
            entry = (-key, pub, item)
            if len(selected) < limit:
                heapq.heappush(selected, entry)
            elif entry[:2] > selected[0][:2]:
                heapq.heapreplace(selected, entry)
    records = sorted((x[2] for x in selected), key=lambda x: x["id"])
    if not records:
        raise ValueError("no valid publications; inspect source schema before continuing")
    path = root / "corpus.jsonl"
    part = path.with_suffix(".part")
    with part.open("w", encoding="utf-8", newline="\n") as out:
        for record in records:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
    part.replace(path)
    manifest = {
        "schemaVersion": "patent-search.corpus.v1", "source": "HUPD/hupd",
        "sourceUrl": HUPD_URL, "sourceRevision": HUPD_REVISION,
        "sourceSha256": HUPD_SHA256, "sourceBytes": HUPD_BYTES,
        "corpusSha256": sha256_file(path), "documentCount": len(records),
        "scannedRecords": scanned, "rejectedRecords": rejected,
        "selection": "lowest SHA256(application_number), not query/keyword selected",
        "scope": "US English applications from HUPD January 2016 filing subset; publication dates vary",
        "countries": ["US"], "languages": ["en"], "koreanCoverage": False,
        "license": "research-only: card metadata CC-BY-SA-4.0 conflicts with CC-BY-NC-SA-4.0 prose",
        "licenseUrl": "https://huggingface.co/datasets/HUPD/hupd#licensing-information",
        "attribution": "Suzgun, Melas-Kyriazi, Sarkar, Kominers and Shieber (2022), Harvard USPTO Patent Dataset, arXiv:2207.04043",
        "permanentStorage": "not_configured", "localStorage": "temporary_rebuildable_cache",
        "collectedAt": datetime.now(timezone.utc).isoformat(),
    }
    atomic_json(root / "corpus-manifest.json", manifest)
    return manifest
