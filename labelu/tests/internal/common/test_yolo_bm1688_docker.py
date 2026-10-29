from pathlib import Path
from types import SimpleNamespace

from labelu.internal.common import yolo_bm1688_docker as docker_mod


def test_ensure_windows_docker_engine_handles_empty_tasklist_output(monkeypatch):
    desktop = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
    launched = []

    monkeypatch.setattr(docker_mod.os, "name", "nt")
    monkeypatch.setattr(
        docker_mod.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=None),
    )
    monkeypatch.setattr(Path, "is_file", lambda self: self == desktop)
    monkeypatch.setattr(
        docker_mod.subprocess,
        "Popen",
        lambda *args, **kwargs: launched.append((args, kwargs)),
    )
    monkeypatch.setattr(docker_mod, "docker_cli_available", lambda: True)

    ok, message = docker_mod._ensure_windows_docker_engine()

    assert ok is True
    assert message == "Docker Desktop Linux engine ready"
    assert launched


def test_ensure_windows_docker_engine_starts_after_tasklist_timeout(monkeypatch):
    desktop = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
    launched = []

    monkeypatch.setattr(docker_mod.os, "name", "nt")

    def _tasklist_timeout(*args, **kwargs):
        raise docker_mod.subprocess.TimeoutExpired(cmd="tasklist", timeout=10)

    monkeypatch.setattr(docker_mod.subprocess, "run", _tasklist_timeout)
    monkeypatch.setattr(Path, "is_file", lambda self: self == desktop)
    monkeypatch.setattr(
        docker_mod.subprocess,
        "Popen",
        lambda *args, **kwargs: launched.append((args, kwargs)),
    )
    monkeypatch.setattr(docker_mod, "docker_cli_available", lambda: True)

    ok, message = docker_mod._ensure_windows_docker_engine()

    assert ok is True
    assert message == "Docker Desktop Linux engine ready"
    assert launched


def test_ensure_bm1688_docker_ready_does_not_probe_container_without_docker(
    monkeypatch,
):
    monkeypatch.setattr(docker_mod, "docker_cli_available", lambda: False)
    monkeypatch.setattr(docker_mod.os, "name", "posix")
    monkeypatch.setattr(
        docker_mod,
        "_container_running",
        lambda: (_ for _ in ()).throw(AssertionError("container probe must be gated")),
    )

    ok, message = docker_mod.ensure_bm1688_docker_ready(allow_bootstrap=False)

    assert ok is False
    assert message == "未检测到 docker 命令"


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._index = 0

    def __iter__(self):
        return iter(self._chunks)

    def read(self, _size=-1):
        if self._index >= len(self._chunks):
            return ""
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk

    def close(self):
        return None


class _FakeProc:
    def __init__(self, stdout_chunks, stderr_chunks, returncode=0):
        self.stdout = _FakeStream(stdout_chunks)
        self.stderr = _FakeStream(stderr_chunks)
        self.returncode = returncode
        self.killed = False
        self._poll_count = 0

    def wait(self, timeout=None):
        if not self.killed and timeout is not None and timeout < 5:
            raise docker_mod.subprocess.TimeoutExpired(cmd="docker", timeout=timeout)
        return self.returncode

    def poll(self):
        self._poll_count += 1
        return self.returncode

    def kill(self):
        self.killed = True


def test_run_docker_int8_quant_streams_output(monkeypatch, tmp_path):
    script = tmp_path / "bm1688_docker_model_quant.py"
    script.write_text("# mock\n", encoding="utf-8")
    monkeypatch.setattr(docker_mod, "_docker_script_path", lambda: script)
    monkeypatch.setattr(docker_mod, "ensure_bm1688_docker_ready", lambda: (True, "ok"))

    seen = []

    def _fake_popen(*args, **kwargs):
        return _FakeProc(
            stdout_chunks=["progress 10%\n", "progress 20%\n"],
            stderr_chunks=["warn line\n"],
            returncode=0,
        )

    monkeypatch.setattr(docker_mod.subprocess, "Popen", _fake_popen)

    ok, log = docker_mod.run_docker_int8_quant(
        pt_path=Path(tmp_path / "best.pt"),
        calib_dir=Path(tmp_path / "calib"),
        out_dir=Path(tmp_path / "out"),
        model_stem="yolov11n",
        timeout_seconds=30,
        line_callback=lambda line, is_stderr=False: seen.append((line, is_stderr)),
    )

    assert ok is True
    assert seen == [
        ("progress 10%", False),
        ("progress 20%", False),
        ("warn line", True),
    ]
    assert "progress 10%" in log
    assert "warn line" in log


def test_run_docker_int8_quant_streams_non_newline_chunks(monkeypatch, tmp_path):
    script = tmp_path / "bm1688_docker_model_quant.py"
    script.write_text("# mock\n", encoding="utf-8")
    monkeypatch.setattr(docker_mod, "_docker_script_path", lambda: script)
    monkeypatch.setattr(docker_mod, "ensure_bm1688_docker_ready", lambda: (True, "ok"))

    seen = []

    def _fake_popen(*args, **kwargs):
        return _FakeProc(
            stdout_chunks=["step 1", " step 2", "\rstep 3", "\nfinish\n"],
            stderr_chunks=[],
            returncode=0,
        )

    monkeypatch.setattr(docker_mod.subprocess, "Popen", _fake_popen)

    ok, log = docker_mod.run_docker_int8_quant(
        pt_path=Path(tmp_path / "best.pt"),
        calib_dir=Path(tmp_path / "calib"),
        out_dir=Path(tmp_path / "out"),
        model_stem="yolov11n",
        timeout_seconds=30,
        line_callback=lambda line, is_stderr=False: seen.append((line, is_stderr)),
    )

    assert ok is True
    assert seen == [
        ("step 1", False),
        (" step 2", False),
        ("step 3", False),
        ("finish", False),
    ]
    assert "step 1" in log
    assert "finish" in log


def test_resolve_docker_executable_falls_back_to_default_windows_install(monkeypatch):
    expected = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")

    monkeypatch.setattr(docker_mod.shutil, "which", lambda _name: None)
    monkeypatch.setattr(docker_mod.os, "name", "nt")
    monkeypatch.setattr(Path, "is_file", lambda self: self == expected)

    resolved = docker_mod._resolve_docker_executable()

    assert resolved == str(expected)


def test_run_docker_int8_quant_uses_container_paths_on_windows(monkeypatch, tmp_path):
    script = tmp_path / "bm1688_docker_model_quant.py"
    script.write_text("# mock\n", encoding="utf-8")
    mount_root = tmp_path / "host_root"
    pt_path = mount_root / "labelu" / "media" / "yolo_train" / "job-1" / "train" / "weights" / "best.pt"
    calib_dir = mount_root / "labelu" / "media" / "yolo_bm1688_exports" / "2" / "calibration"
    out_dir = mount_root / "labelu" / "media" / "yolo_bm1688_exports" / "2" / "models" / "bm1688"
    onnx_path = mount_root / "labelu" / "media" / "yolo_bm1688_exports" / "2" / "models" / "onnx" / "yolo11n.onnx"
    pt_path.parent.mkdir(parents=True, exist_ok=True)
    calib_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    onnx_path.parent.mkdir(parents=True, exist_ok=True)
    pt_path.write_text("pt", encoding="utf-8")
    onnx_path.write_text("onnx", encoding="utf-8")

    monkeypatch.setattr(docker_mod, "_docker_script_path", lambda: script)
    monkeypatch.setattr(docker_mod, "ensure_bm1688_docker_ready", lambda: (True, "ok"))
    monkeypatch.setattr(docker_mod.os, "name", "nt")
    monkeypatch.setattr(docker_mod, "_docker_mount_base", lambda: mount_root)
    monkeypatch.setattr(docker_mod, "_resolve_docker_executable", lambda: "docker")

    captured = {}

    def _fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc(stdout_chunks=["ok\n"], stderr_chunks=[], returncode=0)

    monkeypatch.setattr(docker_mod.subprocess, "Popen", _fake_popen)

    ok, log = docker_mod.run_docker_int8_quant(
        pt_path=pt_path,
        calib_dir=calib_dir,
        out_dir=out_dir,
        model_stem="yolov11n",
        onnx_path=onnx_path,
        imgsz=640,
        timeout_seconds=30,
    )

    assert ok is True
    assert "ok" in log
    inner = captured["cmd"][-1]
    assert "--pt /mnt/labelu/labelu/media/yolo_train/job-1/train/weights/best.pt" in inner
    assert "--calib-dir /mnt/labelu/labelu/media/yolo_bm1688_exports/2/calibration" in inner
    assert "--out-dir /mnt/labelu/labelu/media/yolo_bm1688_exports/2/models/bm1688" in inner
    assert "--onnx /mnt/labelu/labelu/media/yolo_bm1688_exports/2/models/onnx/yolo11n.onnx" in inner
    assert "--inference-num 10" in inner


def test_run_docker_int8_quant_uses_configured_calibration_inference_num(
    monkeypatch, tmp_path
):
    script = tmp_path / "bm1688_docker_model_quant.py"
    script.write_text("# mock\n", encoding="utf-8")
    monkeypatch.setattr(docker_mod, "_docker_script_path", lambda: script)
    monkeypatch.setattr(docker_mod, "ensure_bm1688_docker_ready", lambda: (True, "ok"))

    captured = {}

    def _fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc(stdout_chunks=["ok\n"], stderr_chunks=[], returncode=0)

    monkeypatch.setattr(docker_mod.subprocess, "Popen", _fake_popen)

    docker_mod.run_docker_int8_quant(
        pt_path=Path(tmp_path / "best.pt"),
        calib_dir=Path(tmp_path / "calib"),
        out_dir=Path(tmp_path / "out"),
        model_stem="yolov11n",
        file_cfg={"cali_inference_num": 23},
        timeout_seconds=30,
    )

    assert "--inference-num 23" in captured["cmd"][-1]


def test_run_docker_int8_quant_requests_fp32_fp16_for_full_mode(
    monkeypatch, tmp_path
):
    script = tmp_path / "bm1688_docker_model_quant.py"
    script.write_text("# mock\n", encoding="utf-8")
    monkeypatch.setattr(docker_mod, "_docker_script_path", lambda: script)
    monkeypatch.setattr(docker_mod, "ensure_bm1688_docker_ready", lambda: (True, "ok"))

    captured = {}

    def _fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProc(stdout_chunks=["ok\n"], stderr_chunks=[], returncode=0)

    monkeypatch.setattr(docker_mod.subprocess, "Popen", _fake_popen)

    ok, _ = docker_mod.run_docker_int8_quant(
        pt_path=Path(tmp_path / "best.pt"),
        calib_dir=Path(tmp_path / "calib"),
        out_dir=Path(tmp_path / "out"),
        model_stem="yolov11n",
        timeout_seconds=30,
        emit_fp32_fp16=True,
    )

    assert ok is False
    assert "--emit-fp32-fp16" in captured["cmd"][-1]


def test_run_docker_int8_quant_recovers_when_windows_docker_exec_hangs_after_artifact_written(
    monkeypatch, tmp_path
):
    script = tmp_path / "bm1688_docker_model_quant.py"
    script.write_text("# mock\n", encoding="utf-8")
    pt_path = tmp_path / "best.pt"
    calib_dir = tmp_path / "calib"
    out_dir = tmp_path / "out"
    pt_path.write_text("pt", encoding="utf-8")
    calib_dir.mkdir()
    out_dir.mkdir()

    monkeypatch.setattr(docker_mod, "_docker_script_path", lambda: script)
    monkeypatch.setattr(docker_mod, "ensure_bm1688_docker_ready", lambda: (True, "ok"))
    monkeypatch.setattr(docker_mod.os, "name", "nt")
    monkeypatch.setattr(docker_mod, "_resolve_docker_executable", lambda: "docker")
    monkeypatch.setattr(docker_mod, "WINDOWS_DOCKER_POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(docker_mod, "WINDOWS_DOCKER_STALL_ARTIFACT_GRACE_SECONDS", 0.0)
    monkeypatch.setattr(docker_mod.time, "sleep", lambda _seconds: None)

    fake_proc = _FakeProc(stdout_chunks=["docker start\n"], stderr_chunks=[], returncode=0)
    plat_dir = out_dir
    bmodel_path = plat_dir / "yolov11n_int8_1b.bmodel"

    def _fake_poll():
        fake_proc._poll_count += 1
        if fake_proc._poll_count == 2:
            bmodel_path.write_text("ok", encoding="utf-8")
        return fake_proc.returncode if fake_proc.killed else None

    fake_proc.poll = _fake_poll

    monkeypatch.setattr(docker_mod.subprocess, "Popen", lambda *args, **kwargs: fake_proc)

    ok, log = docker_mod.run_docker_int8_quant(
        pt_path=pt_path,
        calib_dir=calib_dir,
        out_dir=out_dir,
        model_stem="yolov11n",
        timeout_seconds=1,
    )

    assert ok is True
    assert fake_proc.killed is True
    assert "yolov11n_int8_1b.bmodel" in log or "artifact" in log.lower()
