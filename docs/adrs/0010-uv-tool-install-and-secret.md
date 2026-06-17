# ADR 0010: Installed as a `uv tool`, with the secret read from `~/.drain-cycle/.env`

**Date:** 2026-05-23
**Status:** Accepted
**Migrated from:** docs/design-decisions.md §6

`drain-cycle` is installed via `uv tool install`, which puts the executable on `$PATH` in an isolated environment. The Linear API key is read from `~/.drain-cycle/.env` (shell-exported vars still win), beside the `repos.yml` config and the `runs/` logs the tool already kept there.

**Alternatives considered.**

- *`pipx`*. Functionally equivalent for installing a Python CLI in isolation. Rejected because `uv` already anchors this repo's stack (`uv.lock`, the `mise.toml` Python pin) — adding `pipx` spends an innovation token on a second tool that does the same job.
- *Publish to PyPI*. Lets anyone `uv tool install drain-cycle` by name, but buys a release-and-versioning burden — tagging, changelogs, a name on the index — that a single-operator tool doesn't earn. `uv tool install git+https://…` already covers install-from-anywhere with no release step.

**The secret-loading change this forced.** The CLI used to load `.env` only from the repo root (`Path(__file__).parent.parent`). Once installed, the package lives in the isolated tool env, where that path has no `.env` — so the key has to live somewhere stable. The load order is now shell env → `~/.drain-cycle/.env` → repo-root `.env`, first hit wins (`load_dotenv` defaults to `override=False`). The repo-root entry survives only as a dev-checkout fallback (it works under `--editable`, where `__file__` still points into the checkout); the installed tool reads the key from `~/.drain-cycle/.env`.

**Out-of-v1 deliberately.** No PyPI release. No `drain-cycle init` command to scaffold `~/.drain-cycle/` — a missing `repos.yml` already halts with an actionable message that prints the expected shape, and `docs/repos.example.yml` is a copyable template, which is enough for a single operator.
