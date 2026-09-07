from pathlib import Path

import pytest

from jobhunt.llm import FakeProvider
from jobhunt.profile.importers.cv import extract_text, import_cv
from jobhunt.profile.models import AtomKind, Provenance

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_text_reads_txt():
    text = extract_text(FIXTURES / "sample_cv.txt")

    assert "URL Shortener" in text


def test_extract_text_rejects_unknown_suffix(tmp_path):
    bad = tmp_path / "cv.rtf"
    bad.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported"):
        extract_text(bad)


def test_import_cv_builds_atoms_from_llm_response():
    llm = FakeProvider(
        [
            {
                "atoms": [
                    {
                        "kind": "project",
                        "title": "URL Shortener",
                        "description": "Flask service with Redis caching on a VPS.",
                        "skills": ["Python", "Flask", "Redis"],
                        "metrics": "2000 requests/day",
                    }
                ]
            }
        ]
    )

    atoms = import_cv(FIXTURES / "sample_cv.txt", llm)

    assert len(atoms) == 1
    assert atoms[0].title == "URL Shortener"
    assert atoms[0].kind is AtomKind.PROJECT
    assert atoms[0].skills == ["Python", "Flask", "Redis"]
    assert atoms[0].metrics == "2000 requests/day"


def test_imported_atoms_are_marked_unconfirmed_and_sourced():
    llm = FakeProvider(
        [
            {
                "atoms": [
                    {
                        "kind": "project",
                        "title": "T",
                        "description": "D",
                        "skills": [],
                        "metrics": None,
                    }
                ]
            }
        ]
    )

    atoms = import_cv(FIXTURES / "sample_cv.txt", llm)

    assert atoms[0].confirmed is False
    assert atoms[0].provenance is Provenance.CV
    assert atoms[0].source_ref == "sample_cv.txt"


def test_prompt_includes_cv_text_and_no_invention_instruction():
    llm = FakeProvider([{"atoms": []}])

    import_cv(FIXTURES / "sample_cv.txt", llm)

    prompt = llm.prompts[0]
    assert "URL Shortener" in prompt
    assert "Do not invent" in prompt


def test_unknown_kind_falls_back_to_project():
    llm = FakeProvider(
        [
            {
                "atoms": [
                    {
                        "kind": "wildly-unexpected",
                        "title": "T",
                        "description": "D",
                        "skills": [],
                        "metrics": None,
                    }
                ]
            }
        ]
    )

    atoms = import_cv(FIXTURES / "sample_cv.txt", llm)

    assert atoms[0].kind is AtomKind.PROJECT


def test_empty_atom_list_returns_empty():
    llm = FakeProvider([{"atoms": []}])

    assert import_cv(FIXTURES / "sample_cv.txt", llm) == []
