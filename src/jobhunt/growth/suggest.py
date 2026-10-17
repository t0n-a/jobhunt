import logging
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import Session

from jobhunt.growth.audit import audit_repo
from jobhunt.growth.certs import CERT_TABLE
from jobhunt.growth.models import Suggestion, upsert_suggestion
from jobhunt.matching.skills import canonical_skill
from jobhunt.ranking.shortlist import shortlist
from jobhunt.state import get_state, is_paused, set_state

logger = logging.getLogger(__name__)


def _repos(gh, user, visibility):
    # shortcut: isEmpty stands in for "no commits by the candidate"; refine if forks-with-commits matter.
    return [r for r in gh.repos(user, visibility) if not r.get("isFork") and not r.get("isEmpty")]


def _find(session, kind, target) -> Suggestion | None:
    from sqlmodel import select

    return session.exec(
        select(Suggestion).where(Suggestion.kind == kind, Suggestion.target == target)
    ).first()



# Each audit mirror-clones a repo (up to 120 s); the rest are audited on the following weeks.
MAX_AUDITS_PER_REFRESH = 10


def suggest_publish(session, gh, config, *, audit=audit_repo, workdir: Path) -> list[Suggestion]:
    out = []
    repos = sorted(_repos(gh, config.profile.github_username, "private"), key=lambda r: r.get("pushedAt") or "", reverse=True)
    audited = 0
    for repo in repos:
        full = repo["nameWithOwner"]
        # Open suggestions are re-audited every refresh: a secret pushed since the last audit
        # must block, and a fixed one must unblock. Only decided rows are left alone.
        seen = _find(session, "publish", full)
        if seen is not None and seen.status in ("declined", "done"):
            continue
        if audited >= MAX_AUDITS_PER_REFRESH:
            break
        audited += 1
        desc = repo.get("description") or ""
        readme_text, _ = gh.readme(full)
        result = audit(
            gh, full, readme_text=readme_text, description=desc, paths=gh.tree(full), workdir=workdir
        )
        row = upsert_suggestion(
            session, "publish", full, title=f"Make {repo['name']} public", body=desc,
            flags=result.flags, blocking=result.blocking,
        )
        if seen is None:
            out.append(row)
    return out


def suggest_pins(session, gh, config, *, now: datetime | None = None) -> list[Suggestion]:
    now = now or datetime.now().astimezone()
    wanted = Counter()
    for r in shortlist(session, config.ranking):
        if r.score.fit_score >= config.ranking.strong_fit:
            wanted.update({canonical_skill(s) for s in r.score.matched_skills})
    scored = []
    for repo in _repos(gh, config.profile.github_username, "public"):
        full = repo["nameWithOwner"]
        points, why = repo.get("stargazerCount", 0) * 3, []
        if points:
            why.append(f"{repo['stargazerCount']} stars")
        pushed = repo.get("pushedAt")
        if pushed and now - datetime.fromisoformat(pushed) < timedelta(days=180):
            points += 2
            why.append("active in the last 6 months")
        lang = (repo.get("primaryLanguage") or {}).get("name")
        if lang and canonical_skill(lang) in wanted:
            points += 1
            why.append(f"{lang} is asked for by your best matches")
        if gh.readme(full)[1] is not None:
            points += 1
            why.append("has a README")
        scored.append((points, repo, why))
    scored.sort(key=lambda t: -t[0])
    out = []
    for _, repo, why in scored[: config.growth.max_pins]:
        seen = _find(session, "pin", repo["nameWithOwner"])
        row = upsert_suggestion(
            session, "pin", repo["nameWithOwner"], title=f"Pin {repo['name']}",
            body="Worth pinning on your profile: " + ", ".join(why or ["a finished project"]) + ".",
        )
        if seen is None:  # a pin he has seen (or declined) is never announced again
            out.append(row)
    return out


def suggest_certs(session, config) -> list[Suggestion]:
    counts = Counter()
    for r in shortlist(session, config.ranking):
        for key, cert in CERT_TABLE.items():
            if cert.pattern.search(r.job.description or ""):
                counts[key] += 1
    keep = [(n, CERT_TABLE[k].cost_eur, k) for k, n in counts.items() if n >= config.growth.min_cert_jobs]
    keep.sort(key=lambda t: (-t[0], t[1]))
    out = []
    for n, cost, key in keep[:3]:
        cert = CERT_TABLE[key]
        seen = _find(session, "cert", key)
        row = upsert_suggestion(
            session, "cert", key, title=f"Consider {cert.name}",
            body=f"{n} of your matched jobs ask for it. About €{cost} (approximate; check before paying). Free prep: {cert.free_prep}",
        )
        if seen is None:
            out.append(row)
    return out


def refresh(session: Session, gh, config, *, now: datetime, workdir: Path) -> list[Suggestion]:
    if is_paused(session) or not config.profile.github_username:
        return []
    last = get_state(session, "growth_refreshed_at")
    if last and now - datetime.fromisoformat(last) < timedelta(days=config.growth.refresh_days):
        return []
    out = []
    for step in (
        lambda: suggest_publish(session, gh, config, audit=audit_repo, workdir=workdir),
        lambda: suggest_pins(session, gh, config, now=now),
    ):
        try:
            out += step()
        except Exception:
            session.rollback()
            logger.warning("growth refresh step failed", exc_info=True)
    try:
        out += suggest_certs(session, config)
    finally:
        # Set even after a failure, or every daily run re-clones up to the audit cap.
        set_state(session, "growth_refreshed_at", now.isoformat())
    return out
