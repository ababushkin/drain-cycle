"""Hatchling metadata hook that sets the package version from git at build time.

Declared in ``pyproject.toml`` via ``dynamic = ["version"]`` and
``[tool.hatch.metadata.hooks.custom]``. Hatchling calls :meth:`update` while
building the wheel (including every ``uv tool install``), so the installed
metadata records the live commit count. The computation lives in
``drain_cycle/_version.py``; it is loaded by path here because the package is not
importable yet during its own build.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from hatchling.metadata.plugin.interface import MetadataHookInterface


def _public_version() -> str:
    path = Path(__file__).parent / "drain_cycle" / "_version.py"
    spec = importlib.util.spec_from_file_location("_drain_cycle_version", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.public_version()


class CustomMetadataHook(MetadataHookInterface):
    def update(self, metadata: dict) -> None:
        metadata["version"] = _public_version()
