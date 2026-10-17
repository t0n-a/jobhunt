from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from jobhunt.config import Config, LLMConfig, ProfileConfig
from jobhunt.db import create_engine_for, init_db
from jobhunt.llm import FakeProvider
from jobhunt.profile.models import AtomKind, Provenance
from jobhunt.profile.store import AtomStore
from jobhunt.web.app import create_app
from jobhunt.web.deps import get_llm

TEN_MB = 10 * 1024 * 1024


@pytest.fixture
def client(tmp_path):
    cfg = Config(
        profile=ProfileConfig(name="Ada", email="ada@example.com", location="Porto"),
        llm=LLMConfig(cache_dir=tmp_path / "cache"),
        data_dir=tmp_path,
    )
    app = create_app(cfg)
    app.dependency_overrides[get_llm] = lambda: FakeProvider([])
    with TestClient(app) as c:
        c.engine = create_engine_for(tmp_path / "jobhunt.db")
        init_db(c.engine)
        yield c


def _atoms(client):
    with Session(client.engine) as session:
        return AtomStore(session).list()


def test_certificate_upload_creates_unconfirmed_atom(client, tmp_path):
    response = client.post(
        "/import/certificate",
        data={
            "name": "AWS Cloud Practitioner",
            "issuer": "Amazon Web Services",
            "issued_on": "2026-09-30",
        },
        files={"file": ("aws.pdf", b"%PDF-1.4", "application/pdf")},
    )

    assert response.status_code == 200
    assert "Certificate added. Confirm it on this page to use it as evidence." in response.text
    assert (tmp_path / "certificates" / "aws.pdf").read_bytes() == b"%PDF-1.4"

    (atom,) = _atoms(client)
    assert atom.kind == AtomKind.COURSE
    assert atom.title == "AWS Cloud Practitioner"
    assert atom.description == (
        "Certification: AWS Cloud Practitioner (Amazon Web Services), issued 2026-09-30"
    )
    assert atom.skills == []
    assert atom.provenance == Provenance.MANUAL
    assert atom.source_ref == "certificates/aws.pdf"
    assert atom.started_on == date(2026, 9, 30)
    assert atom.confirmed is False


def test_certificate_wrong_type_is_400(client, tmp_path):
    response = client.post(
        "/import/certificate",
        data={"name": "Scrum Master", "issuer": "Scrum.org"},
        files={"file": ("cert.exe", b"MZ", "application/octet-stream")},
    )

    assert response.status_code == 400
    assert _atoms(client) == []
    assert not (tmp_path / "certificates" / "cert.exe").exists()


def test_certificate_too_large_is_400(client, tmp_path):
    response = client.post(
        "/import/certificate",
        data={"name": "Scrum Master", "issuer": "Scrum.org"},
        files={"file": ("big.pdf", b"%PDF-1.4" + b"0" * TEN_MB, "application/pdf")},
    )

    assert response.status_code == 400
    assert _atoms(client) == []
    assert not (tmp_path / "certificates" / "big.pdf").exists()


def test_certificate_bad_date_is_400(client):
    response = client.post(
        "/import/certificate",
        data={"name": "Scrum Master", "issued_on": "last spring"},
        files={"file": ("cert.png", b"\x89PNG", "image/png")},
    )

    assert response.status_code == 400
    assert _atoms(client) == []


def test_certificate_same_name_never_overwrites(client, tmp_path):
    for body in (b"%PDF-first", b"%PDF-second"):
        client.post("/import/certificate", data={"name": "A"}, files={"file": ("aws.pdf", body, "application/pdf")})
    assert (tmp_path / "certificates" / "aws.pdf").read_bytes() == b"%PDF-first"
    assert (tmp_path / "certificates" / "aws-1.pdf").read_bytes() == b"%PDF-second"
    assert [a.source_ref for a in _atoms(client)] == ["certificates/aws.pdf", "certificates/aws-1.pdf"]


def test_oversized_same_name_upload_keeps_the_earlier_file(client, tmp_path):
    client.post("/import/certificate", data={"name": "A"}, files={"file": ("aws.pdf", b"%PDF-first", "application/pdf")})
    r = client.post(
        "/import/certificate", data={"name": "B"},
        files={"file": ("aws.pdf", b"%PDF" + b"0" * TEN_MB, "application/pdf")},
    )
    assert r.status_code == 400
    assert (tmp_path / "certificates" / "aws.pdf").read_bytes() == b"%PDF-first"
    assert sorted(p.name for p in (tmp_path / "certificates").iterdir()) == ["aws.pdf"]


@pytest.mark.parametrize("name", ["c.pdf", "c.png", "c.jpg"])
def test_certificate_content_must_match_type(client, tmp_path, name):
    r = client.post("/import/certificate", data={"name": "A"}, files={"file": (name, b"MZ not an image", "x/y")})
    assert r.status_code == 400 and _atoms(client) == []
    assert not (tmp_path / "certificates").exists() or list((tmp_path / "certificates").iterdir()) == []


def test_certificate_jpeg_magic_accepted(client):
    r = client.post("/import/certificate", data={"name": "A"}, files={"file": ("c.jpeg", b"\xff\xd8\xff\xe0x", "image/jpeg")})
    assert r.status_code == 200
