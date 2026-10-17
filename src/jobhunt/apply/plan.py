"""Decide every form field's answer from known sources; anything else stays unknown."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from sqlmodel import Session

from jobhunt.answers import StandardAnswers
from jobhunt.apply.form import FormField
from jobhunt.config import ProfileConfig
from jobhunt.llm import LLMError, LLMProvider
from jobhunt.profile.models import EvidenceAtom
from jobhunt.tailor.models import Application
from jobhunt.tailor.verify import verify_prose

# Why: a select answer must be the candidate's own EEO choice or a refusal, never a guess.
_DECLINE = ("decline", "don't wish", "do not wish", "prefer not")
_LLM_SCHEMA = {
    "answers": [{"name": "string", "answer": "string", "atom_ids": ["integer"]}],
}
_UNKNOWN = object()


@dataclass
class Planned:
    name: str
    value: str | list[str] | bool
    source: str  # profile | answers | document | evidence


@dataclass
class FormPlan:
    filled: list[Planned] = field(default_factory=list)
    unknown: list[FormField] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not any(f.required for f in self.unknown)

    def to_json(self) -> dict:
        return {
            "filled": [asdict(p) for p in self.filled],
            "unknown": [asdict(f) for f in self.unknown],
            "dropped": [dict(d) for d in self.dropped],
        }

    @classmethod
    def from_json(cls, d: dict) -> FormPlan:
        return cls(
            filled=[Planned(**p) for p in d.get("filled", [])],
            unknown=[FormField(**f) for f in d.get("unknown", [])],
            dropped=[dict(x) for x in d.get("dropped", [])],
        )


def _profile(f: FormField, label: str, p: ProfileConfig):
    n = f.name
    if n == "first_name" or "first name" in label:
        return p.name.rpartition(" ")[0] or p.name
    if n == "last_name" or "last name" in label:
        return p.name.rpartition(" ")[2] if " " in p.name else None
    if n == "name" or label in ("name", "full name") or "full name" in label:
        return p.name
    if n == "email" or "email" in label:
        return p.email
    if n == "phone" or "phone" in label:
        return p.phone
    if "linkedin" in label:
        return p.linkedin
    if "github" in label:
        return f"https://github.com/{p.github_username}" if p.github_username else None
    if "website" in label or "portfolio" in label:
        return p.website
    if n == "location" or "city" in label or re.search(r"\blocation\b", label):
        return p.location
    return _UNKNOWN


def _documents(f: FormField, label: str, cover_letter: str, pdf_path: Path | None):
    if f.kind == "file":
        if any(k in f.name.casefold() or k in label for k in ("resume", "cv")):
            return str(pdf_path) if pdf_path else None
        return None
    if f.name == "cover_letter_text" or (f.kind == "textarea" and "cover letter" in label):
        return cover_letter or None
    return _UNKNOWN


def _pick(options: list[str], pred) -> str | None:
    return next((o for o in options if pred(o.casefold())), None)


def _yes_no(f: FormField, yes: bool):
    if f.kind == "checkbox":
        return yes
    word = "yes" if yes else "no"
    if f.kind == "select":
        return _pick(f.options, lambda o: o.startswith(word))
    return word.capitalize()


def _text(f: FormField, value: str):
    if f.kind != "select":
        return value
    return _pick(f.options, lambda o: o == value.casefold())


def _eeo(f: FormField, value: str):
    if f.kind != "select":
        return value
    return _pick(f.options, lambda o: o == value.casefold()) or _pick(
        f.options, lambda o: any(k in o for k in _DECLINE)
    )


def _answers(f: FormField, label: str, a: StandardAnswers | None, eu_job: bool):
    keys = (
        "sponsor", "authori", "legally", "notice", "salary", "compensation", "relocat",
        "remote", "gender", "race", "ethnic", "veteran", "disab", "consent", "privacy", "gdpr",
    )  # fmt: skip
    if not any(k in label for k in keys):
        return _UNKNOWN
    if a is None:
        return None
    if "sponsor" in label:
        return _yes_no(f, not eu_job and a.needs_sponsorship_outside_eu)
    if ("authori" in label or "legally" in label) and "work" in label:
        return _yes_no(f, eu_job or not a.needs_sponsorship_outside_eu)
    if "notice" in label:
        return _text(f, a.notice_period)
    if "salary" in label or "compensation" in label:
        return None if f.kind == "select" else f"{a.salary_min_eur}-{a.salary_max_eur} EUR"
    if "relocat" in label:
        return _yes_no(f, a.relocation)
    if "remote" in label:
        return _yes_no(f, a.remote)
    for key, val in (
        ("gender", a.gender), ("race", a.ethnicity), ("ethnic", a.ethnicity),
        ("veteran", a.veteran), ("disab", a.disability),
    ):  # fmt: skip
        if key in label:
            return _eeo(f, val)
    if f.kind == "checkbox":
        return a.data_processing_consent
    return _UNKNOWN


def _ask_llm(
    fields: list[FormField], atoms: list[EvidenceAtom], llm: LLMProvider, plan: FormPlan
) -> None:
    by_id = {a.id: a for a in atoms}
    qs = "\n".join(f"- name={f.name!r} label={f.label!r} options={f.options}" for f in fields)
    ev = "\n".join(f"- id={a.id} {a.title}: {a.description} (skills: {a.skills})" for a in atoms)
    prompt = (
        "Answer these job application questions using ONLY the evidence below. Cite the atom ids "
        "each answer rests on; skip a question the evidence does not support. For a select, the "
        f"answer must be one of its options exactly.\n\nQuestions:\n{qs}\n\nEvidence:\n{ev}"
    )
    try:
        raw = llm.complete(prompt, _LLM_SCHEMA).get("answers", [])
    except LLMError:
        raw = []
    asked = {f.name: f for f in fields}
    done: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        name, answer = item.get("name"), str(item.get("answer", ""))
        ids = item.get("atom_ids") or []
        f = asked.get(name)
        if f is None or name in done:
            continue
        reason = None
        if not ids or not all(i in by_id for i in ids):
            reason = "answer cites no known evidence atom"
        elif f.kind == "select":
            if answer not in f.options:
                reason = "answer is not one of the options"
        else:
            res = verify_prose(answer, [by_id[i] for i in ids])
            if not res.passed:
                reason = res.reason or "not supported by the cited evidence"
        if reason:
            plan.dropped.append({"name": name, "answer": answer, "reason": reason})
        else:
            plan.filled.append(Planned(name, answer, "evidence"))
            done.add(name)
    plan.unknown.extend(f for f in fields if f.name not in done)


def plan_form(
    fields: list[FormField],
    *,
    profile: ProfileConfig,
    answers: StandardAnswers | None,
    cover_letter: str,
    pdf_path: Path | None,
    eu_job: bool,
    atoms: list[EvidenceAtom],
    llm: LLMProvider | None,
) -> FormPlan:
    plan = FormPlan()
    pending: list[FormField] = []
    for f in fields:
        label = f.label.casefold()
        for source, value in (
            ("profile", _profile(f, label, profile)),
            ("document", _documents(f, label, cover_letter, pdf_path)),
            ("answers", _answers(f, label, answers, eu_job)),
        ):
            if value is _UNKNOWN:
                continue
            if value is None or value == "":
                if f.required:
                    plan.unknown.append(f)
            else:
                plan.filled.append(Planned(f.name, value, source))
            break
        else:
            if f.required and f.kind in ("text", "textarea", "select"):
                pending.append(f)
            elif f.required:
                plan.unknown.append(f)
    if pending:
        if llm and atoms:
            _ask_llm(pending, atoms, llm, plan)
        else:
            plan.unknown.extend(pending)
    return plan


def save_plan(session: Session, application: Application, plan: FormPlan) -> None:
    application.form_plan = plan.to_json()
    session.add(application)
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
