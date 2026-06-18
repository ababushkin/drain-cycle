#!/usr/bin/env python3
"""Interactive swimlanes demo — NON-PRODUCTION prototype.

Animates the live-execution-swimlanes layout (Track C product spike, variant
"C-status-only") for a whole drain-cycle run: a queue of coding issues worked
one at a time, walking the *real* exec:* chain from the bundled
``agent-skills-shaper`` pack.

Two surfaces, stacked:
  * a queue pane listing every issue in dependency-resolved execution order,
    with the running one marked and a ▶ on whichever you're viewing;
  * the swimlane for the focused issue (header, exec:* stepper spine, and the
    active step's persona drill-down).

You can toggle which issue's swimlane is shown while the cycle runs:
  ↑/↓ or j/k   move the focus up/down the queue
  1..N         jump to issue N
  f            follow the running issue again (auto-follow)
  space        pause / resume playback
  q            quit
By default the focus auto-follows the running issue; any manual move turns that
off until you press ``f``.

What is real here:
  * the step chain and its order (pickup → breakdown → build → review → verify
    → finish), read from the pack's exec:pickup workflow;
  * the review personas (spec-compliance → security-auditor → code-quality),
    discovered from execution-review/personas/;
  * build's escalations (exec:debug on a stuck red loop, exec:simplify on green);
  * each step's one-line caption, pulled from the matching SKILL.md frontmatter;
  * dependency-aware ordering: the queue is topologically sorted from each
    issue's blocked_by deps (the cycle is defined out of order on purpose).

What is simulated: the issues themselves, the timings, token/turn counters, task
lists, and the review finding that drives the NO-GO loop-back. No ``claude`` is
ever invoked. This is a UX prototype for the swimlanes layout in motion —
nothing here is wired to a real run. Do not extend it toward production; that
needs a design doc and a clean implementation (see finding.md).

Run:
    uv run python docs/prototypes/live-execution-swimlanes/demo.py
    uv run python docs/prototypes/live-execution-swimlanes/demo.py --speed 2

When stdout is not a TTY (piped/CI) the demo can't read keys, so it falls back
to a non-interactive play-through that auto-follows the running issue.
"""
from __future__ import annotations

import argparse
import os
import random
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.text import Text
except ModuleNotFoundError:  # pragma: no cover - prototype convenience
    sys.exit(
        "This prototype needs `rich` (a drain-cycle dependency).\n"
        "Run it through the project venv:\n"
        "    uv run python docs/prototypes/live-execution-swimlanes/demo.py"
    )


# ── locating the bundled pack ────────────────────────────────────────────────

def find_pack() -> Path | None:
    """Resolve the agent-skills-shaper pack root, or None if unavailable."""
    env = os.environ.get("SHAPER_PACK")
    candidates = [Path(env)] if env else []
    candidates += [
        Path.home() / "src" / "agent-skills-shaper",
        Path(__file__).resolve().parents[3] / "agent-skills-shaper",
    ]
    # Follow the symlinked skills too, in case the layout differs.
    link = Path.home() / ".claude" / "skills" / "exec-pickup"
    if link.exists():
        candidates.append(link.resolve().parents[1])
    for c in candidates:
        if (c / "skills" / "exec-pickup" / "SKILL.md").exists():
            return c
    return None


def frontmatter_description(skill_md: Path) -> str:
    """First sentence of a SKILL.md frontmatter ``description:`` (folded or not)."""
    try:
        text = skill_md.read_text()
    except OSError:
        return ""
    m = re.search(r"^---\n(.*?)\n---", text, re.DOTALL)
    block = m.group(1) if m else text
    dm = re.search(r"description:\s*(>[-+]?\s*)?\n((?:[ \t]+.*\n?)+)", block)
    if dm:
        body = " ".join(line.strip() for line in dm.group(2).splitlines())
    else:
        dm = re.search(r"description:\s*(.+)", block)
        body = dm.group(1).strip() if dm else ""
    body = re.sub(r"\s+", " ", body).strip().strip('"')
    sentence = re.split(r"(?<=[.—])\s", body)[0] if body else ""
    return sentence.strip(" —")


@dataclass
class Pack:
    root: Path | None
    captions: dict[str, str] = field(default_factory=dict)
    personas: list[str] = field(default_factory=list)
    build_subskills: list[str] = field(default_factory=list)


# step key → skill dir that owns it (breakdown is delegated from exec:pickup)
STEP_SKILL = {
    "pickup": "exec-pickup",
    "breakdown": "exec-pickup",
    "build": "build",
    "review": "execution-review",
    "verify": "verify-implementation",
    "finish": "pr-finishing",
}
STEPS = list(STEP_SKILL)

_FALLBACK_CAPTIONS = {
    "pickup": "Read the issue, gate on blockers, rebase, write exec-state.json",
    "breakdown": "Decompose the issue into ordered tasks, each with a done_when clause",
    "build": "RED → GREEN → commit per slice (exec:debug on stuck red, exec:simplify on green)",
    "review": "Multi-persona review over the diff → one deduped GO/NO-GO verdict",
    "verify": "Check the ac_checklist against the diff → pass/fail",
    "finish": "Write the PR body, post the review summary, transition the issue",
}
_FALLBACK_PERSONAS = ["spec-compliance", "security-auditor", "code-quality"]
_PERSONA_ORDER = {"spec-compliance": 0, "security-auditor": 1, "code-quality": 2}


def load_pack() -> Pack:
    root = find_pack()
    if root is None:
        return Pack(None, dict(_FALLBACK_CAPTIONS), list(_FALLBACK_PERSONAS), ["debugging", "simplify"])

    captions = {}
    for key, skill_dir in STEP_SKILL.items():
        cap = frontmatter_description(root / "skills" / skill_dir / "SKILL.md")
        captions[key] = cap or _FALLBACK_CAPTIONS[key]
    captions["breakdown"] = _FALLBACK_CAPTIONS["breakdown"]  # delegated; no own dir

    persona_dir = root / "skills" / "execution-review" / "personas"
    if persona_dir.is_dir():
        personas = sorted(
            (p.stem for p in persona_dir.glob("*.md")),
            key=lambda n: _PERSONA_ORDER.get(n, 99),
        )
    else:
        personas = list(_FALLBACK_PERSONAS)

    subskills = [d for d in ("debugging", "simplify") if (root / "skills" / d).is_dir()]
    return Pack(root, captions, personas or _FALLBACK_PERSONAS, subskills or ["debugging", "simplify"])


# ── the cycle: issues + dependency-resolved execution order ───────────────────

@dataclass
class IssueSpec:
    id: str
    title: str
    deps: tuple[str, ...] = ()
    risky: bool = False  # triggers the security NO-GO loop-back during review
    pr: str = ""


# Defined deliberately OUT of execution order to show the topo sort at work
# (301, the only dep-free issue, is listed last). Real shape: a chain (301→302)
# that fans out to two dependents (303, 304).
CYCLE_SPECS = [
    IssueSpec("ABA-302", "Retry sender with capped backoff", deps=("ABA-301",),
              risky=True, pr="github.com/acme/app/pull/452"),
    IssueSpec("ABA-303", "Emit retry metrics + dashboard", deps=("ABA-302",),
              pr="github.com/acme/app/pull/453"),
    IssueSpec("ABA-304", "Document retry behaviour + runbook", deps=("ABA-302",),
              pr="github.com/acme/app/pull/454"),
    IssueSpec("ABA-301", "Add webhook retry queue table",
              pr="github.com/acme/app/pull/451"),
]


def execution_order(specs: list[IssueSpec]) -> list[IssueSpec]:
    """Topologically sort specs by their deps; stable on the listing order.

    Mirrors what drain-cycle does with blocked_by[]: an issue cannot run before
    every dependency it lists has run.
    """
    by_id = {s.id: s for s in specs}
    pos = {s.id: i for i, s in enumerate(specs)}
    indeg = {s.id: sum(1 for d in s.deps if d in by_id) for s in specs}
    ready = [s.id for s in specs if indeg[s.id] == 0]
    order: list[str] = []
    while ready:
        ready.sort(key=lambda x: pos[x])
        nid = ready.pop(0)
        order.append(nid)
        for s in specs:
            if nid in s.deps:
                indeg[s.id] -= 1
                if indeg[s.id] == 0:
                    ready.append(s.id)
    # any leftover (cyclic deps) appended in listing order — shouldn't happen here
    order += [s.id for s in specs if s.id not in order]
    return [by_id[i] for i in order]


# ── run state ────────────────────────────────────────────────────────────────

DONE, ACTIVE, PENDING = "done", "active", "pending"
SYM = {DONE: "●", ACTIVE: "◉", PENDING: "○"}
NAME_STYLE = {DONE: "green", ACTIVE: "bold cyan", PENDING: "grey50"}
SPINE_STYLE = {DONE: "green", ACTIVE: "bold cyan", PENDING: "grey37"}

MARK = {"ok": "✓", "run": "▶", "todo": "·", "fail": "✗"}
MARK_STYLE = {"ok": "green", "run": "bold cyan", "todo": "grey50", "fail": "bold red"}

LANE_SYM = {"done": "●", "running": "◉", "queued": "○"}
LANE_STYLE = {"done": "green", "running": "bold cyan", "queued": "grey50"}


@dataclass
class Run:
    spec: IssueSpec
    pack: Pack
    status: dict[str, str] = field(default_factory=lambda: {k: PENDING for k in STEPS})
    active: str | None = None
    # per-step drill-down: list of [mark, label, note]
    subs: dict[str, list[list[str]]] = field(default_factory=lambda: {k: [] for k in STEPS})
    activity: str = ""          # single-line sub-status for steps without a list
    flash: tuple[str, str] | None = None  # (message, style)
    turn: int = 0
    tokens: int = 0
    peak: int = 0
    sim_seconds: float = 0.0

    @property
    def issue_id(self) -> str:
        return self.spec.id

    @property
    def issue_title(self) -> str:
        return self.spec.title

    @property
    def deps(self) -> tuple[str, ...]:
        return self.spec.deps

    def is_done(self) -> bool:
        return all(v == DONE for v in self.status.values())


@dataclass
class Cycle:
    cycle_id: str
    runs: list[Run]
    pack: Pack
    running_idx: int | None = None
    focus_idx: int = 0
    follow: bool = True
    paused: bool = False
    finished: bool = False

    def state_of(self, i: int) -> str:
        run = self.runs[i]
        if run.is_done():
            return "done"
        if i == self.running_idx:
            return "running"
        return "queued"


# ── rendering (variant C-status-only) ────────────────────────────────────────

def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1_000}k"
    return str(n)


def _fmt_elapsed(s: float) -> str:
    s = int(s)
    if s >= 3600:
        return f"{s // 3600}h{(s % 3600) // 60}m"
    if s >= 60:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s}s"


def _lane_status_text(run: Run, state: str) -> Text:
    if state == "done":
        return Text(f"done · {run.spec.pr}", style="green")
    if state == "running":
        step = run.active or "starting…"
        return Text(f"running · {step}", style="bold cyan")
    dep = f" · after {', '.join(run.deps)}" if run.deps else " · ready"
    return Text(f"queued{dep}", style="grey50")


def _queue_pane(cycle: Cycle) -> list:
    title = Text("  ")
    title.append(f" cycle {cycle.cycle_id} ", style="bold white on dark_green")
    n = len(cycle.runs)
    done = sum(1 for r in cycle.runs if r.is_done())
    title.append(f"  {done}/{n} done · dependency-resolved execution order", style="bold")

    rows: list = [title, ""]
    width = max((len(r.issue_title) for r in cycle.runs), default=10)
    for i, run in enumerate(cycle.runs):
        state = cycle.state_of(i)
        focused = i == cycle.focus_idx
        row = Text("  ")
        row.append("▶ " if focused else "  ", style="bold yellow")
        row.append(f"{i + 1} ", style="dim")
        row.append(f"{LANE_SYM[state]} ", style=LANE_STYLE[state])
        id_style = "bold white" if focused else LANE_STYLE[state]
        row.append(f"{run.issue_id}  ", style=id_style)
        title_style = "white" if state != "queued" else "grey50"
        row.append(f"{run.issue_title:<{width}}   ", style=title_style)
        row.append(_lane_status_text(run, state))
        rows.append(row)
    return rows


def _swimlane_body(run: Run, state: str) -> list:
    header = Text("  ")
    header.append(f" {run.issue_id} ", style="bold white on blue")
    header.append(f"  {run.issue_title}", style="bold")

    spine = Text("  ")
    for i, key in enumerate(STEPS):
        st = run.status[key]
        spine.append(SYM[st], style=SPINE_STYLE[st])
        if key == run.active:
            spine.append(f" {key} ", style="bold cyan")
        if i < len(STEPS) - 1:
            spine.append("━━", style="grey37")

    names = Text("  ")
    for key in STEPS:
        names.append(f"{key:<11}", style=NAME_STYLE[run.status[key]])

    body: list = [header, "", spine, names, ""]

    if state == "queued":
        dep = f" · waits on {', '.join(run.deps)}" if run.deps else ""
        body.append(Text(f"  ○ queued — not started yet{dep}", style="grey50"))
        return body

    if run.active:
        cap = run.pack.captions.get(run.active, "")
        head = Text("  ▶ ", style="bold cyan")
        head.append(run.active, style="bold cyan")
        if cap:
            head.append(f"   {cap}", style="dim italic")
        body.append(head)
        rows = run.subs.get(run.active, [])
        if rows:
            for mark, label, note in rows:
                line = Text("      ")
                line.append(f"{MARK[mark]} ", style=MARK_STYLE[mark])
                line.append(f"{label:<30}", style=MARK_STYLE[mark] if mark != "todo" else "grey50")
                if note:
                    line.append(note, style="dim")
                body.append(line)
        elif run.activity:
            body.append(Text(f"        {run.activity}", style="grey70"))

    if run.flash:
        msg, style = run.flash
        body += ["", Text(f"  {msg}", style=style)]

    footer = Text(
        f"  turn {run.turn} · {_fmt_tokens(run.tokens)} tok "
        f"(peak {_fmt_tokens(run.peak)}) · {_fmt_elapsed(run.sim_seconds)}",
        style="dim",
    )
    body += ["", footer]
    return body


def _help_line(cycle: Cycle, interactive: bool) -> Text:
    if not interactive:
        return Text("  non-interactive play-through · auto-follows the running issue", style="dim italic")
    follow = "on" if cycle.follow else "off"
    paused = " · PAUSED" if cycle.paused else ""
    return Text(
        f"  ↑/↓ or 1-{len(cycle.runs)} select · f follow ({follow}) · space pause · q quit{paused}",
        style="dim",
    )


def render_cycle(cycle: Cycle, *, interactive: bool = True) -> Group:
    focus = cycle.runs[cycle.focus_idx]
    body: list = []
    body += _queue_pane(cycle)
    body += ["", Text("  " + "─" * 64, style="grey37"), ""]
    body += _swimlane_body(focus, cycle.state_of(cycle.focus_idx))
    src = "the real agent-skills-shaper pack" if cycle.pack.root else "baked-in fallback (pack not found)"
    body += [
        "",
        _help_line(cycle, interactive),
        Text(f"  reads {src} · scripted timings, no Claude calls", style="dim italic"),
    ]
    return Group(*body)


# ── the scripted run ─────────────────────────────────────────────────────────

class Director:
    """Drives one Run through the scripted timeline.

    Mutates the Run; rendering is the caller's job. In the non-interactive
    play-through a ``paint`` callback refreshes the Live view on each tick; in
    interactive mode the main thread owns rendering and ``paint`` is None.
    """

    def __init__(self, run: Run, cycle: Cycle, idx: int, *, speed: float,
                 run_flag: threading.Event, paint=None, seed: int = 7):
        self.r = run
        self.cycle = cycle
        self.idx = idx
        self.speed = max(speed, 0.01)
        self.run_flag = run_flag
        self.paint = paint
        self.rng = random.Random(seed)
        self.review_pass = 0

    def _paint_now(self):
        self.run_flag.wait()
        if self.cycle.follow and self.cycle.running_idx is not None:
            self.cycle.focus_idx = self.cycle.running_idx
        if self.paint:
            self.paint()

    def _tick(self, real: float, sim: float, *, tokens: int, turns: int = 0):
        self.r.sim_seconds += sim
        self.r.tokens += tokens
        self.r.turn += turns
        ctx = self.rng.randint(40_000, 93_000)
        self.r.peak = max(self.r.peak, ctx)
        self._paint_now()
        time.sleep(real / self.speed)

    def begin(self, key: str):
        self.r.active = key
        self.r.status[key] = ACTIVE
        self.r.flash = None
        self._paint_now()

    def end(self, key: str):
        self.r.status[key] = DONE
        self.r.activity = ""

    def activity(self, text: str, *, real=0.55, sim=7, tokens=1600, turns=0):
        self.r.activity = text
        self._tick(real, sim, tokens=tokens, turns=turns)

    def sub(self, key: str, idx: int, mark: str, label: str, note: str = "",
            *, real=0.6, sim=9, tokens=2200, turns=0):
        rows = self.r.subs[key]
        while len(rows) <= idx:
            rows.append(["todo", "", ""])
        rows[idx] = [mark, label, note]
        self._tick(real, sim, tokens=tokens, turns=turns)

    # — steps —

    def pickup(self):
        self.begin("pickup")
        for note in (
            "loading issue from Linear",
            "checking blocked_by[] — deps satisfied",
            "rebasing branch onto main",
            "writing exec-state.json · pickup section",
        ):
            self.activity(note)
        self.end("pickup")

    def breakdown(self) -> list[str]:
        self.begin("breakdown")
        self.activity("decomposing into ordered slices, each with a done_when…",
                      real=0.9, sim=14, tokens=4000, turns=1)
        tasks = ["slice 1 · core change", "slice 2 · edge cases", "slice 3 · wiring & config"]
        for i, t in enumerate(tasks):
            self.sub("breakdown", i, "ok", t, "done_when ✓")
        self.end("breakdown")
        return tasks

    def _build_one(self, idx: int, label: str, *, debug=False, simplify=True):
        self.sub("build", idx, "run", label, "RED — failing test", tokens=2600)
        if debug:
            self.sub("build", idx, "run", label, "red loop stuck → exec:debug", real=0.8, tokens=3400, turns=1)
            self.sub("build", idx, "run", label, "root cause: off-by-one in backoff", real=0.7, tokens=2800)
        self.sub("build", idx, "run", label, "GREEN — implementation passes", tokens=3000, turns=1)
        if simplify:
            self.sub("build", idx, "run", label, "exec:simplify — post-green cleanup", tokens=1800)
        self.sub("build", idx, "ok", label, "red→green→commit" + (" (+simplify)" if simplify else ""))

    def build(self, tasks: list[str]):
        self.begin("build")
        for i, t in enumerate(tasks):
            self._build_one(i, t, debug=(i == 1))
        self.end("build")

    def review(self) -> bool:
        """Return True for GO, False for NO-GO."""
        self.begin("review")
        self.review_pass += 1
        personas = self.r.pack.personas
        # Only the risky issue surfaces a security finding, and only on pass 1;
        # the re-review after the fix is clean.
        flagged = self.r.spec.risky and self.review_pass == 1
        security = ("fail", "FINDING · unbounded retry → DoS risk") if flagged else (
            "ok", "GO · retries now bounded" if self.r.spec.risky else "GO · no issues")
        verdicts = {
            "spec-compliance": ("ok", "GO · meets ac_checklist"),
            "security-auditor": security,
            "code-quality": ("ok", "GO · clear, no smells"),
        }
        for i, p in enumerate(personas):
            self.sub("review", i, "run", p, "reviewing diff…", real=0.7, tokens=3200, turns=1)
        go = True
        for i, p in enumerate(personas):
            mark, note = verdicts.get(p, ("ok", "GO"))
            self.sub("review", i, mark, p, note, real=0.6, tokens=900)
            if mark == "fail":
                go = False
        if go:
            self.r.flash = ("verdict: GO — all personas clear", "bold green")
        else:
            self.r.flash = ("verdict: NO-GO — security finding → looping back to build", "bold red")
        self._tick(0.9, 4, tokens=500)
        if go:
            self.end("review")
        return go

    def loop_back_fix(self):
        # review reopened; build re-activates for the fix slice
        self.r.status["review"] = PENDING
        self.r.subs["review"] = []
        self.begin("build")
        self.r.flash = None
        fix_idx = len(self.r.subs["build"])
        self._build_one(fix_idx, "fix · cap retries + add jitter", simplify=True)
        self.end("build")

    def verify(self):
        self.begin("verify")
        self.activity("reading ac_checklist from exec-state.json · pickup section")
        for n in (1, 2, 3):
            self.sub("verify", n - 1, "ok", f"AC {n}", "satisfied by diff", tokens=1500)
        self.r.flash = ("verify: PASS — every AC item met", "bold green")
        self._tick(0.8, 4, tokens=400)
        self.end("verify")

    def finish(self):
        self.begin("finish")
        self.r.flash = None
        for note in (
            "writing PR body · What / Why / Focus",
            "posting review-summary comment to the PR",
            "transitioning issue → In Review",
            f"PR ready: {self.r.spec.pr}",
        ):
            self.activity(note, tokens=1400)
        self.end("finish")

    def run(self):
        self.pickup()
        tasks = self.breakdown()
        self.build(tasks)
        if not self.review():
            self.loop_back_fix()
            self.review()  # second pass → GO
        self.verify()
        self.finish()
        self.r.active = None
        self.r.flash = (f"completed · PR {self.r.spec.pr}", "bold green")
        self._paint_now()


def drive_cycle(cycle: Cycle, *, speed: float, run_flag: threading.Event, paint=None):
    for idx, run in enumerate(cycle.runs):
        cycle.running_idx = idx
        if cycle.follow:
            cycle.focus_idx = idx
        Director(run, cycle, idx, speed=speed, run_flag=run_flag, paint=paint).run()
    cycle.running_idx = None
    cycle.finished = True
    if cycle.follow:
        cycle.focus_idx = len(cycle.runs) - 1
    if paint:
        paint()


# ── keyboard (interactive mode, Unix TTY) ────────────────────────────────────

class RawKeys:
    """cbreak-mode stdin reader yielding logical key names, with a timeout."""

    def __enter__(self):
        import termios
        import tty
        self._termios = termios
        self.fd = sys.stdin.fileno()
        self.old = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        return self

    def __exit__(self, *exc):
        self._termios.tcsetattr(self.fd, self._termios.TCSADRAIN, self.old)

    def read(self, timeout: float) -> list[str]:
        import select
        r, _, _ = select.select([sys.stdin], [], [], timeout)
        if not r:
            return []
        data = os.read(self.fd, 64).decode("utf-8", "ignore")
        keys: list[str] = []
        i = 0
        arrows = {"A": "up", "B": "down", "C": "right", "D": "left"}
        while i < len(data):
            c = data[i]
            if c == "\x1b" and data[i + 1:i + 2] == "[":
                keys.append(arrows.get(data[i + 2:i + 3], ""))
                i += 3
            else:
                keys.append(c)
                i += 1
        return [k for k in keys if k]


def run_interactive(cycle: Cycle, speed: float) -> int:
    run_flag = threading.Event()
    run_flag.set()
    console = Console()
    worker = threading.Thread(target=drive_cycle, kwargs=dict(cycle=cycle, speed=speed, run_flag=run_flag), daemon=True)
    n = len(cycle.runs)
    with Live(render_cycle(cycle), console=console, refresh_per_second=30, transient=False) as live, RawKeys() as keys:
        worker.start()
        while True:
            live.update(render_cycle(cycle))
            for k in keys.read(0.05):
                if k in ("q", "\x03"):
                    return 0
                if k in ("down", "j"):
                    cycle.focus_idx = min(cycle.focus_idx + 1, n - 1)
                    cycle.follow = False
                elif k in ("up", "k"):
                    cycle.focus_idx = max(cycle.focus_idx - 1, 0)
                    cycle.follow = False
                elif k.isdigit() and 1 <= int(k) <= n:
                    cycle.focus_idx = int(k) - 1
                    cycle.follow = False
                elif k == "f":
                    cycle.follow = True
                    if cycle.running_idx is not None:
                        cycle.focus_idx = cycle.running_idx
                elif k == " ":
                    if cycle.paused:
                        run_flag.set()
                        cycle.paused = False
                    else:
                        run_flag.clear()
                        cycle.paused = True


def run_noninteractive(cycle: Cycle, speed: float) -> int:
    console = Console()
    run_flag = threading.Event()
    run_flag.set()
    with Live(render_cycle(cycle, interactive=False), console=console,
              refresh_per_second=30, transient=False) as live:
        drive_cycle(cycle, speed=speed, run_flag=run_flag,
                    paint=lambda: live.update(render_cycle(cycle, interactive=False)))
    console.print()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Swimlanes + queue UX prototype (non-production).")
    ap.add_argument("--speed", type=float, default=1.0, help="playback multiplier (higher = faster)")
    args = ap.parse_args(argv)

    pack = load_pack()
    if pack.root is None:
        Console().print(
            "[yellow]note:[/] agent-skills-shaper pack not found; using baked-in fallback labels. "
            "Set SHAPER_PACK to the pack root for the faithful run.\n"
        )

    specs = execution_order(CYCLE_SPECS)
    runs = [Run(spec=s, pack=pack) for s in specs]
    cycle = Cycle(cycle_id="ABA-FEAT · reliable webhooks", runs=runs, pack=pack)

    interactive = sys.stdin.isatty() and sys.stdout.isatty() and os.name != "nt"
    if interactive:
        try:
            return run_interactive(cycle, args.speed)
        except (ImportError, OSError):
            pass  # no termios / not a real TTY — fall through
    return run_noninteractive(cycle, args.speed)


if __name__ == "__main__":
    raise SystemExit(main())
