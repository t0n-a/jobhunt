import re
from dataclasses import dataclass
from urllib.parse import quote_plus

from sqlmodel import Session, select

from jobhunt.jobs.dedupe import normalize
from jobhunt.jobs.models import Job
from jobhunt.outreach.models import Contact
from jobhunt.ranking.shortlist import _clean

# A shared inbox is not a person, so it never becomes a contact.
ROLE_MAILBOX = re.compile(
    r"^(no-?reply|careers?|jobs?|recruit(ing|ment)?|talent|hr|hello|info|apply|people|team)@",
    re.IGNORECASE,
)
_LINKEDIN = re.compile(r"https?://([a-z]{2,3}\.)?linkedin\.com/in/[A-Za-z0-9_-]+/?")
_RECRUITER = re.compile(
    r"(recruiter|talent (acquisition|partner)|hiring manager|technical recruiter)", re.IGNORECASE
)
# Two capitalised words. Lowercase after the capital keeps "Reach out" from matching.
_NAME = re.compile(r"\b([A-ZÀ-Ý][a-zà-ÿ]+)[ \t]+([A-ZÀ-Ý][a-zà-ÿ]+)\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# "first.last" local parts are the only emails whose name we can read off the address.
_LOCAL_NAME = re.compile(r"^([a-z]+)[._]([a-z]+)$", re.IGNORECASE)
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


def _line_names(line: str) -> list[re.Match]:
    """Name matches on a line, leaving out any that overlap a recruiter phrase ("Technical Recruiter")."""
    spans = [m.span() for m in _RECRUITER.finditer(line)]
    return [
        m
        for m in _NAME.finditer(line)
        if not any(m.start() < end and start < m.end() for start, end in spans)
    ]


def find_contacts(job: Job) -> list[ContactCandidate]:
    """People named in the posting text, in order and de-duplicated. Never invented."""
    text = _clean(job.description)
    lines = text.splitlines()
    found: list[ContactCandidate] = []
    seen: set[str] = set()

    def add(candidate: ContactCandidate) -> None:
        keys = {k for k in (candidate.profile_url, candidate.email, candidate.name) if k}
        if keys & seen:
            return
        seen.update(keys)
        found.append(candidate)

    # LinkedIn profiles, named by the nearest name before the link on the same line.
    for line in lines:
        for url in _LINKEDIN.finditer(line):
            before = [m for m in _line_names(line) if m.end() <= url.start()]
            name = _name(before[-1].group(0)) if before else None
            slug = url.group(0).rstrip("/").rsplit("/", 1)[-1]
            add(
                ContactCandidate(
                    name, None, f"https://www.linkedin.com/in/{slug}", None, "posting"
                )
            )

    # Recruiter lines: the name on the line, the role from its longest recruiter phrase.
    for line in lines:
        keywords = list(_RECRUITER.finditer(line))
        names = _line_names(line)
        if keywords and names:
            role = max(keywords, key=lambda m: len(m.group(0))).group(0).title()
            add(ContactCandidate(_name(names[0].group(0)), role, None, None, "posting"))

    for match in _EMAIL.finditer(text):
        address = match.group(0)
        if ROLE_MAILBOX.match(address):
            continue
        local = _LOCAL_NAME.match(address.split("@", 1)[0])
        name = _name(" ".join(p.capitalize() for p in local.groups())) if local else None
        add(ContactCandidate(name, None, None, address, "posting"))

    return found


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


def contact_for(session: Session, job: Job) -> Contact:
    """The job's first contact, reusing the stored row for the same person at the same company."""
    found = find_contacts(job)
    best = found[0] if found else fallback_contact(job.company)
    field = "profile_url" if best.profile_url else "email" if best.email else "name"
    value = getattr(best, field)
    company = normalize(job.company)
    for row in session.exec(select(Contact).where(getattr(Contact, field) == value)).all():
        if normalize(row.company) == company:
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
