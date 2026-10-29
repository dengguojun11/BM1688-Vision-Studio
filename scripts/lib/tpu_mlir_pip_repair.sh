#!/usr/bin/env bash
# 修复 venv bin 中被 envsetup/未 build 的 install 路径污染的 TPU-MLIR 入口脚本

_TPU_BIN_TOOLS=(model_transform.py model_deploy.py run_calibration.py model_tool.py)

tpu_mlir_wrapper_broken() {
  local py="${1:?python}"
  local bindir
  bindir="$(dirname "$py")"
  local f p target
  for f in "${_TPU_BIN_TOOLS[@]}"; do
    p="${bindir}/${f}"
    [[ -f "$p" ]] || return 0
    target="$(head -20 "$p" 2>/dev/null || true)"
    if grep -qE 'tpu-mlir/install|install/python/tools|/python/tools/' <<<"$target"; then
      return 0
    fi
    if ! PYTHONPATH="" "$p" --help >/dev/null 2>&1; then
      return 0
    fi
  done
  return 1
}

_tpu_mlir_rewrite_bin_from_site() {
  local py="${1:?python}"
  local bindir="${2:?bindir}"
  PYTHONPATH="" "${py}" - "${bindir}" "${py}" <<'PY'
import os
import site
import stat
import sys

bindir, py = sys.argv[1:3]
sp = site.getsitepackages()[0]
names = (
    "model_transform.py",
    "model_deploy.py",
    "run_calibration.py",
    "model_tool.py",
)
found = {}
for root, _, files in os.walk(sp):
    for n in names:
        if n in files:
            found[n] = os.path.join(root, n)
written = 0
for n in names:
    src = found.get(n)
    if not src:
        continue
    dst = os.path.join(bindir, n)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(f"#!{py}\n")
        f.write("import runpy, sys\n")
        f.write(f"sys.argv[0] = {src!r}\n")
        f.write(f"runpy.run_path({src!r}, run_name='__main__')\n")
    mode = os.stat(dst).st_mode
    os.chmod(dst, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"[tpu-mlir-repair] wrote {dst} -> {src}", file=sys.stderr)
    written += 1
if written == 0:
    print("[tpu-mlir-repair] ERROR: site-packages 中未找到 TPU 工具脚本", file=sys.stderr)
    sys.exit(1)
PY
}

_tpu_mlir_export_runtime_lib() {
  local py="${1:?python}"
  local libpath
  libpath="$(PYTHONPATH="" "${py}" - <<'PY' 2>/dev/null || true
import glob
import os
import site

sp = site.getsitepackages()[0]
candidates = []
for pattern in (
    os.path.join(sp, "lib"),
    os.path.join(sp, "tpu_mlir", "lib"),
    os.path.join(sp, "**", "lib"),
):
    for p in glob.glob(pattern, recursive=True):
        if not os.path.isdir(p):
            continue
        try:
            if any(n.endswith(".so") for n in os.listdir(p)):
                candidates.append(p)
        except OSError:
            pass
print(":".join(dict.fromkeys(candidates)))
PY
)"
  if [[ -n "${libpath}" ]]; then
    export LD_LIBRARY_PATH="${libpath}:${LD_LIBRARY_PATH:-}"
  fi
}

# INT8 codegen（tpuc-opt）在 RELEASE_PATH/lib 查找芯片 kernel .so；勿使用 Docker 硬编码 /tpu_mlir
tpu_mlir_setup_native_runtime() {
  local py="${1:-}"
  local root="${TPU_MLIR_ROOT:-}"
  if [[ -n "$root" && -d "$root" ]]; then
    export PROJECT_ROOT="${root}"
    # codegen 在 ${RELEASE_PATH}/lib 找 kernel .so；官方 build 产物在 install/lib
    if [[ -d "${root}/install/lib" ]]; then
      export RELEASE_PATH="${root}/install"
      export TPUC_ROOT="${root}/install"
    else
      export RELEASE_PATH="${root}"
      export TPUC_ROOT="${root}"
    fi
    local -a src_dirs=()
    local d
    for d in \
      "${root}/lib" \
      "${root}/install/lib" \
      "${root}/third_party/nntoolchain/lib" \
      "${root}/third_party/bmcompiler/lib"; do
      [[ -d "$d" ]] && src_dirs+=("$d")
    done
    if [[ ${#src_dirs[@]} -gt 0 ]]; then
      local joined
      joined=$(IFS=:; echo "${src_dirs[*]}")
      export LD_LIBRARY_PATH="${joined}:${LD_LIBRARY_PATH:-}"
    fi
  fi
  if [[ -n "$py" && -x "$py" ]]; then
    _tpu_mlir_export_runtime_lib "$py"
  fi
}

tpu_mlir_chip_kernel_so_for_chip() {
  case "${1,,}" in
    bm1688) echo "libbmtpulv60_kernel_module.so" ;;
    bm1684x) echo "libbmtpulv58_kernel_module.so" ;;
    cv186x) echo "libcv186x_kernel_module.so" ;;
    *) echo "libbmtpulv60_kernel_module.so" ;;
  esac
}

tpu_mlir_find_chip_kernel_so() {
  local chip="${1:-bm1688}"
  local name tree="${TPU_MLIR_ROOT:-}"
  name="$(tpu_mlir_chip_kernel_so_for_chip "$chip")"
  local p
  for p in \
    "${tree}/install/lib/${name}" \
    "${tree}/lib/${name}" \
    "${RELEASE_PATH:-}/lib/${name}"; do
    [[ -n "$p" && -f "$p" ]] && { echo "$p"; return 0; }
  done
  local sp=""
  if [[ -n "${TPU_MLIR_PRIMARY_PYTHON:-}" && -x "${TPU_MLIR_PRIMARY_PYTHON}" ]]; then
    sp="$("${TPU_MLIR_PRIMARY_PYTHON}" -c "import site; print(site.getsitepackages()[0])" 2>/dev/null || true)"
    if [[ -n "$sp" && -f "${sp}/tpu_mlir/lib/${name}" ]]; then
      echo "${sp}/tpu_mlir/lib/${name}"
      return 0
    fi
  fi
  return 1
}

tpu_mlir_require_chip_kernel_so() {
  local chip="${1:?chip}"
  tpu_mlir_setup_native_runtime "${TPU_MLIR_PRIMARY_PYTHON:-}"
  local so
  if so="$(tpu_mlir_find_chip_kernel_so "$chip")"; then
    echo "chip kernel so: ${so}" >&2
    return 0
  fi
  local name tree="${TPU_MLIR_ROOT:-}"
  name="$(tpu_mlir_chip_kernel_so_for_chip "$chip")"
  echo "ERROR: INT8 codegen 缺少 ${name}（tpuc-opt 在 \${RELEASE_PATH}/lib 查找）。" >&2
  echo "  RELEASE_PATH=${RELEASE_PATH:-未设置} TPU_MLIR_ROOT=${TPU_MLIR_ROOT:-}" >&2
  echo "  请检查: ls -la ${tree}/install/lib/${name}" >&2
  echo "  需先编译 TPU-MLIR 源码（pip wheel 不含该文件）:" >&2
  echo "    bash scripts/build_tpu_mlir_chip_libs.sh" >&2
  echo "  或官方 Docker: sophgo/tpuc_dev + ./build.sh" >&2
  return 1
}

_tpu_mlir_pip_env() {
  local base="${TPU_MLIR_PIP_TMPDIR:-/data/labeIU/labeIuMedia/.pip_build}"
  mkdir -p "${base}/tmp" "${base}/cache"
  export TMPDIR="${base}/tmp"
  export PIP_CACHE_DIR="${base}/cache"
}

_tpu_mlir_require_free_mb() {
  local need_mb="${1:?need_mb}"
  local dir="${2:-/data/labeIU/labeIuMedia}"
  local avail
  avail="$(df -Pm "${dir}" 2>/dev/null | awk 'NR==2 {print $4}' || echo 0)"
  if [[ "${avail}" -lt "${need_mb}" ]]; then
    echo "[tpu-mlir-repair] ERROR: ${dir} 可用空间 ${avail}MB，不足 ${need_mb}MB（请清理磁盘后重试）" >&2
    echo "  df -h ${dir}" >&2
    echo "  du -sh ${dir}/.pip_build ${dir}/venv-tpu-mlir /tmp ~/.cache/pip 2>/dev/null" >&2
    return 1
  fi
}

_tpu_mlir_install_torch() {
  local py="${1:?python}"
  if PYTHONPATH="" "${py}" -c "import torch" 2>/dev/null; then
    return 0
  fi
  _tpu_mlir_pip_env
  _tpu_mlir_require_free_mb 1800 "${TPU_MLIR_PIP_TMPDIR:-/data/labeIU/labeIuMedia}" || return 1
  echo "[tpu-mlir-repair] pip install torch CPU (ONNX model_transform 必需, --no-cache-dir) -> ${py}" >&2
  PYTHONPATH="" "${py}" -m pip install --no-cache-dir torch \
    --index-url https://download.pytorch.org/whl/cpu || return 1
  PYTHONPATH="" "${py}" -c "import torch"
}

_tpu_mlir_check_pymlir() {
  local py="${1:?python}"
  local root="${TPU_MLIR_ROOT:-/data/labeIU/tpu-mlir}"
  local d
  for d in "${root}/python" "${root}/install/python" "${root}/lib"; do
    [[ -d "$d" ]] || continue
    if PYTHONPATH="${d}" "${py}" -c "import pymlir" 2>/dev/null; then
      echo "[tpu-mlir-repair] pymlir OK via ${d}" >&2
      return 0
    fi
  done
  echo "[tpu-mlir-repair] WARN: pymlir 未找到；INT8 编译将尝试 source ${root}/envsetup.sh" >&2
  return 0
}

_tpu_mlir_install_onnx_deps() {
  local py="${1:?python}"
  _tpu_mlir_pip_env
  for pkg in psutil tqdm numpy; do
    PYTHONPATH="" "${py}" -c "import ${pkg}" 2>/dev/null \
      || PYTHONPATH="" "${py}" -m pip install --no-cache-dir -q "${pkg}" || return 1
  done
  _tpu_mlir_install_torch "$py" || return 1
  _tpu_mlir_check_pymlir "$py" || true
}

# 编译时调用：仅修复 wrapper，不 force-reinstall（避免 260MB 重装拖垮编译）
tpu_mlir_pip_repair_light() {
  local py="${1:?python}"
  local bindir
  bindir="$(dirname "$py")"
  unset PYTHONPATH
  _tpu_mlir_install_onnx_deps "$py" || return 1
  if ! PYTHONPATH="" "${py}" -m pip show tpu-mlir >/dev/null 2>&1 \
    && ! PYTHONPATH="" "${py}" -m pip show tpu_mlir >/dev/null 2>&1; then
    echo "[tpu-mlir-repair] pip install tpu-mlir[onnx] -> ${py}" >&2
    PYTHONPATH="" "${py}" -m pip install -q "tpu-mlir[onnx]" \
      || PYTHONPATH="" "${py}" -m pip install -q "tpu_mlir[onnx]"
  fi
  if tpu_mlir_wrapper_broken "$py"; then
    _tpu_mlir_rewrite_bin_from_site "${py}" "${bindir}"
  fi
  tpu_mlir_setup_native_runtime "${py}"
}

# 安装脚本调用：完整重装 + 重写 wrapper
tpu_mlir_pip_repair() {
  local py="${1:?python}"
  local bindir
  bindir="$(dirname "$py")"
  unset PYTHONPATH
  for f in "${_TPU_BIN_TOOLS[@]}"; do
    rm -f "${bindir}/${f}"
  done
  echo "[tpu-mlir-repair] pip install --force-reinstall tpu-mlir[onnx] -> ${py}" >&2
  PYTHONPATH="" "${py}" -m pip install -U pip "setuptools>=70,<82" wheel
  PYTHONPATH="" "${py}" -m pip install --force-reinstall --no-cache-dir "tpu-mlir[onnx]" \
    || PYTHONPATH="" "${py}" -m pip install --force-reinstall --no-cache-dir "tpu_mlir[onnx]"
  _tpu_mlir_install_onnx_deps "$py" || PYTHONPATH="" "${py}" -m pip install -q torch
  _tpu_mlir_rewrite_bin_from_site "${py}" "${bindir}"
  _tpu_mlir_export_runtime_lib "${py}"
}

tpu_mlir_tools_ok() {
  local py="${1:?python}"
  local bindir
  bindir="$(dirname "$py")"
  unset PYTHONPATH
  export PATH="${bindir}:${PATH}"
  tpu_mlir_setup_native_runtime "${py}"
  local mt="${bindir}/model_transform.py"
  local md="${bindir}/model_deploy.py"
  [[ -f "$mt" && -f "$md" ]] || return 1
  PYTHONPATH="" "$mt" --help >/dev/null 2>&1 \
    && PYTHONPATH="" "$md" --help >/dev/null 2>&1
}
