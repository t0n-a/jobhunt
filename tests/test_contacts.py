from sqlmodel import Session, select

from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job
from jobhunt.outreach.contacts import contact_for, fallback_contact, find_contacts
from jobhunt.outreach.models import Contact


def _job(description: str, company: str = "Acme", url: str = "https://x/1") -> Job:
    return Job(
        title="Backend Engineer",
        company=company,
        description=description,
        url=url,
        content_hash=str(abs(hash(description))),
    )


def test_linkedin_url_and_name_are_found():
    job = _job(
        "Questions? Reach out to Maria Silva "
        "(https://pt.linkedin.com/in/maria-silva-123) about the role."
    )
    found = find_contacts(job)
    assert len(found) == 1
    assert found[0].name == "Maria Silva"
    assert found[0].profile_url == "https://www.linkedin.com/in/maria-silva-123"
    assert found[0].source == "posting"


def test_recruiter_line_gives_name_and_role():
    job = _job("Your recruiter: João Costa, Technical Recruiter\nApply below.")
    found = find_contacts(job)
    assert len(found) == 1
    assert found[0].name == "João Costa"
    assert found[0].role is not None and "Recruiter" in found[0].role


def test_personal_email_is_a_contact():
    job = _job("Send your CV to ana.lopes@acme.com")
    found = find_contacts(job)
    assert len(found) == 1
    assert found[0].email == "ana.lopes@acme.com"
    assert found[0].name == "Ana Lopes"


def test_role_mailboxes_are_not_people():
    job = _job("Apply via careers@acme.com, noreply@acme.com or jobs@acme.com")
    assert find_contacts(job) == []


def test_no_named_contact_falls_back_to_talent_team():
    fallback = fallback_contact("Acme")
    assert fallback.name is None
    assert fallback.role == "talent team"
    assert fallback.source == "fallback"
    assert "Acme+recruiter" in fallback.profile_url


def test_contact_for_upserts_the_same_person(tmp_path):
    engine = create_engine_for(tmp_path / "c.db")
    init_db(engine)
    url = "https://www.linkedin.com/in/maria-silva-123"
    with Session(engine) as s:
        first = _job(f"Reach out to Maria Silva ({url})", url="https://x/1")
        second = _job(f"Hiring? Maria Silva ({url})", url="https://x/2")
        s.add(first)
        s.add(second)
        s.commit()
        a = contact_for(s, first)
        b = contact_for(s, second)
        assert a.id == b.id
        assert len(s.exec(select(Contact)).all()) == 1
