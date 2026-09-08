import pytest
from sqlmodel import Session

from jobhunt.db import create_engine_for, init_db
from jobhunt.tailor.models import (
    Application,
    delete_bullet,
    edit_bullet,
    iter_bullets,
    replace_bullets,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    with Session(engine) as s:
        yield s


def resume() -> dict:
    return {
        "headline": "Backend Engineer",
        "summary": "",
        "sections": [
            {
                "name": "Projects",
                "entries": [
                    {
                        "atom_id": 1,
                        "title": "URL shortener",
                        "org": "Personal",
                        "dates": "2026",
                        "bullets": [
                            {"atom_id": 1, "text": "Built it in Flask.", "source": "model"},
                            {"atom_id": 1, "text": "Cached in Redis.", "source": "model"},
                        ],
                    }
                ],
            },
            {
                "name": "Experience",
                "entries": [
                    {
                        "atom_id": 2,
                        "title": "Intern",
                        "org": "Acme",
                        "dates": "2025",
                        "bullets": [
                            {"atom_id": 2, "text": "Wrote the importer.", "source": "model"}
                        ],
                    }
                ],
            },
        ],
        "skills": ["Python"],
    }


def test_application_roundtrips(session):
    app = Application(job_id=1, keywords=["Python"], resume_json=resume())
    session.add(app)
    session.commit()
    session.refresh(app)

    assert app.id is not None
    assert app.status == "drafted"
    assert app.resume_json["headline"] == "Backend Engineer"


def test_a_fresh_application_has_no_render_results(session):
    """An Application exists from the moment it is composed, which is before any
    PDF has been built. Requiring a coverage figure would mean inventing one."""
    app = Application(job_id=1)
    session.add(app)
    session.commit()
    session.refresh(app)

    assert app.pdf_path is None
    assert app.coverage is None
    assert app.missing_keywords == []
    assert app.dropped_bullets == []


def test_iter_bullets_walks_every_bullet_in_document_order():
    walked = [(index, bullet["text"]) for index, bullet in iter_bullets(resume())]

    assert walked == [
        (0, "Built it in Flask."),
        (1, "Cached in Redis."),
        (2, "Wrote the importer."),
    ]


def test_iter_bullets_on_an_empty_document_is_empty():
    assert iter_bullets({}) == []


def test_replace_bullets_substitutes_in_document_order():
    kept = [
        {"atom_id": 1, "text": "One.", "source": "model"},
        {"atom_id": 2, "text": "Two.", "source": "model"},
    ]

    updated = replace_bullets(resume(), kept)

    assert [b["text"] for _, b in iter_bullets(updated)] == ["One.", "Two."]
    assert len(updated["sections"][0]["entries"][0]["bullets"]) == 2
    assert updated["sections"][1]["entries"][0]["bullets"] == []


def test_replace_bullets_returns_a_new_object():
    """JSON columns are not mutation-tracked: an in-place edit is silently lost
    on commit, so every helper hands back a new dict."""
    original = resume()

    updated = replace_bullets(original, [])

    assert updated is not original
    assert len(list(iter_bullets(original))) == 3


def test_edit_bullet_marks_the_text_as_the_users_own():
    updated = edit_bullet(resume(), 1, "Cached responses in Redis for 24 hours.")

    _, bullet = iter_bullets(updated)[1]
    assert bullet["text"] == "Cached responses in Redis for 24 hours."
    assert bullet["source"] == "user"


def test_edit_bullet_leaves_the_original_untouched():
    original = resume()

    edit_bullet(original, 0, "Something else.")

    assert iter_bullets(original)[0][1]["text"] == "Built it in Flask."


def test_delete_bullet_removes_exactly_one():
    updated = delete_bullet(resume(), 0)

    assert [b["text"] for _, b in iter_bullets(updated)] == [
        "Cached in Redis.",
        "Wrote the importer.",
    ]


def test_editing_out_of_range_raises_index_error():
    with pytest.raises(IndexError):
        edit_bullet(resume(), 99, "nope")


def test_no_streak_or_score_field_creeps_onto_the_application():
    """Phase 3's rule holds here too: nothing in this project accumulates a
    number whose only purpose is to make the user feel behind."""
    names = set(Application.model_fields)

    assert not {n for n in names if "streak" in n.lower()}
