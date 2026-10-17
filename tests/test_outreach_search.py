import json
import logging

import httpx
import respx
from sqlmodel import Session

from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job
from jobhunt.outreach import log as outreach_log
from jobhunt.outreach.contacts import ContactCandidate, contact_for
from jobhunt.outreach.search import TAVILY_URL, search_recruiters
from jobhunt.state import get_state

KEY = "tvly-secret-key"


def _r(title, url="https://www.linkedin.com/in/x", content=""):
    return {"title": title, "url": url, "content": content}


def _run(results=None, **kw):
    with respx.mock:
        route = respx.post(TAVILY_URL).mock(
            return_value=httpx.Response(200, json={"results": results or []}, **kw)
        )
        with httpx.Client() as http:
            return search_recruiters("Acme", api_key=KEY, http=http), route


def test_search_parses_recruiters_only():
    found, _ = _run([
        _r("Maria Silva - Talent Acquisition Partner - Acme | LinkedIn", "https://pt.linkedin.com/in/maria"),
        _r("John Doe - Software Engineer - Acme", "https://www.linkedin.com/in/john"),
        _r("Ana Lopes - Recruiter - OtherCo", "https://www.linkedin.com/in/ana"),
        _r("Rui Costa - Recruiter - Acme", "https://example.com/rui"),
        _r("Hiring Manager - Acme | LinkedIn", "https://www.linkedin.com/in/hm"),
    ])  # fmt: skip
    assert len(found) == 1
    c = found[0]
    assert (c.name, c.profile_url, c.source) == ("Maria Silva", "https://www.linkedin.com/in/maria", "search")
    assert c.role == "Talent Acquisition Partner"


def test_search_errors_give_empty(caplog):
    caplog.set_level(logging.DEBUG)
    with respx.mock, httpx.Client() as http:
        route = respx.post(TAVILY_URL)
        route.respond(500)
        assert search_recruiters("Acme", api_key=KEY, http=http) == []
        route.mock(side_effect=httpx.ReadTimeout("slow"))
        assert search_recruiters("Acme", api_key=KEY, http=http) == []
        route.mock(return_value=httpx.Response(200, content=b"not json"))
        assert search_recruiters("Acme", api_key=KEY, http=http) == []
    assert KEY not in caplog.text


def test_request_shape():
    _, route = _run()
    req = route.calls.last.request
    assert req.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(req.content)
    assert '"Acme"' in body["query"] and body["max_results"] == 5


def _setup(tmp_path):
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    return Session(engine)


def _job(n, company="Acme", description="No names here."):
    return Job(title="Dev", company=company, description=description, url=f"https://x/{n}", content_hash=str(n))


def _cand():
    return ContactCandidate("Maria Silva", "Recruiter", "https://www.linkedin.com/in/maria", None, "search")


def test_contact_for_uses_search_only_when_posting_names_nobody(tmp_path):
    calls = []
    search = lambda c: calls.append(c) or [_cand()]
    with _setup(tmp_path) as s:
        named = _job(1, "Beta", "Your recruiter: Joao Costa, Technical Recruiter")
        anon = _job(2)
        s.add(named)
        s.add(anon)
        s.commit()
        assert contact_for(s, named, search=search).source == "posting"
        assert calls == []
        got = contact_for(s, anon, search=search)
        assert (got.name, got.source) == ("Maria Silva", "search")
        assert calls == ["Acme"]


def test_search_once_per_company_per_30_days(tmp_path):
    calls = []
    search = lambda c: calls.append(c) or [_cand()]
    with _setup(tmp_path) as s:
        job = _job(1)
        s.add(job)
        s.commit()
        a = contact_for(s, job, search=search)
        b = contact_for(s, job, search=search)
        assert len(calls) == 1 and a.id == b.id
        assert get_state(s, "outreach_search:acme") is not None


def test_monthly_cap_falls_back(tmp_path):
    calls = []
    search = lambda c: calls.append(c) or [_cand()]
    with _setup(tmp_path) as s:
        job = _job(1)
        s.add(job)
        s.commit()
        assert contact_for(s, job, search=search, monthly_cap=0).source == "fallback"
        assert calls == []


def test_no_key_means_no_search(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert outreach_log._search() is None
    monkeypatch.setenv("TAVILY_API_KEY", KEY)
    assert outreach_log._search() is not None



@respx.mock
def test_a_company_name_inside_another_word_is_not_a_match():
    respx.post(TAVILY_URL).respond(200, json={"results": [{
        "title": "Ana Lopes - Recruiter - Acmeville Corp | LinkedIn",
        "url": "https://www.linkedin.com/in/ana", "content": "Recruiter at Acmeville Corp"}]})
    with httpx.Client() as http:
        assert search_recruiters("Acme", api_key="tvly-x", http=http) == []
