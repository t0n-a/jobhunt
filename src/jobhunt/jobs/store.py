from datetime import UTC, datetime

from sqlmodel import Session, select

from jobhunt.jobs.dedupe import normalize, role_match, url_key
from jobhunt.jobs.models import Job, RawPosting, posting_hash


class JobStore:
    """The only writer to the job and raw_posting tables."""

    def __init__(self, session: Session):
        self.session = session
        self._by_url: dict[str, Job] | None = None
        self._by_company: dict[str, list[Job]] = {}

    def upsert_posting(self, posting: RawPosting) -> Job:
        """Attach a raw posting to an existing Job, or create one."""
        match = self._find_duplicate(posting)

        if match is None:
            match = Job(
                title=posting.title,
                company=posting.company,
                location=posting.location,
                remote=posting.remote,
                description=posting.description,
                url=posting.url,
                content_hash=posting.content_hash,
                salary_text=posting.salary_text,
            )
        else:
            match.last_seen = datetime.now(UTC)
            # Sources truncate differently; keep the fullest text we have seen,
            # because the scorer's judgement is only as good as the description.
            if len(posting.description) > len(match.description):
                match.description = posting.description
                match.content_hash = posting_hash(match.title, match.company, match.description)
            # A posting seen as remote anywhere is remote.
            match.remote = match.remote or posting.remote
            # The first salary seen wins; a later duplicate never overwrites it.
            if match.salary_text is None:
                match.salary_text = posting.salary_text

        self.session.add(match)
        try:
            self.session.commit()
            self.session.refresh(match)
            self._index(match)
            posting.job_id = match.id
            self.session.add(posting)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        return match

    def _index(self, job: Job) -> None:
        if (key := url_key(job.url)) and self._by_url is not None:
            self._by_url.setdefault(key, job)
        bucket = self._by_company.setdefault(normalize(job.company), [])
        if job not in bucket:
            bucket.append(job)

    def _find_duplicate(self, posting: RawPosting) -> Job | None:
        # Same matching as dedupe.is_duplicate, via indexes built once per store.
        if self._by_url is None:
            self._by_url = {}
            self._by_company = {}
            for job in self._all_jobs():
                self._index(job)
        if (key := url_key(posting.url)) and key in self._by_url:
            return self._by_url[key]
        return next(
            (
                j
                for j in self._by_company.get(normalize(posting.company), [])
                if role_match(posting.title, j.title)
            ),
            None,
        )

    def add_manual(self, title: str, company: str, description: str, url: str) -> Job:
        """Create a job from text the user pasted in themselves."""
        job = Job(
            title=title,
            company=company,
            description=description,
            url=url,
            content_hash=posting_hash(title, company, description),
        )
        self.session.add(job)
        try:
            self.session.commit()
            self.session.refresh(job)
        except Exception:
            self.session.rollback()
            raise
        return job

    def get(self, job_id: int) -> Job | None:
        return self.session.get(Job, job_id)

    def list_jobs(self, *, limit: int | None = None) -> list[Job]:
        statement = select(Job).order_by(Job.last_seen.desc())
        if limit is not None:
            statement = statement.limit(limit)
        return list(self.session.exec(statement).all())

    def _all_jobs(self) -> list[Job]:
        return list(self.session.exec(select(Job)).all())
