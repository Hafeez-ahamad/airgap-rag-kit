"""KV-cache maths (spec §4.3, BUILD_PROMPT §5). Predictions only, never guarantees.

Per full-attention layer and token: 2 (K and V) x kv_heads x head_dim x bytes_per_element.
Sliding-window layers cache at most ``window`` tokens, so for hybrid models the cost is not
linear in context. MoE does not change KV size (it changes weights, handled elsewhere).
"""

from __future__ import annotations

import math

from airgap.capacity.catalog import Architecture


def kv_bytes_per_token_per_layer(arch: Architecture, bytes_per_element: float) -> float:
    return 2 * arch.kv_heads * arch.head_dim * bytes_per_element


def kv_cache_bytes(arch: Architecture, context_tokens: int, bytes_per_element: float) -> int:
    """KV cache for one sequence of ``context_tokens``."""
    if context_tokens < 0:
        raise ValueError("context_tokens must be >= 0")
    per_layer_token = kv_bytes_per_token_per_layer(arch, bytes_per_element)
    if arch.attention == "full":
        return math.ceil(arch.layers * context_tokens * per_layer_token)
    # hybrid_sliding: validated to carry both fields
    assert arch.sliding_window is not None and arch.full_attention_layers is not None
    full = arch.full_attention_layers
    sliding = arch.layers - full
    windowed = min(context_tokens, arch.sliding_window)
    return math.ceil((full * context_tokens + sliding * windowed) * per_layer_token)


def max_sessions(budget_bytes: int, per_session_bytes: int, cap: int) -> int:
    """Concurrent sequences that fit in ``budget_bytes``, capped by policy."""
    if per_session_bytes <= 0:
        return cap
    return max(0, min(cap, budget_bytes // per_session_bytes))
