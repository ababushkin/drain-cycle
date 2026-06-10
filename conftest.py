"""Root conftest.py: repo-local imports win over any installed copy."""
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

    def _noop_submit(
        worktree: Path, *, parent: str, title: str = "", body: str = ""
    ) -> _graphite.PrInfo:
        _counter[0] += 1
        return _graphite.PrInfo(url="https://github.com/r/p/pull/stub", number=_counter[0])

    monkeypatch.setattr(_graphite, "submit", _noop_submit)
    monkeypatch.setattr(_graphite, "ensure_review_high_label", lambda worktree: None)
    monkeypatch.setattr(_graphite, "add_label", lambda pr_number, label, worktree: None)

    # The PR-link comment fires on every stack-mode Done; never let a test
    # reach the real Linear API. Tests that assert on comments override this.
    import drain_cycle.linear as _linear

    monkeypatch.setattr(_linear, "add_comment", lambda issue_id, body: None)
