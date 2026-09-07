from collections import Counter
from typing import Sequence

from sqlmodel import Session, select

from jobhunt.profile.models import AtomKind, EvidenceAtom


class AtomStore:
    """The only writer to the evidence_atom table."""

    def __init__(self, session: Session):
        self.session = session

    def add(self, atom: EvidenceAtom) -> EvidenceAtom:
        self.session.add(atom)
        self.session.commit()
        self.session.refresh(atom)
        return atom

    def add_many(self, atoms: Sequence[EvidenceAtom]) -> list[EvidenceAtom]:
        for atom in atoms:
            self.session.add(atom)
        self.session.commit()
        for atom in atoms:
            self.session.refresh(atom)
        return list(atoms)

    def get(self, atom_id: int) -> EvidenceAtom | None:
        return self.session.get(EvidenceAtom, atom_id)

    def list(
        self,
        *,
        kind: AtomKind | None = None,
        confirmed: bool | None = None,
        skill: str | None = None,
    ) -> list[EvidenceAtom]:
        statement = select(EvidenceAtom)
        if kind is not None:
            statement = statement.where(EvidenceAtom.kind == kind)
        if confirmed is not None:
            statement = statement.where(EvidenceAtom.confirmed == confirmed)

        atoms = list(self.session.exec(statement).all())
        if skill is not None:
            needle = skill.casefold()
            atoms = [a for a in atoms if any(s.casefold() == needle for s in a.skills)]
        return atoms

    def update(self, atom_id: int, **fields) -> EvidenceAtom:
        atom = self.get(atom_id)
        if atom is None:
            raise KeyError(f"No atom with id {atom_id}")
        for key, value in fields.items():
            setattr(atom, key, value)
        self.session.add(atom)
        self.session.commit()
        self.session.refresh(atom)
        return atom

    def delete(self, atom_id: int) -> None:
        atom = self.get(atom_id)
        if atom is None:
            return
        self.session.delete(atom)
        self.session.commit()

    def skill_counts(self) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for atom in self.list():
            counter.update(atom.skills)
        return dict(counter)
