# Ultralytics tasks.py ONNX export shim for LabelU/Sophon BM1688 Docker quantization.
#
# This file is copied over site-packages/ultralytics/nn/tasks.py inside the
# labelu_bm1688 Docker container. The Docker script first saves the original
# tasks.py as tasks_labelu_original.py, then this shim loads that exact installed
# version, patches BaseModel._predict_once's final return from `return x` to
# `return x.permute(0, 2, 1)`, and executes the patched source as
# ultralytics.nn.tasks.
#
# Keeping this as a source-level shim avoids vendoring a version-sensitive full
# Ultralytics file while still applying the Sophon YOLOv8_plus_det ONNX output
# layout required by the export guide.

from pathlib import Path


_ORIGINAL_TASKS_FILENAME = "tasks_labelu_original.py"
_TARGET_RETURN = "        return x\n\n    def _predict_augment(self, x):\n"
_PATCHED_RETURN = (
    "        # LabelU/Sophon ONNX export patch: YOLOv8_plus_det expects [B, boxes, attrs].\n"
    "        return x.permute(0, 2, 1)\n\n"
    "    def _predict_augment(self, x):\n"
)


def _load_patched_original_tasks() -> None:
    original = Path(__file__).resolve().with_name(_ORIGINAL_TASKS_FILENAME)
    if not original.is_file():
        raise ImportError(
            f"LabelU Sophon tasks shim requires backup file: {original}. "
            "Run bm1688_docker_model_quant.py so it can back up the installed ultralytics tasks.py first."
        )

    source = original.read_text(encoding="utf-8")
    if _PATCHED_RETURN not in source:
        if _TARGET_RETURN not in source:
            raise RuntimeError(
                "Cannot patch ultralytics.nn.tasks.BaseModel._predict_once: "
                "expected final `return x` before `_predict_augment` was not found."
            )
        source = source.replace(_TARGET_RETURN, _PATCHED_RETURN, 1)

    # Execute the installed-version source in this module's namespace so classes
    # keep the canonical module path `ultralytics.nn.tasks` for torch pickle loads.
    globals()["__file__"] = str(original)
    globals()["__cached__"] = None
    exec(compile(source, str(original), "exec"), globals(), globals())


_load_patched_original_tasks()

del _load_patched_original_tasks
