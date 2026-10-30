"""OpenAI 兼容视觉识别；优先使用页面配置的火山 Coding Plan。"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import time
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Any
from urllib.parse import unquote_to_bytes, urlsplit

import httpx
from loguru import logger

from labelu.internal.common.ai_labeling_runtime import (
    is_qwen_visual_provider,
    load_ai_labeling_runtime,
)
from labelu.internal.common.config import settings
from labelu.internal.common import model_access_config
from labelu.internal.common.model_access_config import (
    has_configured_model_integration,
    resolve_model_integration,
    validate_volcengine_coding_endpoint,
)
from labelu.internal.common.vision_volcengine import normalize_volcengine_ark_model_id

_VISION_MAX_ATTEMPTS = 3
_VISION_RETRYABLE_STATUS_CODES = frozenset({500, 502, 503, 504})
_VISION_PROVIDER_FAILURES: dict[str, tuple[float, int]] = {}
_VISION_PROVIDER_FAILURE_LOCK = Lock()


def _provider_failure_cooldown_seconds(response: httpx.Response) -> float:
    """Cool connection failures, never image/prompt-specific bad requests."""
    if response.status_code in {401, 403, 404}:
        return 60.0
    if response.status_code == 400:
        try:
            error = response.json().get("error") or {}
            if isinstance(error, dict) and error.get("code") == "Arrearage":
                return 60.0
        except (ValueError, AttributeError):
            pass
        return 0.0
    if response.status_code != 429:
        return 0.0

    retry_after = response.headers.get("Retry-After", "")
    try:
        delay = float(retry_after)
    except ValueError:
        try:
            delay = parsedate_to_datetime(retry_after).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            delay = 60.0
    # A provider must not disable inference indefinitely, even with a bad header.
    return max(1.0, min(delay, 300.0)) if math.isfinite(delay) else 60.0


def _json_response_constraint_rejected(response: httpx.Response) -> bool:
    """Detect a provider rejecting only the optional JSON response constraint.

    Some OpenAI-compatible gateways do not implement ``response_format`` and
    return a generic HTTP 400.  Retrying every 400 would duplicate invalid
    image/prompt requests, so the fallback is enabled only when the response
    identifies the JSON-format parameter (or an equivalent unsupported-field
    error).
    """
    if response.status_code != 400:
        return False
    parts: list[str] = []
    try:
        payload = response.json()
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            for key in ("message", "code", "type", "param", "parameter"):
                value = error.get(key)
                if value is not None:
                    parts.append(str(value))
        for key in ("message", "code", "type", "param", "parameter"):
            value = payload.get(key)
            if value is not None:
                parts.append(str(value))
    if not parts:
        parts.append(response.text[:4000])
    text = " ".join(parts).lower()
    if "response_format" in text or "json_object" in text:
        return True
    unsupported = any(
        marker in text
        for marker in (
            "unsupported",
            "not support",
            "unknown field",
            "unknown parameter",
            "extra inputs",
            "unrecognized",
        )
    )
    return unsupported and any(
        marker in text for marker in ("format", "parameter", "field", "json")
    )


def _check_provider_cooldown(connection_key: str) -> None:
    now = time.monotonic()
    with _VISION_PROVIDER_FAILURE_LOCK:
        expired = [
            key for key, (until, _) in _VISION_PROVIDER_FAILURES.items() if until <= now
        ]
        for key in expired:
            _VISION_PROVIDER_FAILURES.pop(key, None)
        failure = _VISION_PROVIDER_FAILURES.get(connection_key)
    if failure:
        until, status = failure
        raise RuntimeError(
            f"视觉模型上次调用返回 HTTP {status}，临时冷却中，{math.ceil(until - now)} 秒后重试"
        )


def has_managed_volcengine_config(capability: str | None = None) -> bool:
    """A saved page configuration is authoritative even when disabled."""
    records = [
        row
        for row in model_access_config.load_store().get("connections", [])
        if isinstance(row, dict) and row.get("provider_key") == "volcengine"
    ]
    if capability is None:
        return bool(records)
    wanted = str(capability).strip().lower()
    return any(
        str(row.get("capability") or "vision_reasoning").strip().lower() == wanted
        for row in records
    )


def _safe_visual_base_url(base_url: str, model: str, label: str = "") -> str:
    if is_qwen_visual_provider(base_url, model, label):
        raise ValueError("千问已从视觉识别链移除，请在系统管理配置火山 Coding Plan")
    hostname = (urlsplit(base_url).hostname or "").lower()
    if hostname.endswith("volces.com") or label == "volcengine":
        return validate_volcengine_coding_endpoint(base_url)
    return base_url


def _resolve_model_for_capability(capability: str) -> dict[str, Any] | None:
    """Resolve the checked managed Ark record for one capability.

    The compatibility fallback keeps older tests and embedded callers that
    monkeypatch the pre-capability resolver working while production uses the
    capability-aware store query.
    """
    resolver = model_access_config.resolve_model_integration
    try:
        return resolver("volcengine", capability=capability)
    except TypeError:
        return resolver("volcengine")


def _configured_model_for(
    provider_key: str = "tagtool", *, capability: str = "vision_reasoning"
) -> dict[str, Any] | None:
    """The page's Ark configuration overrides every historical call chain.

    Disabled or unchecked Ark records block fallback. Older non-Qwen tagtool
    and generic visual services remain supported until a page Ark record exists.
    """
    configured_ark = _resolve_model_for_capability(capability)
    if configured_ark:
        return configured_ark
    if has_managed_volcengine_config(capability):
        return {"blocked": True, "provider_key": "volcengine"}
    keys = [provider_key]
    if provider_key == "tagtool":
        keys.append("vision_llm")
    for key in keys:
        configured_model = resolve_model_integration(key)
        if configured_model:
            return configured_model
        if has_configured_model_integration(key):
            return {"blocked": True, "provider_key": key}
    return None


def vision_llm_chain_configured(
    provider_key: str = "tagtool", *, capability: str = "vision_reasoning"
) -> bool:
    try:
        configured_model = _configured_model_for(provider_key, capability=capability)
    except TypeError:
        # Compatibility for embedded callers that still provide a
        # one-argument resolver; production resolution remains capability-aware.
        configured_model = _configured_model_for(provider_key)
    if configured_model:
        if configured_model.get("blocked"):
            return False
        try:
            _safe_visual_base_url(
                str(configured_model.get("endpoint") or ""),
                str(configured_model.get("model") or ""),
                str(configured_model.get("provider_key") or ""),
            )
        except ValueError:
            return False
        return True
    if (settings.VISION_VOLCENGINE_API_KEY or "").strip():
        try:
            _safe_visual_base_url(
                settings.VISION_VOLCENGINE_BASE_URL,
                settings.VISION_VOLCENGINE_MODEL,
                "volcengine",
            )
        except ValueError:
            pass
        else:
            return True
    # 自定义非千问视觉档案仍兼容；无需 .env 密钥。
    rt = load_ai_labeling_runtime()
    profiles_by_id = {p["id"]: p for p in rt.get("llm_profiles") or []}
    for pid in rt.get("annotation_chain_profile_ids") or []:
        p = profiles_by_id.get(str(pid).strip())
        if not p:
            continue
        try:
            _safe_visual_base_url(
                p.get("base_url") or "", p.get("model") or "", p.get("name") or ""
            )
        except ValueError:
            continue
        if (
            (p.get("api_key") or "").strip()
            and (p.get("base_url") or "").strip()
            and (p.get("model") or "").strip()
        ):
            return True
    return False


def _chat_url(base: str) -> str:
    base = (base or "").rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _download_image(url: str) -> tuple[bytes, str]:
    """Read an image from an HTTP(S) URL or a bounded data URI.

    Prompt trials upload an image without persisting an attachment first.  A
    data URI keeps that transient image inside the existing vision chain and
    avoids adding a second model client.  HTTP(S) handling remains unchanged.
    """
    if str(url or "").strip().lower().startswith("data:"):
        header, separator, payload = str(url).partition(",")
        if not separator:
            raise ValueError("invalid image data URI")
        metadata = header[5:].split(";")
        mime = (metadata[0] or "image/jpeg").strip().lower()
        if not mime.startswith("image/"):
            raise ValueError("image data URI must use an image MIME type")
        try:
            if any(item.strip().lower() == "base64" for item in metadata[1:]):
                raw = base64.b64decode(payload, validate=True)
            else:
                raw = unquote_to_bytes(payload)
        except (ValueError, UnicodeError, base64.binascii.Error) as exc:
            raise ValueError("invalid image data URI payload") from exc
        if not raw:
            raise ValueError("image data URI is empty")
        return raw, mime

    with httpx.Client(
        timeout=min(settings.AI_MODEL_TIMEOUT_SECONDS, 120), follow_redirects=True
    ) as client:
        r = client.get(url)
        r.raise_for_status()
        ctype = (
            r.headers.get("content-type", "image/jpeg").split(";")[0].strip()
            or "image/jpeg"
        )
        return r.content, ctype


def _prepare_image_for_inference(
    raw_bytes: bytes, *, max_side: int | None = None
) -> tuple[bytes, tuple[int, int]]:
    """Normalize an attachment and bound its inference resolution.

    Task attachments remain at their original resolution.  Only the transient
    image sent to the VLM is resized, preventing 4K camera frames from
    exhausting the model context and causing avoidable request timeouts.
    """
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(raw_bytes)) as image:
        image = image.convert("RGB")
        resolved_max_side = max_side
        if resolved_max_side is None:
            resolved_max_side = getattr(settings, "AI_VISION_MAX_IMAGE_SIDE", 1536)
        resolved_max_side = max(0, int(resolved_max_side or 0))
        if resolved_max_side and max(image.size) > resolved_max_side:
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            image.thumbnail((resolved_max_side, resolved_max_side), resampling)
        size = image.size
        buf = BytesIO()
        image.save(buf, format="JPEG", quality=92)
    return buf.getvalue(), size


def _image_dimensions(raw_bytes: bytes) -> tuple[int, int]:
    """Read the attachment dimensions before any transient VLM resize."""
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(raw_bytes)) as image:
        return image.size


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif block.get("type") == "output_text":
                    parts.append(str(block.get("text", "")))
        return "\n".join(parts)
    return str(content)


def _responses_output_text(response: dict[str, Any]) -> str:
    """Extract text from an OpenAI Responses API result.

    Responses returns assistant content under ``output[].content[]`` rather
    than Chat Completions' ``choices[].message.content``.  Keep the extraction
    tolerant of the top-level ``output_text`` convenience field and of mixed
    reasoning/message output items so the existing JSON parser remains the
    single validation boundary.
    """
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    message_parts: list[str] = []
    fallback_parts: list[str] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type in {"message", "output_message"} and isinstance(
            item.get("text"), str
        ):
            # A few OpenAI-compatible gateways flatten message content onto
            # the output item instead of returning ``content[].text``.
            text = item["text"]
        elif item_type == "output_text" and isinstance(item.get("text"), str):
            text = item["text"]
        else:
            content = item.get("content")
            if isinstance(content, str) and content.strip():
                text = content
            else:
                text = _message_content_text(content)
        if not text.strip():
            continue
        fallback_parts.append(text)
        # Responses may include reasoning/analysis items before the assistant
        # message.  Those texts are not part of the JSON result and must not
        # be concatenated with it.  Prefer strictly typed message output while
        # retaining a fallback for providers that omit item type.
        if item_type in {"message", "output_message", "output_text"}:
            message_parts.append(text)
    return "".join(message_parts or fallback_parts)


def _extract_json_obj(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise json.JSONDecodeError("no json object", text, 0)


SITE_SUFFIX_HEADER = "【站点附加提示（可在 /ai-labeling-settings 页面修改）】"

# 管理页「标准规则模版」展示用：与真实 _build_instruction 逻辑一致，仅 payload 为固定参考样例
INSTRUCTION_TEMPLATE_REFERENCE_PAYLOAD: dict[str, Any] = {
    "labels": [
        {"name": "占道摊点", "tool": "rectTool"},
        {"name": "违规广告牌", "tool": "rectTool"},
    ],
    "tag_dimensions": [
        {
            "field_key": "weather",
            "name_cn": "天气",
            "options": [
                {"value": "sunny", "key": "晴"},
                {"value": "cloudy", "key": "阴"},
                {"value": "rainy", "key": "雨"},
            ],
        },
        {
            "field_key": "scene_type",
            "name_cn": "场景类型",
            "options": [
                {"value": "street", "key": "街面"},
                {"value": "market", "key": "集市"},
            ],
        },
    ],
    "constraints": {
        "allowed_tools": ["rectTool", "tagTool"],
        "filter_by_labels": True,
    },
    "prompt": "",
}


def get_instruction_base_template_reference(
    width: int = 1920,
    height: int = 1080,
) -> str:
    """返回主提示词标准模版（不含站点 runtime 附加段与单次 request prompt）。"""
    return _build_instruction(
        INSTRUCTION_TEMPLATE_REFERENCE_PAYLOAD,
        width,
        height,
        include_runtime_suffix=False,
        include_request_prompt=False,
    )


def _build_instruction(
    payload: dict[str, Any],
    width: int,
    height: int,
    *,
    include_runtime_suffix: bool = True,
    include_request_prompt: bool = True,
) -> str:
    # A published contract prompt is an immutable execution artifact.  It is
    # rendered before the generic instruction builder so a task-level request
    # cannot remove labels, geometry rules, or the JSON schema.  The optional
    # request prompt is appended only as supplementary context.
    contract_binding = payload.get("contract_prompt")
    if isinstance(contract_binding, dict):
        effective_prompt = str(
            contract_binding.get("effective_prompt")
            or contract_binding.get("prompt")
            or ""
        ).strip()
        if effective_prompt:
            from labelu.internal.application.service.annotation_prompt import (
                render_prompt_template,
            )

            rendered = render_prompt_template(effective_prompt, width, height)
            request_prompt = str(payload.get("prompt") or "").strip()
            if request_prompt:
                rendered += (
                    "\n\n单次请求附加说明（仅作补充，不得改变标签契约、坐标规则或输出 JSON 结构）："
                    + request_prompt
                )
            return rendered

    labels = payload.get("labels") or []
    tag_dimensions = payload.get("tag_dimensions") or []
    constraints = payload.get("constraints") or {}
    allowed_tools = constraints.get("allowed_tools") or ["rectTool"]
    filter_by_labels = constraints.get("filter_by_labels", False)
    extra = payload.get("prompt") or ""

    label_lines = []
    for item in labels:
        name = item.get("name") or item.get("display_name") or ""
        display_name = item.get("display_name") or ""
        tool = item.get("tool") or ""
        display_part = (
            f" display_name={display_name!r}"
            if display_name and display_name != name
            else ""
        )
        semantic_text = item.get("description") or item.get("prompt_guidance") or ""
        semantic_part = (
            f" description={str(semantic_text).strip()!r}"
            if str(semantic_text).strip()
            else ""
        )
        label_lines.append(
            f"- label={name!r}{display_part} tool={tool}{semantic_part}"
        )

    dim_lines = []
    for dim in tag_dimensions:
        fk = dim.get("field_key") or ""
        name_cn = dim.get("name_cn") or fk
        opts = dim.get("options") or []
        opt_strs = []
        for o in opts:
            if isinstance(o, dict):
                opt_strs.append(f"{o.get('value')!r}({o.get('key')})")
        dim_lines.append(
            f"- 维度 field_key={fk!r} 名称={name_cn!r} 可选值(value_en): "
            + (", ".join(opt_strs) if opt_strs else "(见任务)")
        )

    tools_help = []
    if "rectTool" in allowed_tools:
        tools_help.append(
            'rectTool: {"x":float,"y":float,"width":float,"height":float} '
            "像素坐标，左上角为原点，x 向右、y 向下。"
        )
    if "polygonTool" in allowed_tools:
        tools_help.append(
            'polygonTool: {"type":"line","points":[{"x":float,"y":float}, ...]} 多边形顶点像素坐标。'
        )
    if "tagTool" in allowed_tools and tag_dimensions:
        tools_help.append(
            '全局标签(tagTool): 在 JSON 根节点增加 "tag_values" 对象；'
            "键为各维度的 field_key（字符串），值为字符串数组（单选时长度 1），"
            "数组内必须是上面列出的 value_en，勿发明新值。"
        )

    geom_tools = ("rectTool", "polygonTool", "pointTool", "lineTool")
    has_geometry = any(t in allowed_tools for t in geom_tools)
    if "rectTool" in allowed_tools:
        geom_hint = (
            f"\n矩形检测规则（必须全部遵守）：\n"
            f"1. 坐标严格使用本次输入图像 {width} x {height} 的像素坐标，"
            "禁止 0-1 或 0-1000 归一化坐标，禁止输出 x2/y2；每个框的 x、y、width、height 必须都是有限数字。\n"
            "2. 从左到右、从上到下逐个实例扫描整张图，检查近处、远处、图像边缘及部分遮挡的目标；"
            "一个目标输出一个框，不得把多个实例合并。\n"
            "3. 框必须紧贴并包含目标的完整可见部分，不得只框头部、躯干、车辆局部或其他局部；"
            "被图像边缘截断的目标应框到可见边界，不得推测画面外部分。\n"
            "4. 对骑车人员等组合类别，框应覆盖类别名称指代的完整可见整体，包括骑行者及对应车辆。\n"
            "5. 输出前复查漏检、重复框、类别和四个坐标字段。"
            '\n若图中确实没有任何配置类别目标，"results" 才可为 []。\n'
        )
    elif has_geometry:
        geom_hint = '\n几何标注：若没有矩形/多边形等目标，"results" 可为 []。\n'
    else:
        geom_hint = ""
    tag_hint = (
        "\n全局标签：必须为上面列出的每一个 field_key 都输出一个合法 value_en；"
        "单选维度输出长度为 1 的数组。不得省略已配置维度，不得输出未配置维度。\n"
        if tag_dimensions
        else ""
    )

    if has_geometry:
        schema_example = (
            '{"results":[{"toolName":"rectTool","label":"类别","result":{"x":0,"y":0,"width":10,"height":10},"score":0.9}]}'
            if not tag_dimensions
            else '{"results":[{"toolName":"rectTool","label":"类别","result":{"x":0,"y":0,"width":10,"height":10},"score":0.9}],'
            '"tag_values":{"每个已配置field_key":["一个合法value_en"]}}'
        )
    else:
        # 仅全局维度：不向模型展示几何框示例，避免引导输出拉框
        schema_example = (
            '{"results":[],"tag_values":{"每个已配置field_key":["一个合法value_en"]}}'
        )

    if has_geometry:
        intro = "你是图像标注助手。请阅读图片，根据下列标签与工具约束输出检测结果。\n"
    else:
        intro = (
            "你是图像标注助手。请阅读图片，仅根据下列全局标签维度推断整张图的属性；"
            "不要输出任何矩形、多边形、点、线段等几何标注。\n"
        )
    part_a = (
        intro
        + f"图像宽高: {width} x {height} 像素。\n"
        + f"允许的工具: {allowed_tools}。\n"
    )
    if label_lines:
        part_a += (
            f"{'只能预测下列几何标签（勿发明新标签）' if filter_by_labels else '几何类别优先使用下列标签'}:\n"
            + "\n".join(label_lines)
            + "\n\n"
        )
    if dim_lines:
        part_a += (
            "全局标签维度（来自数据库全局属性，键必须用 field_key）：\n"
            + "\n".join(dim_lines)
            + "\n\n"
        )
    part_a += "\n".join(tools_help) + geom_hint + tag_hint
    if has_geometry:
        tag_schema_hint = (
            '；只要上方存在全局标签维度，"tag_values" 仍必须包含全部配置维度。'
            if tag_dimensions
            else ""
        )
        json_rules = (
            '\n\n必须只输出一段合法 JSON（不要 Markdown）。根对象须包含 "results"（数组）。\n'
            f"示例结构：{schema_example}\n"
            '若没有几何目标，"results" 可为空。' + tag_schema_hint + "\n"
        )
    else:
        json_rules = (
            '\n\n必须只输出一段合法 JSON（不要 Markdown）。根对象须包含 "results"（可为空数组）与 "tag_values"（对象）。\n'
            f"示例结构：{schema_example}\n"
            "即使图像信息不确定，也必须在每个配置维度的可选值中选择最匹配的一项，不能返回空 tag_values。\n"
        )
    part_a += json_rules
    if include_runtime_suffix:
        ov = load_ai_labeling_runtime()
        suf = (ov.get("instruction_suffix") or "").strip()
        if suf:
            part_a += f"\n\n{SITE_SUFFIX_HEADER}\n" + suf + "\n"
    if include_request_prompt and extra:
        part_a += f"\n单次请求附加说明: {extra}\n"
    return part_a


def _inject_tag_values_into_results(parsed: dict[str, Any]) -> None:
    raw_tv = parsed.get("tag_values")
    if raw_tv is None:
        raw_tv = parsed.get("tags")
    if not isinstance(raw_tv, dict) or not raw_tv:
        return
    nv: dict[str, list[str]] = {}
    for k, v in raw_tv.items():
        key = str(k)
        if isinstance(v, list):
            nv[key] = [str(x) for x in v if x is not None and str(x).strip()]
        elif v is not None and str(v).strip():
            nv[key] = [str(v)]
    if not nv:
        return
    results = parsed.get("results")
    if not isinstance(results, list):
        results = []
        parsed["results"] = results
    results.append({"toolName": "tagTool", "result": {"value": nv}})


def _normalize_parsed(raw: dict[str, Any]) -> dict[str, Any]:
    results = raw.get("results")
    if results is None:
        results = []
    if not isinstance(results, list):
        raise ValueError("results must be list")
    return {
        "model": raw.get("model") or "vision_llm_chain",
        "results": results,
        "latency_ms": raw.get("latency_ms"),
        "warning_message": raw.get("warning_message"),
    }


def _openai_compatible_infer(
    *,
    provider_label: str,
    base_url: str,
    api_key: str,
    model: str,
    instruction: str,
    image_bytes: bytes,
    mime: str,
    timeout_seconds: int | float | None = None,
    auth_type: str = "bearer",
    auth_header: str = "Authorization",
    max_new_tokens: int | None = None,
    verify_tls: bool = True,
    request_protocol: str = "openai_compatible",
    use_responses: bool | None = None,
) -> dict[str, Any]:
    protocol = str(request_protocol or "openai_compatible").strip().lower()
    if use_responses is not None:
        # Compatibility alias for embedded callers that prefer a boolean
        # capability switch over a protocol name.
        protocol = "responses" if use_responses else "openai_compatible"
    use_responses_api = protocol in {"responses", "response"}
    safe_base_url = _safe_visual_base_url(base_url, model, provider_label)
    url = (
        f"{safe_base_url.rstrip('/')}/responses"
        if use_responses_api
        else _chat_url(safe_base_url)
    )
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    data_uri = f"data:{mime};base64,{b64}"
    output_limit = max(1, min(int(max_new_tokens or 4096), 32768))
    if use_responses_api:
        body = {
            "model": model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": instruction},
                        {"type": "input_image", "image_url": data_uri},
                    ],
                }
            ],
            "max_output_tokens": output_limit,
            "thinking": {"type": "disabled"},
            # Keep the contract prompt as the semantic source, and require
            # the provider to return machine-parseable JSON.
            "text": {"format": {"type": "json_object"}},
        }
    else:
        body = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": instruction},
                    ],
                }
            ],
            "max_tokens": output_limit,
            "temperature": 0.2,
            # Ask OpenAI-compatible gateways to keep the model response
            # machine-parseable.  A one-time compatibility fallback below
            # removes this field for gateways that reject it.
            "response_format": {"type": "json_object"},
        }
    headers = {"Content-Type": "application/json"}
    if api_key:
        normalized_auth = str(auth_type or "bearer").strip().lower()
        if normalized_auth == "bearer":
            headers["Authorization"] = f"Bearer {api_key}"
        elif normalized_auth in {"header", "api_key"}:
            headers[str(auth_header or "Authorization").strip() or "Authorization"] = (
                api_key
            )
    # Include the actual endpoint, model and authentication without retaining
    # credentials or response/request bodies in the process-wide failure cache.
    connection_key = hashlib.sha256(
        json.dumps([url, model, sorted(headers.items())]).encode("utf-8")
    ).hexdigest()
    _check_provider_cooldown(connection_key)
    response_output_limit = output_limit
    length_retry_used = False
    parse_retry_used = False
    format_fallback_used = False
    json_constraint_enabled = not use_responses_api
    while True:
        if use_responses_api:
            body["max_output_tokens"] = response_output_limit
        else:
            body["max_tokens"] = response_output_limit
            if json_constraint_enabled:
                body["response_format"] = {"type": "json_object"}
            else:
                body.pop("response_format", None)
        format_fallback_requested = False
        with httpx.Client(
            timeout=float(timeout_seconds or settings.AI_MODEL_TIMEOUT_SECONDS),
            verify=bool(verify_tls),
            follow_redirects=False,
        ) as client:
            # A Responses request may consume a Coding Plan inference before the
            # client observes a transport timeout. Do not resubmit it blindly.
            max_attempts = 1 if use_responses_api else _VISION_MAX_ATTEMPTS
            for attempt in range(1, max_attempts + 1):
                try:
                    r = client.post(url, headers=headers, json=body)
                except httpx.TransportError as exc:
                    if attempt >= max_attempts:
                        raise
                    delay = 2 ** (attempt - 1)
                    logger.warning(
                        "vision chain {} transport error on attempt {}/{}: {}; retrying in {}s",
                        provider_label,
                        attempt,
                        max_attempts,
                        str(exc)[:300],
                        delay,
                    )
                    time.sleep(delay)
                    continue

                if r.status_code >= 400:
                    logger.warning(
                        "vision chain {} HTTP {} on attempt {}/{}: {}",
                        provider_label,
                        r.status_code,
                        attempt,
                        max_attempts,
                        r.text[:500],
                    )
                    if (
                        not use_responses_api
                        and json_constraint_enabled
                        and not format_fallback_used
                        and _json_response_constraint_rejected(r)
                    ):
                        # The request body is otherwise valid, but this
                        # gateway does not understand response_format. Retry
                        # once without the optional constraint; subsequent
                        # requests retain the normal HTTP retry policy.
                        format_fallback_used = True
                        json_constraint_enabled = False
                        format_fallback_requested = True
                        logger.warning(
                            "vision chain {} rejected response_format with HTTP 400; "
                            "retrying once without JSON constraint",
                            provider_label,
                        )
                        break
                    cooldown = _provider_failure_cooldown_seconds(r)
                    if cooldown:
                        with _VISION_PROVIDER_FAILURE_LOCK:
                            _VISION_PROVIDER_FAILURES[connection_key] = (
                                time.monotonic() + cooldown,
                                r.status_code,
                            )
                        # Fall through to the next configured provider immediately.
                        # A 429 must not multiply requests inside this same call.
                        r.raise_for_status()
                    if (
                        r.status_code in _VISION_RETRYABLE_STATUS_CODES
                        and attempt < max_attempts
                    ):
                        delay = 2 ** (attempt - 1)
                        logger.warning(
                            "vision chain {} retrying HTTP {} in {}s",
                            provider_label,
                            r.status_code,
                            delay,
                        )
                        time.sleep(delay)
                        continue
                r.raise_for_status()
                with _VISION_PROVIDER_FAILURE_LOCK:
                    _VISION_PROVIDER_FAILURES.pop(connection_key, None)
                break
        if format_fallback_requested:
            continue
        response_payload = r.json()

        if use_responses_api:
            status_value = str(response_payload.get("status") or "").strip().lower()
            incomplete_details = response_payload.get("incomplete_details")
            incomplete_reason = (
                str(incomplete_details.get("reason") or "").strip().lower()
                if isinstance(incomplete_details, dict)
                else ""
            )
            if status_value == "incomplete":
                if incomplete_reason in {"length", "max_output_tokens"}:
                    expanded_limit = min(max(response_output_limit * 4, 4096), 32768)
                    if not length_retry_used and expanded_limit > response_output_limit:
                        length_retry_used = True
                        logger.warning(
                            "vision chain {} output reached {} tokens; retrying once with {}",
                            provider_label,
                            response_output_limit,
                            expanded_limit,
                        )
                        response_output_limit = expanded_limit
                        continue
                    raise ValueError(
                        "视觉模型输出超过最大长度，请在模型配置中提高最大输出长度后重试"
                    )
                raise ValueError(
                    "视觉模型输出未完成"
                    + (f"（原因：{incomplete_reason}）" if incomplete_reason else "")
                )
            content = _responses_output_text(response_payload)
        else:
            choice0 = (response_payload.get("choices") or [{}])[0]
            msg = choice0.get("message") or {}
            content = _message_content_text(msg.get("content"))

        try:
            parsed = _extract_json_obj(content)
        except json.JSONDecodeError as exc:
            expanded_limit = min(max(response_output_limit * 4, 4096), 32768)
            if (
                not parse_retry_used
                and expanded_limit > response_output_limit
            ):
                parse_retry_used = True
                logger.warning(
                    "vision chain {} returned invalid JSON at position {} ({} chars); "
                    "retrying once with {} output tokens",
                    provider_label,
                    exc.pos,
                    len(content),
                    expanded_limit,
                )
                response_output_limit = expanded_limit
                continue
            logger.warning(
                "vision chain {} returned invalid JSON at position {} ({} chars); "
                "no further parse retry available",
                provider_label,
                exc.pos,
                len(content),
            )
            raise
        break
    _inject_tag_values_into_results(parsed)
    normalized = _normalize_parsed(parsed)
    # Keep the raw model text so the annotation audit view can show exactly
    # what the model returned.  It is never forwarded back to the model.
    normalized["_raw_text"] = content
    return normalized


def _vision_trace(
    *,
    provider_label: str,
    base_url: str,
    model: str,
    request_protocol: str,
    instruction: str,
    model_width: int,
    model_height: int,
    original_width: int,
    original_height: int,
) -> dict[str, Any]:
    """Non-secret provenance for one vision call (stored with the result)."""

    try:
        endpoint_host = urlsplit(str(base_url or "")).netloc
    except Exception:
        endpoint_host = ""
    return {
        "provider_label": provider_label,
        "endpoint_host": endpoint_host,
        "model": model,
        "request_protocol": request_protocol,
        "instruction": instruction,
        "model_image_width": model_width,
        "model_image_height": model_height,
        "original_image_width": original_width,
        "original_image_height": original_height,
    }


def call_vision_llm_chain(
    model_payload: dict[str, Any], *, provider_key: str = "tagtool"
) -> dict[str, Any]:
    """同步调用；供 asyncio.to_thread 或批量线程使用。"""
    image_url = model_payload.get("image_url") or ""
    if not image_url:
        raise ValueError("image_url required")

    capability = (
        "bbox_detection" if provider_key == "bbox_detection" else "vision_reasoning"
    )
    raw_bytes, _mime = _download_image(image_url)
    if capability == "bbox_detection":
        bbox_max_side = max(
            0, int(getattr(settings, "AI_BBOX_VISION_MAX_IMAGE_SIDE", 2560) or 0)
        )
        img_bytes, (w, h) = _prepare_image_for_inference(
            raw_bytes, max_side=bbox_max_side
        )
    else:
        img_bytes, (w, h) = _prepare_image_for_inference(raw_bytes)
    try:
        original_w, original_h = _image_dimensions(raw_bytes)
    except Exception:
        # Embedded callers and offline tests may provide already-prepared
        # bytes that are not a decodable image. In that case the prepared
        # dimensions are the only trustworthy coordinate space available.
        original_w, original_h = w, h

    instruction = _build_instruction(model_payload, w, h)

    # The system-management configuration is authoritative for this ability.
    # Once an administrator has added a configuration, an untested/failed
    # endpoint must be visible as unavailable instead of silently falling back
    # to a stale .env model.
    try:
        configured_model = _configured_model_for(provider_key, capability=capability)
    except TypeError:
        # Compatibility for embedded callers that still provide a
        # one-argument resolver; production remains capability-aware.
        configured_model = _configured_model_for(provider_key)
    if configured_model:
        if configured_model.get("blocked"):
            raise RuntimeError(
                f"{configured_model.get('provider_key') or provider_key} 模型已配置但已禁用或尚未通过连通性检查，请到系统管理中检查"
            )
        try:
            base_url = _safe_visual_base_url(
                str(configured_model.get("endpoint") or ""),
                str(configured_model.get("model") or ""),
                str(configured_model.get("provider_key") or ""),
            )
            resolved_protocol = (
                "responses"
                if capability == "bbox_detection"
                and str(configured_model.get("provider_key") or "").strip().lower()
                == "volcengine"
                else str(
                    configured_model.get("request_protocol") or "openai_compatible"
                )
            )
            resolved_model = normalize_volcengine_ark_model_id(
                str(configured_model.get("model") or "")
            )
            result = _openai_compatible_infer(
                provider_label=str(configured_model.get("name") or "通用视觉模型"),
                base_url=base_url,
                api_key=str(configured_model.get("api_key") or ""),
                model=resolved_model,
                instruction=instruction,
                image_bytes=img_bytes,
                mime="image/jpeg",
                timeout_seconds=configured_model.get("timeout_seconds"),
                auth_type=str(configured_model.get("auth_type") or "none"),
                auth_header=str(configured_model.get("auth_header") or "Authorization"),
                max_new_tokens=configured_model.get("max_new_tokens"),
                verify_tls=bool(configured_model.get("verify_tls", False)),
                # The managed Ark bbox capability uses the Responses image
                # protocol. Keep global-tag/legacy visual calls on Chat
                # Completions unless an embedded caller explicitly selects
                # another protocol.
                request_protocol=resolved_protocol,
            )
            result["_image_size"] = [original_w, original_h]
            if (w, h) != (original_w, original_h):
                result["_geometry_scale"] = [original_w / w, original_h / h]
            result["_trace"] = _vision_trace(
                provider_label=str(configured_model.get("name") or "通用视觉模型"),
                base_url=base_url,
                model=resolved_model,
                request_protocol=resolved_protocol,
                instruction=instruction,
                model_width=w,
                model_height=h,
                original_width=original_w,
                original_height=original_h,
            )
            return result
        except Exception as exc:
            logger.warning("configured vision model failed: {}", str(exc)[:300])
            raise
    if has_configured_model_integration(provider_key):
        raise RuntimeError(
            f"{provider_key} 模型已配置但尚未通过连通性检查，请到系统管理中检查"
        )

    ov_chain = load_ai_labeling_runtime()
    enabled = set(ov_chain.get("enabled_providers") or ["volcengine"])

    attempts: list[tuple[str, str, str, str]] = []
    profiles_by_id = {p["id"]: p for p in ov_chain.get("llm_profiles") or []}
    chain_ids = ov_chain.get("annotation_chain_profile_ids") or []
    for pid in chain_ids:
        p = profiles_by_id.get(pid)
        if not p:
            continue
        key = (p.get("api_key") or "").strip()
        base = (p.get("base_url") or "").strip()
        mname = (p.get("model") or "").strip()
        label = (p.get("name") or "").strip() or pid
        try:
            base = _safe_visual_base_url(base, mname, label)
        except ValueError:
            continue
        if key and base and mname:
            attempts.append(
                (label, base, key, normalize_volcengine_ark_model_id(mname))
            )

    if attempts:
        last_err: Exception | None = None
        for label, base, key, mname in attempts:
            try:
                result = _openai_compatible_infer(
                    provider_label=label,
                    base_url=base,
                    api_key=key,
                    model=mname,
                    instruction=instruction,
                    image_bytes=img_bytes,
                    mime="image/jpeg",
                )
                result["_image_size"] = [original_w, original_h]
                if (w, h) != (original_w, original_h):
                    result["_geometry_scale"] = [original_w / w, original_h / h]
                result["_trace"] = _vision_trace(
                    provider_label=label,
                    base_url=base,
                    model=mname,
                    request_protocol="openai_compatible",
                    instruction=instruction,
                    model_width=w,
                    model_height=h,
                    original_width=original_w,
                    original_height=original_h,
                )
                return result
            except Exception as exc:
                last_err = exc
                logger.warning("vision profile {} failed: {}", label, str(exc)[:300])
        assert last_err is not None
        raise last_err

    if "volcengine" in enabled:
        if (settings.VISION_VOLCENGINE_API_KEY or "").strip():
            attempts.append(
                (
                    "volcengine",
                    validate_volcengine_coding_endpoint(
                        settings.VISION_VOLCENGINE_BASE_URL
                    ),
                    settings.VISION_VOLCENGINE_API_KEY.strip(),
                    normalize_volcengine_ark_model_id(settings.VISION_VOLCENGINE_MODEL),
                )
            )
    if not attempts:
        raise RuntimeError(
            "未配置可用的视觉模型：请在系统管理添加火山 Coding Plan 并完成连通性检查"
        )

    last_err: Exception | None = None
    for label, base, key, mname in attempts:
        try:
            result = _openai_compatible_infer(
                provider_label=label,
                base_url=base,
                api_key=key,
                model=mname,
                instruction=instruction,
                image_bytes=img_bytes,
                mime="image/jpeg",
            )
            result["_image_size"] = [original_w, original_h]
            if (w, h) != (original_w, original_h):
                result["_geometry_scale"] = [original_w / w, original_h / h]
            return result
        except Exception as exc:
            last_err = exc
            logger.warning("vision chain step {} failed: {}", label, str(exc)[:300])

    assert last_err is not None
    raise last_err
