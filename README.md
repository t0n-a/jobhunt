# jobhunt

jobhunt is a local-first job-search agent. It finds and scores job postings, tailors an ATS-parseable resume per application, and converts aggregate skill gaps into one concrete, auto-verified daily task that earns a real resume line. Existing open-source tools (such as career-ops, autopilot-jobhunt, and Resume Matcher) handle discovery and tailoring, but none close the loop from skill gap to daily task to verified evidence.

Only Phase 1 (the profile subsystem) is built today.

## Status

- **Phase 1 (Complete):** Evidence-atom store, guided LLM interview, CV/GitHub/coursework importers, and local web UI.
- **Phases 2–5 (Designed, not built):** Job discovery and scoring, the skill-gap coach, resume tailoring, and LinkedIn integration.

## Core Concept: Evidence Atoms

The profile store does not hold a traditional monolithic resume. Instead, it stores discrete **evidence atoms**: granular, bullet-worthy facts (projects, roles, coursework, technical achievements), each tagged with skills and a provenance (`interview`, `cv`, `github`, `coursework`, `quest`, `manual`).

Downstream tailoring selects and rephrases existing atoms for a specific job; it never invents them. Because the user must defend every bullet point in an interview, fabrication is treated as a correctness bug. All LLM-generated atoms enter the database as unconfirmed and must be reviewed before use.

## Requirements

- **OS:** Linux or WSL (reference environment is WSL2 on Windows).
- **Python:** 3.12, pinned via `uv python pin 3.12`. Do not use Python 3.13+, as several dependencies lack compatible wheels.
- **Package manager:** [uv](https://astral.sh/uv).
- **PDF parser:** `poppler-utils` for `pdftotext` (used during PDF CV import).
  ```bash
  sudo apt install poppler-utils
  ```
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
- **OpenAI Compatible API:** The `openai_compat` provider option is declared in configuration but not yet implemented (raises `NotImplementedError`; arrives in Phase 2).
- **Disk Cache:** All LLM responses are cached on disk under `llm.cache_dir` (default: `./data/llm_cache`), keyed by model, prompt, and schema. Re-runs are free.

## Environment Variables

Configured in `.env`:

- `GITHUB_TOKEN`: Optional but recommended. The GitHub importer makes `1 + N` API calls (one for repo metadata plus one per README). Unauthenticated requests allow only 60 requests per hour, hitting the limit for profiles with ~30 repositories. Setting a token raises the rate limit to 5,000 requests per hour.
- `OPENAI_BASE_URL` and `OPENAI_API_KEY`: Placeholders for the not-yet-implemented provider.

## Using It

Three ways to populate the profile, all reachable from the web UI:

1. **Guided Interview (`/interview`):** Asks follow-up questions to mine specifics (architecture decisions, bottlenecks, metrics) you have not written down. This is the highest-value path when your CV is thin.
2. **CV Import (`/import`):** Parses `.pdf`, `.docx`, or `.txt` resumes into structured atoms.
3. **GitHub Import (`/import`):** Scans non-fork public repositories (forks are ignored since a fork is not evidence of your own work) and extracts project atoms. Pasted coursework or transcripts can also be imported here.

All imported items land on `/atoms`, where you can confirm, edit, or delete each atom.

## Repository Layout

- `src/jobhunt/config.py`: Configuration models and YAML loading.
- `src/jobhunt/db.py`: SQLite engine and session lifecycle.
- `src/jobhunt/llm/`: Provider abstraction (`agy`, fake) and disk cache.
- `src/jobhunt/profile/`: Models, atom store, importers (CV, GitHub, coursework), and interview engine.
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
