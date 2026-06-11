"""Root conftest.py: repo-local imports win over any installed copy."""
import sys
from pathlib import Path

import pytest

_root = str(Path(__file__).parent)
if _root not in sys.path:
    sys.path.insert(0, _root)


@pytest.fixture(autouse=True)
def _default_linear_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto-mock linear.add_comment so tests don't hit the real Linear API."""
    import drain_cycle.linear as _linear

    monkeypatch.setattr(_linear, "add_comment", lambda issue_id, body: None)
