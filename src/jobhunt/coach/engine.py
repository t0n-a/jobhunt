from datetime import UTC, datetime

from sqlmodel import Session, select

from jobhunt.coach.models import Energy, Quest, QuestStep
from jobhunt.coach.quests import generate_quest
from jobhunt.coach.verify import verify_step
from jobhunt.growth.gaps import Gap
from jobhunt.llm import LLMProvider
from jobhunt.profile.models import AtomKind, EvidenceAtom, Provenance
from jobhunt.profile.store import AtomStore


class Coach:
    """Issues exactly one task at a time and records what it earned.

    There is no method returning a list of pending steps. That absence is the
    design: a backlog screen is what makes the next task feel like a choice, and
    choosing is the expensive part.
    """

    def __init__(
        self,
        session: Session,
        llm: LLMProvider,
        *,
        github_user: str | None = None,
        github_token: str | None = None,
    ):
        self.session = session
        self.llm = llm
        self.github_user = github_user
        self.github_token = github_token

    # --- reading state -------------------------------------------------

    def active_quest(self) -> Quest | None:
        return self.session.exec(
            select(Quest).where(Quest.status == "active").order_by(Quest.created_at.desc())
        ).first()

    def current_task(self) -> QuestStep | None:
        """The single next step, or None when there is nothing to do."""
        quest = self.active_quest()
        if quest is None:
            return None
        return self.session.exec(
            select(QuestStep)
            .where(QuestStep.quest_id == quest.id, QuestStep.status == "pending")
            .order_by(QuestStep.ordinal)
        ).first()

    def today(self, energy: Energy) -> dict:
        """One task, why it matters, and the line it earns."""
        quest = self.active_quest()
        step = self.current_task()
        if quest is None or step is None:
            return {
                "step": None,
                "quest": quest,
                "minutes": energy.minutes,
                "why": "",
                "reward": "",
            }
        return {
            "step": step,
            "quest": quest,
            "minutes": energy.minutes,
            "why": self._why(quest),
            "reward": quest.target_bullet,
        }

    def progress(self) -> dict:
        """Only ever-increasing counts. No streak, by design."""
        done = self.session.exec(select(QuestStep).where(QuestStep.status == "done")).all()
        return {
            "steps_done": len(done),
            "atoms_earned": len([s for s in done if s.atom_id is not None]),
            "gaps_closed": len(
                self.session.exec(select(Quest).where(Quest.status == "done")).all()
            ),
        }

    # --- changing state ------------------------------------------------

    def start_quest(self, gap: Gap) -> Quest:
        quest, steps = generate_quest(gap, self.llm, github_user=self.github_user)
        self.session.add(quest)
        self._commit()
        self.session.refresh(quest)
        for step in steps:
            step.quest_id = quest.id
            self.session.add(step)
        self._commit()
        return quest

    def complete_current(self, proof_note: str | None = None) -> EvidenceAtom | None:
        """Mark the current step done and write the evidence it earned."""
        step = self.current_task()
        if step is None:
            return None
        quest = self.active_quest()

        atom = EvidenceAtom(
            kind=AtomKind.PROJECT,
            title=f"{quest.title}: {step.title}",
            description=proof_note or step.instructions,
            skills=[quest.skill],
            provenance=Provenance.QUEST,
            source_ref=quest.repo_url or quest.title,
            confirmed=False,
        )
        stored = AtomStore(self.session).add(atom)

        step.status = "done"
        step.completed_at = datetime.now(UTC)
        step.proof_note = proof_note
        step.atom_id = stored.id
        self.session.add(step)
        self._commit()

        if self.current_task() is None:
            quest.status = "done"
            self.session.add(quest)
            self._commit()
        return stored

    def swap_current(self) -> QuestStep | None:
        """Skip this step and offer the next, keeping the quest alive.

        Avoidance must not escalate into quitting, so a swap costs the step and
        nothing else.
        """
        step = self.current_task()
        if step is None:
            return None
        step.status = "skipped"
        self.session.add(step)
        self._commit()
        return self.current_task()

    def verify_current(self):
        step = self.current_task()
        if step is None:
            return None
        return verify_step(step, token=self.github_token)

    # --- helpers -------------------------------------------------------

    def _why(self, quest: Quest) -> str:
        if quest.gap_job_count:
            return f"{quest.skill} is missing from {quest.gap_job_count} of your best-fit jobs."
        return f"{quest.skill} is one of your top skill gaps."

    def _commit(self) -> None:
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
