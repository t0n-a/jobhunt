import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote_plus

from sqlmodel import Session, select

from jobhunt.jobs.dedupe import normalize
from jobhunt.jobs.models import Job
from jobhunt.outreach.models import Contact
from jobhunt.ranking.geo import _ALIASES
from jobhunt.ranking.shortlist import _clean
from jobhunt.state import get_state, set_state

# Role and team words: a "name" or mailbox containing one is a role or inbox, not a person.
ROLE_WORDS = frozenset({
    "recruiter", "recruiting", "recruitment", "talent", "acquisition", "hiring", "manager",
    "director", "head", "lead", "engineering", "engineer", "technical", "people", "team",
    "office", "department", "hr", "careers", "partner", "operations", "the", "our",
    "remote", "first", "linkedin", "profile",  # review probe: "Remote First"
})  # fmt: skip
# Extra words that mark a shared or generic mailbox part (with ROLE_WORDS).
_MAILBOX_WORDS = ROLE_WORDS | {
    "jobs", "job", "career", "recruit", "privacy", "dpo", "gdpr", "info", "support", "hello",
    "apply", "noreply", "pt", "en", "es", "no", "reply",
}  # fmt: skip
ROLE_MAILBOX = re.compile(
    r"(?:^|[._+-])(" + "|".join(sorted(_MAILBOX_WORDS)) + r")(?=$|[._+-]|\d)", re.IGNORECASE
)
_LINKEDIN = re.compile(r"https?://([a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9_-]+/?")
_RECRUITER = re.compile(
    r"(recruiter|talent (acquisition|partner)|hiring manager|technical recruiter)", re.IGNORECASE
)
# Two capitalised words. Lowercase after the capital keeps "Reach out" from matching.
_WORD = r"[A-ZÀ-Ý][a-zà-ÿ]+"
_NAME = rf"(?P<name>{_WORD}[ \t]+{_WORD})"
_KW = f"(?i:{_RECRUITER.pattern})"  # case-insensitive keyword only; names stay capitalised
# A name counts only when it touches the role: "recruiter: Name", "recruiter is Name", "Name, recruiter".
_AFTER_ROLE = re.compile(rf"{_KW}\s*(?::|\bis\b)\s*{_NAME}\b")
_BEFORE_ROLE = re.compile(rf"\b{_NAME}\s*,\s*{_KW}")
_BEFORE_URL = re.compile(rf"\b({_WORD})[ \t]+({_WORD})[\s(\[<:-]*$")
# Leading words that look like a first name but are verbs, prepositions or headings.
_NOT_NAMES = {
    "contact", "reach", "email", "message", "ask", "with", "to", "out", "questions",
    "our", "the", "about", "join", "apply", "meet", "see", "team",
}  # fmt: skip
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# "first.last" local parts are the only emails whose name we can read off the address.
_LOCAL_NAME = re.compile(r"^([a-z]+(?:-[a-z]+)?)\.([a-z]+(?:-[a-z]+)?)$", re.IGNORECASE)
_MAX_NAME = 60  # names on a posting are short; a longer match is cut, not dropped


@dataclass
class ContactCandidate:
    name: str | None
    role: str | None
    profile_url: str | None
    email: str | None
    source: str


def _name(text: str) -> str:
    return text[:_MAX_NAME]


def _not_person(name: str) -> bool:
    """A role phrase or a place ("Technical Recruiter", "Lisbon Office"): never greeted by name."""
    words = name.casefold().split()
    return any(w in ROLE_WORDS or w in _ALIASES for w in words) or name.casefold() in _ALIASES


def find_contacts(job: Job) -> list[ContactCandidate]:
    """People named in the posting text, in order and de-duplicated. Never invented."""
    # _clean deletes newlines, so it runs per line; a name never spans two lines.
    lines = [_clean(line) for line in job.description.splitlines()]
    found: list[ContactCandidate] = []
    seen: set[str] = set()

    def add(candidate: ContactCandidate) -> None:
        keys = {k for k in (candidate.profile_url, candidate.email, candidate.name) if k}
        if keys & seen:
            return
        seen.update(keys)
        found.append(candidate)

    # LinkedIn profiles, named by the two capitalised words right before the link.
    for line in lines:
        for url in _LINKEDIN.finditer(line):
            before = _BEFORE_URL.search(line[: url.start()])
            name = None
            if before and before.group(1).casefold() not in _NOT_NAMES:
                name = _name(f"{before.group(1)} {before.group(2)}")
                name = None if _not_person(name) else name
            slug = url.group(0).rstrip("/").rsplit("/", 1)[-1]
            kw = [m.group(0) for m in _RECRUITER.finditer(line)]
            role = max(kw, key=len).title() if kw else None
            add(ContactCandidate(name, role, f"https://www.linkedin.com/in/{slug}", None, "posting"))

    # Recruiter lines: a name bound to the role keyword; the role from its longest recruiter phrase.
    for line in lines:
        bound = _AFTER_ROLE.search(line) or _BEFORE_ROLE.search(line)
        if bound and not _not_person(bound.group("name")):
            keywords = list(_RECRUITER.finditer(line))
            role = max(keywords, key=lambda m: len(m.group(0))).group(0).title()
            add(ContactCandidate(_name(re.sub(r"\s+", " ", bound.group("name"))), role, None, None, "posting"))

    for match in _EMAIL.finditer("\n".join(lines)):
        address = match.group(0)
        local = address.split("@", 1)[0]
        # Only a clean first.last address is a person; anything else may be a shared inbox.
        person = _LOCAL_NAME.match(local)
        if not person or ROLE_MAILBOX.search(local) or _not_person(" ".join(person.groups())):
            continue
        add(ContactCandidate(_name(" ".join(p.title() for p in person.groups())), None, None, address, "posting"))

    # Candidates bound to a recruiter keyword rank before bare LinkedIn or email ones (stable).
    return sorted(found, key=lambda c: c.role is None)


def fallback_contact(company: str) -> ContactCandidate:
    """No named person: a LinkedIn people search for the company's recruiter."""
    query = quote_plus(f"{company} recruiter")
    return ContactCandidate(
        None,
        "talent team",
        f"https://www.linkedin.com/search/results/people/?keywords={query}",
        None,
        "fallback",
    )


SEARCH_DAYS = 30


def _searched(
    session: Session, company: str, name: str, search: Callable[[str], list[ContactCandidate]], cap: int
) -> ContactCandidate | None:
    """The first web-search candidate, within the per-company and monthly budgets; None otherwise."""
    now = datetime.now(UTC)
    key, month = f"outreach_search:{company}", f"outreach_search_count:{now:%Y-%m}"
    last = get_state(session, key)
    if last and now.date() - datetime.fromisoformat(last).date() < timedelta(days=SEARCH_DAYS):
        return None
    count = int(get_state(session, month, "0") or 0)
    if count >= cap:
        return None
    found = search(name)  # search_recruiters returns [] on any error
    set_state(session, key, now.date().isoformat())
    set_state(session, month, str(count + 1))
    return found[0] if found else None


def contact_for(
    session: Session,
    job: Job,
    *,
    search: Callable[[str], list[ContactCandidate]] | None = None,
    monthly_cap: int = 300,
) -> Contact:
    """The job's first contact, reusing the stored row for the same person at the same company."""
    found = find_contacts(job)
    company = normalize(job.company)
    best = found[0] if found else None
    if best is None and search:
        best = _searched(session, company, job.company, search, monthly_cap)
        if best is None:  # a search within 30 days already stored its pick: keep greeting that person
            best = next(
                (
                    ContactCandidate(r.name, r.role, r.profile_url, r.email, "search")
                    for r in session.exec(select(Contact).where(Contact.source == "search"))
                    if normalize(r.company) == company
                ),
                None,
            )
    best = best or fallback_contact(job.company)
    name = best.name.casefold() if best.name else None
    for row in session.exec(select(Contact)).all():
        if normalize(row.company) != company:
            continue
        if (
            (best.profile_url and row.profile_url == best.profile_url)
            or (best.email and row.email == best.email)
            or (name and row.name and row.name.casefold() == name)
        ):
            for f in ("name", "role", "profile_url", "email"):
                if not getattr(row, f) and getattr(best, f):
                    setattr(row, f, getattr(best, f))
            session.add(row)
            session.commit()
            session.refresh(row)
            return row
    row = Contact(
        company=job.company,
        name=best.name,
        role=best.role,
        profile_url=best.profile_url,
        email=best.email,
        source=best.source,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
