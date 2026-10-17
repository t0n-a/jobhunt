from pathlib import Path

import pytest
from sqlmodel import Session

from jobhunt.answers import StandardAnswers
from jobhunt.apply.form import FormField
from jobhunt.apply.plan import FormPlan, plan_form, save_plan
from jobhunt.config import ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.jobs.models import Job
from jobhunt.llm import FakeProvider
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance
from jobhunt.tailor.models import Application

PROFILE = ProfileConfig(
    name="Ada Maria Lovelace",
    email="a@example.com",
    location="Porto",
    linkedin="https://linkedin.com/in/ada",
)
ANSWERS = StandardAnswers(
    work_authorization="EU citizen",
    needs_sponsorship_outside_eu=True,
    notice_period="1 month",
    salary_min_eur=50000,
    salary_max_eur=70000,
    relocation=False,
    remote=True,
)
ATOM = EvidenceAtom(
    id=1,
    kind=AtomKind.PROJECT,
    title="URL shortener",
    description="Built a link shortener in Flask, cached in Redis.",
    skills=["Python", "Flask", "Redis"],
    provenance=Provenance.INTERVIEW,
    confirmed=True,
)


def F(name, label, kind="text", required=True, options=None):
    return FormField(name, label, kind, required, options or [])


def run(fields, **kw):
    args = {
        "profile": PROFILE, "answers": ANSWERS, "cover_letter": "Dear team", "pdf_path": None,
        "eu_job": True, "atoms": [], "llm": None,
    }  # fmt: skip
    args.update(kw)
    return plan_form(fields, **args)


def vals(plan):
    return {p.name: p.value for p in plan.filled}


def test_contact_fields_from_profile():
    plan = run(
        [
            F("first_name", "First Name"),
            F("last_name", "Last Name"),
            F("name", "Full name"),
            F("email", "Email"),
            F("q1", "LinkedIn Profile"),
            F("phone", "Phone"),
        ]
    )
    assert vals(plan) == {
        "first_name": "Ada Maria",
        "last_name": "Lovelace",
        "name": "Ada Maria Lovelace",
        "email": "a@example.com",
        "q1": "https://linkedin.com/in/ada",
    }
    assert [f.name for f in plan.unknown] == ["phone"]


def test_resume_and_cover_letter():
    fields = [F("resume", "Resume/CV", "file"), F("cover_letter_text", "Cover letter", "textarea")]
    plan = run(fields, pdf_path=Path("/x/r.pdf"))
    assert vals(plan) == {"resume": "/x/r.pdf", "cover_letter_text": "Dear team"}
    assert {p.source for p in plan.filled} == {"document"}
    plan = run(fields)
    assert [f.name for f in plan.unknown] == ["resume"]
    assert not plan.complete


@pytest.mark.parametrize(("eu", "want"), [(True, "No"), (False, "Yes")])
def test_sponsorship_depends_on_eu_job(eu, want):
    plan = run([F("q", "Will you require sponsorship?")], eu_job=eu)
    assert vals(plan) == {"q": want}


def test_eeo_prefers_decline_option():
    f = F("g", "Gender", "select", options=["Male", "Female", "I don't wish to answer"])
    assert vals(run([f])) == {"g": "I don't wish to answer"}


def test_unanswerable_required_select_stays_unknown():
    f = F("q", "Have you previously worked at Acme?", "select", options=["Yes", "No"])
    plan = run([f])
    assert plan.unknown == [f] and plan.complete is False


def test_llm_answers_are_verified():
    fields = [F(n, n, "text") for n in ("ok", "ghost", "years")] + [
        F("sel", "Worked with Flask?", "select", options=["Yes", "No"])
    ]
    llm = FakeProvider(
        [
            {
                "answers": [
                    {"name": "ok", "answer": "I built a link shortener in Flask.", "atom_ids": [1]},
                    {"name": "ghost", "answer": "Flask", "atom_ids": [999]},
                    {"name": "sel", "answer": "Maybe", "atom_ids": [1]},
                    {"name": "years", "answer": "10 years of Flask", "atom_ids": [1]},
                ]
            }
        ]
    )
    plan = run(fields, atoms=[ATOM], llm=llm)
    assert [(p.name, p.source) for p in plan.filled] == [("ok", "evidence")]
    assert {d["name"] for d in plan.dropped} == {"ghost", "sel", "years"}
    assert all(d["reason"] for d in plan.dropped)
    assert {f.name for f in plan.unknown} == {"ghost", "sel", "years"}


def test_llm_error_leaves_fields_unknown():
    plan = run([F("q", "Why us?", "textarea")], atoms=[ATOM], llm=FakeProvider([]))
    assert [f.name for f in plan.unknown] == ["q"] and not plan.filled


def test_optional_unknowns_are_skipped():
    plan = run([F("q", "Anything else?", "text", required=False)])
    assert not plan.filled and not plan.unknown and plan.complete


def test_plan_round_trips_json():
    plan = run([F("email", "Email"), F("q", "Other?", "select", options=["a"])])
    plan.dropped.append({"name": "x", "answer": "y", "reason": "z"})
    assert FormPlan.from_json(plan.to_json()) == plan


def test_save_plan_persists(tmp_path):
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        job = Job(title="T", company="C", description="d", url="http://x", content_hash="h")
        s.add(job)
        s.commit()
        app = Application(job_id=job.id)
        s.add(app)
        s.commit()
        plan = run([F("email", "Email")])
        save_plan(s, app, plan)
        s.expire_all()
        assert FormPlan.from_json(s.get(Application, app.id).form_plan) == plan
