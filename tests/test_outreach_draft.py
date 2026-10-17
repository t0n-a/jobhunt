from jobhunt.config import ProfileConfig
from jobhunt.jobs.models import Job
from jobhunt.llm import FakeProvider
from jobhunt.outreach.draft import draft_followup, draft_outreach
from jobhunt.outreach.models import Contact, Outreach
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance

PROFILE = ProfileConfig(name="Ada Lovelace", first_name="Ada", email="a@example.com", location="Porto")
JOB = Job(id=1, title="Python Developer", company="Acme", description="Build APIs.", url="u", content_hash="h")
CONTACT = Contact(id=1, company="Acme", name="Bo", role="Recruiter", source="posting")
ATOMS = [
    EvidenceAtom(
        id=1, kind=AtomKind.PROJECT, title="Flask URL shortener",
        description="Built a Flask URL shortener used by 200 students",
        skills=["Flask", "Python"], provenance=Provenance.MANUAL, confirmed=True,
    )
]  # fmt: skip
MSG = "Hi Bo, I'm applying for the Python Developer role at Acme. I built a Flask URL shortener used by 200 students. Could we have a short chat?"
NOTE = "Hi Bo, Python Developer at Acme: I built a Flask URL shortener used by 200 students."


def _resp(msg=MSG, note=NOTE, mids=(1,), nids=(1,)):
    return {
        "message": {"text": msg, "atom_ids": list(mids)},
        "connection_note": {"text": note, "atom_ids": list(nids)},
    }


def _run(resp):
    return draft_outreach(JOB, CONTACT, ATOMS, FakeProvider([resp]), profile=PROFILE)


def test_good_drafts_are_kept():
    r = _run(_resp())
    assert [d.kind for d in r.drafts] == ["message", "connection_note"]
    assert r.drafts[0].atom_ids == [1] and not r.dropped


def test_unverified_draft_is_dropped():
    r = _run(_resp(msg=MSG + " I have 5 years of Kubernetes."))
    assert [d.kind for d in r.drafts] == ["connection_note"]
    assert r.dropped[0]["kind"] == "message"


def test_draft_without_citation_is_dropped():
    r = _run(_resp(mids=(), nids=(99,)))
    assert not r.drafts and len(r.dropped) == 2


def test_generic_draft_is_dropped():
    r = _run(_resp(msg="Hi Bo, I built a Flask URL shortener used by 200 students. Chat?"))
    assert [d.kind for d in r.drafts] == ["connection_note"]


def test_connection_note_over_limit_is_dropped():
    note = NOTE + " " + "x" * (230 - len(NOTE) - 1)
    assert len(note) == 230
    r = _run(_resp(note=note))
    assert [d.kind for d in r.drafts] == ["message"]
    assert "200" in r.dropped[0]["reason"]


def test_llm_error_drops_everything():
    r = draft_outreach(JOB, CONTACT, ATOMS, FakeProvider([]), profile=PROFILE)
    assert r.drafts == [] and r.dropped[0]["reason"] == "llm error"


def test_followup_is_verified():
    o = Outreach(id=5, job_id=1, kind="message", text=MSG, atom_ids=[1])
    ok = "Hi Bo, following up on my note about the Python Developer role at Acme and the Flask URL shortener."
    d = draft_followup(o, JOB, CONTACT, ATOMS, FakeProvider([{"text": ok, "atom_ids": [1]}]))
    assert d and d.kind == "followup" and d.atom_ids == [1]
    bad = ok + " I have 9 years of Rust."
    assert draft_followup(o, JOB, CONTACT, ATOMS, FakeProvider([{"text": bad, "atom_ids": [1]}])) is None
    assert draft_followup(o, JOB, CONTACT, ATOMS, FakeProvider([])) is None



def test_unconfirmed_atom_cannot_back_a_draft():
    from jobhunt.outreach.draft import _check

    atom = EvidenceAtom(id=9, kind=AtomKind.PROJECT, title="Flask app", description="Built a Flask app",
                        skills=["Flask"], provenance=Provenance.CV, confirmed=False)
    job = Job(id=1, title="Backend Engineer", company="Acme", description="d", url="u", content_hash="h")
    out = _check("message", {"text": "Hi Acme, backend: I built a Flask app.", "atom_ids": [9]},
                 job, [atom], 600)
    assert isinstance(out, str)


def test_draft_must_share_a_content_word_with_the_evidence():
    r = _run(_resp(msg="Hi Bo, I'd love to chat about the Developer role at Acme. Could we talk soon?"))
    assert [d.kind for d in r.drafts] == ["connection_note"]


def test_draft_with_url_or_email_is_dropped():
    for extra in (" See https://evil.example/x", " See www.evil.example", " Mail me at x@evil.example"):
        r = _run(_resp(msg=MSG + extra))
        assert [d.kind for d in r.drafts] == ["connection_note"], extra


def test_company_matches_through_legal_suffix():
    job = Job(id=1, title="Python Developer", company="Acme GmbH", description="d", url="u", content_hash="h")
    r = draft_outreach(job, CONTACT, ATOMS, FakeProvider([_resp()]), profile=PROFILE)
    assert len(r.drafts) == 2


def test_short_title_words_count_when_no_long_one():
    from jobhunt.outreach.draft import _check

    job = Job(id=1, title="SRE", company="Acme", description="d", url="u", content_hash="h")
    text = "Hi Bo, I would love the SRE role at Acme: I built a Flask URL shortener."
    assert not isinstance(_check("message", {"text": text, "atom_ids": [1]}, job, ATOMS, 600), str)
    text = "Hi Bo, I would love a role at Acme: I built a Flask URL shortener."
    assert isinstance(_check("message", {"text": text, "atom_ids": [1]}, job, ATOMS, 600), str)


def test_draft_must_greet_the_contact():
    r = _run(_resp(msg=MSG.replace("Hi Bo", "Hi Sam"), note=NOTE.replace("Hi Bo", "Hi Sam")))
    assert not r.drafts


def test_unnamed_contact_draft_must_not_name_a_posting_person():
    job = Job(id=1, title="Python Developer", company="Acme",
              description="Recruiter: Maria Silva", url="u", content_hash="h")
    anon = Contact(id=2, company="Acme", name=None, role="talent team", source="fallback")
    bad = MSG.replace("Hi Bo", "Hi Maria Silva")
    r = draft_outreach(job, anon, ATOMS, FakeProvider([_resp(msg=bad, note=NOTE)]), profile=PROFILE)
    assert [d.kind for d in r.drafts] == ["connection_note"]
