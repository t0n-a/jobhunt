from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import inspect
from sqlmodel import Session, SQLModel, create_engine


def create_engine_for(path: Path):
    """Create a SQLite engine, making the parent directory if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(
        f"sqlite:///{path}",
        echo=False,
        # The tick and the daily timer share this file: wait for the lock, don't fail.
        connect_args={"timeout": 30},
    )


def init_db(engine) -> None:
    """Create all tables. Importing models registers them on SQLModel.metadata."""
    import jobhunt.coach.models
    import jobhunt.counselor.models
    import jobhunt.growth.models
    import jobhunt.jobs.models
    import jobhunt.linkedin.models
    import jobhunt.outreach.models
    import jobhunt.profile.models
    import jobhunt.state
    import jobhunt.tailor.models  # noqa: F401  (registers tables)

    SQLModel.metadata.create_all(engine)
    ensure_columns(engine)
    # One-off rename of the pre-tracker status; a no-op once no row says 'applied'.
    with engine.begin() as conn:
        conn.exec_driver_sql("UPDATE application SET status='sent' WHERE status='applied'")


def ensure_columns(engine) -> list[str]:
    """Add model columns missing from existing tables; returns the "table.column" names added.

    shortcut: additive-only (no type change, no drop), upgrade to Alembic when a column must change.
    """
    added: list[str] = []
    with engine.begin() as conn:
        inspector = inspect(conn)
        for table in SQLModel.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            present = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                ddl = f"{column.name} {column.type.compile(dialect=engine.dialect)}"
                if not column.nullable:
                    if column.default is None or not column.default.is_scalar:
                        raise RuntimeError(
                            f"cannot add {table.name}.{column.name}: "
                            "NOT NULL needs a scalar default"
                        )
                    literal = column.type.literal_processor(engine.dialect)(column.default.arg)
                    ddl += f" NOT NULL DEFAULT {literal}"
                conn.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {ddl}")
                added.append(f"{table.name}.{column.name}")
    return added


@contextmanager
def session_scope(engine):
    with Session(engine) as session:
        yield session
