"""Confidence gate: the refusal decision is made here, in code, never by the model."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from airgap.core.types import ScoredChunk

REFUSAL_TEXT = "I could not find this in your documents."


@dataclass(frozen=True, slots=True)
class GateDecision:
    passed: bool
    best_score: float | None
    threshold: float


@dataclass(frozen=True, slots=True)
class ConfidenceGate:
    """``threshold`` is on the reranker's [0, 1] scale. ``tier_offset`` raises it on
    small hardware tiers, where the generator is weaker (spec §14)."""

    threshold: float
    tier_offset: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.effective_threshold <= 1.0:
            raise ValueError("gate threshold must be within [0, 1]")

    @property
    def effective_threshold(self) -> float:
        return self.threshold + self.tier_offset

    def evaluate(self, reranked: Sequence[ScoredChunk]) -> GateDecision:
        best = max((c.score for c in reranked), default=None)
        passed = best is not None and best >= self.effective_threshold
        return GateDecision(passed, best, self.effective_threshold)
