"""The look-ups the counselor may ask for. Every tool returns a string; an
error is a string starting with "error:" so the model can work around it."""

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

from sqlmodel import Session, select

from jobhunt.config import Config
from jobhunt.growth.gaps import compute_gaps
from jobhunt.jobs.models import Score
from jobhunt.jobs.store import JobStore
from jobhunt.matching.skills import canonical_skill
from jobhunt.profile.importers.github import fetch_repos
from jobhunt.profile.models import EvidenceAtom
from jobhunt.profile.store import AtomStore

REPO_CACHE_TTL_SECONDS = 24 * 3600
README_CHARS = 4000
JOB_DESCRIPTION_CHARS = 1500


def cached_repos(
    username: str | None, token: str | None, cache_dir: Path
) -> tuple[list[dict], str]:
    """The user's repos, fetched at most once a day. Never raises."""
    if not username:
        return [], "no GitHub username configured"
    cache = Path(cache_dir) / "counselor" / f"repos-{username}.json"
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if (
                isinstance(data, dict)
                and time.time() - float(data.get("fetched_at", 0)) < REPO_CACHE_TTL_SECONDS
            ):
                return list(data.get("repos", [])), ""
        except (ValueError, OSError, AttributeError, TypeError):
            pass
    try:
        repos = fetch_repos(username, token)
    except Exception as exc:  # noqa: BLE001 — any failure becomes a note the model reads
        return [], f"GitHub unavailable: {exc}"
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"fetched_at": time.time(), "repos": repos}), encoding="utf-8")
    except OSError:
        pass
    return repos, ""


def _int_arg(args: dict, key: str) -> int:
    try:
        return int(args[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc


def _str_arg(args: dict, key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def atom_line(atom: EvidenceAtom) -> str:
    skills = ", ".join(atom.skills) if atom.skills else "none listed"
    metrics = atom.metrics or "none"
    state = "confirmed" if atom.confirmed else "unconfirmed"
    return f"[{atom.id}] [{atom.kind.value}] {atom.title}: {atom.description} (skills: {skills}; metrics: {metrics}; {state})"


def _atom(args: dict, session: Session, config: Config) -> str:
    atom = AtomStore(session).get(_int_arg(args, "id"))
    if atom is None:
        raise ValueError(f"no atom with id {args['id']}")
    dates = (
        f"; {atom.started_on or '?'} to {atom.ended_on or '?'}"
        if (atom.started_on or atom.ended_on)
        else ""
    )
    return f"{atom_line(atom)}; source: {atom.provenance.value} {atom.source_ref or ''}{dates}"


def _atoms_with_skill(args: dict, session: Session, config: Config) -> str:
    wanted = canonical_skill(_str_arg(args, "skill")).casefold()
    matches = [
        atom_line(atom)
        for atom in session.exec(select(EvidenceAtom)).all()
        if any(canonical_skill(s).casefold() == wanted for s in atom.skills or [])
    ]
    return "\n".join(matches) if matches else "none"


def _gap(args: dict, session: Session, config: Config) -> str:
    wanted = canonical_skill(_str_arg(args, "skill"))
    gap = next(
        (
            g
            for g in compute_gaps(session, limit=100)
            if canonical_skill(g.skill).casefold() == wanted.casefold()
        ),
        None,
    )
    mentioned = _atoms_with_skill({"skill": wanted}, session, config) != "none"
    coverage = "an atom already mentions it" if mentioned else "no atom mentions it"
    if gap is None:
        return f"{wanted}: not a gap in the current scores; {coverage}"
    noun = "job" if gap.job_count == 1 else "jobs"
    examples = ", ".join(gap.example_titles) or "none"
    return f"{wanted}: missing from {gap.job_count} {noun} (e.g. {examples}); {coverage}"


def _repo(args: dict, session: Session, config: Config) -> str:
    name = _str_arg(args, "name").lower()
    repos, note = cached_repos(
        config.profile.github_username, os.environ.get("GITHUB_TOKEN"), config.llm.cache_dir
    )
    if note:
        raise ValueError(note)
    repo = next(
        (
            r
            for r in repos
            if r.get("name", "").lower() == name or r.get("full_name", "").lower() == name
        ),
        None,
    )
    if repo is None:
        raise ValueError(f"no repo named {name}")
    readme = (repo.get("readme") or "").strip()[:README_CHARS] or "(no README)"
    return (
        f"{repo.get('full_name')} — language: {repo.get('language') or 'unknown'}; "
        f"last push: {repo.get('pushed_at') or 'unknown'}\n{readme}"
    )


def _job(args: dict, session: Session, config: Config) -> str:
    job = JobStore(session).get(_int_arg(args, "id"))
    if job is None:
        raise ValueError(f"no job with id {args['id']}")
    score = session.exec(
        select(Score).where(Score.job_id == job.id).order_by(Score.scored_at.desc())
    ).first()
    scored = (
        f"fit {score.fit_score}; matched: {', '.join(score.matched_skills) or 'none'}; "
        f"missing: {', '.join(score.missing_skills) or 'none'}"
        if score
        else "unscored"
    )
    return f"{job.title} at {job.company}; {scored}\n{job.description[:JOB_DESCRIPTION_CHARS]}"


TOOLS: dict[str, Callable[[dict, Session, Config], str]] = {
    "atom": _atom,
    "atoms_with_skill": _atoms_with_skill,
    "gap": _gap,
    "repo": _repo,
    "job": _job,
}


def run_tool(name: str, args: dict, session: Session, *, config: Config) -> str:
    tool = TOOLS.get(name)
    if tool is None:
        return f"error: unknown tool {name!r}"
    try:
        return tool(args or {}, session, config)
    except Exception as exc:  # noqa: BLE001 — the contract is a string, whatever went wrong
        return f"error: {exc}"
