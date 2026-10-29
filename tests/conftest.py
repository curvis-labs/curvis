"""Shared pytest configuration."""

import os

# Run Qt without a display unless the caller chose a platform.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
