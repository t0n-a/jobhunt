import subprocess
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


def test_malformed_atom_is_skipped_and_others_survive():
    llm = FakeProvider(
        [
            {
                "atoms": [
                    {
                        "kind": "project",
                        "description": "Missing title",
                        "skills": ["Python"],
                        "metrics": None,
                    },
                    {
                        "kind": "project",
                        "title": "Good Atom",
                        "description": "Valid description",
                        "skills": ["Python"],
                        "metrics": None,
                    },
                ]
            }
        ]
    )

    atoms = import_cv(FIXTURES / "sample_cv.txt", llm)

    assert len(atoms) == 1
    assert atoms[0].title == "Good Atom"
    assert atoms[0].description == "Valid description"


def test_empty_pdf_text_raises_value_error(monkeypatch, tmp_path):
    pdf = tmp_path / "empty.pdf"
    pdf.write_bytes(b"%PDF-fake")

    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        ),
    )

    with pytest.raises(ValueError, match="extractable text"):
        extract_text(pdf)


def test_missing_pdftotext_raises_runtime_error(monkeypatch, tmp_path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-fake")

    def mock_run(*args, **kwargs):
        raise FileNotFoundError("pdftotext not found")

    monkeypatch.setattr("subprocess.run", mock_run)

    with pytest.raises(RuntimeError, match="poppler"):
        extract_text(pdf)


def test_corrupt_docx_raises_value_error(tmp_path):
    bad = tmp_path / "corrupt.docx"
    bad.write_text("not an ooxml file", encoding="utf-8")

    with pytest.raises(ValueError):
        extract_text(bad)
