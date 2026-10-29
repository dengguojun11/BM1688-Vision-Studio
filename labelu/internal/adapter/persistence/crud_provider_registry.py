from __future__ import annotations

from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.provider_registry import ProviderRegistry


def list_providers(
    db: Session,
    *,
    status: str | None = "ACTIVE",
    capability: str | None = None,
    search: str | None = None,
    page: int | None = None,
    size: int | None = None,
) -> list[ProviderRegistry] | tuple[list[ProviderRegistry], int]:
    """Return catalog rows; JSON filtering is intentionally done in Python.

    SQLite and MySQL use different JSON operators in supported deployments.
    Keeping the small catalog query portable avoids coupling discovery to a
    vendor-specific expression while preserving deterministic ordering.
    """
    query = db.query(ProviderRegistry)
    if status:
        query = query.filter(ProviderRegistry.status == status)
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        query = query.filter(
            or_(
                cast(ProviderRegistry.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                ProviderRegistry.provider_key.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderRegistry.provider_version.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderRegistry.display_name.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderRegistry.provider_kind.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderRegistry.runtime_adapter.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderRegistry.status.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderRegistry.image_digest.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderRegistry.toolchain_version.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderRegistry.availability_reason.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderRegistry.capabilities_json.ilike(pattern, escape=LIKE_ESCAPE),
                ProviderRegistry.supported_media_types_json.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                ProviderRegistry.supported_chips_json.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    rows = query.order_by(
        ProviderRegistry.display_name.asc(),
        ProviderRegistry.provider_key.asc(),
        ProviderRegistry.provider_version.desc(),
    ).all()
    if capability:
        wanted = str(capability).strip().upper()
        rows = [row for row in rows if wanted in _json_list(row.capabilities_json)]
    if page is None or size is None:
        return rows
    total = len(rows)
    start = max(0, int(page)) * max(1, int(size))
    return rows[start : start + max(1, int(size))], total


def get_provider(
    db: Session, provider_key: str, provider_version: str | None = None
) -> ProviderRegistry | None:
    query = db.query(ProviderRegistry).filter(
        ProviderRegistry.provider_key == str(provider_key).strip().lower()
    )
    if provider_version:
        query = query.filter(ProviderRegistry.provider_version == str(provider_version).strip())
    return query.order_by(ProviderRegistry.provider_version.desc()).first()


def _json_list(value: str | None) -> list[str]:
    import json

    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(item).upper() for item in parsed] if isinstance(parsed, list) else []
