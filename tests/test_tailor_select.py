from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance
from jobhunt.tailor.select import shortlist_atoms


def atom(
    title,
    *,
    skills=None,
    description="A thing that was built.",
    kind=AtomKind.PROJECT,
    confirmed=True,
    atom_id=None,
) -> EvidenceAtom:
    return EvidenceAtom(
        id=atom_id,
        kind=kind,
        title=title,
        description=description,
        skills=skills or [],
        provenance=Provenance.INTERVIEW,
        confirmed=confirmed,
    )


def test_no_atoms_yields_no_shortlist():
    assert shortlist_atoms([], ["Python"]) == []


def test_an_atom_sharing_a_skill_with_a_keyword_is_selected():
    picked = shortlist_atoms([atom("Scraper", skills=["Python"])], ["Python"])

    assert [a.title for a in picked] == ["Scraper"]


def test_skill_matches_outrank_text_matches():
    """A tagged skill is a claim the atom makes about itself; a word in prose may
    be incidental, so it counts but counts for less."""
    tagged = atom("Tagged", skills=["Kubernetes"], description="Ran some things.")
    mentioned = atom("Mentioned", skills=[], description="Used Kubernetes once.")

    picked = shortlist_atoms([mentioned, tagged], ["Kubernetes"])

    assert [a.title for a in picked] == ["Tagged", "Mentioned"]


def test_matching_more_keywords_ranks_higher():
    one = atom("One", skills=["Python"])
    two = atom("Two", skills=["Python", "Kubernetes"])

    picked = shortlist_atoms([one, two], ["Python", "Kubernetes"])

    assert [a.title for a in picked] == ["Two", "One"]


def test_keyword_spellings_are_canonicalised_before_matching():
    """The atom says AWS, the posting says Amazon Web Services. Matching on the
    raw strings would score this atom zero and drop the user's best evidence."""
    picked = shortlist_atoms([atom("Cloud", skills=["AWS"])], ["Amazon Web Services"])

    assert [a.title for a in picked] == ["Cloud"]


def test_an_unconfirmed_atom_is_never_shortlisted():
    """An unconfirmed atom is unreviewed model output. However well it matches,
    it must not be eligible to reach an employer."""
    unconfirmed = atom("Perfect match", skills=["Python", "Kubernetes"], confirmed=False)

    assert shortlist_atoms([unconfirmed], ["Python", "Kubernetes"]) == []


def test_education_is_kept_even_with_no_keyword_overlap():
    """Pure overlap ranking drops a degree from a backend posting, and a resume
    with no education section reads as an error rather than a choice."""
    degree = atom("BSc Informatics", kind=AtomKind.EDUCATION, skills=[])
    matching = atom("Scraper", skills=["Python"])

    picked = shortlist_atoms([degree, matching], ["Python"], limit=1)

    assert "BSc Informatics" in [a.title for a in picked]


def test_an_unconfirmed_degree_is_still_refused():
    degree = atom("BSc Informatics", kind=AtomKind.EDUCATION, confirmed=False)

    assert shortlist_atoms([degree], ["Python"]) == []


def test_atoms_matching_nothing_are_dropped():
    assert shortlist_atoms([atom("Unrelated", skills=["Fortran"])], ["Python"]) == []


def test_the_limit_caps_non_education_atoms():
    atoms = [atom(f"P{i}", skills=["Python"], atom_id=i) for i in range(30)]

    assert len(shortlist_atoms(atoms, ["Python"], limit=5)) == 5


def test_the_shortlist_is_deterministic_for_equal_scores():
    """Two runs of the same tailoring must produce the same candidate set, or a
    regenerate silently changes which evidence was eligible."""
    atoms = [atom(f"P{i}", skills=["Python"], atom_id=i) for i in range(10)]

    first = shortlist_atoms(atoms, ["Python"], limit=4)
    second = shortlist_atoms(list(reversed(atoms)), ["Python"], limit=4)

    assert [a.title for a in first] == [a.title for a in second]
