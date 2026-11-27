# Job Collector: Implementation Plan (claude -p edition)

A nightly pipeline that collects Canadian software and data engineering jobs, filters and scores them with Claude, and emails a ranked digest. Last updated November 27, 2026.

## 1. Overview

**Goal:** wake up to a curated, ranked inbox of relevant jobs instead of manually searching Indeed, LinkedIn and niche boards.

**Pipeline:** collect → dedupe → hard-filter → LLM score → email digest.

**Key decisions**

- **Sources:** LinkedIn and Indeed job-alert emails (parsed from a Gmail label), plus the Adzuna API (`country=ca`).
- **Language and state:** Python 3.11+, SQLite for dedupe and score cache, config in `config.yaml`, secrets in `.env` (never committed).
- **Scoring:** the Claude Code CLI in headless print mode (`claude -p`), authenticated with a Claude Pro subscription. No `anthropic` SDK and no `ANTHROPIC_API_KEY` anywhere.
- **Scheduling:** GitHub Actions cron in a private repo.
- **Delivery:** HTML email digest over Gmail SMTP.

## 2. Before you start (about 15 minutes)

### Accounts and keys

- **Claude Code on your Pro plan.** Confirm `claude -p "say hi"` works in the VS Code terminal. Make sure `ANTHROPIC_API_KEY` is **not** set in your shell, because an API key takes priority over subscription login and would bill you through the API.
- **Usage budget.** Programmatic usage (`claude -p`, Agent SDK) draws from its own budget, separate from interactive Claude Code limits. A nightly batch of a few dozen jobs is small, but keep the `--limit` guard against runaway loops, and check current limits in your account settings.
- **Personal use only.** Personal scripts and local development are fine. If this becomes a product for other people, switch to an API key.
- **Gmail app password.** Turn on 2-Step Verification, then create an app password. This enables IMAP and SMTP without an OAuth setup, which matters on GitHub Actions.
- **Adzuna API keys.** Free at developer.adzuna.com, using `country=ca`.

### Alert emails

1. Create daily job alerts on LinkedIn and Indeed ("Data Engineer", "Software Engineer", "Analytics Engineer"; location Canada or Remote).
2. In Gmail, create a filter that applies the label `job-alerts` to mail from those senders. Check the real sender addresses on the first emails you receive. They are typically `jobalerts-noreply@linkedin.com` and `alert@indeed.com`.
3. Let alerts collect for a day or two so you have real samples to build the parsers against.

### Project skeleton

```bash
mkdir job-collector && cd job-collector
git init && python -m venv .venv && source .venv/bin/activate
code .
claude
```

**Prompt for Claude Code:**

> Run /init, then create a CLAUDE.md for this project: a nightly Python job collector for a Software/Data Engineer targeting Canadian roles. Pipeline: collect → dedupe → filter → LLM score → email digest. Python 3.11+, SQLite for state, config in `config.yaml`, secrets in `.env` (never committed), pytest for tests, type hints, small modules. LLM scoring uses the Claude Code CLI in headless mode (`claude -p`) with my Pro subscription. Do not add the `anthropic` SDK and do not use ANTHROPIC\_API\_KEY anywhere. Create the folder structure below and a `.gitignore`.

### Target layout

```
job-collector/
├── CLAUDE.md
├── config.yaml          # keywords, exclusions, locations, rubric, model
├── .env                 # secrets (gitignored)
├── profile.md           # résumé summary + preferences
├── src/jobcollector/
│   ├── models.py        # Job dataclass
│   ├── sources/         # email_alerts.py, adzuna.py
│   ├── store.py         # SQLite dedupe + score cache
│   ├── filters.py
│   ├── claude_cli.py    # run_claude() wrapper around `claude -p`
│   ├── scorer.py
│   ├── digest.py
│   └── main.py
├── tests/fixtures/      # saved sample alert emails
└── .github/workflows/nightly.yml
```

## 3. Phase 1: Collect and dedupe (weekend 1)

**Goal:** `python -m jobcollector.main --dry-run` prints a clean, deduplicated list of new jobs from alert emails and Adzuna.

### Steps

1. **`Job` model:** title, company, location, url, source, description (may be empty), posted\_at, and a `job_id` hash.
2. **Email parser:** connect over IMAP, select the `job-alerts` label, fetch unread messages, and parse the HTML with BeautifulSoup. Write one parser per sender, since LinkedIn and Indeed lay out their emails differently.
3. **Canonical URLs:** strip tracking redirects. Extract LinkedIn's `/jobs/view/<id>` and Indeed's `jk=<id>` so dedupe works.
4. **Adzuna source:** a Canada search, posted within the last day, using your keywords.
5. **Dedupe store:** a SQLite table keyed on `job_id`. Use the source's job ID when available, otherwise normalized title + company. Mark emails as read only after a successful run.

### Prompts for Claude Code

> Save 2 LinkedIn and 2 Indeed alert emails as .eml files in tests/fixtures/ (I'll export them from Gmail). Write `sources/email_alerts.py` with separate parsers for each sender, extracting title, company, location and a canonical job URL with tracking parameters stripped. Write pytest tests against the fixtures first, then make them pass.

> Add `sources/adzuna.py` for country=ca using ADZUNA\_APP\_ID/ADZUNA\_APP\_KEY from .env, then `store.py` with SQLite dedupe, then wire everything together in `main.py` with a `--dry-run` flag that prints results without marking emails as read.

**Done when:** you run it twice in a row; the first run shows new jobs and the second shows none.

## 4. Phase 2: Filter and score with claude -p (weekend 2)

**Goal:** every job that survives the filter gets a 0 to 100 score with reasons, cached in SQLite, using only your Pro subscription.

### Steps

1. **`config.yaml` rules:**

```yaml
include_any: [data engineer, software engineer, analytics engineer, python, spark, dbt]
exclude_any: [director, principal, us citizen, security clearance, intern]
locations_ok: [canada, remote, toronto, ontario]
scoring:
  model: haiku
  batch_size: 10
  timeout_seconds: 120
```

2. **`filters.py`:** case-insensitive, word-boundary matching. Log why each job was dropped so you can tune the rules.
3. **`profile.md`:** your stack, years of experience, preferred domains, minimum rate, and deal-breakers.
4. **`claude_cli.py`:** a single `run_claude(prompt, schema) -> dict` function that:
   - runs `claude -p --model <model> --output-format json --json-schema <schema>` and sends the prompt on stdin;
   - runs from an empty temp directory with no `--allowedTools`, so Claude has no tool access and only reads and replies;
   - reads `structured_output` from the CLI's JSON wrapper, falling back to parsing `result`;
   - raises clear errors for a non-zero exit, a timeout, or an auth or usage-limit failure, with stderr in the message.
5. **`scorer.py`:** batches of 10 jobs per call, Pydantic validation, one retry per batch, and a score cache keyed on `job_id`.

### Scoring rubric

| Criterion | Weight |
| --- | --- |
| Stack match | 40 |
| Seniority fit | 20 |
| Canada eligibility / remote | 20 |
| Domain interest | 10 |
| Compensation | 10 |

### Expected output shape

```json
{"results": [{"job_id": "...", "score": 82, "stack_match": ["python", "airflow"], "reasons": "...", "red_flags": ["contract via agency"]}]}
```

### Prompts for Claude Code

> Implement `filters.py` driven by config.yaml, returning (kept, dropped\_with\_reason). Add tests for edge cases like "Senior Data Engineer" vs "Director of Data Engineering".

> Create `claude_cli.py` with `run_claude(prompt: str, schema: dict, model: str, timeout: int) -> dict`. It must call the Claude Code CLI with `subprocess`, not the Anthropic SDK: `claude -p --model <model> --output-format json --json-schema <json.dumps(schema)>`, with the prompt passed on stdin. Run it with cwd set to a fresh empty temp directory and without `--allowedTools`. Parse the CLI's JSON output, returning `structured_output` if present, otherwise `json.loads(result)`. Define clear exception types for non-zero exit (include stderr), timeout, and unparseable output. Do not read or set ANTHROPIC\_API\_KEY.

> Implement `scorer.py` using `run_claude`. Define Pydantic models `JobScore` (job\_id, score 0–100, stack\_match, reasons, red\_flags) and `Batch` (results: list\[JobScore\]), and pass `Batch.model_json_schema()` as the schema. Build the prompt from profile.md, this rubric (stack match 40, seniority fit 20, Canada eligibility/remote 20, domain interest 10, compensation 10), and the jobs as JSON. Score in batches of `batch_size` from config. If validation fails or a job\_id is missing, retry that batch once, then log and skip it. Cache scores in SQLite by job\_id. Add a `--limit N` flag to main.py. Write pytest tests that mock `run_claude`, so tests never call the real CLI.

**Done when:** `python -m jobcollector.main --dry-run --limit 20` prints scored jobs and the top 5 look right to you. Expect to adjust the rubric and `profile.md` two or three times before they do.

## 5. Phase 3: Deliver and automate (weekend 3)

**Goal:** every morning a ranked digest arrives in your inbox with nothing to run by hand.

### Steps

1. **`digest.py`:** an HTML email with tiers 80+, 60 to 79, and a collapsed "others" section. Each entry shows title, company, location, score, a one-line reason and red flags, linked to the job. Send over Gmail SMTP with the same app password. If nothing new turned up, send a short "No new matches" note so you know the run happened.
2. **Health section:** show each source's status, plus a scoring-health line with jobs scored, batches failed, and the reason for any CLI failure (auth, usage limit, timeout). An expired token or used-up budget then shows up the next morning.
3. **Subscription token for CI:** on your machine, run `claude setup-token`. It generates a long-lived OAuth token for CI pipelines and scripts where browser login isn't available. Save it as the repo secret `CLAUDE_CODE_OAUTH_TOKEN`. It lasts about a year, so set a calendar reminder to renew it.
4. **GitHub Actions** (private repo):
   - `cron: "0 10 * * *"` is 5 a.m. Eastern in winter (the cron runs in UTC and may start a few minutes late), plus `workflow_dispatch` for manual runs.
   - Install Node, then Claude Code, then the Python dependencies.
   - Run a smoke test before the pipeline so an auth failure stops the run immediately.
   - Commit `jobs.db` back to the repo to keep state between runs.
5. **Feedback loop (optional):** keep a `feedback.csv` of thumbs up/down ratings and include your last \~10 ratings in the scoring prompt as examples.

### Required secrets

| Secret | Purpose |
| --- | --- |
| `CLAUDE_CODE_OAUTH_TOKEN` | Subscription auth for `claude -p` (from `claude setup-token`) |
| `GMAIL_USER` | Gmail address for IMAP and SMTP |
| `GMAIL_APP_PASSWORD` | Gmail app password |
| `ADZUNA_APP_ID` | Adzuna API |
| `ADZUNA_APP_KEY` | Adzuna API |

Do **not** define `ANTHROPIC_API_KEY`.

### Prompts for Claude Code

> Build `digest.py`: an HTML email grouped by score tier with a mobile-friendly inline-CSS layout, sent over Gmail SMTP using GMAIL\_USER/GMAIL\_APP\_PASSWORD. Include a source-health section and a scoring-health line (jobs scored, batches failed, and the reason for any Claude CLI failure: auth, usage limit, or timeout). Add a `--preview` flag that writes digest.html locally instead of sending.

> Create `.github/workflows/nightly.yml` that runs daily at 10:00 UTC and on workflow\_dispatch; sets up Python 3.11 and Node 20, then runs `npm install -g @anthropic-ai/claude-code` and installs the Python dependencies; passes `CLAUDE_CODE_OAUTH_TOKEN`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` from repo secrets as env vars (do not define ANTHROPIC\_API\_KEY); runs a smoke-test step, `claude -p "reply OK" --model haiku`, and fails fast with a clear message if it doesn't succeed; runs the pipeline, then commits jobs.db back if it changed; and keeps secrets out of the logs.

> (Optional) Add a feedback loop: read `feedback.csv` (job\_id, rating, note) and include the 10 most recent rated jobs as few-shot examples in the scoring prompt.

**Done when:** three mornings in a row the digest arrives without you touching anything.

## 6. Working well with Claude Code

- **Fixtures first.** Real sample emails keep parsing code grounded in what the alerts actually contain.
- **One module per session.** Small, focused prompts give cleaner code than "build everything".
- **Never call the real CLI in tests.** Mocking `run_claude` keeps tests fast and avoids spending your usage budget.
- **Keep CLAUDE.md current.** Record decisions as you make them, such as "scoring goes only through claude\_cli.run\_claude; no Anthropic SDK" and "LinkedIn IDs are canonical".
- **Use plan mode for Phase 3** (Shift+Tab). The workflow and state-persistence steps are worth reviewing before any code is written.

## 7. Risks and maintenance

| Risk | Mitigation |
| --- | --- |
| Alert email layouts change and parsers break | Fixture-based tests, plus the source-health section in the digest |
| OAuth token expires (about 1 year) | Calendar reminder; scoring-health line and CI smoke test flag it early |
| Monthly programmatic budget runs out | Small batches, `--limit` guard, scoring-health line in the digest |
| `ANTHROPIC_API_KEY` set by accident and bills the API | Never define it in `.env` or CI; check your shell before running locally |
| Terms of use change or the project becomes a product | Re-check Anthropic's current terms; move to an API key if serving other people |
| Scraping restrictions on LinkedIn and Indeed | Use alert emails, Adzuna and other official APIs or feeds instead of scraping |

## 8. Progress checklist

- [ ] Accounts, keys and Gmail label set up
- [ ] Project skeleton and CLAUDE.md created
- [ ] Phase 1: email parsers, Adzuna source, dedupe store, `--dry-run`
- [ ] Phase 2: filters, `claude_cli.py`, `scorer.py`, tests with mocked CLI
- [ ] Phase 2: top 5 scores look right after rubric tuning
- [ ] Phase 3: digest email and health section
- [ ] Phase 3: `claude setup-token` and GitHub secrets
- [ ] Phase 3: nightly workflow with smoke test and state commit
- [ ] Three mornings of digests in a row
- [ ] (Optional) Feedback loop
