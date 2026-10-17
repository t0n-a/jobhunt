import re
from urllib.parse import parse_qsl, urlencode, urlsplit

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


# Query params that mark where a click came from, never which job it is.
_TRACKING_PARAMS = frozenset(
    {
        "gh_src",
        "ref",
        "refid",
        "source",
        "src",
        "trk",
        "trackingid",
        "lever-source",
        "fbclid",
        "gclid",
    }
)
# LinkedIn slugs ("backend-dev-at-acme-<id>") differ per channel; the numeric id is stable.
_LINKEDIN_VIEW_RE = re.compile(r"/jobs/view/(?:[^/]*-)?(\d+)")
_LINKEDIN_CURRENT_RE = re.compile(r"(?:^|&)currentJobId=(\d+)")

_SENIORITY = {
    "intern": "intern",
    "junior": "junior",
    "jr": "junior",
    "graduate": "graduate",
    "associate": "associate",
    "mid": "mid",
    "senior": "senior",
    "sr": "senior",
    "staff": "staff",
    "principal": "principal",
    "lead": "lead",
}
# Roman and arabic numerals both mean a level: "Engineer II" is "Engineer 2".
_LEVELS = {
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "v": 5,
    "vi": 6,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
}
_CONTENT_ALIAS = {"dev": "engineer", "developer": "engineer"}
# Words every engineering title carries: they cannot make two different roles match alone.
_BASELINE = frozenset({"engineer", "software", "developer"})
_MTS_RE = re.compile(r"\bmember of technical staff\b")


def _is_tracking(key: str) -> bool:
    key = key.lower()
    return key.startswith("utm_") or key in _TRACKING_PARAMS


def url_key(url: str) -> str:
    """Canonical form of a posting URL, so copies of one posting compare equal.

    Tracking params, fragments and a trailing slash change per channel for the same
    posting. A LinkedIn job id is the stable part of a LinkedIn URL, so any LinkedIn
    job URL reduces to it. Returns "" for input with no host.
    """
    try:
        parts = urlsplit((url or "").strip())
        host = parts.hostname or ""
    except ValueError:  # scraped URLs are untrusted: "http://[bad" raises
        return ""
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        match = _LINKEDIN_VIEW_RE.search(parts.path) or _LINKEDIN_CURRENT_RE.search(parts.query)
        if match:
            return f"linkedin.com/jobs/view/{match.group(1)}"
    if not host:
        return ""
    host = host.removeprefix("www.")
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)
    )
    key = f"{parts.scheme.lower()}://{host}{parts.path.rstrip('/')}"
    return f"{key}?{urlencode(query)}" if query else key


def _split_title(title: str) -> tuple[frozenset[str], frozenset[int], frozenset[str]]:
    """Split a normalised title into (seniority, levels, content) token sets."""
    text = _MTS_RE.sub("engineer", normalize(title))
    seniority, levels, content = set(), set(), set()
    for token in text.split():
        if token in _SENIORITY:
            seniority.add(_SENIORITY[token])
        elif token in _LEVELS:
            levels.add(_LEVELS[token])
        else:
            content.add(_CONTENT_ALIAS.get(token, token))
    return frozenset(seniority), frozenset(levels), frozenset(content)


def role_match(a: str, b: str) -> bool:
    """True when two job titles name the same role at the same level.

    Seniority and level must match exactly: "Senior" and "II" vs "III" are different
    openings. The remaining words must overlap enough (Jaccard >= 0.6, at least two
    shared, at least one not generic) and a title may only add generic words to
    another's, so "Python Backend Engineer" is not "Backend Engineer".
    """
    if normalize(a) == normalize(b):
        return True
    seniority_a, levels_a, content_a = _split_title(a)
    seniority_b, levels_b, content_b = _split_title(b)
    if seniority_a != seniority_b or levels_a != levels_b:
        return False
    shared = content_a & content_b
    union = content_a | content_b
    if len(shared) < 2 or not shared - _BASELINE or len(shared) / len(union) < 0.6:
        return False
    if content_a < content_b:
        return (content_b - content_a) <= _BASELINE
    if content_b < content_a:
        return (content_a - content_b) <= _BASELINE
    return True


def is_duplicate(posting: RawPosting, job: Job) -> bool:
    """True when a raw posting describes the same opening as an existing job.

    Same URL key is the same posting. Otherwise it must be the same normalised
    company and a matching role title (see role_match), so "Backend Developer" on
    LinkedIn matches "Backend Engineer" at the company's ATS. Seniority and level
    stay distinct: "Backend Engineer" and "Senior Backend Engineer" are different
    jobs the user may want to judge separately.
    """
    # An empty key means no usable URL, not a shared one: never match on it.
    key = url_key(posting.url)
    if key and key == url_key(job.url):
        return True
    return normalize(posting.company) == normalize(job.company) and role_match(
        posting.title, job.title
    )
