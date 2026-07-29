#!/usr/bin/env python3
"""Create a reproducible XLS-R CTC processor from the prepared vocabulary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from transformers import (
    Wav2Vec2CTCTokenizer,
    Wav2Vec2FeatureExtractor,
    Wav2Vec2Processor,
)

from korean_phone_ctc.io_utils import atomic_write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vocab", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-model", default="facebook/wav2vec2-xls-r-300m")
    parser.add_argument(
        "--revision",
        default="1a640f32ac3e39899438a2931f9924c02f080a54",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    vocabulary = json.loads(args.vocab.read_text(encoding="utf-8"))
    if vocabulary.get("<pad>") != 0 or vocabulary.get("<unk>") != 1:
        raise ValueError("vocabulary must use <pad>=0 and <unk>=1")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    local_vocab = output_dir / "vocab.json"
    atomic_write_json(local_vocab, vocabulary)
    tokenizer = Wav2Vec2CTCTokenizer(
        str(local_vocab),
        bos_token=None,
        eos_token=None,
        unk_token="<unk>",
        pad_token="<pad>",
        # Phone targets do not contain word boundaries. Reuse the existing
        # blank token so Transformers does not append a synthetic delimiter
        # and silently change the model vocabulary size.
        word_delimiter_token="<pad>",
    )
    feature_extractor = Wav2Vec2FeatureExtractor(
        feature_size=1,
        sampling_rate=16_000,
        padding_value=0.0,
        do_normalize=True,
        return_attention_mask=True,
    )
    processor = Wav2Vec2Processor(
        feature_extractor=feature_extractor,
        tokenizer=tokenizer,
    )
    processor.save_pretrained(output_dir)
    atomic_write_json(
        output_dir / "base_model.json",
        {
            "model_id": args.base_model,
            "revision": args.revision,
            "vocab_size": len(vocabulary),
            "sampling_rate": 16_000,
            "ctc_blank_token": "<pad>",
            "ctc_blank_id": 0,
        },
    )
    reloaded = Wav2Vec2Processor.from_pretrained(output_dir)
    if reloaded.tokenizer.get_vocab() != tokenizer.get_vocab():
        raise RuntimeError("processor vocabulary changed after reload")
    print(output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
