from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance
from jobhunt.tailor.verify import verify_bullet, verify_document, verify_prose


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
    assert "source atom" in result.reason.casefold()


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


def test_a_number_inside_a_longer_number_is_not_evidence():
    """ "3" appears inside "1.3 million". A substring test would let a fabricated
    headcount through on the strength of an unrelated figure."""
    source = atom(description="Handled 1.3 million requests.", metrics=None)

    result = verify_bullet(bullet("Built 3 services."), source, {1})

    assert result.passed is False


def test_a_number_carrying_a_unit_is_still_checked():
    """ "3x throughput" and "$50k saved" are the shape a fabricated resume metric
    actually takes. A right-hand boundary on extraction would make every one of
    them invisible to this check."""
    source = atom(description="Made the pipeline faster.", skills=["Python"])

    assert verify_bullet(bullet("Improved throughput by 3x."), source, {1}).passed is False
    assert verify_bullet(bullet("Saved $50k in costs."), source, {1}).passed is False


def test_a_unit_suffix_in_the_evidence_counts_as_stating_the_number():
    """The evidence side must accept the same shape, or a true bullet citing a
    figure the atom states with a unit would be dropped."""
    source = atom(description="Got 3x throughput out of the rewrite.", skills=["Python"])

    assert verify_bullet(bullet("Improved throughput by 3x."), source, {1}).passed is True


def test_a_year_in_the_evidence_does_not_authorise_a_headcount():
    """This is the ugly case: every atom carrying a four-digit year would
    otherwise authorise several single- and double-digit fabrications."""
    source = atom(description="Ran the service from 2019.", skills=["Python"])

    result = verify_bullet(bullet("Led 9 engineers."), source, {1})

    assert result.passed is False


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


def test_a_bare_node_claim_is_checked():
    """Node.js is how the canonical map spells it; Node is how a resume
    does. Requiring the suffix let the commonest spelling through unchecked."""
    source = atom(skills=["Python"], description="Wrote the backend.")

    assert verify_bullet(bullet("Built the API service in Node."), source, {1}).passed is False


def test_a_node_claim_the_evidence_supports_is_kept():
    source = atom(skills=["Node.js"], description="Wrote the backend.")

    assert verify_bullet(bullet("Built the API service in Node."), source, {1}).passed is True


def test_a_sentence_opening_with_rest_is_not_a_rest_claim():
    """The stem is taken from the canonical, not from title-casing the alias --
    otherwise Rest starting a sentence would read as a REST claim."""
    source = atom(skills=["Python"], description="Cleaned the data.")

    assert verify_bullet(bullet("Rest of the pipeline was refactored."), source, {1}).passed is True


def test_a_js_claim_is_checked_like_any_other():
    """ "JS" is not an English homograph, so it gets no exemption. Exempting it
    would mean a JavaScript claim is never checked at all."""
    source = atom(skills=["Python"], description="Wrote the backend.")

    result = verify_bullet(bullet("Wrote JS helpers."), source, {1})

    assert result.passed is False


def test_ordinary_prose_in_the_evidence_is_not_a_technology_claim():
    """The homograph exemption applies to the evidence too. An atom that used
    "rest" in its ordinary sense must not stand as evidence of a REST API."""
    source = atom(skills=["Python"], description="Cleaned the rest of the data.")

    result = verify_bullet(bullet("Designed the REST API."), source, {1})

    assert result.passed is False


def test_a_tagged_alias_supports_its_canonical_spelling():
    """The atom tags k8s, the bullet says Kubernetes. This passes only through
    the canonicalised skills set, so it is the one test pinning that branch."""
    source = atom(skills=["k8s"], description="Ran workloads on a cluster.")

    assert verify_bullet(bullet("Ran the service on Kubernetes."), source, {1}).passed is True


def test_a_user_written_bullet_skips_every_check():
    """These are the user's own words. They are responsible for them, and the
    verifier must not fight them when they correct something by hand."""
    result = verify_bullet(bullet("Cut latency by 40% using Docker.", source="user"), atom(), {1})

    assert result.passed is True


def test_a_user_bullet_passes_even_with_no_usable_atom():
    """The exemption is checked FIRST, before the atom is looked up: a bullet the
    user typed is theirs regardless of what it claims to come from."""
    junk = {"atom_id": "nonsense", "text": "Cut latency by 99% with Docker.", "source": "user"}

    assert verify_bullet(junk, None, set()).passed is True


def test_an_unhashable_atom_id_does_not_take_down_the_document():
    """Model JSON is exactly where a list-valued field turns up. One bad bullet
    must cost one bullet, not the whole resume."""
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
                        "bullets": [
                            {"atom_id": [1], "text": "Unusable.", "source": "model"},
                            bullet("Built a link shortener in Flask."),
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


def test_verify_document_keeps_ordinals_aligned_across_entries():
    """Dropping the first entry's first bullet must not pull the second entry's
    bullet forward. keep_bullets selects by ordinal precisely to prevent this."""
    document = {
        "sections": [
            {
                "name": "Projects",
                "entries": [
                    {
                        "atom_id": 1,
                        "title": "One",
                        "org": "",
                        "dates": "",
                        "bullets": [
                            bullet("Cut latency by 40%."),
                            bullet("Built a link shortener in Flask."),
                        ],
                    },
                    {
                        "atom_id": 1,
                        "title": "Two",
                        "org": "",
                        "dates": "",
                        "bullets": [bullet("Cached responses in Redis.")],
                    },
                ],
            }
        ]
    }

    kept, dropped = verify_document(document, {1: atom()}, {1})

    entries = kept["sections"][0]["entries"]
    assert [b["text"] for b in entries[0]["bullets"]] == ["Built a link shortener in Flask."]
    assert [b["text"] for b in entries[1]["bullets"]] == ["Cached responses in Redis."]
    assert len(dropped) == 1


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


def _atom(**kwargs) -> EvidenceAtom:
    fields = {
        "id": 1,
        "kind": AtomKind.PROJECT,
        "title": "URL shortener",
        "description": "Built a link shortener in Flask, cached in Redis.",
        "skills": ["Python", "Flask", "Redis"],
        "provenance": Provenance.INTERVIEW,
        "confirmed": True,
    }
    fields.update(kwargs)
    return EvidenceAtom(**fields)


def test_verify_prose_passes_prose_the_union_supports():
    atoms = [
        _atom(id=1),
        _atom(id=2, title="Scraper", description="Scraped 12 sites.", skills=["Python"]),
    ]
    result = verify_prose("Python engineer who built a Flask service and scraped 12 sites.", atoms)
    assert result.passed


def test_verify_prose_rejects_a_number_no_atom_states():
    result = verify_prose("Cut latency for 40000 daily users.", [_atom()])
    assert not result.passed
    assert "40000" in result.reason


def test_verify_prose_rejects_a_technology_no_atom_names():
    result = verify_prose("Built a link shortener on Kubernetes.", [_atom()])
    assert not result.passed
    assert "Kubernetes" in result.reason


def test_verify_prose_accepts_evidence_from_a_DIFFERENT_atom():
    # This is the whole point of the union: prose legitimately draws on several
    # atoms at once, and per-atom checking would reject every such sentence.
    atoms = [
        _atom(id=1, skills=["Python"], description="Built a shortener.", title="Shortener"),
        _atom(id=2, skills=["Kubernetes"], description="Deployed to k3s.", title="Cluster"),
    ]
    assert verify_prose("Python developer who deploys on Kubernetes.", atoms).passed


def test_verify_prose_refuses_when_there_is_no_evidence_at_all():
    # Vacuous truth is the dangerous default here: with an empty haystack every
    # check trivially finds nothing to complain about.
    result = verify_prose("Led a team of 40 engineers.", [])
    assert not result.passed
    assert "no evidence" in result.reason.lower()
