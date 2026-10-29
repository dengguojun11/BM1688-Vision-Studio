"""事件审核模块 - 业务逻辑（对接后海安防老平台）"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote, urljoin

import httpx
from loguru import logger

from labelu.internal.application.response.event_review import (
    EventReviewDetail,
    EventReviewImage,
    EventReviewItem,
    EventReviewProgress,
)
from labelu.internal.common.config import settings

# ---------------------------------------------------------------------------
# 老平台配置（通过 .env 的 HOUHAI_* 字段配置，经 Settings 类读取）
# ---------------------------------------------------------------------------

OLD_BASE_URL: str = settings.HOUHAI_BASE_URL.strip().rstrip("/")
OLD_API_TOKEN: str = settings.HOUHAI_API_TOKEN.strip()
OLD_VERIFY_SSL: bool = settings.HOUHAI_VERIFY_SSL.strip().lower() != "false"

OLD_APP_IDENTIFIER: str = "RCGYCRK"
OLD_IMAGE_PREVIEW_PREFIX: str = "/qjzh-fs/preview"

# 前端运行时传入的 token（优先于 .env 中的 HOUHAI_API_TOKEN）
_runtime_token: Optional[str] = None


class EventReviewConfigError(RuntimeError):
    """外部事件审核平台未完成配置时抛出的可识别异常。"""


def set_runtime_token(token: str) -> None:
    """前端设置运行时 token，优先于 .env 配置"""
    global _runtime_token
    _runtime_token = token.strip() or None


def _get_active_token() -> str:
    """获取当前有效的 token：运行时 token > .env token"""
    if _runtime_token:
        return _runtime_token
    return OLD_API_TOKEN

# ---------------------------------------------------------------------------
# HTTP 工具
# ---------------------------------------------------------------------------

_http_client: Optional[httpx.Client] = None


def _get_client() -> httpx.Client:
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(
            timeout=httpx.Timeout(30.0),
            verify=OLD_VERIFY_SSL,
        )
    return _http_client


def _ensure_config() -> None:
    """确保老平台基础配置已设置"""
    if not OLD_BASE_URL:
        raise EventReviewConfigError("未配置 HOUHAI_BASE_URL，请在 .env 中设置")
    if not OLD_API_TOKEN and not _runtime_token:
        raise EventReviewConfigError("未配置 HOUHAI_API_TOKEN，请在 .env 中设置")


def _build_multipart_data(fields: dict) -> tuple[str, bytes]:
    """构建 multipart/form-data 请求体"""
    boundary = f"----LabelUEventReview{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:8]}"
    parts: list[bytes] = []
    for key, value in fields.items():
        if value is None:
            continue
        part = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
            f"{value}\r\n"
        )
        parts.append(part.encode("utf-8"))
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return boundary, b"".join(parts)


def _call_old_api(
    endpoint: str,
    fields: Optional[dict] = None,
    method: str = "POST",
) -> dict:
    """调用老平台 API，返回解析后的 JSON 或空字典"""
    _ensure_config()
    fields = dict(fields or {})
    fields["token"] = _get_active_token()

    url = urljoin(OLD_BASE_URL, endpoint)
    boundary, body = _build_multipart_data(fields)
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,en;q=0.9",
        "appidentifier": OLD_APP_IDENTIFIER,
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "origin": OLD_BASE_URL,
        "referer": f"{OLD_BASE_URL}/alarm_list",
        "token": _get_active_token(),
        "content-type": f"multipart/form-data; boundary={boundary}",
        "user-agent": "LabelU-EventReview/1.0",
    }

    try:
        resp = _get_client().request(
            method=method,
            url=url,
            headers=headers,
            content=body,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning(f"老平台 API 请求失败 [{endpoint}]: {exc}")
        return {}

    raw = resp.text
    # 老平台可能返回 GBK 编码
    if "charset=" not in str(resp.headers.get("content-type", "")):
        for enc in ["utf-8", "gb18030", "gbk"]:
            try:
                raw = resp.content.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue

    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning(f"老平台响应非 JSON [{endpoint[:60]}]: {raw[:200]}")
        return {}


def _resolve_image_url(file_obj: dict) -> str:
    """从 detail 返回的 file 对象解析图片路径（返回相对路径，前端通过 /api/v1/event-review/images/proxy 代理）"""
    file_url = (file_obj.get("url") or "").strip()
    if not file_url:
        return ""
    # 返回原始路径，形如 /permanent/2026/.../xxx.jpg
    return file_url


def get_image_proxy_url(file_obj: dict) -> str:
    """构建前端可用的图片代理 URL"""
    file_url = (file_obj.get("url") or "").strip()
    if not file_url:
        return ""
    return f"/api/v1/event-review/images/proxy?path={quote(file_url, safe='')}"


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------


def list_events(
    page: int = 0,
    size: int = 10,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
) -> tuple[list[EventReviewItem], int]:
    """分页查询事件列表（从老平台拉取）"""
    now = datetime.now()
    begin_time = (now - timedelta(days=30)).strftime("%Y-%m-%d 00:00:00")
    end_time = now.strftime("%Y-%m-%d 23:59:59")

    fields: dict = {
        "beginTime": begin_time,
        "endTime": end_time,
        "searchField": "ALARM_NAME",
        "searchValue": keyword or "",
        "searchType": "",
        "alarmTypeId": "",
        "alarmBussType": "",
        "auditStatus": "",
        "processStatus": "",
        "eventName": "",
        "pageNum": str(page + 1),  # 老平台 pageNum 从 1 开始
        "pageSize": str(size),
    }

    # 前端的 pending/reviewing 等是本模块展示状态；老平台只接受自己的 auditStatus 编码。
    if status and status not in {"pending", "reviewing", "skipped", "transferred", "cleared"}:
        fields["auditStatus"] = status

    data = _call_old_api("/iot-api/workform/alarm/commonAiAlarm/list", fields)

    if not data:
        return [], 0

    # 老平台响应格式: {"body": {"list": [...], "paging": {...}}, "status": 0}
    body = data.get("body", data)
    if isinstance(body, dict):
        rows = body.get("list", [])
        paging = body.get("paging", {})
        total = paging.get("total", len(rows)) if isinstance(paging, dict) else len(rows)
    elif isinstance(body, list):
        rows = body
        total = len(rows)
    else:
        rows = []
        total = 0

    items: list[EventReviewItem] = []
    for row in rows:
        alarm_id = str(row.get("id") or "")
        if not alarm_id:
            continue

        # 尝试从 msgJson 中提取摄像头名称
        msg_json = row.get("msgJson") or {}
        if isinstance(msg_json, str):
            try:
                msg_json = json.loads(msg_json)
            except Exception:
                msg_json = {}
        camera_name = (
            msg_json.get("cameraName")
            or msg_json.get("deviceName")
            or row.get("devSn")
            or ""
        )
        # 缩略图
        thumb = row.get("imagePath") or ""
        if thumb and not thumb.startswith("http"):
            thumb = f"/api/v1/event-review/images/proxy?path={quote(thumb, safe='')}"

        item_id = int(alarm_id) if alarm_id.isdigit() else page * size + len(items)
        items.append(
            EventReviewItem(
                id=item_id,
                alarm_id=alarm_id,
                camera_name=str(camera_name),
                event_type=row.get("eventName") or "",
                event_time=row.get("eventTime") or row.get("createTime") or "",
                status="pending",
                reviewer=row.get("auditUser") or None,
                thumbnail_url=thumb,
                created_at=row.get("createTime") or "",
            )
        )

    return items, total


def get_event_detail(event_id: int) -> Optional[EventReviewDetail]:
    """获取事件详情（从列表 API 按 ID 查找对应项，解析 msgJson）"""
    now = datetime.now()
    fields = {
        "beginTime": (now - timedelta(days=30)).strftime("%Y-%m-%d 00:00:00"),
        "endTime": now.strftime("%Y-%m-%d 23:59:59"),
        "searchField": "ALARM_NAME",
        "searchValue": "",
        "searchType": "",
        "alarmTypeId": "",
        "alarmBussType": "",
        "auditStatus": "",
        "processStatus": "",
        "eventName": "",
        "pageNum": "1",
        "pageSize": str(max(50, event_id)),
    }
    # 查询多页查找对应 alarm（最多查 20 页，约 2000 条；当前老平台总量约 1200）
    row = None
    for p in range(1, 21):
        fields["pageNum"] = str(p)
        fields["pageSize"] = "100"
        data = _call_old_api("/iot-api/workform/alarm/commonAiAlarm/list", fields)
        if not data:
            break
        body = data.get("body", data)
        rows = body.get("list", []) if isinstance(body, dict) else []
        row = next((r for r in rows if str(r.get("id") or "") == str(event_id)), None)
        if row is not None:
            break
        if len(rows) < 100:
            break  # 最后一页

    if row is None:
        return None

    # 解析 msgJson
    msg_json = row.get("msgJson") or {}
    if isinstance(msg_json, str):
        try:
            msg_json = json.loads(msg_json)
        except Exception:
            msg_json = {}

    # 递归查找嵌套字段
    def _deep_get(d, key, max_depth=6):
        if max_depth <= 0:
            return None
        if isinstance(d, dict):
            if key in d:
                return d[key]
            for v in d.values():
                r = _deep_get(v, key, max_depth - 1)
                if r is not None:
                    return r
        elif isinstance(d, list):
            for v in d:
                r = _deep_get(v, key, max_depth - 1)
                if r is not None:
                    return r
        return None

    # 解析图片列表（从 alarmPieceList 提取，可能在嵌套层级中）
    images: list[EventReviewImage] = []
    pieces = msg_json.get("alarmPieceList") or _deep_get(msg_json, "alarmPieceList") or []
    if isinstance(pieces, dict):
        pieces = [pieces]
    img_id = 0
    for piece in pieces:
        img_path = (piece.get("imagePiecePath") or "").strip()
        if img_path:
            img_id += 1
            images.append(
                EventReviewImage(
                    id=img_id,
                    url=f"/api/v1/event-review/images/proxy?path={quote(img_path, safe='')}",
                    filename=img_path.rsplit("/", 1)[-1] if "/" in img_path else img_path,
                    width=None,
                    height=None,
                )
            )

    alarm_id = str(row.get("id") or event_id)
    camera_name = (
        msg_json.get("cameraName")
        or _deep_get(msg_json, "cameraName")
        or row.get("devSn")
        or ""
    )
    camera_location = (
        msg_json.get("cameraLocation")
        or _deep_get(msg_json, "cameraLocation")
        or ""
    )
    return EventReviewDetail(
        id=int(event_id),
        alarm_id=alarm_id,
        camera_name=str(camera_name),
        camera_location=str(camera_location),
        event_type=row.get("eventName") or msg_json.get("alarmType") or _deep_get(msg_json, "alarmType") or "",
        event_time=row.get("eventTime") or msg_json.get("eventTime") or _deep_get(msg_json, "eventTime") or "",
        alarm_info={
            "alarmId": alarm_id,
            "cameraName": camera_name,
            "cameraLocation": camera_location,
            "alarmType": _deep_get(msg_json, "alarmType"),
            "auditStatus": row.get("auditStatus"),
            "processStatus": row.get("processStatus"),
            "auditUser": row.get("auditUser"),
            "auditRemark": row.get("auditRemark"),
            "pieces": pieces,
        },
        status=row.get("auditStatus") or "pending",
        reviewer=row.get("auditUser") or None,
        images=images,
        created_at=row.get("createTime") or "",
    )


def claim_events(count: int = 10) -> list[EventReviewItem]:
    """认领一批待审核事件（返回当前列表前 N 条）"""
    items, _ = list_events(page=0, size=count)
    return items[:count]


def perform_action(
    event_id: int,
    action: str,
    event_type: Optional[str] = None,
    remark: Optional[str] = None,
) -> Optional[EventReviewItem]:
    """对事件执行动作：跳过（本地）/ 转工单 / 消除告警（调用老平台 operate）"""
    if action == "skip":
        # 跳过：纯本地操作，不调老平台
        return EventReviewItem(
            id=event_id,
            alarm_id=str(event_id),
            status="skipped",
        )

    operate_type = None
    if action == "transfer":
        operate_type = "TRAN_ORDER"
    elif action == "clear":
        operate_type = "MANU_SOLVE"

    if not operate_type:
        return None

    fields: dict = {
        "operateType": operate_type,
        "alarmId": str(event_id),
    }
    if remark:
        fields["remark"] = remark

    data = _call_old_api("/iot-api/workform/alarm/operate", fields)
    old_status = data.get("status")
    if not data or old_status not in (0, "0", None):
        logger.warning(
            f"事件审核动作失败: event_id={event_id} action={action} "
            f"old_api_status={old_status} old_api_msg={data.get('msg', data.get('message', '')) if data else ''}"
        )
        return None

    status = "transferred" if action == "transfer" else "cleared"
    logger.info(
        f"事件审核动作: event_id={event_id} action={action} "
        f"old_api_result={data.get('msg', data.get('message', ''))}"
    )

    return EventReviewItem(
        id=event_id,
        alarm_id=str(event_id),
        status=status,
        event_type=event_type,
    )


def get_progress() -> EventReviewProgress:
    """获取审核进度统计"""
    # 拉取最近 7 天列表
    items, total = list_events(page=0, size=100)
    pending = total  # 老平台返回的都是待处理
    return EventReviewProgress(
        total_pending=pending,
        reviewing_count=0,
        done_today=0,
        current_batch=0,
        batch_total=min(10, pending),
    )


def get_image_bytes(image_path: str) -> tuple[bytes, str]:
    """通过老平台获取图片原始字节，返回 (bytes, content_type)"""
    _ensure_config()
    # image_path 形如 /permanent/2026/.../xxx.jpg
    if image_path.startswith("http://") or image_path.startswith("https://"):
        url = image_path
    else:
        normalized_path = image_path if image_path.startswith("/") else f"/{image_path}"
        url = f"{OLD_BASE_URL}{OLD_IMAGE_PREVIEW_PREFIX}{normalized_path}"
    headers = {
        "accept": "image/*",
        "appidentifier": OLD_APP_IDENTIFIER,
        "cookie": "theme=light",
        "referer": f"{OLD_BASE_URL}/alarm_list",
        "token": _get_active_token(),
        "user-agent": "LabelU-EventReview/1.0",
    }
    try:
        resp = _get_client().get(url, headers=headers)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "image/jpeg")
        return resp.content, ct
    except httpx.HTTPError as exc:
        logger.warning(f"获取老平台图片失败: {url} err={exc}")
        return b"", "image/jpeg"
