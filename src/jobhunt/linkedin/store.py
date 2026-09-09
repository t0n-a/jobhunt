"""Versions, fields, and the one place the publish decision is made."""

from datetime import UTC, datetime

from sqlmodel import Session, select

from jobhunt.linkedin.fields import FIELDS, field_spec, over_limit
from jobhunt.linkedin.models import LinkedinField, LinkedinProfile


class LinkedinStore:
    """Reads and writes the LinkedIn buildout tables."""

    def __init__(self, session: Session):
        self.session = session

    def add_version(self, version: LinkedinProfile, fields: list[LinkedinField]) -> LinkedinProfile:
        """Persist one version and its fields in a single commit.

        One commit, so a failure part-way through leaves no version at all: a
        half-written version looks generated and would be reviewed as if it
        were complete.
        """
        try:
            self.session.add(version)
            self.session.flush()  # assigns version.id without ending the transaction
            for field in fields:
                field.profile_id = version.id
                # A version is born unapproved. This is the write boundary, so
                # it is where that is guaranteed rather than assumed: approval
                # can only ever come from approve_field, never from a row
                # handed in already carrying one.
                field.approved_at = None
                field.approved_by = None
                self.session.add(field)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        self.session.refresh(version)
        return version

    def latest(self) -> LinkedinProfile | None:
        return self.session.exec(
            select(LinkedinProfile).order_by(LinkedinProfile.id.desc())
        ).first()

    def get(self, profile_id: int) -> LinkedinProfile | None:
        return self.session.get(LinkedinProfile, profile_id)

    def previous(self, version: LinkedinProfile) -> LinkedinProfile | None:
        """The version built immediately before this one, for the diff."""
        return self.session.exec(
            select(LinkedinProfile)
            .where(LinkedinProfile.id < version.id)
            .order_by(LinkedinProfile.id.desc())
        ).first()

    def fields_for(self, version: LinkedinProfile) -> list[LinkedinField]:
        """This version's fields in registry order, then position, then id.

        Ordered by the registry rather than by insertion, so the page reads
        headline-first however the rows happened to be written.
        """
        rows = self.session.exec(
            select(LinkedinField).where(LinkedinField.profile_id == version.id)
        ).all()
        order = {spec.name: index for index, spec in enumerate(FIELDS)}
        return sorted(rows, key=lambda f: (order.get(f.name, 99), f.position, f.id or 0))

    def get_field(self, field_id: int) -> LinkedinField | None:
        return self.session.get(LinkedinField, field_id)

    def edit_field(self, field_id: int, text: str) -> LinkedinField:
        """Replace one field's text with the user's own.

        Editing WITHDRAWS any approval. Approve-then-edit would otherwise leave
        approval standing on text nobody has read, which is precisely what the
        gate exists to prevent. The user re-approves; that is one click.
        """
        field = self._field(field_id)
        field.text = text
        field.source = "user"
        field.over_limit = over_limit(field_spec(field.name), text)
        field.approved_at = None
        field.approved_by = None
        self.session.add(field)
        self._commit()
        self.session.refresh(field)
        return field

    def approve_field(self, field_id: int, *, by: str = "user") -> LinkedinField:
        """Record that this unit was approved, and by whom.

        `by` is "user" today. It exists because the gate in `is_publishable` is
        a maturity gate: if some field kinds are later allowed to publish
        unattended, this column is what lets the user look back and see which
        content went out unchecked.
        """
        field = self._field(field_id)
        field.approved_at = datetime.now(UTC)
        field.approved_by = by
        self.session.add(field)
        self._commit()
        self.session.refresh(field)
        return field

    def is_publishable(self, field: LinkedinField) -> bool:
        """THE publish precondition. Every publisher asks this and nothing else.

        Right now it means "a human approved this exact text". That is a
        judgement about how well-tested the generator is, not a permanent rule:
        when it is time to let some fields publish unattended, this function is
        the only thing that changes, and the gate suite is already pointed here.
        """
        return field.approved_at is not None

    def _field(self, field_id: int) -> LinkedinField:
        field = self.get_field(field_id)
        if field is None:
            raise KeyError(f"No LinkedIn field with id {field_id}")
        return field

    def _commit(self) -> None:
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
