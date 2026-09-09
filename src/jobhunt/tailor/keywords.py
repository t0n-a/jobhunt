from jobhunt.jobs.models import Job
from jobhunt.llm import LLMError, LLMProvider
from jobhunt.matching.skills import canonical_skills

KEYWORDS_SCHEMA = {
    "type": "object",
    "properties": {"keywords": {"type": "array", "items": {"type": "string"}}},
    "required": ["keywords"],
}

PROMPT = """\
Extract the terms an applicant tracking system would screen this posting on.

Rules:
- Take only terms the posting actually states. Do not add adjacent technologies
  it never mentions — a keyword invented here becomes a keyword the candidate is
  told their resume is missing.
- Prefer concrete nouns: technologies, tools, methodologies, named
  qualifications. Skip soft-skill filler such as "team player" or "fast-paced",
  which no parser screens on.
- Use the posting's own spelling.
- Return at most {limit} terms, most important first.

Job posting:
---
Title: {title}
Company: {company}

{description}
---
"""


def extract_keywords(job: Job, llm: LLMProvider, *, limit: int = 25) -> list[str]:
    """Pull the posting's screening vocabulary, canonicalised and de-duplicated.

    Canonicalisation matters because coverage is measured against this list: an
    uncanonicalised "Amazon Web Services" would be reported missing from a
    resume that says "AWS".
    """
    response = llm.complete(
        PROMPT.format(
            limit=limit,
            title=job.title,
            company=job.company,
            description=job.description,
        ),
        schema=KEYWORDS_SCHEMA,
    )
    if not isinstance(response, dict):
        raise LLMError("Keyword extraction expected a JSON object")

    keywords = canonical_skills(response.get("keywords"))
    if not keywords:
        raise LLMError("Keyword extraction returned no usable keywords")
    return keywords[:limit]
