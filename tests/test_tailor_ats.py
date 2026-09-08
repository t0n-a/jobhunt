import pytest

from jobhunt.tailor.ats import check_coverage, extract_pdf_text


class FakeExtract:
    """Stands in for pdftotext so coverage logic is tested without a PDF."""

    def __init__(self, text):
        self.text = text

    def __call__(self, path):
        return self.text


def test_full_coverage_is_a_hundred(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "jobhunt.tailor.ats.extract_pdf_text", FakeExtract("Python and Kubernetes.")
    )

    report = check_coverage(tmp_path / "r.pdf", ["Python", "Kubernetes"])

    assert report.coverage == 100.0
    assert report.missing == []


def test_a_missing_keyword_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr("jobhunt.tailor.ats.extract_pdf_text", FakeExtract("Python only."))

    report = check_coverage(tmp_path / "r.pdf", ["Python", "Kubernetes"])

    assert report.coverage == 50.0
    assert report.missing == ["Kubernetes"]


def test_matching_ignores_case(monkeypatch, tmp_path):
    monkeypatch.setattr("jobhunt.tailor.ats.extract_pdf_text", FakeExtract("python and REDIS"))

    report = check_coverage(tmp_path / "r.pdf", ["Python", "Redis"])

    assert report.missing == []


def test_an_alias_spelling_counts_as_covered(monkeypatch, tmp_path):
    """The resume says Amazon Web Services, the keyword is AWS. Reporting that
    as missing would push the user to stuff a synonym into their bullets."""
    monkeypatch.setattr(
        "jobhunt.tailor.ats.extract_pdf_text", FakeExtract("Hosted on Amazon Web Services.")
    )

    report = check_coverage(tmp_path / "r.pdf", ["AWS"])

    assert report.missing == []
    assert report.coverage == 100.0


def test_no_keywords_is_full_coverage_not_a_crash(monkeypatch, tmp_path):
    monkeypatch.setattr("jobhunt.tailor.ats.extract_pdf_text", FakeExtract("anything"))

    assert check_coverage(tmp_path / "r.pdf", []).coverage == 100.0


def test_empty_extracted_text_means_nothing_is_covered(monkeypatch, tmp_path):
    """A PDF that parses to nothing looks perfect on screen. This is the number
    that tells the user their document is unreadable to a machine."""
    monkeypatch.setattr("jobhunt.tailor.ats.extract_pdf_text", FakeExtract(""))

    report = check_coverage(tmp_path / "r.pdf", ["Python"])

    assert report.coverage == 0.0
    assert report.missing == ["Python"]


def test_a_missing_pdf_raises_runtime_error(tmp_path):
    with pytest.raises(RuntimeError):
        extract_pdf_text(tmp_path / "nope.pdf")
