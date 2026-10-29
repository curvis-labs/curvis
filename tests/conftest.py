"""Shared pytest configuration."""

import os

from hypothesis import settings

# Run Qt without a display unless the caller chose a platform.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Hypothesis profiles. Use HYPOTHESIS_PROFILE=thorough for a long bug hunt.
settings.register_profile("default", max_examples=150, deadline=None)
settings.register_profile("thorough", max_examples=3000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
