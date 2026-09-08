import re

from jobhunt.jobs.models import Job, RawPosting

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")

# Tokens that differ between sources for the same real role and carry no meaning:
# company legal forms, and the gender markers European postings append to titles.
_NOISE = frozenset(
    {
        # legal forms
        "inc",
        "ltd",
        "lda",
        "sa",
        "gmbh",
        "bv",
        "llc",
        "limited",
        "srl",
        "ab",
        "oy",
        "as",
        # gender markers: "(m/f)", "(m/w/d)", "(h/f)" all become single letters
        # once punctuation is stripped
        "m",
        "f",
        "d",
        "w",
        "h",
        "x",
    }
)


def normalize(text: str) -> str:
    """Lowercase, strip punctuation, and drop legal-form and gender-marker noise.

    The same opening is posted to a company's own ATS, to an aggregator, and to a
    job board, each with slightly different cosmetics: "Acme Inc." vs "Acme",
    "Senior Backend Engineer (m/f)" vs "Senior Backend Engineer". Collapsing that
    noise is what lets dedupe recognise them as one job.
    """
    cleaned = _PUNCT_RE.sub(" ", (text or "").casefold())
    words = [w for w in _WS_RE.split(cleaned) if w and w not in _NOISE]
    return " ".join(words).strip()


def is_duplicate(posting: RawPosting, job: Job) -> bool:
    """True when a raw posting describes the same opening as an existing job.

    Deliberately strict: same company AND same normalised title. Fuzzier matching
    (edit distance, description similarity) would collapse genuinely different
    roles at the same company — "Backend Engineer" and "Senior Backend Engineer"
    are different jobs the user may want to judge separately.
    """
    return normalize(posting.company) == normalize(job.company) and normalize(
        posting.title
    ) == normalize(job.title)
