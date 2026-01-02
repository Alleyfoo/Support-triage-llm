"""
Compatibility shim for the optional pipeline feature.

Importing this module requires FEATURE_PIPELINE=1. Prefer using
`app.extensions.pipeline` directly.
"""

from app.features import require_pipeline_enabled

require_pipeline_enabled()

from app.extensions.pipeline import *  # noqa: F401,F403
