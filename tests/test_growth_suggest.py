from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session

from jobhunt.config import Config, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.growth import suggest
from jobhunt.growth.audit import Audit
from jobhunt.growth.gh import GhError
from jobhunt.jobs.models import Job, JobFacts, Score
from jobhunt.state import set_paused

NOW = datetime(2026, 10, 17, tzinfo=UTC)


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "g.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def cfg(user="ada"):
    return Config(profile=ProfileConfig(name="A", email="a@x.com", location="P", github_username=user))


def repo(name, **kw):
    d = {"name": name, "nameWithOwner": f"ada/{name}", "description": "d", "isFork": False,
         "isEmpty": False, "pushedAt": "2026-10-01T00:00:00Z",
         "primaryLanguage": {"name": "Python"}, "stargazerCount": 0, "diskUsage": 1}
    return d | kw


class FakeGh:
    def __init__(self, private=(), public=(), readme=("# hi", "sha")):
        self.by = {"private": list(private), "public": list(public)}
        self._readme = readme

    def repos(self, user, visibility):
        return self.by[visibility]

    def readme(self, full):
        return self._readme

    def tree(self, full):
        return ["a.py"]


def audit(*a, **k):
    return Audit(["looks like coursework"], blocking=True)


def add_job(session, desc, n):
    job = Job(title=f"J{n}", company="C", description=desc, url="u", content_hash=f"h{n}")
    session.add(job)
    session.commit()
    session.refresh(job)
    session.add(Score(job_id=job.id, fit_score=90, seniority_fit="ok", track="b", rationale="r",
                      content_hash=job.content_hash, matched_skills=["Python"]))
    session.add(JobFacts(job_id=job.id, content_hash=job.content_hash, level="junior", visa="easy", pay_yearly_eur=40000))
    session.commit()


def test_forks_and_empty_repos_are_not_suggested(session, tmp_path):
    gh = FakeGh(private=[repo("a"), repo("f", isFork=True), repo("e", isEmpty=True)])
    out = suggest.suggest_publish(session, gh, cfg(), audit=audit, workdir=tmp_path)
    assert [s.target for s in out] == ["ada/a"]


def test_publish_suggestion_carries_flags(session, tmp_path):
    [s] = suggest.suggest_publish(session, FakeGh(private=[repo("a")]), cfg(), audit=audit, workdir=tmp_path)
    assert s.title == "Make a public" and s.flags == ["looks like coursework"] and s.blocking


def test_existing_suggestion_is_not_duplicated(session, tmp_path):
    gh = FakeGh(private=[repo("a")])
    assert len(suggest.suggest_publish(session, gh, cfg(), audit=audit, workdir=tmp_path)) == 1
    assert suggest.suggest_publish(session, gh, cfg(), audit=audit, workdir=tmp_path) == []


def test_pins_ranked_and_capped(session):
    c = cfg()
    c.growth.max_pins = 2
    gh = FakeGh(public=[repo("low"), repo("star", stargazerCount=5), repo("mid", stargazerCount=1), repo("fork", isFork=True, stargazerCount=99)])
    out = suggest.suggest_pins(session, gh, c, now=NOW)
    assert [s.target for s in out] == ["ada/star", "ada/mid"]
    assert "5 stars" in out[0].body


def test_cert_needs_two_jobs_and_shows_cost(session):
    add_job(session, "Want AZ-900 and CKA", 1)
    add_job(session, "AZ-900 helps", 2)
    [s] = suggest.suggest_certs(session, cfg())
    assert s.target == "az-900"
    assert s.body.startswith("2 of your matched jobs ask for it. About €90 (approximate; check before paying). Free prep:")


def test_cheaper_cert_first_on_ties(session):
    for i in range(2):
        add_job(session, "CKA or Terraform Associate", i)
    assert [s.target for s in suggest.suggest_certs(session, cfg())] == ["terraform", "cka"]


def test_refresh_runs_weekly_only(session, tmp_path, monkeypatch):
    monkeypatch.setattr(suggest, "audit_repo", audit)
    gh = FakeGh(private=[repo("a")])
    kw = {"workdir": tmp_path}
    assert len(suggest.refresh(session, gh, cfg(), now=NOW, **kw)) == 1  # the one private repo
    assert suggest.refresh(session, gh, cfg(), now=NOW + timedelta(days=1), **kw) == []
    gh.repos = lambda *a: (_ for _ in ()).throw(GhError("boom"))
    suggest.refresh(session, gh, cfg(), now=NOW + timedelta(days=8), **kw)  # GhError is swallowed


def test_paused_or_no_username_skips(session, tmp_path):
    gh = FakeGh(public=[repo("a")])
    assert suggest.refresh(session, gh, cfg(user=""), now=NOW, workdir=tmp_path) == []
    set_paused(session, True)
    assert suggest.refresh(session, gh, cfg(), now=NOW, workdir=tmp_path) == []



def test_java_developer_requirement_is_not_a_cert():
    from jobhunt.growth.certs import CERT_TABLE

    pat = CERT_TABLE["oracle-java"].pattern
    assert not pat.search("We need a Java SE 17 developer with Spring.")
    assert pat.search("Oracle Certified Professional, Java SE 17 Developer preferred")


def test_open_publish_suggestion_is_reaudited(session, tmp_path):
    from jobhunt.growth.audit import Audit

    gh = FakeGh(private=[repo("a")])
    clean = lambda *a, **k: Audit([], False)
    leaked = lambda *a, **k: Audit(["secret in history: aws_access_key in x.py"], True)
    [s] = suggest.suggest_publish(session, gh, cfg(), audit=clean, workdir=tmp_path)
    assert not s.blocking
    assert suggest.suggest_publish(session, gh, cfg(), audit=leaked, workdir=tmp_path) == []
    session.refresh(s)
    assert s.blocking and s.flags == ["secret in history: aws_access_key in x.py"]


def test_pins_and_certs_are_returned_once_even_when_declined(session):
    from jobhunt.growth.act import decline

    gh = FakeGh(public=[repo("star", stargazerCount=5)])
    [pin] = suggest.suggest_pins(session, gh, cfg(), now=NOW)
    assert suggest.suggest_pins(session, gh, cfg(), now=NOW) == []
    add_job(session, "AZ-900", 1)
    add_job(session, "AZ-900", 2)
    [cert] = suggest.suggest_certs(session, cfg())
    decline(session, pin.id)
    decline(session, cert.id)
    assert suggest.suggest_pins(session, gh, cfg(), now=NOW) == []
    assert suggest.suggest_certs(session, cfg()) == []


def test_publish_audits_at_most_ten_repos_newest_pushed_first(session, tmp_path):
    repos = [repo(f"r{i:02}", pushedAt=f"2026-10-{i + 1:02}T00:00:00Z") for i in range(12)]
    out = suggest.suggest_publish(session, FakeGh(private=repos), cfg(), audit=audit, workdir=tmp_path)
    assert len(out) == suggest.MAX_AUDITS_PER_REFRESH == 10
    assert "ada/r00" not in {s.target for s in out} and "ada/r11" in {s.target for s in out}
