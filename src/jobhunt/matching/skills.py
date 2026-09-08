"""Canonical skill names.

A live scan produced "Amazon Web Services" 9 times and "AWS" 8 times as separate
gap entries. Phase 3's coach ranks gaps to choose what the user studies next, so
a skill split across two spellings is under-ranked against one that is not — AWS
was really the top gap at 17 and lost to Kubernetes at 14 purely on spelling.

The map is deliberately small and hand-curated rather than fuzzy-matched: a
wrong merge ("Java" into "JavaScript") would send the user to learn the wrong
thing, which is worse than leaving two entries unmerged.
"""

# alias (casefolded) -> canonical display name
SKILL_ALIASES: dict[str, str] = {
    "amazon web services": "AWS",
    "aws": "AWS",
    "google cloud platform": "GCP",
    "gcp": "GCP",
    "microsoft azure": "Azure",
    "azure": "Azure",
    "k8s": "Kubernetes",
    "kubernetes": "Kubernetes",
    "js": "JavaScript",
    "javascript": "JavaScript",
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "psql": "PostgreSQL",
    "ci/cd": "CI/CD",
    "cicd": "CI/CD",
    "continuous integration": "CI/CD",
    "golang": "Go",
    "go": "Go",
    "reactjs": "React",
    "react.js": "React",
    "react": "React",
    "nodejs": "Node.js",
    "node.js": "Node.js",
    "node": "Node.js",
    "dotnet": ".NET",
    ".net": ".NET",
    "c#": "C#",
    "csharp": "C#",
    "rest apis": "REST",
    "rest api": "REST",
    "restful apis": "REST",
    "rest": "REST",
    "infrastructure as code": "Terraform",
    "terraform": "Terraform",
    "docker": "Docker",
    "containerisation": "Docker",
    "containerization": "Docker",
}


def canonical_skill(name: str) -> str:
    """Map a skill name to its canonical spelling.

    Unknown skills are returned stripped but otherwise untouched — the map is a
    curated allowlist, not a normaliser that guesses.
    """
    stripped = (name or "").strip()
    return SKILL_ALIASES.get(stripped.casefold(), stripped)


def canonical_skills(names) -> list[str]:
    """Canonicalise a list, dropping blanks and duplicates, order preserved."""
    if not isinstance(names, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        if not isinstance(name, str):
            continue
        canonical = canonical_skill(name)
        key = canonical.casefold()
        if canonical and key not in seen:
            seen.add(key)
            out.append(canonical)
    return out
