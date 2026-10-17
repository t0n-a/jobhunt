"""Read a Greenhouse application form from its public questions API."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from jobhunt.apply.ats import AtsTarget
from jobhunt.ranking.shortlist import _clean

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}?questions=true"

# Greenhouse field types we can fill; anything else (e.g. consent widgets) is skipped.
_KIND = {
    "input_text": "text",
    "textarea": "textarea",
    "input_file": "file",
    "multi_value_single_select": "select",
    "multi_value_multi_select": "multiselect",
}


@dataclass
class FormField:
    name: str
    label: str
    kind: str
    required: bool
    options: list[str] = field(default_factory=list)


def greenhouse_form(target: AtsTarget, http: httpx.Client) -> list[FormField] | None:
    url = GREENHOUSE_API.format(board=target.board, job_id=target.job_id)
    try:
        resp = http.get(url, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    questions = list(data.get("questions") or []) + list(data.get("location_questions") or [])
    for block in data.get("compliance") or []:
        questions += block.get("questions") or []

    fields: list[FormField] = []
    for q in questions:
        label = _clean(q.get("label"))
        # A question with several inputs (resume file + resume_text) is required as a whole,
        # so only its first mapped input carries the flag.
        required = bool(q.get("required"))
        for f in q.get("fields") or []:
            kind = _KIND.get(f.get("type"))
            if kind is None:
                continue
            fields.append(
                FormField(
                    name=f["name"],
                    label=label,
                    kind=kind,
                    required=required,
                    options=[_clean(v.get("label")) for v in f.get("values") or []],
                )
            )
            required = False
    return fields
