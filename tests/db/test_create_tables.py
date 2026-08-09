import app.db.create_tables as create_tables
from app.db.lake_schemas import TABLES, build_create_ddl


def test_create_all_tables_runs_ddl_for_every_table(monkeypatch):
    executed = []
    monkeypatch.setattr(create_tables, "run_query", lambda sql: executed.append(sql) or "qid")

    create_tables.create_all_tables()

    assert len(executed) == len(TABLES) == 13
    assert executed == [build_create_ddl(name) for name in TABLES]
