"""Inventory and verify a quiescent migration tree; never follow symlinks."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

EXCLUDED_NAMES = {".ssh", ".env", ".env.local", "analysis-http.env",
                  "analysis-worker.env", "id_ed25519", "id_rsa"}


def excluded(name: str) -> bool:
    return name in EXCLUDED_NAMES or name.endswith(".log")


def inventory(root: Path) -> dict:
    records = {}
    for parent, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(x for x in directories if not excluded(x))
        for name in sorted(directories + [x for x in files if not excluded(x)]):
            path = Path(parent) / name
            info = path.lstat()
            entry = {"mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}
            if stat.S_ISLNK(info.st_mode):
                entry.update(type="symlink", target=os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                entry.update(type="directory")
            elif stat.S_ISREG(info.st_mode):
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                        digest.update(block)
                after = path.lstat()
                if (info.st_size, info.st_mtime_ns, info.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                    raise RuntimeError("Source changed during inventory: " + str(path.relative_to(root)))
                entry.update(type="file", size=info.st_size, sha256=digest.hexdigest())
            else:
                raise RuntimeError("Unsupported entry: " + str(path.relative_to(root)))
            records[str(path.relative_to(root))] = entry
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "verify"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    output = args.manifest.resolve()
    if output.is_relative_to(root):
        raise SystemExit("Manifest must be outside the inventoried tree")
    records = inventory(root)
    summary = {"entries": len(records), "files": sum(x["type"] == "file" for x in records.values()),
               "bytes": sum(x.get("size", 0) for x in records.values())}
    if args.action == "build":
        payload = {"schemaVersion": "intelligentai.migration-tree.v1", "excludedNames": sorted(EXCLUDED_NAMES),
                   "excludedSuffix": ".log", "records": records, "summary": summary}
        output.parent.mkdir(parents=True, exist_ok=True)
        part = output.with_suffix(output.suffix + ".part")
        part.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        part.replace(output)
    else:
        expected = json.loads(output.read_text(encoding="utf-8"))
        if expected.get("schemaVersion") != "intelligentai.migration-tree.v1":
            raise SystemExit("Unsupported manifest")
        differences = [p for p in sorted(records.keys() | expected["records"].keys())
                       if records.get(p) != expected["records"].get(p)]
        if differences:
            print(json.dumps({"mismatchCount": len(differences), "firstPaths": differences[:20]}))
            raise SystemExit(1)
    summary.update(action=args.action, status="verified" if args.action == "verify" else "inventoried",
                   manifestSha256=hashlib.sha256(output.read_bytes()).hexdigest())
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
