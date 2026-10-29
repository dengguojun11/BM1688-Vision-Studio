from __future__ import annotations

import sys
from types import SimpleNamespace
from pathlib import Path

import start_labelu_8003 as launcher

_VALID_WRAPPER = """\
from pathlib import Path
import start_labelu_8003 as launcher

root = Path(__file__).resolve().parents[1]
launcher._take_over_existing_project_listener(root / "start_labelu_8003.py", root)
raise SystemExit(launcher._run_backend(root, {}))
"""


def _wrapper_path(project_root: Path) -> Path:
    path = project_root / ".tmp" / "run_built_backend_8003.py"
    path.parent.mkdir(parents=True)
    return path


def test_root_entrypoint_is_supported(tmp_path: Path):
    root_script = tmp_path / "start_labelu_8003.py"

    assert launcher._is_supported_entrypoint(root_script, root_script, tmp_path)


def test_valid_built_backend_wrapper_is_supported(tmp_path: Path):
    root_script = tmp_path / "start_labelu_8003.py"
    wrapper = _wrapper_path(tmp_path)
    wrapper.write_text(_VALID_WRAPPER, encoding="utf-8")

    assert launcher._is_supported_entrypoint(wrapper, root_script, tmp_path)


def test_same_named_unrelated_wrapper_is_rejected(tmp_path: Path):
    root_script = tmp_path / "start_labelu_8003.py"
    wrapper = _wrapper_path(tmp_path)
    wrapper.write_text("print('unrelated')\n", encoding="utf-8")

    assert not launcher._is_supported_entrypoint(wrapper, root_script, tmp_path)


def test_listener_check_falls_back_to_legacy_psutil_connections(monkeypatch):
    class _LegacyProcess:
        def __init__(self, pid):
            self.pid = pid

        def net_connections(self, kind):
            raise AttributeError("legacy psutil")

        def connections(self, kind):
            return [SimpleNamespace(laddr=SimpleNamespace(port=8003), status="LISTEN")]

    fake_psutil = SimpleNamespace(
        Process=_LegacyProcess,
        CONN_LISTEN="LISTEN",
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    assert launcher._pid_listens_on_port(1234, 8003)
