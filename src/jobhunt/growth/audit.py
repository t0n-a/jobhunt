"""Audit a repo before it is suggested for publishing: secrets anywhere in history block it."""

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from jobhunt.growth.gh import Gh, GhError

SECRET_PATTERNS: dict[str, re.Pattern] = {
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private_key": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{36}"),
    "slack_token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "google_api_key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "generic_secret": re.compile(
        r"(?i)(api[_-]?key|secret|passw(or)?d|token)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"
    ),
}
_MAX_LOG = 50 * 1024 * 1024
_COURSEWORK = re.compile(
    r"\b(assignment|homework|coursework|lab ?\d|exercise ?\d|problem set|tp\d|trabalho prático"
    r"|projeto final|university|universidade|faculdade|course code [a-z]{2,4}\d{3})"
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d ().-]{7,}\d")
_DATA_EXT = (".csv", ".xlsx", ".json", ".sql")
_DATA_MIN_BYTES = 20 * 1024
_DATA_MIN_HITS = 20


@dataclass
class Audit:
    flags: list[str]
    blocking: bool


def _is_env(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name == ".env" or (name.startswith(".env.") and name not in {".env.example", ".env.sample"})


def scan_history(repo_dir: Path, run=subprocess.run) -> list[str]:
    proc = run(
        ["git", "-C", str(repo_dir), "log", "-p", "--all", "--no-color"],
        capture_output=True, text=True, errors="replace",
    )
    # An audit that could not read the whole history must block, never pass as clean.
    if proc.returncode != 0:
        return ["could not audit: git log failed"]
    out = proc.stdout or ""
    if len(out) > _MAX_LOG:
        return ["could not audit: history larger than 50 MB"]
    found: dict[tuple[str, str], None] = {}
    path = ""
    for line in out.splitlines():
        if line.startswith("+++ "):
            path = line[4:].removeprefix("b/")
            if _is_env(path):
                found[("env_file", path)] = None
        elif line.startswith("+"):
            for name, pat in SECRET_PATTERNS.items():
                if pat.search(line):
                    found[(name, path)] = None
    flags = [f"secret in history: {n} in {p}" for n, p in found]
    if shutil.which("gitleaks"):
        g = run(
            ["gitleaks", "detect", "--source", str(repo_dir), "--no-banner",
             "--report-format", "json", "--report-path", "-"],
            capture_output=True, text=True,
        )
        try:
            hits = json.loads(g.stdout or "[]")
        except ValueError:
            hits = []
        for h in hits:
            flag = f"secret in history: gitleaks {h.get('RuleID', '?')} in {h.get('File', '?')}"
            if flag not in flags:
                flags.append(flag)
    return flags


def coursework_signals(name, description, readme_text, paths) -> list[str]:
    sources = [("name", name), ("description", description), ("README", readme_text)]
    sources += [("file names", "\n".join(paths))]
    flags = []
    for label, text in sources:
        m = _COURSEWORK.search((text or "").casefold())
        if m:
            flags.append(f"looks like coursework: '{m.group(0)}' in {label} (check your course's rules)")
    return flags


def personal_data_signals(repo_dir: Path) -> list[str]:
    def git(*args):
        return subprocess.run(["git", "-C", str(repo_dir), *args], capture_output=True,
                              errors="replace", check=False)

    flags = []
    for line in git("ls-tree", "-r", "-l", "HEAD").stdout.splitlines():
        meta, _, path = line.partition("\t")
        size = meta.split()[-1] if meta else "-"
        if not path.lower().endswith(_DATA_EXT) or not size.isdigit() or int(size) <= _DATA_MIN_BYTES:
            continue
        text = git("show", f"HEAD:{path}").stdout
        for pat, what in ((_EMAIL, "emails"), (_PHONE, "phone numbers")):
            n = len(pat.findall(text))
            if n >= _DATA_MIN_HITS:
                flags.append(f"may hold personal data: {path} ({n:,} {what})")
    return flags


def audit_repo(
    gh: Gh, full_name, *, readme_text, description, paths, workdir: Path, run=subprocess.run
) -> Audit:
    dest = workdir / re.sub(r"[^A-Za-z0-9_.-]", "_", full_name)
    try:
        try:
            clone = gh.clone_mirror(full_name, dest)
        except GhError as e:
            return Audit([f"could not audit: {e}"], blocking=True)
        secrets = scan_history(clone, run=run)
        flags = secrets + coursework_signals(
            full_name.rsplit("/", 1)[-1], description, readme_text, paths
        ) + personal_data_signals(clone)
        return Audit(flags, blocking=bool(secrets))
    finally:
        shutil.rmtree(dest, ignore_errors=True)
