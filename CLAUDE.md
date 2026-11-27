# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Nightly Python job collector for a Software/Data Engineer targeting Canadian roles.

Status: scaffold only. The modules contain no implementation yet; the sections below describe the intended design.

## Commands

Run from the repo root (Windows, PowerShell; venv in `.venv`):

```powershell
.venv\Scripts\python -m pip install -e ".[dev]"     # not installed yet
.venv\Scripts\python -m pytest                      # all tests
.venv\Scripts\python -m pytest tests/test_x.py::test_name   # single test
```

`pyproject.toml` puts `src` on pytest's path, so tests can import `jobcollector` without installing it.

## Hard constraints

- **No `anthropic` SDK, and no use of `ANTHROPIC_API_KEY` anywhere** (code, config, `.env`, CI). LLM scoring goes only through the Claude Code CLI in headless mode (`claude -p`), authenticated by the user's Pro subscription login.
- Never commit `.env`. Secrets live in `.env`; all other settings live in `config.yaml`, which is committed and must contain no secrets.
- Python 3.11 is the minimum. The local venv is newer (3.14), so do not use syntax or stdlib features added after 3.11.
- Type hints on all functions; small, single-purpose modules.
- SQLite holds all state. Tests use pytest.

## Architecture

```
collect -> dedupe -> filter -> LLM score -> email digest
```

Code lives in `src/jobcollector/`:

| Stage | Module |
| --- | --- |
| collect | `sources/` (one module per source: `email_alerts.py`, `adzuna.py`) |
| dedupe | `store.py` |
| filter | `filters.py` |
| LLM score | `scorer.py`, via `run_claude()` in `claude_cli.py` |
| email digest | `digest.py` |

`main.py` runs the stages; `models.py` holds the `Job` dataclass they share. `store.py` is the only module containing SQL (dedupe, score cache, emailed state), and `claude_cli.py` is the only one that invokes `claude`. `config.yaml` holds keywords, exclusions, locations, rubric and model; `profile.md` holds the résumé summary and preferences used for scoring. Sample alert emails for tests go in `tests/fixtures/`; remove personal data before committing them. The SQLite file is `data/jobs.db`, which the nightly workflow commits; everything else in `data/` and the logs in `logs/` are gitignored.

Design rules across stages:

- Stage order is a cost control: scoring consumes the Pro plan's usage limits, so only new postings that pass the filters reach it. Persist scores so a posting is never scored twice.
- Runs are unattended and must be safe to rerun. A failing source must not abort the others, unscored postings are retried next run, and a posting is marked as emailed only after the send succeeds.
- Tests make no network, SMTP or real `claude` calls; replace these at the module boundary.

## Calling `claude -p`

- Do not use `--bare`: it never reads the subscription login and only accepts API-key auth.
- Job descriptions are untrusted input. Disable tools (`--tools ""`), add `--restricted --strict-mcp-config`, and pass the prompt on stdin: never interpolate posting text into a shell command.
- Use `--output-format json` with `--json-schema`, and validate the result before storing it.
- Use `--no-session-persistence`, and run from a directory outside this repo so this CLAUDE.md is not loaded into the scoring context.
- Set a subprocess timeout. Treat a usage limit or expired login as "leave unscored and retry next run", not as a low score.
- Read the parsed score from `structured_output` and check `is_error` (verified with CLI 2.1.332).
- Pass `--model` explicitly from `config.yaml`; the CLI default is the user's interactive model (Opus when checked), which uses up Pro limits faster.
