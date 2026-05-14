"""Tests del permission enforcer.

Testean la lógica pura `pick_autonomy` — no necesita DB. La integración
con SQLAlchemy se prueba con la suite de integración (Postgres real,
fuera de este file).
"""
from __future__ import annotations

from jarvis_orch.permissions import AutonomyLevel, pick_autonomy


class TestPickAutonomy:
    def test_no_permissions_defaults_to_read_only(self):
        assert pick_autonomy([], "email.send") == AutonomyLevel.READ_ONLY

    def test_exact_pattern_matches_with_glob(self):
        perms = [("query.*", 3)]
        assert pick_autonomy(perms, "query.unidades") == AutonomyLevel.AUTO

    def test_returns_max_when_multiple_match(self):
        # `email.*` matchea con 2, `*` matchea con 1 → debe ganar 2.
        perms = [("*", 1), ("email.*", 2)]
        assert pick_autonomy(perms, "email.draft") == AutonomyLevel.APPROVE_TO_ACT

    def test_max_wins_in_either_insertion_order(self):
        for perms in (
            [("query.*", 0), ("query.unidades", 3)],
            [("query.unidades", 3), ("query.*", 0)],
        ):
            assert pick_autonomy(perms, "query.unidades") == AutonomyLevel.AUTO

    def test_pattern_does_not_match(self):
        perms = [("report.*", 2)]
        assert pick_autonomy(perms, "email.send") == AutonomyLevel.READ_ONLY

    def test_wildcard_matches_anything(self):
        perms = [("*", 4)]
        for action in ("query.x", "email.y", "report.z", "anything"):
            assert pick_autonomy(perms, action) == AutonomyLevel.FULL_AUTO

    def test_case_sensitive(self):
        # fnmatchcase es case-sensitive. Esto previene typos accidentales.
        perms = [("EMAIL.*", 3)]
        assert pick_autonomy(perms, "email.send") == AutonomyLevel.READ_ONLY

    def test_question_mark_wildcard(self):
        # fnmatch soporta `?` (un char) — verificamos comportamiento explícito.
        perms = [("sql.?", 2)]
        assert pick_autonomy(perms, "sql.x") == AutonomyLevel.APPROVE_TO_ACT
        assert pick_autonomy(perms, "sql.xy") == AutonomyLevel.READ_ONLY

    def test_dot_is_literal_not_regex(self):
        # `.` debe ser literal en fnmatch — NO debe matchear cualquier char.
        perms = [("query.x", 3)]
        assert pick_autonomy(perms, "query.x") == AutonomyLevel.AUTO
        assert pick_autonomy(perms, "queryxx") == AutonomyLevel.READ_ONLY
