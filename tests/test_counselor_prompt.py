from jobhunt.counselor.actions import LookUp
from jobhunt.counselor.briefing import Briefing
from jobhunt.counselor.models import Turn
from jobhunt.counselor.prompt import PERSONA, render


def briefing() -> Briefing:
    return Briefing(True, None, 0, 0, (), (), 0, 0, (), (), "", {})


def test_prompt_has_persona_briefing_actions_transcript_and_lookups():
    turns = [
        Turn(conversation_id=1, role="counselor", text="Hi. What did you build last?"),
        Turn(conversation_id=1, role="user", text="A URL shortener."),
    ]
    text = render(
        briefing(),
        turns,
        [(LookUp("gap", {"skill": "Docker"}), "Docker: missing from 2 jobs")],
    )
    assert PERSONA in text
    assert "# Briefing" in text
    assert '"action": "ask"' in text and "propose_atom" in text and "end_sitting" in text
    assert "COUNSELOR: Hi. What did you build last?" in text
    assert "USER: A URL shortener." in text
    assert "look_up gap {'skill': 'Docker'} -> Docker: missing from 2 jobs" in text
    assert text.rstrip().endswith("Answer with exactly one JSON action.")


def test_persona_carries_the_spec_rules_verbatim():
    for rule in [
        "One question per turn. Never two.",
        "Never ask about something the atoms already state.",
        "When an answer contains a CV-worthy fact, propose the atom now.",
        "Goals, constraints and preferences are notes, not atoms.",
        "After about six exchanges, or when they seem tired, offer to stop with `end_sitting`.",
    ]:
        assert rule in PERSONA


def test_persona_denies_the_model_any_tools_of_its_own():
    # The agy runtime offers real tools (shell, files). A model that reaches for one in
    # headless mode is auto-denied and returns nothing — observed live. The prompt has
    # to say the only tool is the look_up action.
    assert "You have no tools, files or shell of your own." in PERSONA
    assert "Never run a command" in PERSONA


def test_persona_does_not_let_the_model_claim_a_proposal_is_saved():
    assert "A proposal is not recorded until they accept it." in PERSONA
