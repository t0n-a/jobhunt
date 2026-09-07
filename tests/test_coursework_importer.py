from jobhunt.llm import FakeProvider
from jobhunt.profile.importers.coursework import import_coursework
from jobhunt.profile.models import AtomKind, Provenance


def test_import_coursework_builds_education_atoms():
    llm = FakeProvider(
        [
            {
                "atoms": [
                    {
                        "title": "Distributed Systems",
                        "description": "Studied consensus, replication, and fault tolerance.",
                        "skills": ["Distributed Systems", "Consensus"],
                    }
                ]
            }
        ]
    )

    atoms = import_coursework("Distributed Systems, 2025", "ISEP", llm)

    assert len(atoms) == 1
    assert atoms[0].kind is AtomKind.EDUCATION
    assert atoms[0].provenance is Provenance.COURSEWORK
    assert atoms[0].source_ref == "ISEP"
    assert atoms[0].confirmed is False


def test_prompt_includes_institution_and_text():
    llm = FakeProvider([{"atoms": []}])

    import_coursework("Compilers; Operating Systems", "ISEP", llm)

    prompt = llm.prompts[0]
    assert "ISEP" in prompt
    assert "Compilers" in prompt
    assert "Do not invent" in prompt


def test_prompt_forbids_listing_courses_not_in_the_text():
    llm = FakeProvider([{"atoms": []}])

    import_coursework("Compilers; Operating Systems", "ISEP", llm)

    prompt = llm.prompts[0]
    assert "Only list courses that appear" in prompt


def test_blank_input_short_circuits_without_calling_llm():
    llm = FakeProvider([])

    assert import_coursework("   ", "ISEP", llm) == []
    assert llm.prompts == []
