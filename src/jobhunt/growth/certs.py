import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Cert:
    name: str
    pattern: re.Pattern
    cost_eur: int
    free_prep: str


def _c(name, pattern, cost, prep="the vendor's free study guide"):
    return Cert(name, re.compile(pattern, re.IGNORECASE), cost, prep)


# Costs are approximate list prices in EUR; re-check them yearly.
CERT_TABLE: dict[str, Cert] = {
    "aws-cp": _c("AWS Cloud Practitioner", r"aws\s+(certified\s+)?cloud\s+practitioner", 95, "AWS Skill Builder free tier"),
    "aws-saa": _c("AWS Solutions Architect Associate", r"aws\s+(certified\s+)?solutions\s+architect", 140, "AWS Skill Builder free tier"),
    "az-900": _c("Azure AZ-900", r"\baz[- ]?900\b", 90, "Microsoft Learn; free vouchers via Microsoft Virtual Training Days"),
    "az-104": _c("Azure AZ-104", r"\baz[- ]?104\b", 150, "Microsoft Learn"),
    "gcp-ace": _c("Google Associate Cloud Engineer", r"associate\s+cloud\s+engineer", 115, "Google Cloud Skills Boost"),
    "cka": _c("CKA", r"\bCKA\b|certified\s+kubernetes\s+administrator", 420, "the Kubernetes docs and killer.sh-style free labs"),
    "ckad": _c("CKAD", r"\bCKAD\b|certified\s+kubernetes\s+application\s+developer", 420, "the Kubernetes docs"),
    "terraform": _c("Terraform Associate", r"terraform\s+associate", 65, "HashiCorp Learn tutorials"),
    "psm1": _c("PSM I", r"\bPSM\s*(I\b|1\b)|professional\s+scrum\s+master", 185, "the free Scrum Guide and Scrum.org open assessments"),
    "istqb": _c("ISTQB Foundation", r"\bISTQB\b", 200, "the free ISTQB syllabus"),
    "security-plus": _c("CompTIA Security+", r"security\+|comptia\s+security", 370, "Professor Messer free videos"),
    "oracle-java": _c("Oracle Java SE Developer", r"oracle\s+certified[^.\n]{0,40}java|\bOCP\b[^.\n]{0,20}java|java[^.\n]{0,20}\bOCP\b|java\s+se\s+\d+\s+certified", 230, "the Oracle Java tutorials"),
    "gh-foundations": _c("GitHub Foundations", r"github\s+foundations", 90, "GitHub Skills; free for students"),
    "fcc": _c("freeCodeCamp certifications", r"freecodecamp", 0, "freeCodeCamp itself"),
}
