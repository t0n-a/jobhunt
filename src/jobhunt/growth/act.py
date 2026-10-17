"""The two outward-facing actions, run only on the candidate's explicit command."""

from sqlmodel import Session

from jobhunt.growth.audit import audit_repo
from jobhunt.growth.gh import GhError
from jobhunt.growth.models import Suggestion, _now
from jobhunt.growth.readme import REPLACES


def _open(session: Session, suggestion_id: int, kinds: tuple[str, ...]) -> Suggestion | str:
    s = session.get(Suggestion, suggestion_id)
    if s is None:
        return f"No suggestion #{suggestion_id}."
    if s.kind not in kinds:
        return ""  # caller words the redirect
    if s.status not in ("suggested", "approved", "failed"):  # failed: gh errors are transient, retry
        return f"#{s.id} is {s.status}; nothing changed."
    return s


def _save(session: Session, s: Suggestion, status: str, error: str = "") -> None:
    s.status, s.error, s.updated_at = status, error, _now()
    session.add(s)
    session.commit()


def publish(session: Session, gh, config, suggestion_id: int, *, audit=None) -> str:
    audit = audit or audit_repo  # looked up per call so tests can patch the module name
    s = _open(session, suggestion_id, ("publish",))
    if isinstance(s, str):
        return s or f"Nothing to publish for #{suggestion_id}."
    try:
        # Never publish on a stale audit: a secret may have been pushed since the weekly one.
        fresh = audit(
            gh, s.target, readme_text=gh.readme(s.target)[0], description=s.body,
            paths=gh.tree(s.target), workdir=config.data_dir / "growth_audit",
        )
        if fresh.blocking != s.blocking or fresh.flags != s.flags:
            s.flags, s.blocking = fresh.flags, fresh.blocking
            _save(session, s, s.status)
        if s.blocking:
            first = s.flags[0] if s.flags else "a secret was found"
            return (
                f"Not publishing {s.target}: {first}. Rotate that secret and rewrite the repo's "
                f"history first (e.g. git filter-repo), then send publish {s.id} again after the next weekly audit."
            )
        gh.make_public(s.target)
    except GhError as e:
        _save(session, s, "failed", str(e))
        return f"Couldn't publish {s.target}: {e}"
    _save(session, s, "done")
    return f"{s.target} is public now."


# A draft is approved from a Telegram message (4096 chars); anything longer was never fully seen.
MAX_SHOWN = 3500


def approve(session: Session, gh, suggestion_id: int) -> str:
    s = _open(session, suggestion_id, ("readme", "profile_readme"))
    if isinstance(s, str):
        found = session.get(Suggestion, suggestion_id)
        if s == "" and found is not None:
            if found.kind == "publish":
                return f"Use publish {suggestion_id} to make a repo public."
            return f"Nothing to approve for #{suggestion_id}; it's a suggestion you act on yourself."
        return s
    if s.shown_at is None:
        return f"#{s.id} hasn't reached you on Telegram yet; nothing committed."
    if len(s.body) > MAX_SHOWN:
        return (
            f"#{s.id} is too long to show in full on Telegram ({len(s.body)} characters), so nothing "
            "was committed. Decline it, or copy what you want into the repo yourself."
        )
    try:
        sha = gh.readme(s.target)[1]
        if sha and REPLACES not in (s.flags or []):  # the draft said "new README"; one has appeared
            msg = f"A README appeared on {s.target} since the draft; nothing committed. Decline it; a new draft comes next week."
            _save(session, s, "failed", msg)
            return msg
        gh.put_file(s.target, "README.md", s.body, "docs: README (drafted with jobhunt, approved by you)", sha)
    except GhError as e:
        if "409" in str(e) or "does not match" in str(e):
            msg = f"The README on {s.target} changed since the draft; nothing committed. Decline it; a new draft comes next week."
            _save(session, s, "failed", str(e))
            return msg
        _save(session, s, "failed", str(e))
        return f"Couldn't commit the README to {s.target}: {e}"
    _save(session, s, "done")
    replaced = " (it replaced the old one)" if sha else ""
    return f"README committed to {s.target}{replaced}."


def decline(session: Session, suggestion_id: int) -> str:
    s = session.get(Suggestion, suggestion_id)
    if s is None:
        return f"No suggestion #{suggestion_id}."
    _save(session, s, "declined")
    return f"Declined #{suggestion_id}."
