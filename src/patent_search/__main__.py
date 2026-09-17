"""Commands for collecting, indexing, comparing, querying and serving a pilot."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import re
import sys
import ssl
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

from .collect import acquire_hupd, atomic_json, sha256_file


class ArxivAbstract(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.abstract = []
        self.inside = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("name") == "citation_title":
            self.title = attrs.get("content", "")
        if tag == "blockquote" and "abstract" in attrs.get("class", ""):
            self.inside = True

    def handle_endtag(self, tag):
        if tag == "blockquote":
            self.inside = False

    def handle_data(self, data):
        if self.inside:
            self.abstract.append(data)


def collect_papers(root: Path) -> list[dict]:
    import certifi
    papers = []
    for paper_id in ["1611.01599v2", "1611.05358v2"]:
        url = "https://arxiv.org/abs/"+paper_id
        with urlopen(Request(url, headers={"User-Agent": "patent-search-research/0.1"}),
                     context=ssl.create_default_context(cafile=certifi.where()), timeout=30) as response:
            raw = response.read(2*1024*1024+1)
        if len(raw) > 2*1024*1024:
            raise ValueError("PAPER_METADATA_TOO_LARGE")
        parser = ArxivAbstract()
        parser.feed(raw.decode("utf-8"))
        abstract = re.sub(r"^\s*Abstract:\s*", "", "".join(parser.abstract)).strip()
        if not parser.title or len(abstract) < 100:
            raise ValueError("PAPER_METADATA_UNAVAILABLE")
        papers.append({"id": paper_id, "title": parser.title, "text": abstract, "sourceUrl": url,
                       "sourceSha256": hashlib.sha256(raw).hexdigest(), "language": "en",
                       "inputKind": "public_paper_abstract", "relevanceLabels": None})
        time.sleep(3)  # respect arXiv request pacing
    atomic_json(root / "public-papers.json", papers)
    return papers


def compare(root: Path, aliases: list[str], papers_file: Path, device: str) -> dict:
    from .engine import Encoder, SearchEngine, build_index
    papers = json.loads(papers_file.read_text(encoding="utf-8"))
    report = {"kind": "actual_model_comparison_without_relevance_judgments", "models": {},
              "paperSources": [{k: p[k] for k in ("id", "title", "sourceUrl")} for p in papers],
              "qualityMetrics": {"precisionAt10": None, "recall": None, "ndcg": None},
              "qualityReason": "No human relevance labels; overlap is not accuracy or semantic quality.",
              "device": device}
    for alias in aliases:
        started = time.perf_counter()
        encoder = Encoder(alias, root / "model-cache", device)
        load_seconds = time.perf_counter()-started
        manifest = build_index(root, encoder)
        engine = SearchEngine(root, manifest["indexRevision"], encoder)
        rows = []
        for paper in papers:
            modes = {}
            for mode in ("lexical_only", "dense_only", "hybrid_semantic"):
                result = engine.search(paper["text"], mode=mode)
                modes[mode] = result
                print(json.dumps({"stage": "comparison", "model": alias, "paper": paper["id"],
                                  "mode": mode, "seconds": result["seconds"]}), flush=True)
            lexical = set(modes["lexical_only"]["candidateTrace"]["lexical"])
            dense = set(modes["dense_only"]["candidateTrace"]["dense"])
            rows.append({"paperId": paper["id"], "modes": modes,
                         "denseOnlyCandidateCount": len(dense-lexical),
                         "denseOnlyIsRelevanceJudgment": False,
                         "top10OverlapDenseLexical": len(set(x["publicationId"] for x in modes["dense_only"]["results"][:10]) &
                                                         set(x["publicationId"] for x in modes["lexical_only"]["results"][:10]))})
        report["models"][alias] = {"index": manifest, "loadSeconds": load_seconds, "queries": rows}
        atomic_json(root / "comparison.json", report)
        del engine, encoder
        gc.collect()
    atomic_json(root / "comparison.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("data/cache/patent-pilot"))
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect")
    collect.add_argument("--limit", type=int, default=512)
    commands.add_parser("collect-papers")
    for name in ("index", "search", "serve"):
        command = commands.add_parser(name)
        command.add_argument("--model", choices=["bge-m3", "e5-large"], default="bge-m3")
        command.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
        if name == "index":
            command.add_argument("--include-claims", action="store_true")
        else:
            command.add_argument("--index-revision", required=True)
        if name == "search":
            command.add_argument("--text-file", type=Path, required=True)
            command.add_argument("--rerank", action="store_true")
            command.add_argument("--mode", choices=["dense_only", "lexical_only", "hybrid_semantic"], default="hybrid_semantic")
        if name == "serve":
            command.add_argument("--port", type=int, default=8091)
    comparison = commands.add_parser("compare")
    comparison.add_argument("--models", nargs="+", choices=["bge-m3", "e5-large"], default=["bge-m3", "e5-large"])
    comparison.add_argument("--papers", type=Path)
    comparison.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command == "collect":
        result = acquire_hupd(root, args.limit)
    elif args.command == "collect-papers":
        papers = collect_papers(root)
        result = {"count": len(papers), "path": str(root / "public-papers.json")}
    elif args.command == "compare":
        result = compare(root, args.models, args.papers or root / "public-papers.json", args.device)
        result = {"modelsCompared": list(result["models"]), "report": str(root / "comparison.json"), "qualityMetrics": result["qualityMetrics"]}
    else:
        from .engine import Encoder, SearchEngine, build_index
        encoder = Encoder(args.model, root / "model-cache", args.device)
        if args.command == "index":
            result = build_index(root, encoder, args.include_claims)
        else:
            engine = SearchEngine(root, args.index_revision, encoder)
            if args.command == "search":
                result = engine.search(args.text_file.read_text(encoding="utf-8-sig"), mode=args.mode, rerank=args.rerank)
            else:
                import uvicorn
                from .api import create_app
                from .service import JobService
                token = os.environ.get("PATENT_API_TOKEN", "")
                if len(token) < 32:
                    raise ValueError("Set PATENT_API_TOKEN in this process; minimum 32 characters")
                app = create_app(JobService(root, engine), token)
                uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1, access_log=False)
                return
    print(json.dumps(result, ensure_ascii=True, indent=2), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Operational details never include manuscript text or authentication data.
        print(json.dumps({"error": type(error).__name__, "reason": "COMMAND_FAILED_CHECK_CONFIGURATION_OR_SOURCE"}), file=sys.stderr)
        raise SystemExit(1)
