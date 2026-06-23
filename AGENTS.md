# AGENTS.md

## Verification

Run the test suite with:

```bash
uv run pytest
```

Optional lint:

```bash
uv run ruff check .
```

## Versioning

The package version is derived from the git commit count (`0.1.<count>`), which
`hatch_build.py` sets at build time — no static version to bump and no git tags.
`drain-cycle --version` and the Honeycomb `service.version` report the live value
plus the short SHA.

`uv tool list` and the packaged metadata refresh only when the editable install is
rebuilt. Install the post-commit hook that rebuilds after each commit:

```bash
sh scripts/install-git-hooks.sh
```

It is idempotent and chains after any existing hooks.
