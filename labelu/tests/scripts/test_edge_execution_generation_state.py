"""边端执行世代的跨进程持久化与防重放门禁。"""

import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


def _load_board_agent():
    path = Path(__file__).parents[2] / ".." / "scripts" / "ops" / "labelu_board_agent.py"
    path = path.resolve()
    spec = importlib.util.spec_from_file_location("labelu_board_agent_generation_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _cfg(generation, sequence, export_id=7):
    return {
        "export_id": export_id,
        "execution_generation": generation,
        "execution_generation_sequence": sequence,
        "execution_generation_scope": "export:{}".format(export_id),
    }


def test_generation_state_persists_and_same_generation_is_idempotent(monkeypatch, tmp_path):
    agent = _load_board_agent()
    state = tmp_path / "device" / "execution_generations.json"
    monkeypatch.setenv("LABELU_EDGE_EXECUTION_STATE_FILE", str(state))

    first = agent.consume_edge_execution_generation(_cfg("gen-a", 1))
    second = agent.consume_edge_execution_generation(_cfg("gen-a", 1))

    assert first["status"] == "accepted_new"
    assert second["status"] == "idempotent"
    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["entries"]["export:7"]["generation"] == "gen-a"
    assert not list(state.parent.glob("*.tmp"))


def test_generation_state_rejects_old_generation_and_accepts_newer(monkeypatch, tmp_path):
    agent = _load_board_agent()
    state = tmp_path / "execution_generations.json"
    monkeypatch.setenv("LABELU_EDGE_EXECUTION_STATE_FILE", str(state))

    agent.consume_edge_execution_generation(_cfg("gen-a", 1))
    agent.consume_edge_execution_generation(_cfg("gen-b", 2))

    with pytest.raises(RuntimeError, match="已过期"):
        agent.consume_edge_execution_generation(_cfg("gen-a", 1))

    payload = json.loads(state.read_text(encoding="utf-8"))
    assert payload["entries"]["export:7"]["generation"] == "gen-b"


def test_generation_state_fails_closed_for_incomparable_generations(monkeypatch, tmp_path):
    agent = _load_board_agent()
    state = tmp_path / "execution_generations.json"
    monkeypatch.setenv("LABELU_EDGE_EXECUTION_STATE_FILE", str(state))

    agent.consume_edge_execution_generation(_cfg("gen-a", 1))
    with pytest.raises(RuntimeError, match="无法比较"):
        agent.consume_edge_execution_generation(
            {
                "export_id": 7,
                "execution_generation": "gen-b",
                "execution_generation_scope": "export:7",
            }
        )


def test_generation_state_rejects_corrupt_state_without_overwriting(monkeypatch, tmp_path):
    agent = _load_board_agent()
    state = tmp_path / "execution_generations.json"
    state.write_text('{"schema_version": 1, "entries": [}', encoding="utf-8")
    monkeypatch.setenv("LABELU_EDGE_EXECUTION_STATE_FILE", str(state))

    with pytest.raises(RuntimeError, match="状态文件损坏"):
        agent.consume_edge_execution_generation(_cfg("gen-a", 1))
    assert state.read_text(encoding="utf-8") == '{"schema_version": 1, "entries": [}'


def test_generation_state_rejects_invalid_timestamp_order(monkeypatch, tmp_path):
    agent = _load_board_agent()
    state = tmp_path / "execution_generations.json"
    monkeypatch.setenv("LABELU_EDGE_EXECUTION_STATE_FILE", str(state))

    with pytest.raises(RuntimeError, match="issued_at 无效"):
        agent.consume_edge_execution_generation(
            {
                "export_id": 7,
                "execution_generation": "gen-a",
                "execution_generation_issued_at": "not-a-time",
            }
        )


def test_timestamp_generation_order_is_used_when_sequence_is_absent(monkeypatch, tmp_path):
    agent = _load_board_agent()
    state = tmp_path / "execution_generations.json"
    monkeypatch.setenv("LABELU_EDGE_EXECUTION_STATE_FILE", str(state))

    agent.consume_edge_execution_generation(
        {
            "export_id": 7,
            "execution_generation": "gen-a",
            "execution_generation_issued_at": "2026-09-20T10:00:00Z",
        }
    )
    agent.consume_edge_execution_generation(
        {
            "export_id": 7,
            "execution_generation": "gen-b",
            "execution_generation_issued_at": "2026-09-20T10:00:01Z",
        }
    )
    with pytest.raises(RuntimeError, match="已过期"):
        agent.consume_edge_execution_generation(
            {
                "export_id": 7,
                "execution_generation": "gen-a",
                "execution_generation_issued_at": "2026-09-20T10:00:00Z",
            }
        )


def test_generation_state_serializes_concurrent_same_generation(monkeypatch, tmp_path):
    agent = _load_board_agent()
    state = tmp_path / "execution_generations.json"
    monkeypatch.setenv("LABELU_EDGE_EXECUTION_STATE_FILE", str(state))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: agent.consume_edge_execution_generation(_cfg("gen-a", 1)),
                (1, 2),
            )
        )

    assert sorted(item["status"] for item in results) == ["accepted_new", "idempotent"]
