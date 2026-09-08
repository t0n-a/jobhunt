# jobhunt

jobhunt is a local-first job-search agent. It finds and scores job postings, tailors an ATS-parseable resume per application, and converts aggregate skill gaps into one concrete, auto-verified daily task that earns a real resume line. Existing open-source tools (such as career-ops, autopilot-jobhunt, and Resume Matcher) handle discovery and tailoring, but none close the loop from skill gap to daily task to verified evidence.

Phases 1 through 3 are built today: the profile subsystem, job discovery with scoring, and the daily skill-gap coach.

## Status

- **Phase 1 (Complete):** Evidence-atom store, guided LLM interview, CV/GitHub/coursework importers, and local web UI.
- **Phase 2 (Complete):** Nine job sources, dedupe into canonical jobs, LLM scoring with track tagging, and a ranked inbox.
- **Phase 3 (Complete):** The skill-gap coach, converting aggregate gaps into concrete daily tasks.
- **Phase 4 (Complete):** Resume tailoring with verifiable facts and ATS self-checking.
- **Phase 5 (Designed, not built):** LinkedIn integration.

## Core Concept: Evidence Atoms

The profile store does not hold a traditional monolithic resume. Instead, it stores discrete **evidence atoms**: granular, bullet-worthy facts (projects, roles, coursework, technical achievements), each tagged with skills and a provenance (`interview`, `cv`, `github`, `coursework`, `quest`, `manual`).

Downstream tailoring selects and rephrases existing atoms for a specific job; it never invents them. Because the user must defend every bullet point in an interview, fabrication is treated as a correctness bug. All LLM-generated atoms enter the database as unconfirmed and must be reviewed before use.

## Requirements

- **OS:** Linux or WSL (reference environment is WSL2 on Windows).
- **Python:** 3.12, pinned via `uv python pin 3.12`. Do not use Python 3.13+, as several dependencies lack compatible wheels.
- **Package manager:** [uv](https://astral.sh/uv).
- **PDF tooling:** A TeX distribution providing `latexmk`, and `poppler-utils` providing `pdftotext` (used during PDF CV import and tailoring ATS checks). On Debian or Ubuntu:
  ```bash
  sudo apt install latexmk texlive-latex-recommended poppler-utils
  ```
  Without them, everything works except the PDF step.
- **LLM provider:** Antigravity CLI (`agy`) by default (see LLM Provider below).

## Setup

```
git clone <repo> && cd jobhunt
uv python install 3.12 && uv python pin 3.12
uv sync
cp config.example.yaml config.yaml
cp .env.example .env
uv run jobhunt serve
```

Then open http://localhost:8000. Both `config.yaml` and `.env` are gitignored.

## LLM Provider

The default provider is `agy`, running Google Gemini models via the Antigravity CLI. Configure `llm.provider`, `llm.model`, and `llm.binary` in `config.yaml`.

- **WSL Path Translation:** When jobhunt runs inside WSL and uses the Windows binary (`agy.exe`), the provider automatically translates temporary JSON-schema paths using `wslpath -w` so the Windows binary can read them.
- **OpenAI Compatible API:** Set `llm.provider: openai_compat` with `OPENAI_BASE_URL` and `OPENAI_API_KEY` in `.env` to use any OpenAI-compatible endpoint (OpenRouter, Gemini, a local server) instead of the Antigravity CLI.
- **Disk Cache:** All LLM responses are cached on disk under `llm.cache_dir` (default: `./data/llm_cache`), keyed by model, prompt, and schema. Re-runs are free.

## Environment Variables

Configured in `.env`:

- `GITHUB_TOKEN`: Optional but recommended. The GitHub importer makes `1 + N` API calls (one for repo metadata plus one per README). Unauthenticated requests allow only 60 requests per hour, hitting the limit for profiles with ~30 repositories. Setting a token raises the rate limit to 5,000 requests per hour.
- `OPENAI_BASE_URL` and `OPENAI_API_KEY`: required when `llm.provider` is `openai_compat`.
- `ITJOBS_API_KEY`: optional. A free key from itjobs.pt/api enables ITJobs.pt, Portugal's largest IT board. Without it that source is skipped.

## Using It

Four ways to populate the profile, all reachable from the web UI:

1. **Guided Interview (`/interview`):** Asks follow-up questions to mine specifics (architecture decisions, bottlenecks, metrics) you have not written down. This is the highest-value path when your CV is thin.
2. **CV Import (`/import`):** Parses `.pdf`, `.docx`, or `.txt` resumes into structured atoms.
3. **GitHub Import (`/import`):** Scans non-fork public repositories (forks are ignored since a fork is not evidence of your own work) and extracts project atoms.
4. **Coursework Import (`/import`):** Turns a pasted course list or transcript into education atoms. The prompt is deliberately conservative — a course is weaker evidence than a project, and generic non-technical courses are skipped.

All imported items land on `/atoms`, where you can confirm, edit, or delete each atom.

## Finding Jobs

```bash
uv run jobhunt scan
```

Fetches from every enabled source, deduplicates postings that describe the same
opening, and scores each one against your **confirmed** atoms. Results appear at
`/jobs`, ranked by fit, with a track breakdown ("backend · 12, ai · 6") so the
data can tell you what kind of role you actually match.

Scoring refuses to run against an empty profile: with nothing to match, the
model rates whichever job demands least, which is a confidently wrong ranking.
Build the profile first.

The scan reports which sources failed or timed out rather than only a total, and
one slow or broken source never aborts the rest. Scoring is cached per job, so a
re-scan only pays for genuinely new postings — but the first run scores every
job sequentially at roughly ten seconds each, so start it and walk away.

You can also paste any job you found elsewhere into the box on `/jobs`; it enters
the same scoring pipeline.

## Daily Coaching

The coach converts your aggregated skill gaps into concrete, day-sized tasks:

- **`/gaps`:** Shows your missing skills ranked by fit-weighted job count (e.g. "missing from 14 of your best-fit jobs"), with a button to start a multi-day project (quest) to close one.
- **`/today`:** Displays exactly one task at a time. The duration matches your chosen energy level (low 15 / normal 45 / high 120 minutes). It states why the task matters and the exact CV line it earns *before* the work starts. Features "Done" and "Not this one" buttons, plus "Check my repo" to verify work against GitHub. Completing a task writes an unconfirmed EvidenceAtom (with `provenance=quest`) that you must still review.
- **`jobhunt today`:** Prints your current task. This is used by the Claude Code SessionStart hook at `scripts/jobhunt-session-hook.sh`.
- **`jobhunt notify`:** Pushes the current task to Telegram. Requires `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.

**No streaks by design:** The coach shows only one task at a time and maintains NO streak counters. A broken-streak screen converts a lapse into abandonment, so missing days must cost nothing.

## Tailoring

Opening a job and pressing "Tailor a resume for this" produces an Application: extracted ATS keywords, selected evidence, rewritten bullets, and a cover letter.

Because an LLM fabricates facts when rewriting a resume, the system structurally guarantees this cannot happen:
- Every bullet is checked against the evidence atom it came from. A bullet stating a number or naming a technology the evidence does not support is dropped, and shown separately with the reason — so the user can tell a verifier bug from a model that is inventing.
- Only confirmed atoms are eligible; unconfirmed atoms are unreviewed model output and cannot reach a document an employer reads.
- Bullets can be edited; an edited bullet is marked as the user's own and is not re-checked, because those are their words and they are responsible for them.

"Render the PDF" builds it with `latexmk`, then re-reads the result with `pdftotext` and reports keyword coverage — a PDF that looks perfect on screen can parse to nothing, and this is what turns that silent failure into a number. 

You can set optional `phone`, `linkedin`, and `website` fields in `config.yaml` to include them on the generated PDF.

## Repository Layout

- `src/jobhunt/config.py`: Configuration models and YAML loading.
- `src/jobhunt/db.py`: SQLite engine and session lifecycle.
- `src/jobhunt/llm/`: Provider abstraction (`agy`, fake) and disk cache.
- `src/jobhunt/profile/`: Models, atom store, importers (CV, GitHub, coursework), and interview engine.
- `src/jobhunt/sources/`: One adapter per job board, plus ATS board discovery.
- `src/jobhunt/jobs/`: Raw postings, canonical jobs, dedupe, and the job store.
- `src/jobhunt/matching/`: LLM scoring, track tagging, and skill-name canonicalisation.
- `src/jobhunt/scan.py`: Scan orchestration with per-source isolation and deadlines.
- `src/jobhunt/web/`: FastAPI app, Jinja2 templates, HTMX partials.

## Development

- Run the full test suite:
  ```bash
  uv run pytest
  ```
  Tests never touch the network.
- Run the live integration test:
  ```bash
  uv run pytest -m live
  ```
  Deselected by default (`addopts = "-m 'not live'"`). Requires authentication and network access to hit the real `agy` CLI. Every other test mocks the subprocess boundary, and three real bugs in the provider were only ever caught by running the real binary.
- Check and format code:
  ```bash
  uv run ruff check src tests
  uv run ruff format src tests
  ```

## Licence

MIT. `NOTICE` records third-party attribution.
