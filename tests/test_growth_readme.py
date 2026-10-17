import pytest
from sqlmodel import Session

from jobhunt.db import create_engine_for, init_db
from jobhunt.growth.readme import SKILL_GROUPS, draft_profile_readme, draft_repo_readme
from jobhunt.llm.fake import FakeProvider
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "g.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


class FakeGh:
    def __init__(self, tree=("pyproject.toml", "app.py")):
        self._tree = list(tree)

    def tree(self, full):
        return self._tree

    def languages(self, full):
        return {"Python": 100}

    def readme(self, full):
        return ("# old", "sha")


def atom(skills=("Docker",)):
    return EvidenceAtom(id=1, kind=AtomKind.PROJECT, title="Tool", description="A tool",
                        skills=list(skills), provenance=Provenance.GITHUB,
                        source_ref="https://github.com/ada/tool", confirmed=True)


def reply(**kw):
    return {"summary": "A Python tool.", "features": [], "skills": ["Python"], "run": "uv run app.py"} | kw


def draft(session, resp, gh=None):
    return draft_repo_readme(session, gh or FakeGh(), "ada/tool", [atom()], FakeProvider([resp]))


def test_unevidenced_skill_is_dropped(session):
    s = draft(session, reply(skills=["Python", "Docker", "Kubernetes"]))
    assert "Docker" in s.body and "Python" in s.body
    assert "Dropped (no evidence): Kubernetes" in s.body


def test_skills_are_grouped_and_editors_are_tools(session):
    assert SKILL_GROUPS["Vim"] == SKILL_GROUPS["VS Code"] == "Tools"
    s = draft(session, reply(skills=["Python", "Docker"]))
    assert "**Languages:** Python" in s.body and "**Tools:** Docker" in s.body


def test_unverified_summary_is_dropped(session):
    s = draft(session, reply(summary="Handles 9000 requests a second."))
    assert "9000" not in s.body


def test_run_section_only_with_an_entry_point(session):
    assert "## Running it" in draft(session, reply()).body
    assert "## Running it" not in draft(session, reply(), FakeGh(tree=("app.py",))).body


def test_profile_readme_lists_no_forks(session):
    repos = [{"name": "mine", "nameWithOwner": "ada/mine", "description": "Mine", "isFork": False},
             {"name": "theirs", "nameWithOwner": "ada/theirs", "description": "Not mine", "isFork": True}]
    llm = FakeProvider([{"intro": "A Python developer.", "skills": ["Python", "Rust"]}])
    s = draft_profile_readme(session, FakeGh(), "ada", [atom()], repos, llm)
    assert s.target == "ada/ada" and s.kind == "profile_readme"
    assert "mine" in s.body and "theirs" not in s.body
    assert "Dropped (no evidence): Rust" in s.body


def test_llm_error_gives_none(session):
    assert draft(session, "not a dict") is None
    assert draft_repo_readme(session, FakeGh(), "ada/tool", [], FakeProvider([])) is None
