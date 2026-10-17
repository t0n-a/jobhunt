# jobhunt

[![CI](https://github.com/t0n-a/jobhunt/actions/workflows/ci.yml/badge.svg)](https://github.com/t0n-a/jobhunt/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**A local-first job-search assistant that does the volume and leaves you the decisions.**

jobhunt finds jobs worldwide, ranks them with pay first, tailors a resume and cover letter per application from
facts you have confirmed, pre-fills the real application form, drafts recruiter messages, and tells you what to
learn next. It runs on your own machine with your own keys and talks to you through a private Telegram bot.

## What it does

- **Finds and ranks jobs.** LinkedIn and Indeed (logged-out, via JobSpy), Greenhouse/Lever/Ashby boards, remote
  boards and Hacker News. Duplicates are merged across sources; senior roles, wrong-language postings and closed
  listings are dropped before any LLM call; the shortlist is sorted by pay (posted or a labelled estimate), then fit.
- **Tailors every application from evidence.** Your profile is a set of confirmed facts ("evidence atoms"). Each
  resume bullet must trace to one; anything the model invents is dropped and shown to you with the reason.
- **Queues them for you.** Each day the best matches are tailored and sent to Telegram with Yes/No buttons, then
  tracked from sent → replied → interview → offer, with follow-up reminders and a daily digest.
- **Pre-fills the real form.** `jobhunt apply --id N` opens the Greenhouse/Lever/Ashby form in a browser, filled
  from your profile and standard answers; you check it and click submit.
- **Drafts outreach.** Short, specific, evidence-checked messages and connection notes to the recruiter the posting
  names (or one found by web search), sent to Telegram for you to copy. Nobody is contacted twice.
- **Grows your profile.** Suggests private repos worth making public (after a secret scan of their whole history),
  drafts evidence-backed READMEs, suggests pins and certifications that matched jobs ask for, and turns skill gaps
  into one daily task with no streaks.

## Safety by design

- **No lying.** Every claim in a CV, letter, form answer, message or README traces to confirmed evidence; unverifiable
  text is dropped, never sent.
- **You stay in the loop.** Nothing is sent in your name without your yes. Autopilot is off until you switch it on
  after enough clean approvals, and even then it only submits forms with no login and no captcha.
- **It never gets around protections.** No captcha bypass, no password typing, no action through your LinkedIn
  account; LinkedIn is read logged-out and is best-effort.
- **Your data stays local.** SQLite on your disk; only LLM calls leave the machine (the free Gemini tier may use
  prompts for training, and setup says so).

## Requirements

- **OS:** Linux or WSL (reference environment is WSL2 on Windows). Windows natively also runs the full suite; the only difference is that you need a Windows TeX distribution such as MiKTeX for the PDF step.
- **Python:** 3.12, pinned via `uv python pin 3.12`. Do not use Python 3.13+, as several dependencies lack compatible wheels.
- **Package manager:** [uv](https://astral.sh/uv).
- **PDF tooling:** A TeX distribution providing `latexmk`, and `poppler-utils` providing `pdftotext` (used during PDF CV import and tailoring ATS checks). On Debian or Ubuntu:
  ```bash
  sudo apt install latexmk texlive-latex-recommended poppler-utils
  ```
  Without them, everything works except the PDF step.
- **LLM provider:** a free Google AI Studio key (what `jobhunt setup` configures), any OpenAI-compatible endpoint, or the Antigravity CLI (see LLM Provider below).
- **Optional:** a Telegram bot (for the queue and digest), `gh` (profile growth), and Playwright's Chromium (form filling; `jobhunt setup` offers to install it).

## Setup

```
git clone https://github.com/t0n-a/jobhunt.git && cd jobhunt
uv python install 3.12 && uv python pin 3.12
uv sync
cp config.example.yaml config.yaml
cp .env.example .env
uv run jobhunt serve
```

Then open http://localhost:8000. Both `config.yaml` and `.env` are gitignored. On a fresh install,
`uv run jobhunt setup` (below) does the copying and filling for you.

## Guided Setup

```bash
uv sync
uv run jobhunt setup
```

`setup` asks, in order: your name, email and location; a free AI Studio key (read the training warning first); a Telegram bot token from @BotFather, then a one-time code you send to your bot so only your chat is bound; your standard answers (work authorisation, salary range, and so on); the path to your CV, which it imports and walks you through confirming fact by fact; whether to install the browser that fills application forms (about 150 MB); and whether to install the daily and 5-minute timers. Press Enter to skip any step; rerunning keeps what exists.

Telegram commands: yes N, no N (queue), sent N (you applied), replied N, interview N, offer N, rejected N, done N (follow-up sent), osent N, oskip N, oreplied N (outreach), pause, resume, status, autopilot on|off, unblock HOST (lift a blocked ATS host), publish N, approve N, decline N (GitHub profile suggestions; publish makes a private repo public and is refused if a secret is found). "yes" means *you* apply, using the prepared PDF, then send `sent N`. Autopilot is off until you switch it on after enough clean approvals.

Timers can be installed later with `uv run jobhunt install-timers`; run `loginctl enable-linger $USER` so they fire while you are logged out.

## Applying

Each daily run reads the form of every newly queued application (Greenhouse, Lever and Ashby) and plans its answers from your profile, `answers.yaml`, the tailored PDF and cover letter, and confirmed evidence. The plan shows on the review page under "Form answers"; anything it can't answer is left for you.

At your computer, `uv run jobhunt apply --id N` opens the real form in a visible browser, already filled. You check it and click submit yourself, then answer the prompt to mark it sent. It needs a desktop session.

Autopilot rarely fires: these ATSs use invisible captchas, and jobhunt won't get past them. Those applications stay with you, and assisted apply is the usual route.

Autopilot is off by default. Once you have approved enough applications by hand (`trust_target`, 10 by default), send `autopilot on` on Telegram to switch it on; before that it refuses and says how many approvals are left. While it is on, jobhunt submits a queued application without you only when all of these hold: its fit is at least `autopilot_fit` (85 by default); its ATS is in `ats_allowlist` (Greenhouse, Lever and Ashby); the form needs no login and shows no captcha; every question is answered from your profile or standard answers (a form with an answer drafted from your evidence goes to you instead); the site is not blocked; and today's count is under `daily_cap` (10 by default). Greenhouse, Lever and Ashby currently use invisible captchas, so most applications stay in your queue, and `jobhunt apply --id N` is the usual way through them. Each application is tried at most once: a form that stops stays with you. To allow more per day, raise `daily_cap` under `autopilot:` in `config.yaml` (the commented block in `config.example.yaml` shows the keys). Send `autopilot off` to stop it.

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
- **Free Google AI Studio key:** Get a key at aistudio.google.com/apikey. In `.env`, set `OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/` and `OPENAI_API_KEY=<key>`. In `config.yaml`, set `llm.provider: openai_compat`, `llm.model: gemini-flash-latest`, and `llm.min_interval_seconds: 6` (the free tier allows about 10 requests a minute). jobhunt spaces requests that far apart and retries once after a 429, waiting the `Retry-After` seconds (default 30, capped at 60). On the free tier, Google may use your prompts and responses to improve its products. Your CV and profile are in those prompts. If that is not acceptable, use a paid key (same settings, billing enabled) or the agy provider.
- **Disk Cache:** All LLM responses are cached on disk under `llm.cache_dir` (default: `./data/llm_cache`), keyed by model, prompt, and schema. Re-runs are free.

## Environment Variables

Configured in `.env`:

- `GITHUB_TOKEN`: Optional but recommended. The GitHub importer makes `1 + N` API calls (one for repo metadata plus one per README). Unauthenticated requests allow only 60 requests per hour, hitting the limit for profiles with ~30 repositories. Setting a token raises the rate limit to 5,000 requests per hour.
- `OPENAI_BASE_URL` and `OPENAI_API_KEY`: required when `llm.provider` is `openai_compat`.
- `ITJOBS_API_KEY`: optional. A free key from itjobs.pt/api enables ITJobs.pt, Portugal's largest IT board. Without it that source is skipped.
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`: the bot that carries the queue, digest and commands (`jobhunt setup` finds the chat id).
- `TAVILY_API_KEY`: optional. Free plan at tavily.com, no card; enables recruiter web search for outreach.

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

Before any LLM call, cheap rules drop senior roles and postings needing a
language you don't list, and flag visa difficulty and pay (posted, or a labelled
estimate). `uv run jobhunt shortlist` prints the survivors ranked by pay first
(visa-hard last, unknown pay after known), then fit; closed postings are checked
for and removed.

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

## Outreach

Once a day jobhunt drafts a short message to a recruiter or hiring manager for your strongest jobs, and a follow-up seven days after you mark one sent with no reply. Each draft names the role and the company and cites confirmed evidence; it is dropped otherwise. At most one first message per contact, ever, and one open outreach per company.

- **You send it yourself.** jobhunt only drafts and sends the text to Telegram with Copy, Sent and Skip buttons. It never uses your LinkedIn account and fetches no LinkedIn page.
- **Commands:** `osent N`, `oskip N`, `oreplied N`. The digest has an Outreach section listing drafts to send and follow-ups due.
- **Limits:** connection notes are at most 200 characters (the LinkedIn free-tier limit), messages at most 600.
- **Contacts:** taken from the posting when it names someone, otherwise the talent team plus a LinkedIn people-search link you open yourself. If `TAVILY_API_KEY` is set (free plan at tavily.com, no card, 1,000 credits/month), a posting that names nobody triggers one public web search for the company's recruiters with a public LinkedIn profile; the first match whose title names the company and a recruiter role is used and flagged "Found by web search" in Telegram. At most one search per company per 30 days and `outreach.search_per_month` (default 300) per month; no key, over budget or any error means the talent-team link. No LinkedIn account is used.
- Pausing (`pause`) stops new drafts.

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

**Not built:** posting to LinkedIn (there is no API for it). Outreach drafts are covered under Outreach above.

### Counselor

The counselor is not an interviewer with a topic box. It decides what to ask from what the system already knows: atoms with no numbers get probed, skills your best-fit jobs want get asked about, repositories no atom covers get pulled up. Each turn the model answers with one structured action — ask, look something up, propose an atom, write a note, or stop — and the engine runs it; there is no free-form generation between you and the database.

Proposed atoms quote your own words and arrive unconfirmed, like everything else a model writes. Notes of kind goal and constraint are appended to the scoring prompt ("Lower fit_score for a job that violates a stated constraint"), so telling the counselor "no on-site in Lisbon" shapes every job scored afterwards — newly scanned jobs and any job you re-score — and a wrong note would too, which is why they are listed on the page with a Forget button. Jobs already scored keep their score until you press Re-score on them.

### Profile growth

Once a week (`growth.refresh_days`, 7 by default; never while paused or with no `profile.github_username` set), jobhunt checks your GitHub profile and your shortlisted jobs and sends suggestions to Telegram. Each suggestion has a number.

- **README drafts:** a profile README is committed only after you send `approve N`. If one already exists, the reply says it will be replaced, and the commit fails rather than overwrite an edit you made in the meantime.
- **Making a repo public:** send `publish N`. It is refused when the audit finds a secret, whatever you reply: rotate the secret and rewrite the history first. jobhunt never force-pushes your repositories; it tells you how.
- **Pins:** pinning is manual, because GitHub has no API for it. Pin the repositories you want yourself, up to six.
- **Certifications:** suggested only when at least two shortlisted jobs ask for one. Costs are approximate and marked as such; free and cheap options come first.
- **Certificates as evidence:** upload a PDF, PNG or JPG at `/import` (Certificate). It arrives as an unconfirmed atom; confirm it on `/atoms` before it counts as evidence.

## Repository Layout

- `src/jobhunt/config.py`: Configuration models and YAML loading.
- `src/jobhunt/db.py`: SQLite engine and session lifecycle.
- `src/jobhunt/llm/`: Provider abstraction (`agy`, OpenAI-compatible, fake) and disk cache.
- `src/jobhunt/profile/`: Models, atom store, importers (CV, GitHub, coursework).
- `src/jobhunt/sources/`: One adapter per job board, plus ATS board discovery.
- `src/jobhunt/jobs/`: Raw postings, canonical jobs, dedupe, and the job store.
- `src/jobhunt/matching/`: LLM scoring, track tagging, and skill-name canonicalisation.
- `src/jobhunt/growth/`: Gap aggregation, plus GitHub profile growth (repo audit, README drafts, pin and certification suggestions).
- `src/jobhunt/coach/`: Quest engine, daily task selection, GitHub verification, Telegram push.
- `src/jobhunt/tailor/`: Keyword extraction, atom shortlisting, composition, the fabrication verifier, LaTeX rendering, and the ATS self-check.
- `src/jobhunt/linkedin/`: Field registry and limits, profile versions, composition, the approval gate, and the per-field diff.
- `src/jobhunt/counselor/`: The counselor — briefing, action schema, tools, turn loop, proposals, and the notes block the scorer reads.
- `src/jobhunt/scan.py`: Scan orchestration with per-source isolation and deadlines.
- `src/jobhunt/web/`: FastAPI app, Jinja2 templates, HTMX partials, the Home page state, flash messages.
- `src/jobhunt/ranking/`: Rule-based triage (level, language, visa, pay) and the pay-first shortlist.
- `src/jobhunt/apply/`: Tracker, follow-ups, queue and trust, Telegram commands, daily run and digest, ATS detection, form planning, the Playwright form filler and the autopilot gate.
- `src/jobhunt/outreach/`: Contacts from postings (and optional web search), evidence-checked drafts, the contact log.
- `src/jobhunt/telegram.py`, `src/jobhunt/setup.py`, `src/jobhunt/timers.py`: the bot, first-run setup, systemd timers.

## Known Limitations

- **Autopilot rarely submits.** Greenhouse, Lever and Ashby forms carry invisible captchas, and jobhunt stops at
  any captcha by design. Assisted apply is the everyday path.
- **LinkedIn discovery is best-effort.** It reads public listings logged-out; LinkedIn may rate-limit or block a run,
  and the digest says so.
- **Live-model quality is only partly tested.** The test suite uses a fake provider, so the verifiers and approval
  gates are proven, but the quality of what a real model writes is not.
- **Pay estimates are coarse.** A small per-country table, always labelled "estimated".

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

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## Responsible use

You are responsible for how you use jobhunt: respect each site's terms, keep volumes sensible, and only send what
you would put your name to. The tool is built to make spraying hard; please don't work around that.

## Licence

MIT. `NOTICE` records third-party attribution.
