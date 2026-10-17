# Contributing

Thanks for your interest. Issues and pull requests are welcome.

## Setup

```bash
uv python install 3.12 && uv sync
uv run playwright install chromium   # only needed for the form-filling tests
```

## Before you open a pull request

```bash
uv run ruff check
uv run pytest -q
```

Both must pass. Tests never touch the network; add a test that fails without your change.

## Ground rules for changes

- **No fabrication.** Anything a model writes that reaches an employer, a recruiter or a public README must be
  checked against confirmed evidence, and dropped (and shown) when it can't be.
- **The human decides.** Don't add paths that send, submit or publish without an explicit yes, and never add
  captcha solving, login automation or anything that acts through the user's LinkedIn account.
- **Fail safe.** When a check can't run (network error, unreadable page, failed audit), block the action rather than
  let it through.
- Keep the existing style: small modules, a one-line *why* comment on constants, no new dependency for a few lines.
