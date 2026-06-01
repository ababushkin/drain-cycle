"""Root conftest.py for the ABA-302 worktree.

The editable install points to the main-branch source tree. When pytest runs
from this worktree, that installed copy shadows the worktree-local changes.
Prepend the worktree root so imports resolve here first.
"""
import sys
from pathlib import Path

import pytest

_root = str(Path(__file__).parent)
if _root not in sys.path:
    sys.path.insert(0, _root)


@pytest.fixture(autouse=True)
def _default_graphite_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto-mock graphite so tests that don't test graphite don't hit gt/gh.

    Tests in test_orchestrator_graphite.py override these with their own
    ``monkeypatch.setattr`` calls, which take effect after this fixture runs
    and win because the last setattr on the same attribute wins.
    """
    import drain_cycle.graphite as _graphite

    _counter: list[int] = [0]

    def _noop_submit(worktree: Path, *, parent: str, body: str = "") -> _graphite.PrInfo:
        _counter[0] += 1
        return _graphite.PrInfo(url="https://github.com/r/p/pull/stub", number=_counter[0])

    monkeypatch.setattr(_graphite, "submit", _noop_submit)
    monkeypatch.setattr(_graphite, "ensure_review_high_label", lambda worktree: None)
    monkeypatch.setattr(_graphite, "add_label", lambda pr_number, label, worktree: None)
