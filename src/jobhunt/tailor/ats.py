import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from jobhunt.matching.skills import SKILL_ALIASES, canonical_skill, mentions_term

TIMEOUT = 60


@dataclass
class AtsReport:
    coverage: float
    missing: list[str] = field(default_factory=list)
    text: str = ""


def extract_pdf_text(pdf_path: Path) -> str:
    """Re-read the generated PDF the way a parser would.

    Reuses pdftotext, already required by the CV importer, so this adds no new
    dependency.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise RuntimeError(f"No PDF to check at {pdf_path}")
    if shutil.which("pdftotext") is None:
        raise RuntimeError(
            "pdftotext is not installed. Install poppler-utils to run the ATS check."
        )
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), "-"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"pdftotext timed out after {TIMEOUT} seconds") from exc
    if result.returncode != 0:
        raise RuntimeError(f"pdftotext failed on {pdf_path.name}: {result.stderr[-500:]}")
    return result.stdout


def check_coverage(pdf_path: Path, keywords: list[str]) -> AtsReport:
    """Report which posting keywords survived into the generated document.

    A keyword counts as present under any of its known spellings: the resume is
    written in the evidence's own words, and reporting "AWS" missing from a
    resume that says "Amazon Web Services" would send the user to pad bullets
    for no reason.
    """
    text = extract_pdf_text(pdf_path)
    haystack = text.casefold()

    if not keywords:
        return AtsReport(coverage=100.0, missing=[], text=text)

    missing = [word for word in keywords if not _mentioned(word, haystack)]
    found = len(keywords) - len(missing)
    return AtsReport(
        coverage=round(100.0 * found / len(keywords), 1),
        missing=missing,
        text=text,
    )


def _spellings(keyword: str) -> set[str]:
    canonical = canonical_skill(keyword)
    spellings = {keyword.casefold(), canonical.casefold()}
    spellings.update(
        alias
        for alias, mapped in SKILL_ALIASES.items()
        if mapped.casefold() == canonical.casefold()
    )
    return {s for s in spellings if s}


def _mentioned(keyword: str, haystack: str) -> bool:
    """Word boundaries, not substrings -- the same rule the shortlist uses.

    A substring test reads "Go" inside "Django" and "R" inside almost any prose,
    and here that error runs the dangerous way round: a false "covered" tells the
    user nothing is missing and silently suppresses the one signal this report
    exists to give. A false "missing" only costs them a second look.
    """
    return any(mentions_term(haystack, spelling) for spelling in _spellings(keyword))
