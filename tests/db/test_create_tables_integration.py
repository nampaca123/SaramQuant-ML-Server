import os

import pytest

from app.db.aws_session import build_session
from app.db.create_tables import create_all_tables
from app.db.lake_schemas import TABLES

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def created_tables():
    create_all_tables()
    return build_session().client("glue")


def test_all_13_iceberg_tables_exist_in_glue(created_tables):
    glue = created_tables
    database = os.getenv("GLUE_DATABASE", "saramquant")

    for name in TABLES:
        table = glue.get_table(DatabaseName=database, Name=name)["Table"]
        params = table["Parameters"]
        assert params.get("table_type", "").upper() == "ICEBERG", name
        assert params.get("metadata_location", "").startswith("s3://"), name


def test_create_all_tables_is_idempotent(created_tables):
    create_all_tables()
