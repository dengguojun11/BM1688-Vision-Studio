import json
import os
import asyncio
from pathlib import Path
from typing import Any, Optional
from contextlib import asynccontextmanager

from loguru import logger
import click
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from labelu.internal.common.labelu_frontend_integration import (
    EXTENSION_ASSET_FILES,
    KIT_DIR,
    STATICS_DIR,
    asset_media_type,
    extension_asset_path,
    extension_page_path,
    gzip_static_asset_path,
    hashed_static_asset_cache_control,
    integrated_frontend_enabled,
    kit_source_present,
    statics_built_asset_path,
    statics_has_built_spa,
)

from labelu.internal.adapter.routers import add_router
from labelu.internal.adapter.ws import add_ws_router
from labelu.internal.middleware import add_middleware
from labelu.internal.common.logger import init_logging
from labelu.internal.common.boot_diagnostics import set_jwt_secret_was_from_env
from labelu.internal.common.bootstrap_admin import ensure_default_admin
from labelu.internal.common.service_account import ensure_open_system_user
from labelu.internal.common.db import SessionLocal
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import add_exception_handler
from labelu.internal.common.openapi_docs import (
    add_chinese_api_docs,
    configure_chinese_openapi,
)
from labelu.internal.common.local_dev_session import (
    dev_session_headers,
    is_force_no_cache,
    maybe_inject_local_dev_reload,
)
from labelu.alembic_labelu.run_migrate import run_db_migrations
from labelu.scripts.migrate_to_mysql import migrate_to_mysql

from .version import version as labelu_version

description = """
7GAI Evolution Platform backend (LabelU compatibility API).

## Users

You will be able to:

* **Signup**
* **Login**
* **Logout**.

## Tasks

You will be able to:

* **CRUD**

## Task attachment

You will be able to:

* **upload attachment**
* **download attachment**
* **delete attachment**

## Task sample

You will be able to:

* **list sample**
* **create sample**
* **get sample**
* **update sample**
* **export sample**
"""


def _enforce_dev_database_safety() -> None:
    """Prevent local commands from silently opening a shared MySQL database."""
    environment = (settings.APP_ENV or "development").strip().lower()
    if environment in {"prod", "production"}:
        return
    database_url = (settings.DATABASE_URL or "").strip().lower()
    if database_url.startswith("mysql") and not settings.LABELU_ALLOW_DEV_MYSQL:
        raise click.ClickException(
            "开发模式检测到 MySQL DATABASE_URL，已拒绝启动；"
            "如确需联调共享库，请显式设置 LABELU_ALLOW_DEV_MYSQL=true，"
            "并确认已完成数据风险审批。"
        )


tags_metadata = [
    {
        "name": "users",
        "description": "Operations with users.",
    },
    {
        "name": "tasks",
        "description": "Task management.",
    },
    {
        "name": "attachments",
        "description": "Task attachment management.",
    },
    {
        "name": "samples",
        "description": "Task sample management.",
    },
]

@asynccontextmanager
async def lifespan(app):
    # Apply the same guard for direct Uvicorn/ASGI startup as for the Click
    # commands. The application must not migrate or bootstrap an inherited
    # shared MySQL database in a development environment.
    _enforce_dev_database_safety()
    # 直接通过 uvicorn/ASGI 启动时不会经过 `labelu dev` 命令。开发环境仍
    # 必须自动消费已受理的专家回流包，否则接口会返回 PENDING，但进化中心
    # 永远没有后续状态。生产环境继续使用独立 Worker，不在 Web 进程内消费。
    if (settings.APP_ENV or "development").strip().lower() not in {"prod", "production"}:
        settings.EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED = True
    # Fail closed before any migration or bootstrap can touch a production
    # database. CLI startup also performs this check, but ASGI lifespan may be
    # entered directly by uvicorn, TestClient, or another process runner.
    from labelu.internal.common.production_security import enforce_production_security

    enforce_production_security(jwt_from_env=_jwt_secret_from_env)
    # Schema migration must happen before any bootstrap row is created. Otherwise
    # the SQLite-to-MySQL migrator sees the bootstrap admin and skips real data.
    run_db_migrations()
    if settings.need_migration_to_mysql:
        logger.info("Migrating database to MySQL")
        migrate_to_mysql()
    ensure_default_admin()
    from labelu.internal.application.service.provider_runtime_bootstrap import (
        register_configured_provider_adapters,
    )

    provider_bootstrap = register_configured_provider_adapters()
    if provider_bootstrap.enabled:
        logger.info(
            "Provider runtime bootstrap: registered={} skipped={}",
            provider_bootstrap.registered,
            provider_bootstrap.skipped,
        )
    # Test clients share an isolated SQLite fixture and invoke watchdog cycles
    # explicitly.  A single switch keeps daemon schedulers out of the test
    # lifespan while leaving every scheduler enabled by default in production.
    disable_background_watchdogs = (
        os.environ.get("LABELU_DISABLE_BACKGROUND_WATCHDOGS", "")
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
    )
    db = SessionLocal()
    try:
        ensure_open_system_user(db)
    finally:
        db.close()
    # Board Agent enrollment/recovery is an edge control-plane concern and
    # must not depend on the legacy watchdog switch or YOLO training.  The
    # registry scheduler keeps the compatibility SSH environment scan gated by
    # both YOLO and LABELU_DISABLE_BACKGROUND_WATCHDOGS, so recovery remains
    # active even in production deployments that disable legacy watchdogs.
    from labelu.internal.application.service.edge_device_registry import (
        schedule_system_environment_checks,
    )

    schedule_system_environment_checks()
    if settings.YOLO_TRAIN_ENABLED and not disable_background_watchdogs:
        from labelu.internal.application.service.yolo_train import (
            prepare_cuda_env_for_yolo_train,
            schedule_completed_post_train_recovery,
            yolo_train_cuda_mismatch_hint,
        )
        from labelu.internal.common.bm1688_deploy_bootstrap import ensure_bm1688_compiler_ready

        prepare_cuda_env_for_yolo_train()
        cuda_hint = yolo_train_cuda_mismatch_hint()
        if cuda_hint:
            logger.warning("YOLO train CUDA: {}", cuda_hint)
        ensure_bm1688_compiler_ready()
        try:
            from labelu.internal.common.bm1688_deploy_bootstrap import (
                ensure_bm1688_docker_deploy,
            )

            ok, note = ensure_bm1688_docker_deploy()
            if ok:
                logger.info("BM1688 Docker: {}", note[:200])
            else:
                logger.warning("BM1688 Docker 未就绪: {}", note[:500])
        except Exception as exc:
            logger.warning("BM1688 Docker bootstrap skipped: {}", exc)
        if settings.YOLO_TRAIN_ENABLED:
            from labelu.internal.application.service.yolo_train_queue import (
                schedule_train_queue_watchdog,
            )

            schedule_train_queue_watchdog()
        schedule_completed_post_train_recovery()
        from labelu.internal.application.service.yolo_quality_evidence_auto_recovery import (
            schedule_quality_evidence_auto_recovery,
        )

        schedule_quality_evidence_auto_recovery()
        if settings.YOLO_AUTO_RESUME_TRAIN_ENABLED:
            from labelu.internal.application.service.yolo_train_auto_resume import (
                schedule_train_auto_resume_watchdog,
            )

            schedule_train_auto_resume_watchdog()
        try:
            from labelu.internal.common.yolo_train_deploy import (
                resume_deploy_paused_jobs,
            )

            n = resume_deploy_paused_jobs(delay_seconds=3.0)
            if n:
                logger.info("deploy auto-resume: restarted {} YOLO train job(s)", n)
        except Exception as exc:
            logger.warning("deploy auto-resume skipped: {}", exc)
        if settings.YOLO_BM1688_AUTO_RESUME_ENABLED:
            from labelu.internal.application.service.yolo_bm1688_export_auto_resume import (
                schedule_bm1688_export_auto_resume_watchdog,
            )

            schedule_bm1688_export_auto_resume_watchdog()
        if settings.YOLO_EDGE_VALIDATION_AUTO_RESUME_ENABLED:
            from labelu.internal.application.service.yolo_edge_validation_auto_resume import (
                schedule_edge_validation_auto_resume_watchdog,
            )

            schedule_edge_validation_auto_resume_watchdog()
        if settings.STRATEGY_EVOLUTION_AUTO_ADVANCE_ENABLED:
            from labelu.internal.application.service.strategy_evolution import (
                schedule_strategy_evolution_watchdog,
            )

            schedule_strategy_evolution_watchdog()
        if settings.YOLO_EDGE_VALIDATION_EXTERNAL_CLEANUP_ENABLED:
            from labelu.internal.application.service.yolo_edge_validation_external_cleanup import (
                schedule_edge_validation_external_cleanup_watchdog,
            )

            schedule_edge_validation_external_cleanup_watchdog()
        from labelu.internal.application.service.edge_remote_maintenance import (
            schedule_edge_remote_maintenance_watchdog,
        )

        schedule_edge_remote_maintenance_watchdog()
    local_experiment_worker_task = None
    if (
        settings.EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED
        and not disable_background_watchdogs
    ):
        from labelu.internal.application.service.experiment_dataset_import_worker import (
            run_import_worker_once,
        )

        async def _run_local_experiment_worker() -> None:
            logger.info("本地专家 S3 回流 Worker 已启动，扫描间隔={}秒", settings.EXPERIMENT_DATASET_WORKER_INTERVAL_SECONDS)
            while True:
                try:
                    result = await asyncio.to_thread(
                        run_import_worker_once,
                        limit=settings.EXPERIMENT_DATASET_WORKER_BATCH_SIZE,
                    )
                    if result.executed or result.failed or result.candidates:
                        logger.info(
                            "本地专家 S3 回流 Worker 扫描完成 claimed={} executed={} failed={}",
                            result.claimed,
                            result.executed,
                            result.failed,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("本地专家 S3 回流 Worker 扫描失败: {}", exc)
                await asyncio.sleep(settings.EXPERIMENT_DATASET_WORKER_INTERVAL_SECONDS)

        local_experiment_worker_task = asyncio.create_task(_run_local_experiment_worker())
    if not disable_background_watchdogs:
        from labelu.internal.application.service.auto_training_watchdog import (
            schedule_auto_training_watchdog,
        )

        schedule_auto_training_watchdog()
        from labelu.internal.application.service.auto_sample_preannotate import (
            schedule_auto_preannotate_watchdog,
        )

        schedule_auto_preannotate_watchdog()
    try:
        yield
    finally:
        if local_experiment_worker_task is not None:
            local_experiment_worker_task.cancel()
            await asyncio.gather(local_experiment_worker_task, return_exceptions=True)


app = FastAPI(
    lifespan=lifespan,
    title="7GAI Evolution Platform",
    description=description,
    version=labelu_version,
    terms_of_service="",
    contact={
        "name": "7GAI Evolution Platform",
        "url": "http://7gai.example.com/contact/",
        "email": "support@7gai.example.com",
    },
    license_info={
        "name": "Apache 2.0",
        "url": "https://www.apache.org/licenses/LICENSE-2.0.html",
    },
    # The platform owns the docs pages so /docs and /api-docs cannot drift
    # into separate OpenAPI documents during the LabelU compatibility period.
    docs_url=None,
    redoc_url=None,
    openapi_tags=tags_metadata,
)

_jwt_secret_from_env = bool((settings.PASSWORD_SECRET_KEY or "").strip())
set_jwt_secret_was_from_env(_jwt_secret_from_env)
if not settings.PASSWORD_SECRET_KEY:
    import secrets
    settings.PASSWORD_SECRET_KEY = secrets.token_hex(32)
    logger.warning("PASSWORD_SECRET_KEY not set, using auto-generated key. Set it in .env for production.")

init_logging()
add_exception_handler(app=app)
add_router(app=app)
add_ws_router(app=app)
configure_chinese_openapi(app)
add_chinese_api_docs(app)
add_middleware(app=app)

class NoCacheStaticFiles(StaticFiles):
    def __init__(self, *args: Any, **kwargs: Any):
        self.cachecontrol = "max-age=0, no-cache, no-store, must-revalidate"
        self.pragma = "no-cache"
        self.expires = "0"
        super().__init__(*args, **kwargs)

    async def get_response(self, path: str, scope: Any) -> Response:
        """Serve the SPA entry for extensionless client-side routes.

        Starlette's ``html=True`` only resolves directory indexes. The platform
        uses history-based routes such as ``/training`` and ``/models``; in
        static mode those paths must fall back to ``index.html`` while API and
        asset paths keep their normal 404 behaviour.
        """
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            request_path = str(scope.get("path") or "")
            extensionless = "." not in Path(path).name
            backend_path = request_path.startswith(("/api/", "/internal/", "/ws/"))
            if exc.status_code == 404 and extensionless and not backend_path:
                return await super().get_response("index.html", scope)
            raise

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        resp = super().file_response(*args, **kwargs)
        
        # No cache for html files
        if resp.media_type == "text/html":
            resp.headers.setdefault("Cache-Control", self.cachecontrol)
            resp.headers.setdefault("Pragma", self.pragma)
            resp.headers.setdefault("Expires", self.expires)
            
        return resp


def _extension_page_response(
    route: str,
    *,
    headers: Optional[dict[str, str]] = None,
) -> Response:
    page_path = extension_page_path(route)
    body = page_path.read_text(encoding="utf-8")
    body = maybe_inject_local_dev_reload(body)
    response_headers = dev_session_headers()
    if headers:
        response_headers.update(headers)
    return HTMLResponse(
        content=body,
        media_type="text/html; charset=utf-8",
        headers=response_headers,
    )


@app.get("/annotation-settings")
async def annotation_settings_page():
    """Compatibility entry; governance is now native to the 7GAI shell."""
    return RedirectResponse(url="/admin?view=governance", status_code=307)


@app.get("/yolo-training")
async def yolo_training_page():
    """Legacy YOLO training hub kept for advanced compatibility panels.

    The platform training center at ``/training`` is the canonical entry.  We
    intentionally keep this page available because its recipe, auto-plan and
    model-library panels are still consumed by existing links and workflows.
    """
    return _extension_page_response(
        "/yolo-training",
        headers={
            # RFC 8594-style deprecation signal without a sunset date: the
            # compatibility page remains supported until the advanced panels
            # are migrated, so callers must not infer an immediate shutdown.
            "Deprecation": "true",
            "Link": '</training>; rel="canonical"',
            "X-7GAI-Canonical-Route": "/training",
        },
    )


@app.get("/yolo-delivery-hub", include_in_schema=False)
async def yolo_delivery_hub_removed():
    """模型交付中心已下线，保留路由仅返回 404 避免旧链接落入 SPA 兜底。"""
    return JSONResponse(
        status_code=404,
        content={"detail": "模型交付中心已移除，请使用「模型编译」或「模型发布」"},
    )


@app.get("/ai-labeling-settings")
async def ai_labeling_settings_page():
    """AI 标注：提示词说明、附加提示与视觉链路（密钥仍在 .env）。"""
    return _extension_page_response("/ai-labeling-settings")


@app.get("/yolo-eval-prompts")
async def yolo_eval_prompts_page():
    """YOLO 训练完成后的多模型评估提示词（可编辑，存数据目录 JSON）。"""
    return _extension_page_response("/yolo-eval-prompts")


@app.get("/yolo-eval-reports-hub")
async def yolo_eval_reports_hub_page():
    """训练作业全链路评估报告阅读与对比中心。"""
    return _extension_page_response("/yolo-eval-reports-hub")


@app.get("/yolo-bm1688-exports")
async def yolo_bm1688_exports_page():
    """训练模型导出 ONNX / BM1688 BModel 列表与下载。"""
    return _extension_page_response("/yolo-bm1688-exports")


@app.get("/edge-device-registry")
async def edge_device_registry_page():
    """边端设备配置：多设备 CRUD、启用切换与连通探测。"""
    return _extension_page_response("/edge-device-registry")


@app.get("/aicm-publish-environments")
async def aicm_publish_environments_page():
    """AICM 发布环境配置：多环境 CRUD 与当前发布环境切换。"""
    return _extension_page_response("/aicm-publish-environments")


@app.get("/strategy-integration-s3-config")
async def strategy_integration_s3_config_page():
    """专家回流 S3/MinIO 服务地址配置。"""
    return _extension_page_response("/strategy-integration-s3-config")


@app.get("/third-party-calls")
async def third_party_calls_page():
    """第三方入站调用与异步业务链路只读工作台。"""
    return _extension_page_response("/third-party-calls")


@app.get("/yolo-preannotate")
async def yolo_preannotate_page():
    """选择 YOLO 权重对指定图像任务批量写入预标注。"""
    return _extension_page_response("/yolo-preannotate")


@app.get("/yolo-model-validate")
async def yolo_model_validate_page():
    """训练模型单图验证页：上传一张图片并用指定 best.pt 做即时识别。"""
    return _extension_page_response("/yolo-model-validate")


@app.get("/yolo-model-workflow")
async def yolo_model_workflow_page():
    """模型工作流已下线，历史地址统一返回训练页。"""
    return RedirectResponse(url="/training", status_code=307)


@app.get("/annotation-stats")
async def annotation_stats_page():
    """标注统计大屏（任务超管）。"""
    return _extension_page_response("/annotation-stats")


@app.get("/yolo-model-publish")
async def yolo_model_publish_page():
    """将本系统训练完成且已编译 bmodel 的模型发布到 AICM AI 控制后台。"""
    return _extension_page_response("/yolo-model-publish")


@app.get("/yolo-model-catalog")
async def yolo_model_catalog_page():
    """已发布最终模型包的目录、兼容标识与下载管理。"""
    return _extension_page_response("/yolo-model-catalog")


@app.get("/yolo-quality-policies")
async def yolo_quality_policies_page():
    """模型质量标准管理与 AI 推荐草稿。"""
    return _extension_page_response("/yolo-quality-policies")


@app.get("/yolo-gt-pred-compare")
async def yolo_gt_pred_compare_page():
    """训练作业逐图标注与预测对比（独立页，支持放大查看）。"""
    return _extension_page_response("/yolo-gt-pred-compare")


def _favicon_path() -> Optional[Path]:
    for candidate in (
        KIT_DIR / "apps" / "frontend" / "public" / "favicon.svg",
        Path(__file__).resolve().parent / "internal" / "statics" / "favicon.svg",
    ):
        if candidate.is_file():
            return candidate
    return None


@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    path = _favicon_path()
    if path is None:
        raise HTTPException(status_code=404, detail="favicon not found")
    return FileResponse(path, media_type="image/svg+xml")


@app.get("/favicon.svg", include_in_schema=False)
async def favicon_svg():
    path = _favicon_path()
    if path is None:
        raise HTTPException(status_code=404, detail="favicon not found")
    return FileResponse(path, media_type="image/svg+xml")


@app.get("/brand/7g-logo-purple.png", include_in_schema=False)
@app.get("/favicon.png", include_in_schema=False)
@app.get("/logo192.png", include_in_schema=False)
@app.get("/logo512.png", include_in_schema=False)
@app.get("/manifest.json", include_in_schema=False)
async def frontend_brand_asset(request: Request):
    """Serve the fixed public brand assets before the SPA history fallback."""
    relative_path = request.url.path.lstrip("/")
    for root in (
        KIT_DIR / "apps" / "frontend" / "public",
        Path(__file__).resolve().parent / "internal" / "statics",
    ):
        path = root / relative_path
        if path.is_file():
            media_type = (
                "application/manifest+json"
                if relative_path == "manifest.json"
                else "image/png"
            )
            return FileResponse(
                path, media_type=media_type, headers={"Cache-Control": "no-cache"}
            )
    raise HTTPException(status_code=404, detail="brand asset not found")


def _runtime_version_script_path(name: str) -> Path:
    """Return a generated runtime version script from the local statics bundle.

    In integrated development mode Vite can be temporarily unavailable while
    the backend remains reachable on port 8003.  These two files are loaded by
    the SPA shell and must still be served as JavaScript; otherwise the generic
    history-route fallback returns ``index.html`` and the browser reports
    ``Unexpected token '<'`` before the application has finished loading.
    """

    path = Path(__file__).resolve().parent / "internal" / "statics" / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not Found")
    return path


@app.get("/frontend_version.js", include_in_schema=False)
async def frontend_version_script():
    return FileResponse(
        _runtime_version_script_path("frontend_version.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/backend_version.js", include_in_schema=False)
async def backend_version_script():
    return FileResponse(
        _runtime_version_script_path("backend_version.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/assets/{asset_name}")
async def labelu_extension_asset(asset_name: str, request: Request):
    """/assets/*：优先 extensions 自研资源，否则回退 statics 内 Vite 构建产物。"""
    if ".." in asset_name or "/" in asset_name or "\\" in asset_name:
        raise HTTPException(status_code=404, detail="Not Found")
    if asset_name in EXTENSION_ASSET_FILES:
        path = extension_asset_path(asset_name)
        if path is not None:
            headers = (
                dev_session_headers()
                if is_force_no_cache()
                else {"Cache-Control": "no-cache"}
            )
            return FileResponse(
                path,
                media_type=asset_media_type(asset_name),
                headers=headers,
            )
    built = statics_built_asset_path(asset_name)
    if built is not None:
        headers = {"Cache-Control": hashed_static_asset_cache_control(asset_name)}
        accept_encoding = request.headers.get("Accept-Encoding", "")
        if "gzip" in accept_encoding.lower():
            gz_path = gzip_static_asset_path(built)
            if gz_path is not None:
                headers["Content-Encoding"] = "gzip"
                headers["Vary"] = "Accept-Encoding"
                return FileResponse(gz_path, media_type=asset_media_type(asset_name), headers=headers)
        return FileResponse(built, media_type=asset_media_type(asset_name), headers=headers)
    raise HTTPException(status_code=404, detail="Not Found")


@app.get("/", include_in_schema=False)
async def frontend_root_fallback():
    """Return the SPA entry when the development Vite proxy is unavailable."""
    index = STATICS_DIR / "index.html"
    if index.is_file():
        return FileResponse(index, media_type="text/html")
    raise HTTPException(status_code=404, detail="Not Found")


@app.get("/{spa_path:path}", include_in_schema=False)
async def frontend_spa_fallback(spa_path: str):
    """Keep history-based frontend routes usable after a Vite restart/failure."""
    if spa_path.startswith(("api/", "internal/", "ws/", "assets/", "src/", "@vite/", "@react-refresh", "node_modules/")):
        raise HTTPException(status_code=404, detail="Not Found")
    index = STATICS_DIR / "index.html"
    if index.is_file():
        return FileResponse(index, media_type="text/html")
    raise HTTPException(status_code=404, detail="Not Found")


def _mount_production_statics() -> None:
    """生产/回退：挂载当前服务配置的静态构建目录。"""
    if os.environ.get("LABELU_VITE_ENABLED", "") == "1":
        return
    if not statics_has_built_spa():
        # Source-mode startup mounts Vite after this module is imported.
        if not (integrated_frontend_enabled() and kit_source_present()):
            logger.warning(
                "未启用 Vite 且 statics 无构建产物；请 labelu frontend-clone 后重启，"
                "或执行: labelu frontend-build"
            )
        return
    app.mount("", NoCacheStaticFiles(directory=str(STATICS_DIR), html=True))


_mount_production_statics()

@click.group(invoke_without_command=True)
@click.option('--host', default='0.0.0.0', help='Server host')
@click.option('--port', default=8000, help='Server port')
@click.option('--frontend-port', default=3004, type=int, help='Vite 开发端口（源码模式）')
@click.option(
    '--static-only',
    is_flag=True,
    help='仅后端 + statics 构建产物，不启动 Vite（生产或离线）',
)
@click.option(
    '--skip-install',
    is_flag=True,
    help='跳过 pnpm install',
)
@click.option(
    '--media-host',
    default=None,
    help=(
        '浏览器可访问的站点根 URL（用于媒体/绝对链接）。'
        '省略时自动设为 http://<host>:<port>（host 为 0.0.0.0 时用 127.0.0.1）。'
        '端口或域名与访问不一致时，易出现上传后预览 404、页面跳到 404。'
    ),
)
@click.pass_context
def cli(
    ctx: click.Context,
    host: str,
    port: int,
    frontend_port: int,
    static_only: bool,
    skip_install: bool,
    media_host: Optional[str],
):
    if ctx.invoked_subcommand is None:
        _enforce_dev_database_safety()
        if static_only:
            os.environ["LABELU_STATIC_ONLY"] = "1"
        from labelu.internal.common.labelu_frontend_integration import run_labelu_server

        run_labelu_server(
            host,
            port,
            media_host,
            frontend_port=frontend_port,
            skip_install=skip_install,
            jwt_from_env=_jwt_secret_from_env,
        )

@cli.command("frontend-clone")
@click.option(
    "--version",
    "kit_version",
    default=None,
    help="labelU-Kit tag/分支，默认与 .VERSION 中 release_version 一致",
)
def frontend_clone(kit_version: Optional[str]):
    """克隆官方 labelU-Kit 到 frontend/labelU-Kit（二次开发用）。"""
    from labelu.internal.common.labelu_frontend_integration import clone_kit

    try:
        clone_kit(version=kit_version)
    except Exception as exc:
        click.echo(f"克隆失败: {exc}", err=True)
        click.echo(
            "可设置代理后重试: set LABELU_KIT_GH_PROXY=https://ghproxy.net/ "
            "(Linux: export LABELU_KIT_GH_PROXY=...)"
        )
        raise SystemExit(1) from exc


@cli.command("dev")
@click.option("--host", default="0.0.0.0", help="后端监听地址")
@click.option("--port", default=8000, type=int, help="后端端口")
@click.option("--frontend-port", default=3004, type=int, help="Vite 前端端口")
@click.option("--static-only", is_flag=True, help="同 labelu --static-only")
@click.option("--skip-install", is_flag=True, help="跳过 pnpm install")
@click.option("--media-host", default=None, help="MEDIA_HOST")
def dev(
    host: str,
    port: int,
    frontend_port: int,
    static_only: bool,
    skip_install: bool,
    media_host: Optional[str],
):
    """开发模式（与直接执行 labelu 相同）。"""
    _enforce_dev_database_safety()
    # 本地开发需要和生产相同地消费已受理的专家 S3 数据包；生产仍由独立
    # systemd Worker 运行，不通过 Web 进程执行导入副作用。
    if (settings.APP_ENV or "development").strip().lower() not in {"prod", "production"}:
        settings.EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED = True
    if static_only:
        os.environ["LABELU_STATIC_ONLY"] = "1"
    from labelu.internal.common.labelu_frontend_integration import run_labelu_server

    run_labelu_server(
        host,
        port,
        media_host,
        frontend_port=frontend_port,
        skip_install=skip_install,
        jwt_from_env=_jwt_secret_from_env,
    )


@cli.command("frontend-build")
def frontend_build():
    """从源码构建主站并写入 labelu/internal/statics（生产部署用）。"""
    from labelu.internal.common.labelu_frontend_integration import build_kit_to_statics

    try:
        build_kit_to_statics()
    except Exception as exc:
        click.echo(f"构建失败: {exc}", err=True)
        raise SystemExit(1) from exc


@cli.command('migrate_to_mysql')
def to_mysql():
    """Migrate database to MySQL"""
    migrate_to_mysql()


@cli.command("doctor")
def doctor():
    """打印配置诊断（密钥脱敏），用于部署与排障。"""
    from labelu.internal.common.boot_diagnostics import print_doctor_report

    print_doctor_report()


@cli.group("dev-log-watch")
def dev_log_watch():
    """开发记录自动 watcher。"""


@dev_log_watch.command("start")
@click.option(
    "--repo-root",
    type=click.Path(path_type=Path, exists=True, file_okay=False, dir_okay=True),
    default=Path.cwd,
    show_default=True,
    help="项目根目录（默认当前目录）",
)
@click.option("--interval-seconds", default=8, type=int, show_default=True, help="轮询间隔")
@click.option("--quiet-seconds", default=20, type=int, show_default=True, help="静默聚合时长")
def dev_log_watch_start(repo_root: Path, interval_seconds: int, quiet_seconds: int):
    """后台启动开发记录 watcher。"""
    from labelu.internal.common.dev_change_watchdog import start_background_watchdog

    result = start_background_watchdog(
        repo_root,
        interval_seconds=interval_seconds,
        quiet_seconds=quiet_seconds,
    )
    click.echo(json.dumps(result, ensure_ascii=False))


@dev_log_watch.command("stop")
@click.option(
    "--repo-root",
    type=click.Path(path_type=Path, exists=True, file_okay=False, dir_okay=True),
    default=Path.cwd,
    show_default=True,
    help="项目根目录（默认当前目录）",
)
def dev_log_watch_stop(repo_root: Path):
    """停止后台开发记录 watcher。"""
    from labelu.internal.common.dev_change_watchdog import stop_background_watchdog

    click.echo(json.dumps(stop_background_watchdog(repo_root), ensure_ascii=False))


@dev_log_watch.command("status")
@click.option(
    "--repo-root",
    type=click.Path(path_type=Path, exists=True, file_okay=False, dir_okay=True),
    default=Path.cwd,
    show_default=True,
    help="项目根目录（默认当前目录）",
)
def dev_log_watch_status(repo_root: Path):
    """查看后台开发记录 watcher 状态。"""
    from labelu.internal.common.dev_change_watchdog import watchdog_status

    click.echo(json.dumps(watchdog_status(repo_root), ensure_ascii=False))


@dev_log_watch.command("once")
@click.option(
    "--repo-root",
    type=click.Path(path_type=Path, exists=True, file_okay=False, dir_okay=True),
    default=Path.cwd,
    show_default=True,
    help="项目根目录（默认当前目录）",
)
def dev_log_watch_once(repo_root: Path):
    """将当前工作区变更立即写入一次开发记录。"""
    from labelu.internal.common.dev_change_watchdog import record_current_changes_once

    click.echo(json.dumps(record_current_changes_once(repo_root), ensure_ascii=False))


@cli.command("verify-sophon")
@click.option(
    "--export-dir",
    type=click.Path(path_type=Path, exists=True, file_okay=False, dir_okay=True),
    help="导出任务目录（含 models/）",
)
@click.option("--export-id", type=int, help="导出记录 ID（在 MEDIA_ROOT 下解析）")
@click.option("--config-only", is_flag=True, help="仅检查编译配置与环境（FAQ §1、§3）")
@click.option("--json", "as_json", is_flag=True, help="JSON 输出")
@click.option("--no-env", is_flag=True, help="跳过环境项，仅验证目录结构")
def verify_sophon(export_dir, export_id, config_only, as_json, no_env):
    """按 sophon-demo FAQ 验证 BM1688 导出产物与编译环境。"""
    from labelu.internal.common.config import settings
    from labelu.internal.common.sophon_faq_verify import (
        SophonVerifyReport,
        print_report,
        verify_compiler_config,
        verify_export_tree,
    )

    if config_only:
        report = SophonVerifyReport()
        verify_compiler_config(report)
        raise SystemExit(print_report(report, as_json=as_json))

    root = export_dir
    if export_id is not None:
        root = (
            Path(settings.MEDIA_ROOT)
            / settings.YOLO_BM1688_EXPORT_SUBDIR
            / str(export_id)
        )
    if root is None:
        raise click.UsageError("请指定 --export-dir、--export-id 或 --config-only")

    report = verify_export_tree(root.resolve(), check_env=not no_env)
    raise SystemExit(print_report(report, as_json=as_json))


@cli.command("bm1688-recompile")
@click.option("--export-id", required=True, type=int, help="yolo_bm1688_export 表主键 ID")
def bm1688_recompile(export_id: int):
    """对已导出 ONNX 的记录补编 bmodel（TPU-MLIR，同步执行至结束）。"""
    from labelu.internal.application.service.yolo_bm1688_export import (
        _run_bm1688_recompile_sync,
        format_recompile_cli_report,
    )

    click.echo(f"开始编译 bmodel export_id={export_id} …")
    summary = _run_bm1688_recompile_sync(export_id)
    click.echo(format_recompile_cli_report(summary))
    status = summary.get("status")
    if not summary.get("found"):
        raise SystemExit(1)
    if status == "COMPLETED" and summary.get("bmodel_files"):
        click.echo("完成。请执行: labelu verify-sophon --export-id " + str(export_id))
        return
    click.echo(
        "编译未成功（status 非 COMPLETED 或缺少 bmodel）。"
        " 若 BASE_DATA_DIR 为 ~/.local/share/labelu，请先: "
        "export LABELU_ENV_FILE=/data/labeIU/labeIuMedia/.env 或在该目录执行 CLI。"
    )
    raise SystemExit(1)


if __name__ == "__main__":
    cli()
