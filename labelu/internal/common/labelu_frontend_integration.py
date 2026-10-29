"""labelU 前端源码集成：extensions 自研资源 + labelU-Kit 官方 monorepo。"""

from __future__ import annotations

import atexit
import gzip
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional, TextIO

REPO_ROOT = Path(__file__).resolve().parents[3]
KIT_DIR = Path(os.environ.get("LABELU_KIT_DIR", REPO_ROOT / "frontend" / "labelU-Kit"))
EXTENSIONS_DIR = REPO_ROOT / "frontend" / "extensions"
FRONTEND_VERSION_FILE = EXTENSIONS_DIR / "FRONTEND_VERSION"
EXTENSIONS_PAGES = EXTENSIONS_DIR / "pages"
EXTENSIONS_ASSETS = EXTENSIONS_DIR / "assets"
_DEFAULT_STATICS_DIR = REPO_ROOT / "labelu" / "internal" / "statics"
# Allow a local service to pin an isolated static bundle.  This is useful when
# two local ports share the source tree but must be restarted independently.
# Production builds keep the default package directory unless the variable is
# explicitly set by the service launcher.
STATICS_DIR = Path(
    os.environ.get("LABELU_STATIC_DIR", str(_DEFAULT_STATICS_DIR))
).expanduser().resolve()
KIT_PUBLIC_ASSETS = KIT_DIR / "apps" / "frontend" / "public" / "assets"
KIT_INDEX_HTML = KIT_DIR / "apps" / "frontend" / "index.html"
VERSION_FILE = REPO_ROOT / ".VERSION"
KIT_REPO_URL = "https://github.com/opendatalab/labelU-Kit.git"
# Node 20 生产环境：pnpm 10+ 需 Node ≥ 22（含 node:sqlite），固定 9.x
PNPM_VERSION = os.environ.get("LABELU_PNPM_VERSION", "9.15.0")

# These browser-side tools must be loaded by both the local Vite shell and the
# production static shell. Keep the VLX entry here so the local LabelU page
# actually executes the asset it serves at /assets/.
SPA_EXTENSION_SCRIPTS: tuple[str, ...] = (
    "task_vlx_seek_preannotate_entry.js",
)

MANAGED_EXTENSION_SCRIPTS = (
    "labelu_auth_bridge.js",
    "labelu_pager.js",
    "task_rect_preset_tabs.js",
    "task_list_yolo_train_link.js",
    "task_yolo_preannotate_entry.js",
    "task_tagtool_preannotate_entry.js",
    "task_locateanything_preannotate_entry.js",
    "task_vlx_seek_preannotate_entry.js",
)

EXTENSION_ASSET_FILES = (
    "labelu_auth_bridge.js",
    "labelu_embedded_boot.js",
    "labelu_local_dev_reload.js",
    "labelu_portal.js",
    "labelu_pager.js",
    "labelu_standalone_shell.css",
    "labelu_toolkit_compact.css",
    "labelu_train_hub.css",
    "yolo_training_observability.css",
    "yolo_training_observability.js",
    "task_rect_preset_tabs.js",
    "task_list_yolo_train_link.js",
    "task_yolo_preannotate_entry.js",
    "task_tagtool_preannotate_entry.js",
    "task_vlx_seek_preannotate_entry.js",
    "task_toolbar_extensions.js",
    "task_ai_label_geometry_toggle.js",
)

# FastAPI 路由 -> extensions/pages 下文件名
EXTENSION_PAGE_FILES = {
    "/annotation-settings": "annotation_settings.html",
    "/yolo-training": "yolo_training.html",
    "/ai-labeling-settings": "ai_labeling_settings.html",
    "/yolo-eval-prompts": "yolo_eval_prompts.html",
    "/yolo-eval-reports-hub": "yolo_eval_reports_hub.html",
    "/yolo-bm1688-exports": "yolo_bm1688_exports.html",
    "/edge-device-registry": "edge_device_registry.html",
    "/aicm-publish-environments": "aicm_publish_environments.html",
    "/strategy-integration-s3-config": "strategy_integration_s3_config.html",
    "/third-party-calls": "third_party_calls.html",
    "/yolo-preannotate": "yolo_preannotate.html",
    "/yolo-model-validate": "yolo_model_validate.html",
    "/annotation-stats": "annotation_stats.html",
    "/yolo-model-publish": "yolo_model_publish.html",
    "/yolo-model-catalog": "yolo_model_catalog.html",
    "/yolo-quality-policies": "yolo_quality_policies.html",
    "/yolo-train-detail-mockup": "yolo_train_detail_mockup.html",
    "/yolo-gt-pred-compare": "yolo_gt_pred_compare.html",
}

RETIRED_EXTENSION_PAGE_PATHS = frozenset({"/yolo-model-workflow"})

EXTENSION_MARKER = "<!-- labelu-custom-extensions -->"
HASHED_STATIC_ASSET_RE = re.compile(r"^[A-Za-z0-9_.-]+-[0-9a-fA-F]{8,}\.(?:js|css|svg)$")

_vite_proc: Optional[subprocess.Popen] = None
_vite_log_handle: Optional[TextIO] = None
_frontend_port: int = 3004


def _prefer_windows_npm_bin() -> None:
    """Ensure Windows resolves the user-global npm bin before bundled runtimes."""
    if os.name != "nt":
        return
    npm_bin = (Path.home() / "AppData" / "Roaming" / "npm").resolve()
    if not npm_bin.is_dir():
        return
    current = os.environ.get("PATH", "")
    parts = [p for p in current.split(os.pathsep) if p]
    normalized_bin = str(npm_bin).casefold()
    filtered = [p for p in parts if p.casefold() != normalized_bin]
    os.environ["PATH"] = str(npm_bin) + os.pathsep + os.pathsep.join(filtered)


def integrated_frontend_enabled() -> bool:
    if os.environ.get("LABELU_STATIC_ONLY", "").strip() in ("1", "true", "yes"):
        return False
    if os.environ.get("LABELU_NO_FRONTEND", "").strip() in ("1", "true", "yes"):
        return False
    return True


def statics_has_built_spa() -> bool:
    index = STATICS_DIR / "index.html"
    if not index.is_file():
        return False
    assets = STATICS_DIR / "assets"
    if not assets.is_dir():
        return False
    return any(assets.glob("index-*.js"))


def extension_page_path(route: str) -> Path:
    name = EXTENSION_PAGE_FILES.get(route)
    if not name:
        raise KeyError(route)
    path = EXTENSIONS_PAGES / name
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def extension_asset_path(filename: str) -> Optional[Path]:
    path = EXTENSIONS_ASSETS / filename
    return path if path.is_file() else None


def statics_built_asset_path(filename: str) -> Optional[Path]:
    """Vite 构建产物（如 index-*.js），与 extensions 共用 /assets/ URL。"""
    if ".." in filename or "/" in filename or "\\" in filename:
        return None
    path = STATICS_DIR / "assets" / filename
    return path if path.is_file() else None


def asset_media_type(filename: str) -> str:
    if filename.endswith(".css"):
        return "text/css; charset=utf-8"
    if filename.endswith(".js"):
        return "application/javascript; charset=utf-8"
    if filename.endswith(".svg"):
        return "image/svg+xml"
    return "application/octet-stream"


def is_hashed_static_asset(filename: str) -> bool:
    if ".." in filename or "/" in filename or "\\" in filename:
        return False
    return bool(HASHED_STATIC_ASSET_RE.match(filename))


def hashed_static_asset_cache_control(filename: str) -> str:
    if is_hashed_static_asset(filename):
        return "public, max-age=31536000, immutable"
    return "no-cache"


def gzip_static_asset_path(path: Path) -> Optional[Path]:
    gz_path = path.with_suffix(path.suffix + ".gz")
    return gz_path if gz_path.is_file() else None


def compress_static_assets(root: Path = STATICS_DIR) -> list[Path]:
    assets = root / "assets"
    if not assets.is_dir():
        return []
    created: list[Path] = []
    for path in assets.iterdir():
        if not path.is_file() or not is_hashed_static_asset(path.name):
            continue
        gz_path = path.with_suffix(path.suffix + ".gz")
        with path.open("rb") as src, gz_path.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=0) as dst:
                shutil.copyfileobj(src, dst)
        created.append(gz_path)
    return created


def is_extension_asset_url(path: str) -> bool:
    if not path.startswith("/assets/"):
        return False
    name = path.rsplit("/", 1)[-1]
    return name in EXTENSION_ASSET_FILES


def is_backend_only_path(path: str) -> bool:
    if not path or path == "/":
        return False
    if path.startswith("/api") or path.startswith("/internal") or path.startswith("/ws"):
        return True
    # Operational probes must always reach FastAPI. In integrated Vite mode
    # they otherwise look like SPA navigations and receive index.html.
    if path in (
        "/docs",
        "/redoc",
        "/openapi.json",
        "/healthz",
        "/readyz",
        "/version",
        "/favicon.ico",
        "/favicon.svg",
    ):
        return True
    if path in EXTENSION_PAGE_FILES or path in RETIRED_EXTENSION_PAGE_PATHS:
        return True
    if is_extension_asset_url(path):
        return True
    return False


def should_proxy_to_vite(path: str) -> bool:
    if not integrated_frontend_enabled():
        return False
    if not kit_is_cloned():
        return False
    if is_backend_only_path(path):
        return False
    if path.startswith("/@vite") or path.startswith("/__vite"):
        return True
    return True


def read_kit_version() -> str:
    if os.environ.get("LABELU_KIT_VERSION"):
        return os.environ["LABELU_KIT_VERSION"].strip()
    if VERSION_FILE.is_file():
        for line in VERSION_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("release_version:"):
                parts = line.split()
                if len(parts) >= 2:
                    return parts[1].strip()
    return "main"


def read_frontend_release_version() -> str:
    """前端发版号真源：frontend/extensions/FRONTEND_VERSION（仅前端改动时递增）。"""
    env = (os.environ.get("LABELU_FRONTEND_VERSION") or "").strip()
    if env:
        return env
    if FRONTEND_VERSION_FILE.is_file():
        for line in FRONTEND_VERSION_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    return "dev"


def read_backend_release_version() -> str:
    from labelu.version import version

    return str(version).strip()


def _runtime_version_js(global_name: str, version: str) -> str:
    safe = str(version or "").replace("\\", "\\\\").replace("'", "\\'")
    return f"(function(){{window.{global_name}={{version:'{safe}'}};}})();\n"


def write_runtime_version_scripts(
    target_dir: Path,
    log: Callable[..., None] = print,
) -> None:
    """写入 frontend_version.js / backend_version.js 供 SPA 账号菜单展示。"""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    fe = read_frontend_release_version()
    be = read_backend_release_version()
    (target_dir / "frontend_version.js").write_text(
        _runtime_version_js("__frontend", fe),
        encoding="utf-8",
    )
    (target_dir / "backend_version.js").write_text(
        _runtime_version_js("__backend", be),
        encoding="utf-8",
    )
    log(f"[labelu-frontend] 版本脚本: 前端 v{fe} · 后端 v{be} -> {target_dir}")


def kit_is_cloned() -> bool:
    """Kit 源码是否可用（monorepo 目录或独立 clone）。"""
    return kit_source_present()


def kit_source_present() -> bool:
    return (KIT_DIR / "package.json").is_file() and (
        KIT_DIR / "apps" / "frontend" / "package.json"
    ).is_file()


def clone_url() -> str:
    proxy = (os.environ.get("LABELU_KIT_GH_PROXY") or "").strip()
    if proxy:
        if not proxy.endswith("/"):
            proxy += "/"
        return f"{proxy}{KIT_REPO_URL}"
    return KIT_REPO_URL


def clone_kit(version: Optional[str] = None, log: Callable[..., None] = print) -> None:
    ver = version or read_kit_version()
    KIT_DIR.parent.mkdir(parents=True, exist_ok=True)
    url = clone_url()

    if kit_is_cloned():
        log(f"[labelu-frontend] fetch tags: {KIT_DIR}")
        subprocess.run(
            ["git", "-C", str(KIT_DIR), "fetch", "--tags", "origin"],
            check=False,
        )
    else:
        log(f"[labelu-frontend] 克隆 {url} -> {KIT_DIR} ({ver})")
        cmd = ["git", "clone", "--depth", "1", "--branch", ver, url, str(KIT_DIR)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            log(f"[labelu-frontend] 浅克隆失败，完整克隆…")
            if KIT_DIR.exists():
                shutil.rmtree(KIT_DIR, ignore_errors=True)
            r2 = subprocess.run(
                ["git", "clone", url, str(KIT_DIR)],
                capture_output=True,
                text=True,
            )
            if r2.returncode != 0:
                shutil.rmtree(KIT_DIR, ignore_errors=True)
                raise RuntimeError(
                    f"git clone 失败: {(r2.stderr or r2.stdout or '').strip()}"
                )
            subprocess.run(
                ["git", "-C", str(KIT_DIR), "fetch", "--tags", "origin"],
                check=True,
            )

    for ref in (ver, f"origin/{ver}"):
        r = subprocess.run(
            ["git", "-C", str(KIT_DIR), "checkout", "-f", ref],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            log(f"[labelu-frontend] 已检出 {ref}")
            return
    raise RuntimeError(f"无法检出 {ver}")


def _sync_managed_extension_scripts(html: str) -> str:
    """已有 EXTENSION_MARKER 时同步主应用受管 script，移除已迁入 React 的扩展注入。"""
    active_scripts = set(SPA_EXTENSION_SCRIPTS)
    for name in MANAGED_EXTENSION_SCRIPTS:
        if name in active_scripts:
            continue
        html = re.sub(
            rf"[ \t]*<script[^>]+src=[\"']/assets/{re.escape(name)}[\"'][^>]*></script>[ \t]*\r?\n?",
            "",
            html,
            flags=re.IGNORECASE,
        )
    for name in SPA_EXTENSION_SCRIPTS:
        tag = f'<script defer src="/assets/{name}"></script>'
        if tag in html or not (EXTENSIONS_ASSETS / name).is_file():
            continue
        html = re.sub(
            EXTENSION_MARKER,
            f"{EXTENSION_MARKER}\n  {tag}",
            html,
            count=1,
            flags=re.IGNORECASE,
        )
    return html


def sync_extensions_to_kit(log: Callable[..., None] = print) -> None:
    if not kit_is_cloned():
        raise FileNotFoundError(f"未找到 {KIT_DIR}，请执行: labelu frontend-clone")
    KIT_PUBLIC_ASSETS.mkdir(parents=True, exist_ok=True)
    for name in EXTENSION_ASSET_FILES:
        src = EXTENSIONS_ASSETS / name
        if src.is_file():
            shutil.copy2(src, KIT_PUBLIC_ASSETS / name)
    if not KIT_INDEX_HTML.is_file():
        raise FileNotFoundError(KIT_INDEX_HTML)
    html = KIT_INDEX_HTML.read_text(encoding="utf-8")
    html = re.sub(
        r'\s*<script[^>]*analyze-wiz[^>]*></script>\s*',
        "\n",
        html,
        flags=re.IGNORECASE,
    )
    if EXTENSION_MARKER in html:
        html = _sync_managed_extension_scripts(html)
        KIT_INDEX_HTML.write_text(html, encoding="utf-8")
        kit_public = KIT_DIR / "apps" / "frontend" / "public"
        if kit_public.is_dir():
            write_runtime_version_scripts(kit_public, log=log)
        log("[labelu-frontend] 已同步 extensions -> Kit public（主应用不注入扩展脚本）")
        return
    tags = "\n".join(
        f'  <script defer src="/assets/{name}"></script>'
        for name in SPA_EXTENSION_SCRIPTS
        if (EXTENSIONS_ASSETS / name).is_file()
    )
    if tags:
        block = f"{EXTENSION_MARKER}\n{tags}\n"
        html = re.sub(r"</head>", f"{block}</head>", html, count=1, flags=re.IGNORECASE)
    KIT_INDEX_HTML.write_text(html, encoding="utf-8")
    kit_public = KIT_DIR / "apps" / "frontend" / "public"
    if kit_public.is_dir():
        write_runtime_version_scripts(kit_public, log=log)
    log("[labelu-frontend] 已同步 extensions -> Kit public & index.html")


def sync_extensions_to_statics(log: Callable[..., None] = print) -> None:
    """生产构建后：把自研资源写入 statics（与 Kit dist 并存）。"""
    STATICS_DIR.mkdir(parents=True, exist_ok=True)
    assets_dst = STATICS_DIR / "assets"
    assets_dst.mkdir(parents=True, exist_ok=True)
    for name in EXTENSION_ASSET_FILES:
        src = EXTENSIONS_ASSETS / name
        if src.is_file():
            shutil.copy2(src, assets_dst / name)
    for _route, fname in EXTENSION_PAGE_FILES.items():
        src = EXTENSIONS_PAGES / fname
        if src.is_file():
            shutil.copy2(src, STATICS_DIR / fname)
    log("[labelu-frontend] 已同步 extensions -> statics")


def _frontend_build_env() -> dict[str, str]:
    env = dict(os.environ)
    env["HUSKY"] = "0"
    env.setdefault("CI", "true")
    env.setdefault("COREPACK_ENABLE_DOWNLOAD_PROMPT", "0")
    # Keep the SPA feature flag aligned with the backend runtime setting while
    # keeping VITE_* out of the backend Settings environment.
    if "VITE_SAMPLE_LOCKING_ENABLED" not in env:
        from labelu.internal.common.config import settings

        env["VITE_SAMPLE_LOCKING_ENABLED"] = (
            "1" if settings.SAMPLE_LOCKING_ENABLED else "0"
        )
    return env


def _pnpm_major_version() -> int:
    if shutil.which("pnpm") is None:
        return 0
    proc = subprocess.run(
        ["pnpm", "--version"],
        capture_output=True,
        text=True,
        shell=os.name == "nt",
    )
    ver = (proc.stdout or "").strip().split(".", 1)[0]
    return int(ver) if ver.isdigit() else 0


def ensure_pnpm_cli(log: Callable[..., None] = print) -> None:
    """锁定 pnpm 9.x，避免 corepack 默认升到 11（Node 20 无法运行）。"""
    _prefer_windows_npm_bin()
    if shutil.which("corepack"):
        subprocess.run(
            ["corepack", "enable"],
            capture_output=True,
            shell=os.name == "nt",
        )
        subprocess.run(
            ["corepack", "prepare", f"pnpm@{PNPM_VERSION}", "--activate"],
            check=True,
            shell=os.name == "nt",
        )
    if shutil.which("pnpm") is None and shutil.which("npm"):
        subprocess.run(
            ["npm", "install", "-g", f"pnpm@{PNPM_VERSION}"],
            check=True,
            shell=os.name == "nt",
        )
    if shutil.which("pnpm") is None:
        raise RuntimeError(
            f"需要 pnpm {PNPM_VERSION}: corepack enable && "
            f"corepack prepare pnpm@{PNPM_VERSION} --activate"
        )
    if _pnpm_major_version() >= 10:
        log(
            f"[labelu-frontend] 警告: 当前 pnpm 与 Node 20 不兼容，"
            f"降级至 {PNPM_VERSION}…"
        )
        if shutil.which("npm"):
            subprocess.run(
                ["npm", "install", "-g", f"pnpm@{PNPM_VERSION}"],
                check=True,
                shell=os.name == "nt",
            )
        if shutil.which("corepack"):
            subprocess.run(
                ["corepack", "prepare", f"pnpm@{PNPM_VERSION}", "--activate"],
                check=True,
                shell=os.name == "nt",
            )
        if _pnpm_major_version() >= 10:
            raise RuntimeError(
                f"pnpm 版本仍 ≥ 10，请手动: npm install -g pnpm@{PNPM_VERSION}"
            )


def ensure_pnpm_deps(log: Callable[..., None] = print) -> None:
    if not kit_is_cloned():
        raise FileNotFoundError(f"未找到 {KIT_DIR}")
    ensure_pnpm_cli(log=log)
    log("[labelu-frontend] pnpm install …")
    # CI=true 时 pnpm 默认 frozen；服务器可能残留旧 lockfile，与 overrides 不一致会失败
    subprocess.run(
        ["pnpm", "install", "--no-frozen-lockfile"],
        cwd=str(KIT_DIR),
        check=True,
        env=_frontend_build_env(),
        shell=os.name == "nt",
    )


def build_kit_to_statics(log: Callable[..., None] = print) -> None:
    ensure_pnpm_deps(log=log)
    sync_extensions_to_kit(log=log)
    ensure_pnpm_cli(log=log)
    log("[labelu-frontend] pnpm run build:frontend …")
    subprocess.run(
        ["pnpm", "run", "build:frontend"],
        cwd=str(KIT_DIR),
        check=True,
        env=_frontend_build_env(),
        shell=os.name == "nt",
    )
    dist = KIT_DIR / "apps" / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        raise RuntimeError(f"构建失败，缺少 {dist}/index.html")
    if STATICS_DIR.exists():
        for item in STATICS_DIR.iterdir():
            if item.name in (".gitkeep", "README.md"):
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    shutil.copytree(dist, STATICS_DIR, dirs_exist_ok=True)
    sync_extensions_to_statics(log=log)
    write_runtime_version_scripts(STATICS_DIR, log=log)
    _inject_extension_scripts_into_statics_index()
    compressed = compress_static_assets(STATICS_DIR)
    log(f"[labelu-frontend] 已生成 gzip 静态资源: {len(compressed)}")
    log(f"[labelu-frontend] 构建产物已写入 {STATICS_DIR}")


def _inject_extension_scripts_into_statics_index() -> None:
    index = STATICS_DIR / "index.html"
    if not index.is_file():
        return
    html = index.read_text(encoding="utf-8")
    if EXTENSION_MARKER in html:
        html = _sync_managed_extension_scripts(html)
        index.write_text(html, encoding="utf-8")
        return
    tags = "\n".join(
        f'  <script defer src="/assets/{name}"></script>'
        for name in SPA_EXTENSION_SCRIPTS
        if (EXTENSIONS_ASSETS / name).is_file()
    )
    if not tags:
        return
    index.write_text(
        re.sub(r"</head>", f"{EXTENSION_MARKER}\n{tags}\n</head>", html, count=1, flags=re.IGNORECASE),
        encoding="utf-8",
    )


def _terminate_vite() -> None:
    global _vite_proc, _vite_log_handle
    if _vite_proc is None:
        if _vite_log_handle is not None:
            _vite_log_handle.close()
            _vite_log_handle = None
        return
    if _vite_proc.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(_vite_proc.pid)],
                capture_output=True,
            )
        else:
            try:
                os.killpg(os.getpgid(_vite_proc.pid), signal.SIGTERM)
            except (ProcessLookupError, OSError):
                _vite_proc.terminate()
    _vite_proc = None
    if _vite_log_handle is not None:
        _vite_log_handle.close()
        _vite_log_handle = None


def start_vite_dev(
    frontend_port: int = 3004,
    backend_port: Optional[int] = None,
    skip_install: bool = False,
    log: Callable[..., None] = print,
) -> subprocess.Popen:
    global _vite_proc, _vite_log_handle, _frontend_port
    _frontend_port = frontend_port
    if _vite_proc is not None and _vite_proc.poll() is None:
        return _vite_proc
    if not skip_install:
        ensure_pnpm_deps(log=log)
    sync_extensions_to_kit(log=log)
    log(f"[labelu-frontend] Vite 开发服务 (内部) :{frontend_port}")
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    vite_env = os.environ.copy()
    vite_env["LABELU_VITE_PORT"] = str(frontend_port)
    hmr_client = backend_port or int(
        os.environ.get("LABELU_VITE_HMR_CLIENT_PORT", str(frontend_port))
    )
    vite_env["LABELU_VITE_HMR_CLIENT_PORT"] = str(hmr_client)
    if not vite_env.get("VITE_BACKEND_PORT"):
        vite_env["VITE_BACKEND_PORT"] = os.environ.get("VITE_BACKEND_PORT", "8000")
    popen_kwargs = {
        "cwd": str(KIT_DIR),
        "env": vite_env,
        "shell": os.name == "nt",
        "creationflags": creationflags,
    }
    if os.environ.get("LABELU_LOCAL_LAUNCHER") == "1":
        runtime_log_dir = REPO_ROOT / ".tmp" / "local_runtime"
        runtime_log_dir.mkdir(parents=True, exist_ok=True)
        _vite_log_handle = (runtime_log_dir / "vite.log").open(
            "w", encoding="utf-8", buffering=1
        )
        popen_kwargs["stdout"] = _vite_log_handle
        popen_kwargs["stderr"] = subprocess.STDOUT
    try:
        _vite_proc = subprocess.Popen(
            ["pnpm", "--filter", "@labelu/frontend", "exec", "vite", "--port", str(frontend_port)],
            **popen_kwargs,
        )
    except Exception:
        if _vite_log_handle is not None:
            _vite_log_handle.close()
            _vite_log_handle = None
        raise
    atexit.register(_terminate_vite)
    _wait_vite_ready(frontend_port, log=log)
    _warm_vite_dev_bundle(frontend_port, log=log)
    return _vite_proc


def _vite_probe_ok(port: int, path: str = "/@vite/client") -> bool:
    """Vite 根路径 / 常返回 404，须探测 /@vite/client 等真实端点。"""
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status < 500
    except urllib.error.HTTPError as exc:
        return exc.code < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _wait_vite_ready(port: int, timeout: float = 45.0, log: Callable[..., None] = print) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _vite_probe_ok(port):
            log(f"[labelu-frontend] Vite 就绪: http://127.0.0.1:{port}/@vite/client")
            return
        time.sleep(0.35)
    log(f"[labelu-frontend] 警告: {timeout}s 内未确认 Vite 就绪，继续启动后端…")


def _warm_vite_dev_bundle(port: int, log: Callable[..., None] = print) -> None:
    """首次访问会触发 optimizeDeps；在启动阶段预热，缩短浏览器白屏时间。"""
    if os.environ.get("LABELU_SKIP_VITE_WARMUP", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return
    import urllib.error
    import urllib.request

    targets = (
        f"http://127.0.0.1:{port}/src/index.tsx",
        f"http://127.0.0.1:{port}/src/App.tsx",
    )
    log("[labelu-frontend] 预热前端依赖（仅首次较慢，约 15–40 秒）…")
    started = time.time()
    for url in targets:
        try:
            urllib.request.urlopen(url, timeout=120)
        except urllib.error.HTTPError as exc:
            if exc.code >= 500:
                log(f"[labelu-frontend] 预热警告 {url}: HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log(f"[labelu-frontend] 预热警告 {url}: {exc}")
    log(f"[labelu-frontend] 预热完成 ({time.time() - started:.1f}s)")


def get_vite_port() -> int:
    return _frontend_port


def prepare_integrated_frontend(
    frontend_port: int = 3004,
    backend_port: Optional[int] = None,
    auto_clone: bool = False,
    skip_install: bool = False,
    log: Callable[..., None] = print,
) -> bool:
    """返回 True 表示已启用 Vite 源码模式；False 表示回退 statics。"""
    if not integrated_frontend_enabled():
        return False
    if not kit_source_present():
        if not auto_clone:
            log(
                "[labelu-frontend] 缺少 frontend/labelU-Kit 源码，回退 statics；"
                "生产由 deploy 自动 frontend-build"
            )
            return False
        log("[labelu-frontend] 正在克隆 labelU-Kit …")
        clone_kit(log=log)
    if not skip_install:
        ensure_pnpm_deps(log=log)
    start_vite_dev(
        frontend_port=frontend_port,
        backend_port=backend_port,
        skip_install=skip_install,
        log=log,
    )
    return True


def print_startup_banner(
    host: str,
    backend_port: int,
    *,
    vite_enabled: bool,
    frontend_port: int,
    statics_fallback: bool,
    log: Callable[..., None] = print,
) -> None:
    display = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    log("")
    log("========== LabelU ==========")
    if vite_enabled:
        log(f"  访问入口:          http://{display}:{backend_port}/  (仅此端口)")
        log(f"  Vite 热更新:       127.0.0.1:{frontend_port} (进程内部，勿直连)")
        log("  二次开发:")
        log(f"    - Kit 主站:     frontend/labelU-Kit/apps/frontend/src/")
        log(f"    - 自研扩展:     frontend/extensions/")
    elif statics_fallback and statics_has_built_spa():
        log(f"  访问入口:        http://{display}:{backend_port}/  (statics 构建产物)")
    else:
        log(f"  后端 API:        http://{display}:{backend_port}/api/v1/")
        log("  主站未就绪: git pull 应含 frontend/labelU-Kit，或 labelu frontend-build")
    for route in EXTENSION_PAGE_FILES:
        log(f"  扩展页:          http://{display}:{backend_port}{route}")
    log("============================")
    log("")


def run_labelu_server(
    host: str,
    port: int,
    media_host: Optional[str],
    *,
    frontend_port: int = 3004,
    skip_install: bool = False,
    jwt_from_env: bool = False,
) -> None:
    import uvicorn

    from labelu.internal.common.config import settings
    from labelu.internal.common.server_runtime import configure_server_from_cli

    log = print
    os.environ["VITE_BACKEND_PORT"] = str(port)
    os.environ["LABELU_VITE_HMR_CLIENT_PORT"] = str(port)
    vite_enabled = prepare_integrated_frontend(
        frontend_port=frontend_port,
        backend_port=port,
        skip_install=skip_install,
        log=log,
    )
    statics_fallback = not vite_enabled and statics_has_built_spa()
    os.environ["LABELU_VITE_ENABLED"] = "1" if vite_enabled else "0"
    os.environ["LABELU_VITE_PORT"] = str(frontend_port)

    print_startup_banner(
        host,
        port,
        vite_enabled=vite_enabled,
        frontend_port=frontend_port,
        statics_fallback=statics_fallback,
        log=log,
    )

    configure_server_from_cli(host, port, media_host, jwt_from_env=jwt_from_env)
    uvicorn.run(
        app="labelu.main:app",
        host=settings.HOST,
        port=int(settings.PORT),
        ws="websockets",
        factory=False,
    )
