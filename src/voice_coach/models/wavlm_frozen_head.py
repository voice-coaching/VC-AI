"""Small neural heads for frozen SSL speech representations.

The encoder never appears in this module.  Training consumes an already
reviewed feature cache so the same head code can be reused for Base+ and
Large by changing only ``embedding_dim`` in the experiment configuration.
"""

from __future__ import annotations

import torch
from torch import nn


class FrozenPhoneHead(nn.Module):
    """Binary phone-error head with a train-vocabulary target embedding."""

    def __init__(
        self,
        *,
        embedding_dim: int,
        target_vocabulary_size: int,
        target_embedding_dim: int,
        hidden_dim: int,
        dropout: float,
    ) -> None:
        super().__init__()
        if embedding_dim <= 0 or target_vocabulary_size <= 0:
            raise ValueError("embedding and vocabulary dimensions must be positive")
        if target_embedding_dim <= 0 or hidden_dim <= 0:
            raise ValueError("head dimensions must be positive")
        if not 0 <= dropout < 1:
            raise ValueError("dropout must be in [0, 1)")
        self.embedding_dim = int(embedding_dim)
        self.target_embedding = nn.Embedding(
            int(target_vocabulary_size),
            int(target_embedding_dim),
        )
        self.network = nn.Sequential(
            nn.LayerNorm(int(embedding_dim)),
            nn.Linear(int(embedding_dim), int(hidden_dim)),
            nn.GELU(),
        )
        self.classifier = nn.Sequential(
            nn.Dropout(float(dropout)),
            nn.Linear(int(hidden_dim) + int(target_embedding_dim), int(hidden_dim)),
            nn.GELU(),
            nn.Dropout(float(dropout)),
            nn.Linear(int(hidden_dim), 1),
        )

    def forward(
        self,
        embeddings: torch.Tensor,
        target_indices: torch.Tensor,
    ) -> torch.Tensor:
        if embeddings.ndim != 2 or embeddings.shape[1] != self.embedding_dim:
            raise ValueError("phone embeddings have an unexpected shape")
        if target_indices.ndim != 1 or target_indices.shape[0] != embeddings.shape[0]:
            raise ValueError("target indices have an unexpected shape")
        acoustic = self.network(embeddings)
        target = self.target_embedding(target_indices)
        return self.classifier(torch.cat((acoustic, target), dim=1)).squeeze(1)


class FrozenUtteranceHead(nn.Module):
    """Bounded three-score utterance head.

    A sigmoid makes every output lie in the predeclared ordinal score range.
    The model is optimized as a continuous bounded regressor; QWK is computed
    only after rounding at evaluation time.
    """

    def __init__(
        self,
        *,
        embedding_dim: int,
        hidden_dim: int,
        dropout: float,
        outcome_count: int = 3,
        score_minimum: float = 1.0,
        score_maximum: float = 5.0,
    ) -> None:
        super().__init__()
        if embedding_dim <= 0 or hidden_dim <= 0 or outcome_count <= 0:
            raise ValueError("head dimensions must be positive")
        if not 0 <= dropout < 1:
            raise ValueError("dropout must be in [0, 1)")
        if score_minimum >= score_maximum:
            raise ValueError("score range is invalid")
        self.embedding_dim = int(embedding_dim)
        self.score_minimum = float(score_minimum)
        self.score_maximum = float(score_maximum)
        self.network = nn.Sequential(
            nn.LayerNorm(int(embedding_dim)),
            nn.Linear(int(embedding_dim), int(hidden_dim)),
            nn.GELU(),
            nn.Dropout(float(dropout)),
            nn.Linear(int(hidden_dim), int(outcome_count)),
        )

    def forward(self, embeddings: torch.Tensor) -> torch.Tensor:
        if embeddings.ndim != 2 or embeddings.shape[1] != self.embedding_dim:
            raise ValueError("utterance embeddings have an unexpected shape")
        unit_interval = torch.sigmoid(self.network(embeddings))
        width = self.score_maximum - self.score_minimum
        return self.score_minimum + width * unit_interval
