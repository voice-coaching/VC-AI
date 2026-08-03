"""Incremental phone→word→utterance heads for WP4 S105.

The module consumes already pooled, all-layer representations from one
utterance.  It deliberately exposes no word prediction head: word intervals
are an unlabeled grouping and representation level only.  The selected S104
encoder and adapter are owned by the caller and remain frozen for every S105
variant so the ablation changes only the hierarchy head.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import torch
from torch import nn


VariantName = Literal[
    "simple_head_control",
    "flat_shared_multitask",
    "final_layer_hierarchy",
    "layerwise_weighted_hierarchy",
    "levelwise_multi_aspect_hierarchy",
]

VARIANT_ORDER: tuple[VariantName, ...] = (
    "simple_head_control",
    "flat_shared_multitask",
    "final_layer_hierarchy",
    "layerwise_weighted_hierarchy",
    "levelwise_multi_aspect_hierarchy",
)


class HierarchyContractError(ValueError):
    """Raised when an S105 hierarchy input or architecture is invalid."""


@dataclass(frozen=True, slots=True)
class HierarchyOutput:
    """Task outputs for one utterance.

    There is intentionally no word output field.
    """

    phone_logits: torch.Tensor
    utterance_scores: torch.Tensor


class LayerWeightedSum(nn.Module):
    """Learn one softmax-normalized mixture across every encoder layer."""

    def __init__(self, layer_count: int) -> None:
        super().__init__()
        if layer_count <= 0:
            raise HierarchyContractError("layer_count must be positive")
        self.layer_count = int(layer_count)
        self.logits = nn.Parameter(torch.zeros(self.layer_count))

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        if values.ndim < 2 or values.shape[0] != self.layer_count:
            raise HierarchyContractError(
                "all-layer tensor must have layer as its first dimension"
            )
        weights = torch.softmax(self.logits, dim=0)
        broadcast = (self.layer_count,) + (1,) * (values.ndim - 1)
        return (values * weights.reshape(broadcast)).sum(dim=0)


class MaskedAttentionPool(nn.Module):
    """Pool a non-empty set of vectors with learned scalar attention."""

    def __init__(self, input_dim: int, attention_hidden_dim: int) -> None:
        super().__init__()
        if input_dim <= 0 or attention_hidden_dim <= 0:
            raise HierarchyContractError("attention dimensions must be positive")
        self.input_dim = int(input_dim)
        self.scorer = nn.Sequential(
            nn.LayerNorm(self.input_dim),
            nn.Linear(self.input_dim, int(attention_hidden_dim)),
            nn.Tanh(),
            nn.Linear(int(attention_hidden_dim), 1, bias=False),
        )

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        if (
            values.ndim != 2
            or values.shape[0] <= 0
            or values.shape[1] != self.input_dim
        ):
            raise HierarchyContractError(
                "attention pool requires a non-empty [rows, input_dim] tensor"
            )
        weights = torch.softmax(self.scorer(values).squeeze(-1), dim=0)
        return torch.sum(values * weights.unsqueeze(-1), dim=0)


class MultiAspectAttentionPool(nn.Module):
    """Pool vectors through several independent aspects and fuse them."""

    def __init__(
        self,
        input_dim: int,
        attention_hidden_dim: int,
        aspect_count: int,
    ) -> None:
        super().__init__()
        if input_dim <= 0 or attention_hidden_dim <= 0 or aspect_count <= 0:
            raise HierarchyContractError("multi-aspect dimensions must be positive")
        self.input_dim = int(input_dim)
        self.aspect_count = int(aspect_count)
        self.scorer = nn.Sequential(
            nn.LayerNorm(self.input_dim),
            nn.Linear(self.input_dim, int(attention_hidden_dim)),
            nn.Tanh(),
            nn.Linear(int(attention_hidden_dim), self.aspect_count, bias=False),
        )
        self.fusion = nn.Linear(self.input_dim * self.aspect_count, self.input_dim)

    def forward(self, values: torch.Tensor) -> torch.Tensor:
        if (
            values.ndim != 2
            or values.shape[0] <= 0
            or values.shape[1] != self.input_dim
        ):
            raise HierarchyContractError(
                "multi-aspect pool requires a non-empty [rows, input_dim] tensor"
            )
        # [rows, aspects] -> normalize independently over rows for each aspect.
        weights = torch.softmax(self.scorer(values), dim=0)
        aspects = torch.einsum("ra,rd->ad", weights, values)
        return self.fusion(aspects.reshape(-1))


class IncrementalHierarchyHead(nn.Module):
    """One of the five preregistered, cumulative S105 architecture variants."""

    def __init__(
        self,
        *,
        variant: VariantName,
        hidden_size: int,
        encoder_layer_count: int,
        projection_dim: int,
        target_vocabulary_size: int,
        target_embedding_dim: int,
        attention_hidden_dim: int,
        attention_aspects: int,
        dropout: float,
        outcome_count: int = 3,
        score_range: Sequence[float] = (1.0, 5.0),
    ) -> None:
        super().__init__()
        if variant not in VARIANT_ORDER:
            raise HierarchyContractError("variant is not preregistered")
        if (
            hidden_size <= 0
            or encoder_layer_count <= 0
            or projection_dim <= 0
            or target_vocabulary_size <= 0
            or target_embedding_dim <= 0
            or outcome_count <= 0
        ):
            raise HierarchyContractError("model dimensions must be positive")
        if not 0 <= dropout < 1:
            raise HierarchyContractError("dropout must be in [0, 1)")
        if len(score_range) != 2 or float(score_range[0]) >= float(score_range[1]):
            raise HierarchyContractError("score_range must contain increasing bounds")
        self.variant = variant
        self.hidden_size = int(hidden_size)
        self.encoder_layer_count = int(encoder_layer_count)
        self.projection_dim = int(projection_dim)
        self.score_minimum = float(score_range[0])
        self.score_maximum = float(score_range[1])

        self.layer_mixture: LayerWeightedSum | None = None
        if variant in {
            "layerwise_weighted_hierarchy",
            "levelwise_multi_aspect_hierarchy",
        }:
            self.layer_mixture = LayerWeightedSum(self.encoder_layer_count)

        def projection() -> nn.Sequential:
            return nn.Sequential(
                nn.LayerNorm(self.hidden_size),
                nn.Linear(self.hidden_size, self.projection_dim),
                nn.GELU(),
                nn.Dropout(float(dropout)),
            )

        self.shared_projection: nn.Module | None = None
        self.simple_phone_projection: nn.Module | None = None
        self.simple_utterance_projection: nn.Module | None = None
        if variant == "simple_head_control":
            self.simple_phone_projection = projection()
            self.simple_utterance_projection = projection()
        else:
            self.shared_projection = projection()
        hierarchy_enabled = variant not in {
            "simple_head_control",
            "flat_shared_multitask",
        }
        self.word_projection: nn.Module | None = (
            projection() if hierarchy_enabled else None
        )
        self.target_embedding = nn.Embedding(
            int(target_vocabulary_size),
            int(target_embedding_dim),
        )
        self.phone_classifier = nn.Sequential(
            nn.Linear(
                self.projection_dim + int(target_embedding_dim),
                self.projection_dim,
            ),
            nn.GELU(),
            nn.Dropout(float(dropout)),
            nn.Linear(self.projection_dim, 1),
        )
        self.word_fusion: nn.Module | None = None
        if hierarchy_enabled:
            self.word_fusion = nn.Sequential(
                nn.LayerNorm(self.projection_dim * 2),
                nn.Linear(self.projection_dim * 2, self.projection_dim),
                nn.GELU(),
            )
        pool_type = (
            MultiAspectAttentionPool
            if variant == "levelwise_multi_aspect_hierarchy"
            else MaskedAttentionPool
        )
        self.phone_to_word_pool: nn.Module | None = None
        self.word_to_utterance_pool: nn.Module | None = None
        if hierarchy_enabled and pool_type is MultiAspectAttentionPool:
            self.phone_to_word_pool = MultiAspectAttentionPool(
                self.projection_dim,
                int(attention_hidden_dim),
                int(attention_aspects),
            )
            self.word_to_utterance_pool = MultiAspectAttentionPool(
                self.projection_dim,
                int(attention_hidden_dim),
                int(attention_aspects),
            )
        elif hierarchy_enabled:
            self.phone_to_word_pool = MaskedAttentionPool(
                self.projection_dim,
                int(attention_hidden_dim),
            )
            self.word_to_utterance_pool = MaskedAttentionPool(
                self.projection_dim,
                int(attention_hidden_dim),
            )
        self.utterance_head = nn.Sequential(
            nn.LayerNorm(self.projection_dim),
            nn.Linear(self.projection_dim, self.projection_dim),
            nn.GELU(),
            nn.Dropout(float(dropout)),
            nn.Linear(self.projection_dim, int(outcome_count)),
        )

    def _select_layers(self, values: torch.Tensor, *, name: str) -> torch.Tensor:
        if (
            values.ndim not in {2, 3}
            or values.shape[0] != self.encoder_layer_count
            or values.shape[-1] != self.hidden_size
        ):
            raise HierarchyContractError(f"{name} all-layer shape differs")
        if self.layer_mixture is None:
            return values[-1]
        return self.layer_mixture(values)

    def _validate_inputs(
        self,
        *,
        phone_layers: torch.Tensor,
        word_layers: torch.Tensor,
        utterance_layers: torch.Tensor,
        phone_to_word: torch.Tensor,
        target_indices: torch.Tensor,
    ) -> None:
        if phone_layers.ndim != 3 or word_layers.ndim != 3:
            raise HierarchyContractError("phone and word layers must be 3-D")
        if utterance_layers.ndim != 2:
            raise HierarchyContractError("utterance layers must be 2-D")
        phone_count = phone_layers.shape[1]
        word_count = word_layers.shape[1]
        if phone_count <= 0:
            raise HierarchyContractError("at least one target phone is required")
        if phone_to_word.shape != (phone_count,) or phone_to_word.dtype != torch.long:
            raise HierarchyContractError("phone_to_word must be a long phone vector")
        if target_indices.shape != (phone_count,) or target_indices.dtype != torch.long:
            raise HierarchyContractError("target_indices must be a long phone vector")
        if bool(torch.any(phone_to_word < -1)) or bool(
            torch.any(phone_to_word >= word_count)
        ):
            raise HierarchyContractError("phone_to_word index is out of range")

    def _hierarchical_utterance(
        self,
        *,
        phone: torch.Tensor,
        word: torch.Tensor,
        utterance: torch.Tensor,
        phone_to_word: torch.Tensor,
    ) -> torch.Tensor:
        if (
            self.phone_to_word_pool is None
            or self.word_to_utterance_pool is None
            or self.word_fusion is None
        ):
            raise HierarchyContractError("hierarchy modules are absent")
        word_nodes: list[torch.Tensor] = []
        for word_index in range(word.shape[0]):
            selected = phone[phone_to_word == word_index]
            if selected.shape[0] == 0:
                # The word interval itself remains an unlabeled representation.
                word_nodes.append(word[word_index])
            else:
                pooled = self.phone_to_word_pool(selected)
                word_nodes.append(
                    self.word_fusion(torch.cat((word[word_index], pooled), dim=0))
                )
        orphan = phone[phone_to_word == -1]
        if orphan.shape[0] > 0:
            # The preregistered utterance-level orphan bucket is represented in
            # the hierarchy but is never treated as a labeled word.
            word_nodes.append(self.phone_to_word_pool(orphan))
        if not word_nodes:
            return utterance
        hierarchy = self.word_to_utterance_pool(torch.stack(word_nodes))
        return self.word_fusion(torch.cat((utterance, hierarchy), dim=0))

    def forward(
        self,
        *,
        phone_layers: torch.Tensor,
        word_layers: torch.Tensor,
        utterance_layers: torch.Tensor,
        phone_to_word: torch.Tensor,
        target_indices: torch.Tensor,
    ) -> HierarchyOutput:
        self._validate_inputs(
            phone_layers=phone_layers,
            word_layers=word_layers,
            utterance_layers=utterance_layers,
            phone_to_word=phone_to_word,
            target_indices=target_indices,
        )
        selected_phone = self._select_layers(phone_layers, name="phone")
        selected_word = self._select_layers(word_layers, name="word")
        selected_utterance = self._select_layers(
            utterance_layers,
            name="utterance",
        )
        if self.variant == "simple_head_control":
            if (
                self.simple_phone_projection is None
                or self.simple_utterance_projection is None
            ):
                raise HierarchyContractError("simple projections are absent")
            phone = self.simple_phone_projection(selected_phone)
            utterance = self.simple_utterance_projection(selected_utterance)
        else:
            if self.shared_projection is None:
                raise HierarchyContractError("shared projection is absent")
            phone = self.shared_projection(selected_phone)
            utterance = self.shared_projection(selected_utterance)
        if self.word_projection is None:
            word = selected_word.new_empty(
                (selected_word.shape[0], self.projection_dim)
            )
        else:
            word = self.word_projection(selected_word)
        acoustic_and_target = torch.cat(
            (phone, self.target_embedding(target_indices)),
            dim=1,
        )
        phone_logits = self.phone_classifier(acoustic_and_target).squeeze(1)
        if self.variant in {"simple_head_control", "flat_shared_multitask"}:
            utterance_representation = utterance
        else:
            utterance_representation = self._hierarchical_utterance(
                phone=phone,
                word=word,
                utterance=utterance,
                phone_to_word=phone_to_word,
            )
        unit_interval = torch.sigmoid(self.utterance_head(utterance_representation))
        utterance_scores = (
            self.score_minimum
            + (self.score_maximum - self.score_minimum) * unit_interval
        )
        return HierarchyOutput(
            phone_logits=phone_logits,
            utterance_scores=utterance_scores,
        )


__all__ = [
    "HierarchyContractError",
    "HierarchyOutput",
    "IncrementalHierarchyHead",
    "LayerWeightedSum",
    "MaskedAttentionPool",
    "MultiAspectAttentionPool",
    "VARIANT_ORDER",
    "VariantName",
]
