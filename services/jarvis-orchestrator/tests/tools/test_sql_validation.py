"""Tests de la validación SQL — barrera de seguridad crítica.

Estos tests NO necesitan Postgres real: validan solo la lógica de
sqlparse + whitelist (`_validate_select_query` y `_check_tables_in_query`).
"""
from __future__ import annotations

import pytest

from jarvis_orch.tools.sql_servers import (
    ALLOWED_TABLES,
    _check_tables_in_query,
    _validate_select_query,
)
from jarvis_orch.tools.registry import ToolError, ToolNotAllowed


# ---------------------------------------------------------------------------
# _validate_select_query
# ---------------------------------------------------------------------------
class TestValidateSelectQuery:
    def test_simple_select_passes(self):
        assert _validate_select_query("SELECT * FROM unidades") == "SELECT * FROM unidades"

    def test_select_with_trailing_semicolon_is_stripped(self):
        assert _validate_select_query("SELECT 1;") == "SELECT 1"

    def test_with_cte_passes(self):
        q = "WITH x AS (SELECT 1 AS n) SELECT n FROM x"
        assert _validate_select_query(q) == q

    @pytest.mark.parametrize(
        "query",
        [
            "DROP TABLE unidades",
            "DELETE FROM unidades",
            "UPDATE unidades SET precio = 0",
            "INSERT INTO unidades VALUES (1)",
            "TRUNCATE unidades",
            "ALTER TABLE unidades ADD COLUMN x INT",
            "CREATE TABLE foo (x INT)",
            "GRANT SELECT ON unidades TO public",
            "REVOKE ALL ON unidades FROM jarvis_ro",
            "EXEC sp_who",
            "MERGE INTO unidades USING src ON 1=1",
            "VACUUM FULL unidades",
            "COPY unidades TO '/tmp/dump'",
        ],
    )
    def test_destructive_keywords_rejected(self, query):
        with pytest.raises(ToolNotAllowed):
            _validate_select_query(query)

    @pytest.mark.parametrize(
        "query",
        [
            "SELECT 1; DROP TABLE unidades",
            "SELECT 1; SELECT 2",
            "SELECT * FROM unidades; UPDATE unidades SET x=1",
        ],
    )
    def test_multiple_statements_rejected(self, query):
        with pytest.raises(ToolNotAllowed):
            _validate_select_query(query)

    def test_empty_query_rejected(self):
        with pytest.raises(ToolError):
            _validate_select_query("")

    def test_whitespace_only_rejected(self):
        with pytest.raises(ToolError):
            _validate_select_query("   \n\t  ")

    def test_unknown_starting_keyword_rejected(self):
        # `SHOW`, `PRAGMA`, `SET` no son SELECT/WITH.
        with pytest.raises(ToolNotAllowed):
            _validate_select_query("SET search_path = public")

    def test_nested_subquery_passes(self):
        q = "SELECT * FROM (SELECT id FROM unidades WHERE estado = 100) AS sub"
        assert _validate_select_query(q).startswith("SELECT")


# ---------------------------------------------------------------------------
# _check_tables_in_query
# ---------------------------------------------------------------------------
class TestCheckTablesInQuery:
    def test_allowed_table_passes(self):
        found = _check_tables_in_query("SELECT * FROM unidades", "lar")
        assert "unidades" in found

    def test_quoted_table_normalized(self):
        found = _check_tables_in_query('SELECT * FROM "unidades"', "lar")
        assert "unidades" in found

    def test_schema_prefix_stripped(self):
        found = _check_tables_in_query("SELECT * FROM public.unidades", "lar")
        assert "unidades" in found

    def test_disallowed_table_rejected(self):
        with pytest.raises(ToolNotAllowed) as exc:
            _check_tables_in_query("SELECT * FROM secret_table", "lar")
        assert "secret_table" in str(exc.value)

    def test_join_against_disallowed_rejected(self):
        query = "SELECT * FROM unidades u JOIN secret_table s ON u.id = s.id"
        with pytest.raises(ToolNotAllowed):
            _check_tables_in_query(query, "lar")

    def test_atempora_empty_whitelist_rejects_anything(self):
        assert ALLOWED_TABLES["atempora"] == set()
        with pytest.raises(ToolNotAllowed):
            _check_tables_in_query("SELECT * FROM contratos", "atempora")


# ---------------------------------------------------------------------------
# Smoke test del registry: las 3 tools deben estar registradas en SUB-SQL
# ---------------------------------------------------------------------------
def test_sql_tools_registered_in_subsql_scope():
    from jarvis_orch.tools.registry import get_tools_for

    # El import del módulo dispara los @tool — fuerzo el import explícito.
    import jarvis_orch.tools.sql_servers  # noqa: F401

    tool_names = {t["function"]["name"] for t in get_tools_for("SUB-SQL")}
    assert {"sql_describe", "sql_preview", "sql_execute"} <= tool_names
