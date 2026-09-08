import shutil
import subprocess
import tempfile
from pathlib import Path

import jinja2

from jobhunt.config import ProfileConfig
from jobhunt.tailor.models import Application

HERE = Path(__file__).parent
TEMPLATES = HERE / "templates"
TIMEOUT = 120

ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


class LatexError(RuntimeError):
    """The document could not be built. Carries the log tail, never swallows it."""


def latex_escape(value) -> str:
    """Escape one interpolated value for LaTeX.

    An unescaped & is an alignment tab and a # is a parameter marker, so a real
    employer's name is enough to fail the build.
    """
    text = "" if value is None else str(value)
    return "".join(ESCAPES.get(char, char) for char in text)


def build_env() -> jinja2.Environment:
    """Jinja2 with LaTeX-safe delimiters and automatic escaping.

    Escaping runs through finalize rather than a per-variable filter: a filter
    works until someone adds a variable and forgets it, and that failure lands
    on the user's own surname.
    """
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATES),
        block_start_string=r"\BLOCK{",
        block_end_string="}",
        variable_start_string=r"\VAR{",
        variable_end_string="}",
        comment_start_string=r"\#{",
        comment_end_string="}",
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
        undefined=jinja2.StrictUndefined,
    )
    env.finalize = latex_escape
    return env


def render_tex(resume_json: dict, profile: ProfileConfig) -> str:
    """Render the document to LaTeX source."""
    document = resume_json or {}
    skills = [s for s in document.get("skills", []) if s]
    return (
        build_env()
        .get_template("resume.tex.j2")
        .render(
            profile=profile,
            headline=document.get("headline", ""),
            summary=document.get("summary", ""),
            sections=document.get("sections", []),
            skills=skills,
            skills_line=", ".join(skills),
        )
    )


def render_pdf(application: Application, profile: ProfileConfig, out_dir: Path) -> Path:
    """Build the PDF and return its path.

    Returns a path rather than bytes because the path is what Application.pdf_path
    stores and what the ATS check re-reads.
    """
    if shutil.which("latexmk") is None:
        raise LatexError(
            "latexmk is not installed. Install a TeX distribution (on Debian or "
            "Ubuntu: apt install latexmk texlive-latex-recommended) to render PDFs."
        )

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"application-{application.id}.pdf"

    with tempfile.TemporaryDirectory() as work:
        work_dir = Path(work)
        (work_dir / "resume.tex").write_text(
            render_tex(application.resume_json, profile), encoding="utf-8"
        )
        build = subprocess.run(
            ["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error", "resume.tex"],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
        built = work_dir / "resume.pdf"
        if build.returncode != 0 or not built.exists():
            # Surfaced, not swallowed: the LaTeX error is the only thing that
            # tells the user which character in their own data broke the build.
            raise LatexError(f"latexmk failed:\n{build.stdout[-2000:]}")
        target.write_bytes(built.read_bytes())

    return target
