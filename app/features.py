"""Feature flags for optional components."""

from __future__ import annotations

from . import config

PIPELINE_DISABLED_MSG = "Pipeline feature disabled. Set FEATURE_PIPELINE=1 to enable."


def pipeline_enabled() -> bool:
    """Return True when the optional pipeline extension is enabled."""
    return bool(getattr(config, "FEATURE_PIPELINE", False))


def require_pipeline_enabled() -> None:
    """Raise a clear ImportError when the pipeline extension is disabled."""
    if not pipeline_enabled():
        raise ImportError(PIPELINE_DISABLED_MSG)


__all__ = ["pipeline_enabled", "require_pipeline_enabled", "PIPELINE_DISABLED_MSG"]
