from __future__ import annotations

import pytest
import torch

from voice_coach.models.wavlm_frozen_head import (
    FrozenPhoneHead,
    FrozenUtteranceHead,
)


def test_phone_head_shape_and_target_category_affect_logits() -> None:
    torch.manual_seed(7)
    model = FrozenPhoneHead(
        embedding_dim=8,
        target_vocabulary_size=3,
        target_embedding_dim=4,
        hidden_dim=6,
        dropout=0.0,
    )
    embeddings = torch.ones((3, 8), dtype=torch.float32)
    logits = model(embeddings, torch.tensor([0, 1, 2]))

    assert logits.shape == (3,)
    assert torch.isfinite(logits).all()
    assert torch.unique(logits).numel() > 1


def test_utterance_head_is_bounded_to_score_contract() -> None:
    model = FrozenUtteranceHead(
        embedding_dim=16,
        hidden_dim=8,
        dropout=0.0,
        outcome_count=3,
        score_minimum=1,
        score_maximum=5,
    )
    predictions = model(torch.randn((5, 16)))

    assert predictions.shape == (5, 3)
    assert torch.all(predictions >= 1)
    assert torch.all(predictions <= 5)


def test_heads_reject_wrong_embedding_dimensions() -> None:
    phone = FrozenPhoneHead(
        embedding_dim=8,
        target_vocabulary_size=2,
        target_embedding_dim=2,
        hidden_dim=4,
        dropout=0.0,
    )
    utterance = FrozenUtteranceHead(
        embedding_dim=16,
        hidden_dim=4,
        dropout=0.0,
    )

    with pytest.raises(ValueError, match="unexpected shape"):
        phone(torch.zeros((2, 7)), torch.zeros(2, dtype=torch.long))
    with pytest.raises(ValueError, match="unexpected shape"):
        utterance(torch.zeros((2, 15)))
