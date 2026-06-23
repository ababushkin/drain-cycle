#!/bin/sh
# Install the drain-cycle post-commit version-bump trigger.
#
# The version is derived from the git commit count at build time (see
# hatch_build.py), so `uv tool list` and the packaged metadata only refresh when
# the editable install is rebuilt. This hook reruns that rebuild after each
# commit. Idempotent: re-running is a no-op once the block is present, and it
# chains after any existing hook content (e.g. the Entire CLI hooks) instead of
# replacing it.
set -eu

hooks_dir="$(git rev-parse --git-common-dir)/hooks"
hook="$hooks_dir/post-commit"
marker=">>> drain-cycle version-bump >>>"

mkdir -p "$hooks_dir"

if [ -f "$hook" ] && grep -qF "$marker" "$hook"; then
  echo "drain-cycle: post-commit version-bump hook already installed"
  exit 0
fi

if [ ! -f "$hook" ]; then
  printf '%s\n' '#!/bin/sh' >"$hook"
fi

cat >>"$hook" <<'HOOK'

# >>> drain-cycle version-bump >>>
# Refresh the editable drain-cycle install so `uv tool list` and the packaged
# metadata track the new commit count. --reinstall-package busts uv's build cache
# (a new commit changes no tracked file, so --force alone reuses the stale wheel).
# Skipped inside a linked worktree: drained issues commit constantly and must not
# repoint the global install at a worktree.
case "$(git rev-parse --git-dir 2>/dev/null)" in
  */worktrees/*) : ;;
  *)
    if command -v uv >/dev/null 2>&1; then
      uv tool install --reinstall-package drain-cycle --editable "$(git rev-parse --show-toplevel)" -q >/dev/null 2>&1 || true
    fi
    ;;
esac
# <<< drain-cycle version-bump <<<
HOOK

chmod +x "$hook"
echo "drain-cycle: installed post-commit version-bump hook -> $hook"
