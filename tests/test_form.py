import json
from pathlib import Path

import httpx
import respx

from jobhunt.apply.ats import AtsTarget
from jobhunt.apply.form import GREENHOUSE_API, greenhouse_form

FIXTURE = Path(__file__).parent / "fixtures" / "greenhouse_questions.json"
TARGET = AtsTarget("greenhouse", "acme", "4001", "https://boards.greenhouse.io/acme/jobs/4001")
URL = GREENHOUSE_API.format(board="acme", job_id="4001")


@respx.mock
def test_greenhouse_form_maps_types_and_required():
    respx.get(URL).respond(200, json=json.loads(FIXTURE.read_text()))
    with httpx.Client() as http:
        fields = greenhouse_form(TARGET, http)
    assert fields is not None
    by_name = {f.name: f for f in fields}

    assert by_name["resume"].kind == "file"
    assert by_name["resume"].required is True
    assert by_name["resume_text"].kind == "textarea"
    assert by_name["resume_text"].required is False

    assert by_name["first_name"].kind == "text"
    assert by_name["phone"].required is False

    sponsorship = by_name["question_2"]
    assert sponsorship.kind == "select"
    assert sponsorship.options == ["Yes", "No"]
    assert sponsorship.required is True
    assert by_name["question_4"].kind == "multiselect"
    assert "question_5" not in by_name

    assert "veteran_status" in by_name
    assert by_name["veteran_status"].options == ["I don't wish to answer", "I am a veteran"]


@respx.mock
def test_greenhouse_api_error_is_none():
    respx.get(URL).respond(500)
    with httpx.Client() as http:
        assert greenhouse_form(TARGET, http) is None


@respx.mock
def test_greenhouse_non_json_is_none():
    respx.get(URL).respond(200, text="<html>not json</html>")
    with httpx.Client() as http:
        assert greenhouse_form(TARGET, http) is None
