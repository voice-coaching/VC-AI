"""Exact cosine + BM25 retrieval on an immutable, explicit corpus snapshot.

This is the small-corpus backend. Production ANN/OpenSearch is a separate
adapter, not silently simulated here. No model score is a relevance verdict.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Callable

import numpy as np
from rank_bm25 import BM25Okapi

from .collect import atomic_json, sha256_file

MODEL_SPECS = {
    "bge-m3": ("BAAI/bge-m3", "5617a9f61b028005a4858fdac845db406aefb181", "", ""),
    "e5-large": ("intfloat/multilingual-e5-large", "3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3", "query: ", "passage: "),
}
RERANKER = ("BAAI/bge-reranker-v2-m3", "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e")
CONFIG_REVISION = "exact-pilot.v1"


def lexical_tokens(text: str) -> list[str]:
    # Deterministic baseline. Hangul bigrams supplement whole Hangul tokens;
    # this is explicitly not a production Korean morphological analyzer.
    words = re.findall(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*|[가-힣]+", text.lower())
    out = list(words)
    for word in words:
        if re.fullmatch(r"[가-힣]{3,}", word):
            out.extend(word[i:i+2] for i in range(len(word)-1))
    return out or ["__empty__"]


class Encoder:
    def __init__(self, alias: str, cache: Path, device: str = "cpu"):
        from sentence_transformers import SentenceTransformer
        import torch
        if alias not in MODEL_SPECS:
            raise ValueError("unknown encoder")
        self.alias = alias
        self.device = device
        self.model_id, self.revision, self.qprefix, self.dprefix = MODEL_SPECS[alias]
        torch.set_num_threads(4)
        self.model = SentenceTransformer(
            self.model_id, revision=self.revision, cache_folder=str(cache),
            device=device, trust_remote_code=False, model_kwargs={"weights_only": True},
        )
        self.model.max_seq_length = 512
        self.tokenizer = self.model.tokenizer

    def encode(self, texts: list[str], query: bool = False, batch_size: int = 4) -> np.ndarray:
        prefix = self.qprefix if query else self.dprefix
        inputs = [prefix + x for x in texts]
        if any(len(self.tokenizer.encode(x, add_special_tokens=True)) > 512 for x in inputs):
            raise ValueError("ENCODER_INPUT_TOO_LONG")
        result = self.model.encode(inputs, batch_size=min(batch_size, 2) if self.device == "cuda" else batch_size, normalize_embeddings=True,
                                   show_progress_bar=False, convert_to_numpy=True)
        result = np.asarray(result, dtype=np.float32)
        if not np.isfinite(result).all() or np.any(np.linalg.norm(result, axis=1) < 0.99):
            raise ValueError("INVALID_EMBEDDING")
        return result

    def windows(self, text: str, limit: int = 400, overlap: int = 40) -> list[dict]:
        tokens = self.tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
        offsets = tokens["offset_mapping"]
        if not offsets:
            return []
        result = []
        for start in range(0, len(offsets), limit-overlap):
            part = offsets[start:start+limit]
            left, right = part[0][0], part[-1][1]
            result.append({"start": left, "end": right, "text": text[left:right]})
            if start+limit >= len(offsets):
                break
        return result


def load_corpus(root: Path) -> tuple[list[dict], dict]:
    manifest = json.loads((root / "corpus-manifest.json").read_text(encoding="utf-8"))
    if sha256_file(root / "corpus.jsonl") != manifest["corpusSha256"]:
        raise ValueError("CORPUS_DIGEST_MISMATCH")
    records = [json.loads(x) for x in (root / "corpus.jsonl").read_text(encoding="utf-8").splitlines()]
    if len(records) != manifest["documentCount"] or len({x["id"] for x in records}) != len(records):
        raise ValueError("CORPUS_COUNT_OR_ID_MISMATCH")
    return records, manifest


def build_index(root: Path, encoder: Encoder, include_claims: bool = False) -> dict:
    records, corpus = load_corpus(root)
    spec = {"corpus": corpus["corpusSha256"], "model": encoder.model_id,
            "revision": encoder.revision, "chunkTokens": 400, "overlap": 40,
            "claims": include_claims, "config": CONFIG_REVISION}
    revision = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
    folder = root / "indexes" / revision
    if (folder / "manifest.json").exists():
        # Opening the index below validates arrays/chunks; do not trust existence alone.
        return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    folder.mkdir(parents=True, exist_ok=True)
    chunks = []
    for doc in records:
        fields = [("overview", doc["title"] + "\n" + doc["abstract"]), ("abstract", doc["abstract"])]
        if include_claims:
            fields.append(("claims", str(doc["claims"])))
        for field, text in fields:
            windows = encoder.windows(text)
            for number, window in enumerate(windows):
                # All overview windows kept: title+abstract is not silently truncated.
                chunks.append({"id": f'{doc["id"]}:{field}:{number}', "publicationId": doc["id"],
                               "field": field, "parentField": field, **window})
    if not chunks:
        raise ValueError("EMPTY_INDEX")
    started = time.perf_counter()
    batches = []
    for offset in range(0, len(chunks), 32):
        batches.append(encoder.encode([x["text"] for x in chunks[offset:offset+32]]))
        print(json.dumps({"stage": "embedding", "model": encoder.alias,
                          "done": min(offset+32, len(chunks)), "total": len(chunks)}), flush=True)
    vectors = np.concatenate(batches)
    with (folder / "vectors.npy.part").open("wb") as out:
        np.save(out, vectors, allow_pickle=False)
    (folder / "vectors.npy.part").replace(folder / "vectors.npy")
    atomic_json(folder / "chunks.json", chunks)
    manifest = {**spec, "indexRevision": revision, "encoderAlias": encoder.alias,
                "documentCount": len(records), "chunkCount": len(chunks),
                "dimension": vectors.shape[1], "dtype": "float32", "normalized": True, "buildDevice": encoder.device,
                "distance": "exact_cosine", "buildSeconds": time.perf_counter()-started,
                "vectorsSha256": sha256_file(folder / "vectors.npy"),
                "chunksSha256": sha256_file(folder / "chunks.json"),
                "lexicalAnalyzer": "unicode-words+hangul-bigrams.v1",
                "evidenceScope": "abstract_and_unparsed_claim_windows" if include_claims else "abstract_only",
                "corpusManifest": corpus}
    atomic_json(folder / "manifest.json", manifest)  # completion marker written last
    return manifest


def ranked(scores: dict[str, float]) -> list[str]:
    return sorted(scores, key=lambda key: (-scores[key], key))


def rrf(lists: list[list[str]], weights: list[float] | None = None) -> dict[str, float]:
    result = defaultdict(float)
    for docs, weight in zip(lists, weights or [1.0]*len(lists)):
        for rank, doc in enumerate(docs, 1):
            result[doc] += weight/(60+rank)
    return dict(result)


def protected_candidates(dense: list[str], lexical: list[str], fused: list[str]) -> list[str]:
    if len(fused) <= 100:
        return fused
    selected = fused[:80]
    ds, ls = set(dense), set(lexical)
    selected += [x for x in dense if x not in ls and x not in selected][:10]
    selected += [x for x in lexical if x not in ds and x not in selected][:10]
    selected += [x for x in fused if x not in selected][:100-len(selected)]
    return selected


class SearchEngine:
    def __init__(self, root: Path, index_revision: str, encoder: Encoder):
        if not re.fullmatch(r"[0-9a-f]{64}", index_revision):
            raise ValueError("INVALID_INDEX_REVISION")
        self.root, self.encoder = root, encoder
        records, corpus = load_corpus(root)
        folder = root / "indexes" / index_revision
        self.manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        if self.manifest["corpus"] != corpus["corpusSha256"] or self.manifest["revision"] != encoder.revision:
            raise ValueError("INDEX_MODEL_OR_CORPUS_MISMATCH")
        for name, key in [("vectors.npy", "vectorsSha256"), ("chunks.json", "chunksSha256")]:
            if sha256_file(folder / name) != self.manifest[key]:
                raise ValueError("INDEX_DIGEST_MISMATCH")
        self.vectors = np.load(folder / "vectors.npy", allow_pickle=False)
        self.chunks = json.loads((folder / "chunks.json").read_text(encoding="utf-8"))
        if self.vectors.shape != (len(self.chunks), self.manifest["dimension"]):
            raise ValueError("INDEX_SHAPE_MISMATCH")
        if not np.isfinite(self.vectors).all() or not np.allclose(np.linalg.norm(self.vectors, axis=1), 1, atol=0.002):
            raise ValueError("INDEX_VECTOR_INVALID")
        self.records = {x["id"]: x for x in records}
        self.ids = sorted(self.records)
        self.bm25 = BM25Okapi([lexical_tokens(self.records[x]["title"]+"\n"+self.records[x]["abstract"]+
                            ("\n"+str(self.records[x]["claims"]) if self.manifest["claims"] else "")) for x in self.ids])
        self.reranker = None

    def search(self, summary: str, features: list[str] | None = None, *, countries: list[str] | None = None,
               date_from: str | None = None, date_to: str | None = None,
               mode: str = "hybrid_semantic", rerank: bool = False,
               cancel: Callable[[], bool] = lambda: False) -> dict:
        started = time.perf_counter()
        if mode not in {"hybrid_semantic", "dense_only", "lexical_only"}:
            raise ValueError("INVALID_SEARCH_MODE")
        features = features or []
        if not summary.strip() or len(features) > 8 or any(not x.strip() for x in features):
            raise ValueError("INVALID_QUERY")
        queries = [summary] + features
        # Full query semantic input is retained by splitting into bounded windows.
        query_windows, window_weights, query_origins = [], [], []
        qweights = [0.5]+[0.5/len(features)]*len(features) if features else [1.0]
        for qi, (q, weight) in enumerate(zip(queries, qweights)):
            windows = self.encoder.windows(q)
            if len(windows) > 8:
                raise ValueError("QUERY_TOO_LONG_SELECT_SHORTER_PASSAGES")
            query_windows += [x["text"] for x in windows]
            window_weights += [weight/len(windows)]*len(windows)
            query_origins += [qi]*len(windows)
        allowed = {pid for pid, doc in self.records.items()
                   if (not countries or doc["country"] in countries)
                   and (not date_from or doc["publicationDate"] and doc["publicationDate"] >= date_from)
                   and (not date_to or doc["publicationDate"] and doc["publicationDate"] <= date_to)}
        if cancel():
            raise InterruptedError("CANCELLED")
        dense_score, lexical_score = {}, {}
        lexical_raw = defaultdict(list)
        best_pairs = defaultdict(list)
        if mode != "lexical_only" and allowed:
            vectors = self.encoder.encode(query_windows, query=True)
            lists, weights = [], []
            for qi, vector in enumerate(vectors):
                if cancel():
                    raise InterruptedError("CANCELLED")
                cosines = self.vectors @ vector
                for overview in (True, False):
                    indexes = [i for i, c in enumerate(self.chunks) if c["publicationId"] in allowed
                               and (c["field"] == "overview") == overview]
                    indexes.sort(key=lambda i: (-float(cosines[i]), self.chunks[i]["id"]))
                    # Exact scoring is used on the entire allowed set before selection.
                    doc_scores = {}
                    for i in indexes:
                        pid = self.chunks[i]["publicationId"]
                        if pid not in doc_scores:
                            doc_scores[pid] = float(cosines[i])
                            best_pairs[pid].append((float(cosines[i]), query_origins[qi], qi, i))
                        if len(doc_scores) >= 100:
                            break
                    lists.append(ranked(doc_scores))
                    weights.append(window_weights[qi]*0.5)
            dense_score = rrf(lists, weights)
        dense = ranked(dense_score)[:300]
        if mode != "dense_only" and allowed:
            lists = []
            for query in queries:
                values = self.bm25.get_scores(lexical_tokens(query))
                scores = {pid: float(values[i]) for i, pid in enumerate(self.ids)
                          if pid in allowed and values[i] > 0}
                for i, pid in enumerate(self.ids):
                    if pid in allowed:
                        lexical_raw[pid].append(float(values[i]))
                lists.append(ranked(scores)[:300])
            lexical_score = rrf(lists, qweights)
        lexical = ranked(lexical_score)[:300]
        fusion = rrf([dense, lexical])
        fused = ranked(fusion)
        candidates = protected_candidates(dense, lexical, fused)
        ranking_status = "NOT_REQUESTED"
        ranking_error = None
        rerank_scores = {}
        selected_pairs = {}
        for pid in candidates:
            pairs = sorted(best_pairs[pid], reverse=True)
            chosen, seen_origins, seen_pairs = [], set(), set()
            for entry in pairs:
                if entry[1] not in seen_origins:
                    chosen.append(entry)
                    seen_origins.add(entry[1])
                    seen_pairs.add((entry[2], entry[3]))
                if len(chosen) == 5:
                    break
            for entry in pairs:
                if len(chosen) == 5:
                    break
                if (entry[2], entry[3]) not in seen_pairs:
                    chosen.append(entry)
                    seen_pairs.add((entry[2], entry[3]))
            if not chosen:
                idx = next(i for i, c in enumerate(self.chunks) if c["publicationId"] == pid)
                chosen = [(None, 0, 0, idx)]
            selected_pairs[pid] = chosen
        if rerank and candidates:
            if cancel():
                raise InterruptedError("CANCELLED")
            try:
                from sentence_transformers import CrossEncoder
                if self.reranker is None:
                    self.reranker = CrossEncoder(RERANKER[0], revision=RERANKER[1], max_length=1024,
                                                 cache_folder=str(root_cache(self.root)), device="cpu",
                                                 trust_remote_code=False, model_kwargs={"weights_only": True})
                pairs, owners = [], []
                for pid, entries in selected_pairs.items():
                    for _, _, qi, ci in entries:
                        pair = [query_windows[qi], self.chunks[ci]["text"]]
                        length = len(self.reranker.tokenizer(*pair, add_special_tokens=True)["input_ids"])
                        if length > 1024:
                            raise ValueError("RERANK_INPUT_TOO_LONG")
                        pairs.append(pair)
                        owners.append(pid)
                batches = []
                for offset in range(0, len(pairs), 8):
                    if cancel():
                        raise InterruptedError("CANCELLED")
                    batches.extend(self.reranker.predict(pairs[offset:offset+8], batch_size=2, show_progress_bar=False))
                scores = np.asarray(batches)
                for pid, score in zip(owners, scores):
                    if not np.isfinite(score):
                        raise ValueError("INVALID_RERANK_SCORE")
                    rerank_scores[pid] = max(float(score), rerank_scores.get(pid, -float("inf")))
                candidates.sort(key=lambda pid: (-rerank_scores[pid], -fusion[pid], pid))
                ranking_status = "COMPLETED"
            except Exception as error:
                ranking_status = "FAILED"
                ranking_error = {"reasonCode": "RERANK_FAILED", "exceptionType": type(error).__name__}
        if cancel():
            raise InterruptedError("CANCELLED")
        results = []
        for pid in candidates[:20]:
            doc = self.records[pid]
            passages = []
            for cosine, origin, _, ci in selected_pairs[pid]:
                c = self.chunks[ci]
                passages.append({"chunkId": c["id"], "field": c["field"], "start": c["start"],
                                 "end": c["end"], "text": c["text"], "queryElement": origin,
                                 "cosine": cosine, "relationStatus": "NOT_EVALUATED"})
            results.append({"publicationId": pid, "title": doc["title"], "sourceUrl": doc["sourceUrl"],
                            "publicationDate": doc["publicationDate"], "country": doc["country"],
                            "denseRank": dense.index(pid)+1 if pid in dense else None,
                            "lexicalRank": lexical.index(pid)+1 if pid in lexical else None,
                            "rrfScore": fusion[pid], "rerankScore": rerank_scores.get(pid),
                            "bm25ScoresByQuery": lexical_raw.get(pid, []),
                            "passages": passages, "evidenceStatus": "PASSAGES_ONLY_UNVERIFIED",
                            "explanation": None, "featureRelations": ["NOT_EVALUATED"]*len(queries)})
        return {"status": "PARTIAL" if ranking_status == "FAILED" else "COMPLETED",
                "mode": mode, "retrievalStatus": "COMPLETED", "rankingStatus": ranking_status,
                "rankingError": ranking_error,
                "evidenceStatus": "PASSAGES_ONLY_UNVERIFIED", "explanationStatus": "NOT_IMPLEMENTED",
                "indexRevision": self.manifest["indexRevision"], "corpusRevision": self.manifest["corpus"],
                "queryEncoderRevision": self.encoder.revision, "retrievalConfigRevision": CONFIG_REVISION,
                "scope": self.manifest["corpusManifest"]["scope"], "eligibleDocumentCount": len(allowed),
                "counts": {"dense": len(dense), "lexical": len(lexical), "union": len(fused), "rerankCandidates": len(candidates)},
                "candidateTrace": {"dense": dense, "lexical": lexical, "rerank": candidates},
                "seconds": time.perf_counter()-started, "results": results,
                "qualityVerdict": "UNJUDGED_NO_RELEVANCE_LABELS"}


def root_cache(root: Path) -> Path:
    return root / "model-cache"
