from contextlib import contextmanager
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine


def create_engine_for(path: Path):
    """Create a SQLite engine, making the parent directory if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}", echo=False)


def init_db(engine) -> None:
    """Create all tables. Importing models registers them on SQLModel.metadata."""
    import jobhunt.profile.models  # noqa: F401  (registers tables)

    SQLModel.metadata.create_all(engine)


@contextmanager
def session_scope(engine):
    with Session(engine) as session:
        yield session
