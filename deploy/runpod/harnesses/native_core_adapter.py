"""Packaged core adapter: consume the one existing Native forward result."""
from functools import lru_cache
import base64
import hashlib
import json
from pathlib import Path

from native_scoring.native_phone_assessment import policy_contract, validate_vocabulary
from native_scoring.native_phone_evidence import build_evidence

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def assessment_policy():
    raw = (ROOT / "config/native_phone_assessment.json").read_bytes()
    manifest = json.loads((ROOT / "release_manifest.json").read_bytes())
    if hashlib.sha256(raw).hexdigest() != manifest["file_sha256"]["config/native_phone_assessment.json"]:
        raise ValueError("NATIVE_POLICY_DIGEST")
    policy = json.loads(raw)
    policy_contract(policy, approved=True)
    return policy


def attach_native_evidence(envelope, *, case, state, vocabulary, legacy, output):
    if envelope["input_validation"]["status"] != "ACCEPT":
        envelope["nativeScoringEvidence"] = None
        envelope["nativeScoringLogits"] = None
        return
    native = state["native"]
    metadata = [row.model_dump(mode="json") for row in state["phone_contract"].full_metadata]
    # Keep only uniquely identified original words; no count-only phone mapping.
    words = []
    spoken = [w for w in legacy["word_intervals"] if w["label"]]
    import unicodedata
    def clean(text):
        return "".join(c for c in text if not unicodedata.category(c).startswith(("P", "Z")))
    for word_index, word in enumerate(spoken):
        owners = {p["original_word_text"] for p in metadata if p["original_word_index"] == word_index}
        if len(owners) == 1 and clean(next(iter(owners))) == clean(word["label"]):
            words.append(dict(wordIndex=word_index, startSeconds=word["start_s"], endSeconds=word["end_s"]))
    binding = dict(requestId=case["id"], executionId=case["executionId"],
                   sourceAudioSha256=envelope["source_audio_sha256"], preparedWavSha256=state["prepared_sha"],
                   scriptSha256=hashlib.sha256(case["script"].encode()).hexdigest(),
                   coreManifestSha256=hashlib.sha256((ROOT / "release_manifest.json").read_bytes()).hexdigest(),
                   vocabularySha256=validate_vocabulary(vocabulary))
    evidence = build_evidence(logits=native["arr"], ctc_ids=native["ids"], vocabulary=vocabulary,
                              metadata=metadata, words=words, binding=binding, policy=assessment_policy())
    # The existing private core artifact is transferred after result publication.
    # Keep logits inside that artifact so workspace cleanup cannot orphan them;
    # never include them in GPT input or the public result projection.
    raw = native["arr"].astype("<f4", copy=False).tobytes(order="C")
    envelope["nativeScoringLogits"] = dict(schemaVersion="native-logits-fp32-v1", encoding="base64-le-f32",
                                          rows=len(native["ids"]), columns=45, sha256=evidence["logitsSha256"],
                                          data=base64.b64encode(raw).decode("ascii"))
    envelope["nativeScoringEvidence"] = evidence
