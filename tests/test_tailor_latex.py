import shutil
import subprocess

import pytest

from jobhunt.config import ProfileConfig
from jobhunt.tailor.latex import latex_escape, render_pdf, render_tex
from jobhunt.tailor.models import Application

HAS_LATEX = shutil.which("latexmk") is not None and shutil.which("pdftotext") is not None


def profile(**over) -> ProfileConfig:
    fields = {"name": "Ada Lovelace", "email": "ada@example.com", "location": "Porto"}
    fields.update(over)
    return ProfileConfig(**fields)


def resume() -> dict:
    return {
        "headline": "Backend Engineer",
        "summary": "Shipped Python services.",
        "sections": [
            {
                "name": "Experience",
                "entries": [
                    {
                        "atom_id": 1,
                        "title": "Backend Intern",
                        "org": "Marks & Spencer",
                        "dates": "2025",
                        "bullets": [
                            {
                                "atom_id": 1,
                                "text": "Refactored some_module.py and cut p95 by 40%.",
                                "source": "model",
                            }
                        ],
                    }
                ],
            }
        ],
        "skills": ["Python", "C#"],
    }


def test_latex_escape_handles_every_metacharacter():
    escaped = latex_escape("a & b % c $ d # e _ f { g } h")

    for raw in ("\\&", "\\%", "\\$", "\\#", "\\_", "\\{", "\\}"):
        assert raw in escaped


def test_latex_escape_handles_none():
    assert latex_escape(None) == ""


def test_an_ampersand_in_a_company_name_is_escaped_not_dropped():
    """An unescaped & is an alignment tab: it fails the build on a real
    employer's name, which is the worst possible time to find out."""
    tex = render_tex(resume(), profile())

    assert r"Marks \& Spencer" in tex


def test_an_underscore_and_percent_in_a_bullet_are_escaped():
    tex = render_tex(resume(), profile())

    assert r"some\_module.py" in tex
    assert r"40\%" in tex


def test_optional_contact_fields_are_omitted_when_unset():
    tex = render_tex(resume(), profile())

    assert "linkedin" not in tex.casefold()


def test_optional_contact_fields_appear_when_set():
    tex = render_tex(resume(), profile(phone="+351 000", linkedin="linkedin.com/in/ada"))

    assert "+351 000" in tex
    assert "linkedin.com/in/ada" in tex


def test_the_document_uses_nothing_an_ats_parser_chokes_on():
    """Tables interleave into gibberish, graphics do not parse, and header or
    footer content is routinely stripped along with the contact details."""
    tex = render_tex(resume(), profile())

    for forbidden in (r"\begin{tabular}", "multicol", "graphicx", "tikz", r"\fancyhead"):
        assert forbidden not in tex
    assert r"\pagestyle{empty}" in tex


def test_skills_render_as_one_line():
    tex = render_tex(resume(), profile())

    assert r"Python, C\#" in tex


@pytest.mark.latex
@pytest.mark.skipif(not HAS_LATEX, reason="needs latexmk and pdftotext")
def test_the_pdf_builds_and_the_text_comes_back_out(tmp_path):
    """The round trip is the real test of the pipeline: a PDF an ATS cannot read
    is indistinguishable from no application at all."""
    application = Application(id=1, job_id=1, resume_json=resume())

    pdf = render_pdf(application, profile(), tmp_path)

    assert pdf.exists()
    text = subprocess.run(
        ["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=True
    ).stdout
    assert "Ada Lovelace" in text
    assert "Marks & Spencer" in text
    assert "40%" in text
    assert "some_module.py" in text
    assert "C#" in text


@pytest.mark.latex
@pytest.mark.skipif(not HAS_LATEX, reason="needs latexmk and pdftotext")
def test_an_empty_document_still_builds(tmp_path):
    """A resume whose every bullet was dropped must render something the user
    can look at, not crash on the way to telling them why."""
    application = Application(
        id=2, job_id=1, resume_json={"headline": "", "summary": "", "sections": [], "skills": []}
    )

    assert render_pdf(application, profile(), tmp_path).exists()
