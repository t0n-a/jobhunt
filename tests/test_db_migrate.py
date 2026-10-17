import sqlite3

from jobhunt.db import create_engine_for, init_db


def test_init_db_adds_missing_column_to_existing_table(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE job (id INTEGER PRIMARY KEY, title TEXT NOT NULL, company TEXT NOT NULL,"
                " location TEXT, remote BOOLEAN NOT NULL, description TEXT NOT NULL, url TEXT NOT NULL,"
                " first_seen DATETIME NOT NULL, last_seen DATETIME NOT NULL, content_hash TEXT NOT NULL)")
    con.execute("INSERT INTO job VALUES (1,'Dev','Acme',NULL,0,'d','u','2026-01-01','2026-01-01','h')")
    con.commit()
    con.close()
    init_db(create_engine_for(path))
    con = sqlite3.connect(path)
    cols = [r[1] for r in con.execute("PRAGMA table_info(job)")]
    assert "salary_text" in cols
    assert con.execute("SELECT title FROM job WHERE id=1").fetchone() == ("Dev",)


def test_ensure_columns_is_idempotent(tmp_path):
    from jobhunt.db import ensure_columns

    engine = create_engine_for(tmp_path / "t.db")
    init_db(engine)
    assert ensure_columns(engine) == []
