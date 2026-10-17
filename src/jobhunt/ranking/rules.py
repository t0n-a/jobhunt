"""Cheap rule-based triage: level, required languages, pay and visa. Runs before any LLM call."""

import re

from jobhunt.config import RankingConfig
from jobhunt.jobs.models import Job, JobFacts
from jobhunt.ranking.geo import country_of, visa_class
from jobhunt.ranking.pay import PAY_SOURCE, annual_eur, estimate_pay, parse_salary

_SENIOR_TITLE = re.compile(
    r"\b(senior|sr|staff|principal|lead|head|director|architect|manager|vp)\b"
    r"|\b(iii|iv|v|3|4|5)$",
    re.IGNORECASE,
)
_JUNIOR_TITLE = re.compile(
    r"\b(junior|jr|graduate|grad|entry[- ]level|intern|internship|trainee|apprentice"
    r"|new grad|associate)\b"
    r"|\b(i|1)$",
    re.IGNORECASE,
)
# Years count as seniority only when experience is asked for, not "we've been around for 5 years".
# A range ("2-5 years") counts by its lower bound.
_YEARS = re.compile(r"\b(\d{1,3})(?:\s?[-–]\s?\d{1,3}(?!\d))?\+?\s*(?:years?|yrs?)\b", re.IGNORECASE)
_EXPERIENCE_NEAR = re.compile(r"experience|years of", re.IGNORECASE)

_LANGUAGES = [
    "english",
    "portuguese",
    "spanish",
    "french",
    "german",
    "dutch",
    "italian",
    "polish",
    "swedish",
    "danish",
    "norwegian",
    "finnish",
    "czech",
    "hungarian",
    "romanian",
    "greek",
    "turkish",
    "russian",
    "ukrainian",
    "hebrew",
    "arabic",
    "japanese",
    "korean",
    "mandarin",
    "chinese",
]
# Local spellings of a language name map to the canonical name.
_LANGUAGE_ALIASES = {"deutsch": "german", "français": "french", "nederlands": "dutch"}
_LANGUAGE_WORD = re.compile(
    r"\b(" + "|".join(sorted([*_LANGUAGES, *_LANGUAGE_ALIASES], key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_REQUIRED_HINT = re.compile(
    r"fluent|fluency|native|required|mandatory|must|business[- ]level|proficien|\bc1\b|\bc2\b",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"[.!?;\n]")


def level_of(title: str, description: str) -> str:
    if _SENIOR_TITLE.search(title.strip()):
        return "senior"
    for m in _YEARS.finditer(description):
        if int(m.group(1)) >= 5:
            window = description[max(0, m.start() - 40) : m.end() + 40]
            if _EXPERIENCE_NEAR.search(window):
                return "senior"
    if _JUNIOR_TITLE.search(title.strip()):
        return "junior"
    return "mid"


def required_languages(description: str) -> list[str]:
    found: set[str] = set()
    for sentence in _SENTENCE_END.split(description):
        if not _REQUIRED_HINT.search(sentence):
            continue
        for m in _LANGUAGE_WORD.finditer(sentence):
            word = m.group(1).lower()
            found.add(_LANGUAGE_ALIASES.get(word, word))
    return sorted(found)


def _posted_eur(job: Job) -> int | None:
    for text in (job.salary_text, job.description):
        salary = parse_salary(text)
        if salary is not None and (eur := annual_eur(salary)) is not None:
            return eur
    return None


def triage(job: Job, cfg: RankingConfig) -> JobFacts:
    level = level_of(job.title, job.description)
    languages = required_languages(job.description)
    country = country_of(job.location)
    posted = _posted_eur(job)
    if posted is not None:
        pay, estimated, source = posted, False, "posted"
    else:
        pay, estimated, source = estimate_pay(country, level), True, PAY_SOURCE

    reason = None
    if level == "senior":
        reason = "senior"
    else:
        missing = [lang for lang in languages if lang not in cfg.languages]
        if missing:
            reason = f"language:{missing[0]}"

    return JobFacts(
        job_id=job.id,
        content_hash=job.content_hash,
        level=level,
        languages_required=languages,
        country=country,
        visa=visa_class(country, job.description, pay, cfg.min_pay_outside_eu),
        pay_yearly_eur=pay,
        pay_estimated=estimated,
        pay_source=source,
        filtered_reason=reason,
    )
