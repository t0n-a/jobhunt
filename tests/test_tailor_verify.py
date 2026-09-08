from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance
from jobhunt.tailor.verify import verify_bullet, verify_document


def atom(
    atom_id=1,
    *,
    title="URL shortener",
    description="Built a link shortener in Flask, cached in Redis.",
    skills=None,
    metrics=None,
    confirmed=True,
) -> EvidenceAtom:
    return EvidenceAtom(
        id=atom_id,
        kind=AtomKind.PROJECT,
        title=title,
        description=description,
        skills=skills if skills is not None else ["Python", "Flask", "Redis"],
        metrics=metrics,
        provenance=Provenance.INTERVIEW,
        confirmed=confirmed,
    )


def bullet(text, *, atom_id=1, source="model") -> dict:
    return {"atom_id": atom_id, "text": text, "source": source}


def test_a_faithful_bullet_passes():
    result = verify_bullet(bullet("Built a link shortener in Flask."), atom(), {1})

    assert result.passed is True


def test_a_bullet_with_no_atom_id_is_rejected():
    """An unattributable claim cannot be checked against anything."""
    result = verify_bullet({"text": "Led the company.", "source": "model"}, atom(), {1})

    assert result.passed is False


def test_a_bullet_whose_atom_is_missing_is_rejected():
    result = verify_bullet(bullet("Anything.", atom_id=99), None, {1, 99})

    assert result.passed is False


def test_a_bullet_from_outside_the_shortlist_is_rejected():
    """The model reached for an atom it was never offered, which means it is
    inventing associations rather than selecting from the evidence."""
    result = verify_bullet(bullet("Anything.", atom_id=5), atom(5), {1, 2})

    assert result.passed is False
    assert "shortlist" in result.reason.casefold()


def test_a_bullet_from_an_unconfirmed_atom_is_rejected():
    """An unconfirmed atom is unreviewed model output. Selection already refuses
    these; this is the second lock on the same door."""
    result = verify_bullet(bullet("Built it in Flask."), atom(confirmed=False), {1})

    assert result.passed is False
    assert "confirmed" in result.reason.casefold()


def test_an_invented_metric_is_rejected():
    """The single most common resume fabrication: a percentage that makes the
    line sound better and that the atom never claimed."""
    result = verify_bullet(bullet("Cut latency by 40%."), atom(), {1})

    assert result.passed is False
    assert "40" in result.reason


def test_a_metric_the_atom_states_is_kept():
    source = atom(description="Served 12000 requests a day.", metrics="12000 requests/day")

    result = verify_bullet(bullet("Served 12000 requests a day."), source, {1})

    assert result.passed is True


def test_a_metric_stated_only_in_the_metrics_field_is_kept():
    source = atom(description="Ran the checkout service.", metrics="p95 down 40%")

    assert verify_bullet(bullet("Cut p95 by 40%."), source, {1}).passed is True


def test_an_invented_technology_is_rejected():
    """Models add adjacent tools: Docker because Kubernetes was mentioned."""
    source = atom(skills=["Kubernetes"], description="Ran workloads on a cluster.")

    result = verify_bullet(bullet("Containerised the service with Docker."), source, {1})

    assert result.passed is False
    assert "docker" in result.reason.casefold()


def test_a_technology_the_atom_tags_is_kept():
    assert verify_bullet(bullet("Cached responses in Redis."), atom(), {1}).passed is True


def test_a_technology_named_only_in_the_atom_text_is_kept():
    """CV-imported atoms often name a tool in prose that the importer never
    tagged. Rejecting on the tag alone would drop true bullets."""
    source = atom(skills=[], description="Deployed the service with Terraform.")

    assert verify_bullet(bullet("Deployed it with Terraform."), source, {1}).passed is True


def test_a_technology_alias_matches_its_canonical_spelling():
    """The atom says AWS, the bullet says Amazon Web Services. Same claim."""
    source = atom(skills=["AWS"], description="Hosted the service.")

    assert verify_bullet(bullet("Hosted it on Amazon Web Services."), source, {1}).passed is True


def test_an_ordinary_english_word_is_not_read_as_a_technology():
    """ "rest" is an alias for REST. Matching it case-insensitively would drop a
    true bullet for using an ordinary English word."""
    source = atom(skills=["Python"], description="Cleaned up the pipeline.")

    result = verify_bullet(bullet("Refactored the rest of the pipeline."), source, {1})

    assert result.passed is True


def test_a_capitalised_rest_claim_is_still_checked():
    source = atom(skills=["Python"], description="Cleaned up the pipeline.")

    result = verify_bullet(bullet("Designed the REST API."), source, {1})

    assert result.passed is False


def test_a_user_written_bullet_skips_every_check():
    """These are the user's own words. They are responsible for them, and the
    verifier must not fight them when they correct something by hand."""
    result = verify_bullet(bullet("Cut latency by 40% using Docker.", source="user"), atom(), {1})

    assert result.passed is True


def test_verify_document_keeps_survivors_and_reports_the_rest():
    document = {
        "sections": [
            {
                "name": "Projects",
                "entries": [
                    {
                        "atom_id": 1,
                        "title": "URL shortener",
                        "org": "Personal",
                        "dates": "2026",
                        "bullets": [
                            bullet("Built a link shortener in Flask."),
                            bullet("Cut latency by 40%."),
                        ],
                    }
                ],
            }
        ]
    }

    kept, dropped = verify_document(document, {1: atom()}, {1})

    texts = [b["text"] for entry in kept["sections"][0]["entries"] for b in entry["bullets"]]
    assert texts == ["Built a link shortener in Flask."]
    assert len(dropped) == 1
    assert dropped[0]["text"] == "Cut latency by 40%."
    assert dropped[0]["reason"]


def test_verify_document_returns_a_new_object():
    document = {
        "sections": [
            {
                "name": "Projects",
                "entries": [
                    {
                        "atom_id": 1,
                        "title": "t",
                        "org": "",
                        "dates": "",
                        "bullets": [bullet("Cut latency by 40%.")],
                    }
                ],
            }
        ]
    }

    kept, _ = verify_document(document, {1: atom()}, {1})

    assert kept is not document
    assert len(document["sections"][0]["entries"][0]["bullets"]) == 1
