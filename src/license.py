"""Stub for the legacy Pro-Feature license system.

The original printix-mcp-docker had a feature-flag gate. mysecureprint-server
removed it (everything always-on) but a few admin-page code paths in
web/app.py still do lazy `from license import ...`. This stub keeps those
paths working without a real license check.
"""

PRO_FEATURES: dict = {}


def is_feature_enabled(feature_id: str) -> bool:
    return True


def get_active_features() -> set:
    return set(PRO_FEATURES.keys())
