import pytest
from sqlmodel import Session

from jobhunt.config import Config, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.growth import act
from jobhunt.growth.audit import Audit
from jobhunt.growth.gh import GhError
from jobhunt.growth.models import Suggestion, upsert_suggestion
from jobhunt.telegram import growth_message

CONFIG = Config(profile=ProfileConfig(name="A", email="a@x.com", location="P", github_username="ada"))


class FakeGh:
    def __init__(self, fail=None, sha="old"):
        self.calls, self.fail, self.sha = [], fail, sha

    def make_public(self, name):
        self.calls.append(("public", name))
        if self.fail:
            raise GhError(self.fail)

    def put_file(self, name, path, text, message, sha):
        self.calls.append(("put", name, path, text, sha))
        if self.fail:
            raise GhError(self.fail)

    def readme(self, name):
        return "# hi", self.sha

    def tree(self, name):
        return ["a.py"]


def clean_audit(*a, **k):
    return Audit([], blocking=False)


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "g.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def pub(session, **kw):
    return upsert_suggestion(session, "publish", "ada/x", title="Make x public", **kw)


def test_publish_refused_when_secret_found(session):
    s = pub(session, flags=["secret in history: key"], blocking=True)
    gh = FakeGh()
    reply = act.publish(session, gh, CONFIG, s.id, audit=lambda *a, **k: Audit(["secret in history: key"], True))
    assert reply.startswith("Not publishing ada/x: secret in history: key. Rotate that secret")
    assert gh.calls == [] and session.get(Suggestion, s.id).status == "suggested"


def test_publish_refuses_on_fresh_audit_even_if_stored_clean(session):
    s = pub(session)
    gh = FakeGh()
    reply = act.publish(session, gh, CONFIG, s.id, audit=lambda *a, **k: Audit(["new secret"], True))
    assert reply.startswith("Not publishing") and gh.calls == []
    row = session.get(Suggestion, s.id)
    assert row.blocking and row.flags == ["new secret"]


def test_publish_makes_public_and_marks_done(session):
    s = pub(session)
    gh = FakeGh()
    assert act.publish(session, gh, CONFIG, s.id, audit=clean_audit) == "ada/x is public now."
    assert gh.calls == [("public", "ada/x")] and session.get(Suggestion, s.id).status == "done"
    assert act.publish(session, gh, CONFIG, s.id, audit=clean_audit) == f"#{s.id} is done; nothing changed."


def test_approve_does_not_publish(session):
    s = pub(session)
    gh = FakeGh()
    assert act.approve(session, gh, s.id) == f"Use publish {s.id} to make a repo public."
    assert gh.calls == [] and session.get(Suggestion, s.id).status == "suggested"
    pin = upsert_suggestion(session, "pin", "ada/y", title="Pin")
    assert act.approve(session, gh, pin.id).startswith("Nothing to approve")


def test_gh_failure_is_reported_not_done(session):
    s = pub(session)
    reply = act.publish(session, FakeGh(fail="boom"), CONFIG, s.id, audit=clean_audit)
    row = session.get(Suggestion, s.id)
    assert reply == "Couldn't publish ada/x: boom" and row.status == "failed" and row.error == "boom"


def test_approve_commits_readme_with_sha(session):
    s = upsert_suggestion(session, "readme", "ada/x", title="README", body="# x\n")
    gh = FakeGh()
    assert act.approve(session, gh, s.id) == "README committed to ada/x (it replaced the old one)."
    assert gh.calls == [("put", "ada/x", "README.md", "# x\n", "old")]
    assert session.get(Suggestion, s.id).status == "done"


def test_decline(session):
    s = pub(session)
    assert act.decline(session, s.id) == f"Declined #{s.id}."
    assert session.get(Suggestion, s.id).status == "declined"


def test_growth_messages_are_clean_per_line(session):
    s = pub(session, flags=["f1"], blocking=False)
    assert growth_message(s) == f"#{s.id} Make x public\n  ⚠ f1\npublish {s.id} / decline {s.id}"
    b = pub(session, flags=["f1"], blocking=True)
    assert growth_message(b).endswith("Blocked until fixed.")
    r = upsert_suggestion(session, "readme", "ada/z", title="t", body="# z\n\nbody")
    assert growth_message(r).splitlines()[0] == f"#{r.id} README draft for ada/z"
    assert growth_message(r).endswith(f"approve {r.id} / decline {r.id}")



def test_overlong_draft_is_not_committed(session):
    s = upsert_suggestion(session, "readme", "ada/big", title="t", body="x" * (act.MAX_SHOWN + 1))
    gh = FakeGh()
    assert "too long" in act.approve(session, gh, s.id)
    assert gh.calls == []
