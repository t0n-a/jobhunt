import pytest
from sqlmodel import Session

from jobhunt.config import Config, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.growth import act
from jobhunt.growth.audit import Audit
from jobhunt.growth.gh import GhError
from jobhunt.growth.models import Suggestion, _now, upsert_suggestion
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
    # A suggestion he has already seen on Telegram, unless the test says otherwise.
    kw = {"shown_at": _now(), "shown_flags": list(kw.get("flags", []))} | kw
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


def shown(session, **kw):
    kw = {"title": "README", "body": "# x\n", "flags": ["replaces existing README"], "shown_at": _now(),
          "base_sha": "old"} | kw
    return upsert_suggestion(session, "readme", "ada/x", **kw)


def test_approve_commits_readme_with_sha(session):
    s = shown(session)
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
    s = upsert_suggestion(session, "readme", "ada/big", title="t", body="x" * (act.MAX_SHOWN + 1), shown_at=_now())
    gh = FakeGh()
    assert "too long" in act.approve(session, gh, s.id)
    assert gh.calls == []


def test_failed_publish_and_approve_can_be_retried(session):
    s = pub(session)
    assert act.publish(session, FakeGh(fail="boom"), CONFIG, s.id, audit=clean_audit).startswith("Couldn't")
    assert act.publish(session, FakeGh(), CONFIG, s.id, audit=clean_audit) == "ada/x is public now."
    r = shown(session)
    assert act.approve(session, FakeGh(fail="boom"), r.id).startswith("Couldn't commit")
    assert session.get(Suggestion, r.id).status == "failed"
    assert act.approve(session, FakeGh(), r.id).startswith("README committed")


def test_approve_sha_conflict_says_changed_and_commits_nothing(session):
    s = shown(session)
    gh = FakeGh(fail="HTTP 409: README.md does not match")
    gh.readme = lambda name: ("# hi", "old")
    reply = act.approve(session, gh, s.id)
    assert reply == "The README on ada/x changed since the draft; nothing committed. Decline it; a new draft comes next week."
    assert session.get(Suggestion, s.id).status == "failed"


def test_approve_refuses_an_unseen_draft(session):
    s = shown(session, shown_at=None)
    gh = FakeGh()
    assert act.approve(session, gh, s.id) == f"#{s.id} hasn't reached you on Telegram yet; nothing committed."
    assert gh.calls == []


def test_approve_refuses_when_a_readme_appeared_after_a_new_readme_draft(session):
    s = shown(session, flags=[], base_sha=None)
    gh = FakeGh()
    assert "changed since the draft" in act.approve(session, gh, s.id) and gh.calls == []


def test_approve_refuses_when_he_edited_the_readme_after_the_draft(session):
    s = shown(session, base_sha="v1")
    gh = FakeGh(sha="v2")
    assert "changed since the draft" in act.approve(session, gh, s.id) and gh.calls == []
    assert "changed since the draft" in act.approve(session, gh, s.id)  # a retry refuses too


def test_publish_shows_new_findings_before_acting(session):
    s = pub(session)
    gh = FakeGh()
    found = lambda *a, **k: Audit(["may hold personal data: users.csv (30 emails)"], False)
    first = act.publish(session, gh, CONFIG, s.id, audit=found)
    assert "users.csv" in first and "publish" in first and gh.calls == []
    assert act.publish(session, gh, CONFIG, s.id, audit=found) == "ada/x is public now."


def test_publish_never_shown_asks_first(session):
    s = pub(session, shown_at=None)
    gh = FakeGh()
    assert "again to go ahead" in act.publish(session, gh, CONFIG, s.id, audit=clean_audit) and gh.calls == []


def test_readme_message_says_replace_and_shows_dropped_note(session):
    r = shown(session, flags=["replaces existing README", "dropped skills: Rust"])
    lines = growth_message(r).splitlines()
    assert lines[0] == f"#{r.id} README draft for ada/x (replaces your current README)"
    assert "⚠ dropped skills: Rust" in lines and "⚠ replaces existing README" not in lines
