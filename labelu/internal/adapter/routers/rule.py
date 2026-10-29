from typing import List

from fastapi import APIRouter, Depends, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.rule import (
    BindRuleCommand,
    CreateRuleTemplateCommand,
    CreateRuleVersionCommand,
    RuleTransitionCommand,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.rule import (
    RuleAuditResponse,
    RuleBindingResponse,
    RuleDeviceSignatureReportResponse,
    RuleTemplateResponse,
    RuleVersionAuditResponse,
    RuleVersionResponse,
)
from labelu.internal.application.service import rule as service
from labelu.internal.adapter.routers._phase_one_gate import (
    hide_router_from_openapi,
    require_legacy_control_plane_write,
)
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import require_system_admin
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/rule-templates", tags=["rule-center"])


@router.post(
    "", response_model=OkResp[RuleTemplateResponse], status_code=status.HTTP_201_CREATED
)
async def create_template(
    cmd: CreateRuleTemplateCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    return OkResp[RuleTemplateResponse](
        data=await service.create_template(db, cmd, current_user)
    )


@router.get("", response_model=OkRespWithMeta[List[RuleTemplateResponse]])
async def list_templates(
    project_id: int = Query(gt=0),
    search: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description="按模板名称、标识、算法、状态或描述搜索",
    ),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    data, total = await service.list_templates(
        db,
        project_id,
        current_user,
        search=search,
        page=page,
        size=size,
    )
    return OkRespWithMeta[List[RuleTemplateResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@router.post(
    "/{template_id}/versions",
    response_model=OkResp[RuleVersionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_version(
    template_id: int,
    cmd: CreateRuleVersionCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    return OkResp[RuleVersionResponse](
        data=await service.create_version(db, template_id, cmd, current_user)
    )


@router.get(
    "/{template_id}/versions",
    response_model=OkRespWithMeta[List[RuleVersionResponse]],
)
async def list_versions(
    template_id: int,
    search: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description="按版本号、Schema、配置摘要、状态或签名信息搜索",
    ),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    data, total = await service.list_versions(
        db,
        template_id,
        current_user,
        search=search,
        page=page,
        size=size,
    )
    return OkRespWithMeta[List[RuleVersionResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@router.post(
    "/versions/{version_id}/publish", response_model=OkResp[RuleVersionResponse]
)
async def publish_version(
    version_id: int,
    cmd: RuleTransitionCommand | None = None,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    return OkResp[RuleVersionResponse](
        data=await service.publish_version(
            db, version_id, cmd or RuleTransitionCommand(), current_user
        )
    )


@router.post("/bindings/{device_id}", response_model=OkResp[RuleBindingResponse])
async def bind(
    device_id: str,
    project_id: int = Query(gt=0),
    cmd: BindRuleCommand = ...,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    return OkResp[RuleBindingResponse](
        data=await service.bind(db, project_id, device_id, cmd, current_user)
    )


@router.get("/bindings", response_model=OkRespWithMeta[List[RuleBindingResponse]])
async def list_bindings(
    project_id: int = Query(gt=0),
    device_id: str | None = Query(default=None, max_length=128),
    search: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description="按设备、规则版本、状态或验签信息搜索",
    ),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    data, total = await service.list_bindings(
        db,
        project_id,
        device_id,
        current_user,
        search=search,
        page=page,
        size=size,
    )
    return OkRespWithMeta[List[RuleBindingResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@router.post(
    "/bindings/{binding_id}/unbind", response_model=OkResp[RuleBindingResponse]
)
async def unbind(
    binding_id: int,
    cmd: RuleTransitionCommand | None = None,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    return OkResp[RuleBindingResponse](
        data=await service.unbind(
            db, binding_id, cmd or RuleTransitionCommand(), current_user
        )
    )


@router.post(
    "/bindings/{device_id}/rollback", response_model=OkResp[RuleBindingResponse]
)
async def rollback(
    device_id: str,
    project_id: int = Query(gt=0),
    cmd: BindRuleCommand = ...,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    return OkResp[RuleBindingResponse](
        data=await service.rollback(db, project_id, device_id, cmd, current_user)
    )


@router.get(
    "/binding-audits", response_model=OkRespWithMeta[List[RuleAuditResponse]]
)
async def list_audits(
    project_id: int = Query(gt=0),
    search: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description="按设备、操作、状态、原因或幂等键搜索",
    ),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    data, total = await service.list_audits(
        db,
        project_id,
        current_user,
        search=search,
        page=page,
        size=size,
    )
    return OkRespWithMeta[List[RuleAuditResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@router.get(
    "/version-audits", response_model=OkRespWithMeta[List[RuleVersionAuditResponse]]
)
async def list_version_audits(
    project_id: int = Query(gt=0),
    search: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description="按模板、版本、操作、状态、原因或幂等键搜索",
    ),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    data, total = await service.list_version_audits(
        db,
        project_id,
        current_user,
        search=search,
        page=page,
        size=size,
    )
    return OkRespWithMeta[List[RuleVersionAuditResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@router.get(
    "/device-signature-reports",
    response_model=OkRespWithMeta[List[RuleDeviceSignatureReportResponse]],
)
async def list_device_signature_reports(
    project_id: int = Query(gt=0),
    device_id: str | None = Query(default=None, max_length=128),
    search: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description="按设备、报告、状态、摘要或失败原因搜索",
    ),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    data, total = await service.list_device_signature_reports(
        db,
        project_id,
        device_id,
        current_user,
        search=search,
        page=page,
        size=size,
    )
    return OkRespWithMeta[List[RuleDeviceSignatureReportResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


hide_router_from_openapi(router)
