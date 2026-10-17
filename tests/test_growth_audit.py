import shutil
import subprocess

import pytest

from jobhunt.growth.audit import (
    audit_repo,
    coursework_signals,
    personal_data_signals,
    scan_history,
)
from jobhunt.growth.gh import GhError

KEY = "AKIA" + "ABCDEFGHIJKLMNOP"


@pytest.fixture(autouse=True)
def _no_gitleaks(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _n: None)


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   check=True, capture_output=True)


def make_repo(tmp_path, files, name="src"):
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q")
    for path, text in files.items():
        (repo / path).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "c1")
    return repo


class FakeGh:
    def __init__(self, src=None):
        self.src = src

    def clone_mirror(self, full_name, dest):
        if self.src is None:
            raise GhError("nope")
        subprocess.run(["git", "clone", "-q", "--mirror", str(self.src), str(dest)], check=True)
        return dest


def test_secret_in_history_is_found_after_deletion(tmp_path):
    repo = make_repo(tmp_path, {"config.py": f"k = '{KEY}'\n"})
    git(repo, "rm", "-q", "config.py")
    git(repo, "commit", "-qm", "rm")
    flags = scan_history(repo)
    assert flags == ["secret in history: aws_access_key in config.py"]
    assert KEY not in "".join(flags)


def test_private_key_and_env_file(tmp_path):
    repo = make_repo(tmp_path, {"id": "-----BEGIN RSA PRIVATE KEY-----\nx\n", ".env": "A=1\n"})
    flags = scan_history(repo)
    assert "secret in history: private_key in id" in flags
    assert "secret in history: env_file in .env" in flags


def test_clean_repo_has_no_flags(tmp_path):
    repo = make_repo(tmp_path, {"a.py": "print(1)\n"})
    a = audit_repo(FakeGh(repo), "me/clean", readme_text="hi", description="tool",
                   paths=["a.py"], workdir=tmp_path / "w")
    assert a.flags == [] and not a.blocking
    assert not (tmp_path / "w" / "me_clean").exists()


def test_audit_blocks_on_secret(tmp_path):
    repo = make_repo(tmp_path, {"c.py": f"k='{KEY}'\n"})
    a = audit_repo(FakeGh(repo), "me/x", readme_text="", description="", paths=[], workdir=tmp_path)
    assert a.blocking


def test_coursework_signals():
    flags = coursework_signals("x", None, "My Assignment 3 for Universidade", ["lab1.py", "http2.py"])
    assert any("assignment" in f and "README" in f for f in flags)
    assert any("lab1" in f for f in flags)
    assert coursework_signals("tool", "a cli", "plain http2 client", ["main.py"]) == []


def test_personal_data_signals(tmp_path):
    rows = "".join(f"user{i}.longname.example@example-domain.com,{i}\n" for i in range(1000))
    repo = make_repo(tmp_path, {"users.csv": rows, "small.csv": "a@b.co\n"})
    flags = personal_data_signals(repo)
    assert len(flags) == 1 and flags[0].startswith("may hold personal data: users.csv (1,000 emails")


def test_clone_failure_blocks(tmp_path):
    a = audit_repo(FakeGh(), "me/x", readme_text="", description="", paths=[], workdir=tmp_path)
    assert a.blocking and a.flags == ["could not audit: nope"]
