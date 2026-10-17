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
        "name": "Ada Maria Lovelace",
        "email": "a@example.com",
        "q1": "https://linkedin.com/in/ada",
    }
    assert [f.name for f in plan.unknown] == ["first_name", "last_name", "phone"]


def test_first_and_last_name_split_only_a_two_part_name_or_use_overrides():
    fields = [F("first_name", "First Name"), F("last_name", "Last Name")]
    two = PROFILE.model_copy(update={"name": "Ada Lovelace"})
    assert vals(run(fields, profile=two)) == {"first_name": "Ada", "last_name": "Lovelace"}
    set_ = PROFILE.model_copy(update={"first_name": "Ada Maria", "last_name": "Lovelace"})
    assert vals(run(fields, profile=set_)) == {"first_name": "Ada Maria", "last_name": "Lovelace"}


def test_current_salary_and_referral_labels_stay_unknown():
    plan = run(
        [
            F("a", "Current salary"),
            F("b", "Current compensation (EUR)"),
            F("c", "Referrer's email"),
            F("d", "Reference phone number"),
            F("e", "How did you hear about us? (e.g. LinkedIn)"),
            F("f", "Were you referred by an employee? LinkedIn"),
            F("g", "Expected salary"),
        ]
    )
    assert vals(plan) == {"g": "50000-70000 EUR"}


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
    plan = run([F("q", "Will you require sponsorship?", "select", options=["Yes", "No"])], eu_job=eu)
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
    # A select is never answered from evidence: it is not even asked.
    assert {d["name"] for d in plan.dropped} == {"ghost", "years"}
    assert all(d["reason"] for d in plan.dropped)
    assert {f.name for f in plan.unknown} == {"ghost", "sel", "years"}
    assert "Worked with Flask?" not in llm.prompts[0]


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



def test_eeo_never_goes_into_free_text():
    plan = run([F("q", "Describe a time you embraced a challenge", "textarea"),
                F("g", "Gender", "text")])
    assert not plan.filled
    assert {f.name for f in plan.unknown} == {"q", "g"}


def test_only_processing_consent_is_ticked():
    plan = run([
        F("c1", "I consent to the processing of my personal data for this application", "checkbox"),
        F("c2", "Send me marketing emails and future opportunities", "checkbox", required=False),
        F("c3", "I have read the privacy notice", "checkbox"),
    ])
    filled = {p.name: p.value for p in plan.filled}
    assert filled.get("c1") is True and filled.get("c3") is True and "c2" not in filled


def test_privacy_notice_is_not_a_notice_period():
    plan = run([F("n", "Privacy notice acknowledgement", "text", required=False)])
    assert not plan.filled


@pytest.mark.parametrize("label", ["Ethnicity", "Team capacity", "Which city would you relocate to?"])
def test_profile_rules_match_whole_words(label):
    name = "Ethnicity" if label == "Ethnicity" else "q"
    plan = run([F(name, label)])
    assert not any(p.source == "profile" for p in plan.filled)


def test_city_as_a_word_still_matches():
    assert vals(run([F("q", "Current city")])) == {"q": PROFILE.location}


@pytest.mark.parametrize(
    "label",
    ["Describe your remote work experience", "Which city would you relocate to?", "Who would sponsor your visa?"],
)
def test_yes_no_never_goes_into_free_text(label):
    plan = run([F("q", label, kind="textarea")])
    assert vals(plan) == {} and [f.name for f in plan.unknown] == ["q"]


def test_unknown_country_leaves_sponsorship_and_authorisation_unknown():
    fields = [F("a", "Will you require sponsorship?"), F("b", "Are you legally authorised to work here?")]
    plan = run(fields, eu_job=None)
    assert vals(plan) == {} and [f.name for f in plan.unknown] == ["a", "b"]
