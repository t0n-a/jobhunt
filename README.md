# jobhunt

jobhunt is a local-first job-search agent. It finds and scores job postings, tailors an ATS-parseable resume per application, and converts aggregate skill gaps into one concrete, auto-verified daily task that earns a real resume line. Existing open-source tools (such as career-ops, autopilot-jobhunt, and Resume Matcher) handle discovery and tailoring, but none close the loop from skill gap to daily task to verified evidence.

All five phases are built: the profile subsystem, job discovery with scoring, the daily skill-gap coach, resume tailoring, and LinkedIn profile buildout. Phase 5 covers the profile text only — posting is not built, and the Status section says exactly where the line falls.

## Status

- **Phase 1 (Complete):** Evidence-atom store, guided LLM interview, CV/GitHub/coursework importers, and local web UI.
- **Phase 2 (Complete):** Nine job sources, dedupe into canonical jobs, LLM scoring with track tagging, and a ranked inbox.
- **Phase 3 (Complete):** The skill-gap coach, converting aggregate gaps into concrete daily tasks.
- **Phase 4 (Complete):** Resume tailoring with verifiable facts and ATS self-checking.
- **Phase 5 (Partially built):** LinkedIn profile buildout — generate, verify and approve the profile text.
- **Phase 5, remaining:** post queue, posting via the official API, outreach drafts. Needs a registered LinkedIn app.

## Core Concept: Evidence Atoms

The profile store does not hold a traditional monolithic resume. Instead, it stores discrete **evidence atoms**: granular, bullet-worthy facts (projects, roles, coursework, technical achievements), each tagged with skills and a provenance (`interview`, `cv`, `github`, `coursework`, `quest`, `manual`).

Downstream tailoring selects and rephrases existing atoms for a specific job; it never invents them. Because the user must defend every bullet point in an interview, fabrication is treated as a correctness bug. All LLM-generated atoms enter the database as unconfirmed and must be reviewed before use.

## Requirements

- **OS:** Linux or WSL (reference environment is WSL2 on Windows). Windows natively also runs the full suite; the only difference is that you need a Windows TeX distribution such as MiKTeX for the PDF step.
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

## First Run, In Order

The Home page shows the four stages and names the one thing to do next. Everything in this system is downstream of **confirmed evidence atoms**, so the order matters more than it looks:

1. **Put something in the profile.** Import a CV, your GitHub, or a course list at `/import`, or talk to the counselor at `/counselor`.
2. **Confirm the atoms at `/atoms`.** Imported atoms arrive *unconfirmed* — they are unreviewed model output, and nothing downstream will touch them until you have read each one. This is the step people skip.
3. **`uv run jobhunt scan`.** Finds and scores jobs against what you confirmed. Start it and walk away; the first run is slow.
4. **Then everything else works:** `/jobs` to see ranked matches, `/gaps` and `/today` for daily tasks, "Tailor a resume for this" on any job, and `/linkedin` for profile text.

Steps 3 onward all **refuse to run** against an empty profile rather than producing something plausible. That is deliberate: with no evidence to match against, the scorer rates whichever job asks for least, and a generator with nothing to draw on writes confident fiction. An early refusal is the honest answer.

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

1. **Counselor (`/counselor`):** One ongoing conversation. The counselor reads your atoms, the gaps in your scored jobs and your GitHub, asks one question at a time, and proposes an evidence atom the moment you say something CV-worthy — you accept, edit, or reject it in the chat. It also keeps notes (goals, constraints, preferences) that the job scorer and the LinkedIn text use; you can see and delete them on the page. This is the highest-value path when your CV is thin.
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

- **`/gaps` (the Gaps tab):** Shows your missing skills ranked by fit-weighted job count (e.g. "missing from 14 of your best-fit jobs"), with a button to start a multi-day project (quest) to close one.
- **`/today`:** Displays exactly one task at a time. The duration matches your chosen energy level (low 15 / normal 45 / high 120 minutes). It states why the task matters and the exact CV line it earns *before* the work starts. Features "Done" and "Not this one" buttons, plus "Check my repo" to verify work against GitHub. Completing a task writes an unconfirmed EvidenceAtom (with `provenance=quest`) that you must still review.
- **`jobhunt today`:** Prints your current task. This is used by the Claude Code SessionStart hook at `scripts/jobhunt-session-hook.sh`.
- **`jobhunt notify`:** Pushes the current task to Telegram. Requires `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.

**No streaks by design:** The coach shows only one task at a time and maintains NO streak counters. A broken-streak screen converts a lapse into abandonment, so missing days must cost nothing.

## Tailoring

Opening a job and pressing "Tailor a resume for this" produces an Application: extracted ATS keywords, selected evidence, rewritten bullets, and a cover letter.

Because an LLM fabricates facts when rewriting a resume, the system bounds the risk structurally:
- Every bullet is checked against the evidence atom it came from, and the skills line is checked the same way. A bullet stating a number or naming a technology the evidence does not support is dropped, and shown separately with the reason — so the user can tell a verifier bug from a model that is inventing.
- The headline, summary, and the employer and dates on each entry are model-written and are NOT checked, so they are shown on the application page for the user to read before sending.
- Only confirmed atoms are eligible; unconfirmed atoms are unreviewed model output and cannot reach a document an employer reads.
- Bullets can be edited; an edited bullet is marked as the user's own and is not re-checked, because those are their words and they are responsible for them.

"Render the PDF" builds it with `latexmk`, then re-reads the result with `pdftotext` and reports keyword coverage — a PDF that looks perfect on screen can parse to nothing, and this is what turns that silent failure into a number.

You can set optional `phone`, `linkedin`, and `website` fields in `config.yaml` to include them on the generated PDF.

### LinkedIn

`/linkedin` builds your profile text — headline, About, experience entries, projects and skills — from your confirmed evidence atoms, aimed at the roles you have actually applied to (or, with no applications yet, at the skills on your confirmed atoms).

Every field is checked against your evidence before it is stored. Entries carry the atom they came from and get the same per-atom check as a resume bullet: the atom must state every number and name every technology the entry claims. The headline and About section blend several atoms at once, so they are checked against your evidence as a whole — every number and technology in them appears somewhere in your confirmed atoms, though not necessarily in one. Anything that fails is dropped and shown to you under "Dropped, unverified", so you can tell a bug from a model inventing things.

Nothing is published. LinkedIn exposes no write API for profile fields, so you copy each field across yourself — and each field has to be approved before it counts as reviewed. Editing a field withdraws its approval, so approved text is always text you have read in its final form. Rebuilding creates a new version and shows you what changed per field, so you only re-paste what moved.

Fields longer than LinkedIn accepts are flagged rather than cut. The limits are recorded in `src/jobhunt/linkedin/fields.py`; LinkedIn does not publish them formally, so if one is wrong, that is the one line to change.

**Not yet built:** posting, the post queue, and outreach drafts.

### Counselor

The counselor is not an interviewer with a topic box. It decides what to ask from what the system already knows: atoms with no numbers get probed, skills your best-fit jobs want get asked about, repositories no atom covers get pulled up. Each turn the model answers with one structured action — ask, look something up, propose an atom, write a note, or stop — and the engine runs it; there is no free-form generation between you and the database.

Proposed atoms quote your own words and arrive unconfirmed, like everything else a model writes. Notes of kind goal and constraint are appended to the scoring prompt ("Lower fit_score for a job that violates a stated constraint"), so telling the counselor "no on-site in Lisbon" changes the ranking on the next scan — and a wrong note would too, which is why they are listed on the page with a Forget button.

## Repository Layout

- `src/jobhunt/config.py`: Configuration models and YAML loading.
- `src/jobhunt/db.py`: SQLite engine and session lifecycle.
- `src/jobhunt/llm/`: Provider abstraction (`agy`, OpenAI-compatible, fake) and disk cache.
- `src/jobhunt/profile/`: Models, atom store, importers (CV, GitHub, coursework).
- `src/jobhunt/sources/`: One adapter per job board, plus ATS board discovery.
- `src/jobhunt/jobs/`: Raw postings, canonical jobs, dedupe, and the job store.
- `src/jobhunt/matching/`: LLM scoring, track tagging, and skill-name canonicalisation.
- `src/jobhunt/growth/`: Gap aggregation across scored jobs, fit-weighted.
- `src/jobhunt/coach/`: Quest engine, daily task selection, GitHub verification, Telegram push.
- `src/jobhunt/tailor/`: Keyword extraction, atom shortlisting, composition, the fabrication verifier, LaTeX rendering, and the ATS self-check.
- `src/jobhunt/linkedin/`: Field registry and limits, profile versions, composition, the approval gate, and the per-field diff.
- `src/jobhunt/counselor/`: The counselor — briefing, action schema, tools, turn loop, proposals, and the notes block the scorer reads.
- `src/jobhunt/scan.py`: Scan orchestration with per-source isolation and deadlines.
- `src/jobhunt/web/`: FastAPI app, Jinja2 templates, HTMX partials, the Home page state, flash messages.

## Known Limitations

Written down rather than left to be rediscovered:

- `docs/superpowers/phase5-known-limitations.md` — the LinkedIn buildout: entry-level diffs mis-pair after an entry is dropped, an empty profile returns a 502 from the tailoring route (the LinkedIn route handles the same case properly), and smaller items.
- `docs/superpowers/phase1-known-limitations.md` — the profile subsystem's original gaps, three of which Phase 2 closed.

Two things worth knowing before you rely on this:

- **The LinkedIn buildout has never been run against a live model.** Its tests all use a fake provider, so the approval gate and the verifier are proven, but the quality of what the real model writes is not.
- **The design documents in `docs/superpowers/` are the reasoning**, not marketing. If you want to know why something works the way it does — particularly why fabrication is treated as a correctness bug — the specs argue it out.

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
