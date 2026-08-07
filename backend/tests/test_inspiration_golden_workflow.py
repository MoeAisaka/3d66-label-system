from __future__ import annotations

import json
from datetime import datetime, timezone

from scripts.inspiration_golden_workflow import _emit


def test_emit_serializes_datetimes_to_iso8601(tmp_path, capsys) -> None:
    output = tmp_path / "run.json"
    created_at = datetime(2026, 8, 7, 12, 34, 56, tzinfo=timezone.utc)

    _emit({"run_id": 1, "created_at": created_at}, str(output))

    expected = "2026-08-07T12:34:56+00:00"
    assert json.loads(capsys.readouterr().out)["created_at"] == expected
    assert json.loads(output.read_text(encoding="utf-8"))["created_at"] == expected
