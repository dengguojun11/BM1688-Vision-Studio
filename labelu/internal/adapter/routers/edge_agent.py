from typing import Optional

from fastapi import APIRouter, Depends, Header, Path as PathParam, Query, Request, Response, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.edge_agent import (
    ActivateEdgeAgentOtaCommand,
    CreateEdgeAgentReleaseCommand,
    HeartbeatEdgeAgentCommand,
    RegisterEdgeAgentCommand,
    ReportEdgeAgentCapabilitiesCommand,
    RollbackEdgeAgentOtaCommand,
    VerifyEdgeAgentOtaCommand,
)
from labelu.internal.application.command.rule import ReportRuleSignatureCommand
from labelu.internal.application.command.deployment import (
    CreateDeploymentObservationCommand,
    ReportDeploymentTargetCommand,
)
from labelu.internal.application.response.base import OkResp, OkRespWithMeta
from labelu.internal.application.response.edge_agent import (
    EdgeAgentDeploymentResponse,
    EdgeAgentDesiredStateResponse,
    EdgeAgentManifestResponse,
    EdgeAgentRegistrationResponse,
    EdgeAgentReleaseResponse,
    EdgeAgentResponse,
)
from labelu.internal.application.response.rule import RuleDeviceSignatureReportResponse
from labelu.internal.application.response.deployment import (
    DeploymentObservationResponse,
    DeploymentTargetResponse,
)
from labelu.internal.application.service import edge_agent as service
from labelu.internal.application.service import rule as rule_service
from labelu.internal.application.service import deployment as deployment_service
from labelu.internal.common import db as db_module
from labelu.internal.common.config import settings
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import require_system_admin
from labelu.internal.domain.models.user import User
from labelu.internal.adapter.routers._phase_one_gate import (
    ensure_legacy_control_plane_open,
    hide_router_from_openapi,
    require_legacy_control_plane_write,
)

router = APIRouter(prefix="/edge-agents", tags=["edge_agents"])


def _agent_auth(
    agent_id: str,
    x_agent_token: Optional[str] = Header(default=None, alias="X-Seven-GAI-Agent-Token"),
    x_board_token: Optional[str] = Header(default=None, alias="X-Board-Agent-Token"),
    db: Session = Depends(db_module.get_db),
):
    ensure_legacy_control_plane_open()
    return service.authenticate(db, agent_id, x_agent_token or x_board_token or "")


@router.post("/register", response_model=OkResp[EdgeAgentRegistrationResponse], status_code=status.HTTP_201_CREATED)
async def register(cmd: RegisterEdgeAgentCommand, request: Request, db: Session = Depends(db_module.get_db)):
    ensure_legacy_control_plane_open()
    data = service.register(db, cmd, client_ip=request.client.host if request.client else None)
    db.commit()
    return OkResp[EdgeAgentRegistrationResponse](data=data)


@router.get("/monitor", response_model=OkRespWithMeta[list[EdgeAgentResponse]])
async def monitor(
    project_id: int | None = Query(default=None, gt=0),
    search: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description="按 Agent 名称、标识、平台、版本或状态搜索",
    ),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization
    rows, total = service.list_agents_page(
        db,
        project_id,
        page=page,
        size=size,
        search=search,
        user=current_user,
    )
    return OkRespWithMeta[list[EdgeAgentResponse]](
        data=rows, meta_data={"total": total, "page": page, "size": len(rows)}
    )


@router.post("/{agent_id}/heartbeat", response_model=OkResp[EdgeAgentResponse])
async def heartbeat(agent_id: str, cmd: HeartbeatEdgeAgentCommand, request: Request, agent=Depends(_agent_auth), db: Session = Depends(db_module.get_db)):
    data = service.heartbeat(db, agent, cmd, client_ip=request.client.host if request.client else None)
    db.commit()
    return OkResp[EdgeAgentResponse](data=data)


@router.post("/{agent_id}/capabilities", response_model=OkResp[EdgeAgentResponse])
async def capabilities(agent_id: str, cmd: ReportEdgeAgentCapabilitiesCommand, agent=Depends(_agent_auth), db: Session = Depends(db_module.get_db)):
    data = service.capabilities(db, agent, cmd)
    db.commit()
    return OkResp[EdgeAgentResponse](data=data)


@router.post(
    "/{agent_id}/rule-bindings/{binding_id}/signature-report",
    response_model=OkResp[RuleDeviceSignatureReportResponse],
)
async def report_rule_signature(
    agent_id: str,
    binding_id: int,
    cmd: ReportRuleSignatureCommand,
    agent=Depends(_agent_auth),
    db: Session = Depends(db_module.get_db),
):
    data = await rule_service.report_device_signature(db, agent, binding_id, cmd)
    db.commit()
    return OkResp[RuleDeviceSignatureReportResponse](data=data)


@router.post(
    "/{agent_id}/deployments/{release_id}/report",
    response_model=OkResp[DeploymentTargetResponse],
)
async def report_deployment_target(
    agent_id: str,
    release_id: int,
    cmd: ReportDeploymentTargetCommand,
    agent=Depends(_agent_auth),
    db: Session = Depends(db_module.get_db),
):
    """Accept a deployment target report authenticated by the Agent token.

    The legacy deployment route still accepts the board-level token for
    compatibility.  New edge devices use their scoped registration token so
    one device cannot report another device's target.
    """
    data = await deployment_service.report_target(db, release_id, agent.agent_id, cmd)
    return OkResp[DeploymentTargetResponse](data=data)


@router.post(
    "/{agent_id}/deployments/{release_id}/observe",
    response_model=OkResp[DeploymentObservationResponse],
    status_code=status.HTTP_201_CREATED,
)
async def observe_deployment(
    agent_id: str,
    release_id: int,
    cmd: CreateDeploymentObservationCommand,
    agent=Depends(_agent_auth),
    db: Session = Depends(db_module.get_db),
):
    """Submit a deployment health observation using scoped Agent auth."""
    data = await deployment_service.observe(db, release_id, agent.agent_id, cmd)
    return OkResp[DeploymentObservationResponse](data=data)


@router.get("/{agent_id}/desired", response_model=OkResp[EdgeAgentDesiredStateResponse])
async def desired(agent_id: str, agent=Depends(_agent_auth)):
    return OkResp[EdgeAgentDesiredStateResponse](data=service.desired_state(agent))


@router.get("/{agent_id}/ota/{release_id}/manifest", response_model=OkResp[EdgeAgentManifestResponse])
async def manifest(agent_id: str, release_id: int, request: Request, agent=Depends(_agent_auth), db: Session = Depends(db_module.get_db)):
    base = str(request.base_url).rstrip("/") + settings.API_V1_STR
    data = service.manifest(db, agent, release_id, base)
    db.commit()
    return OkResp[EdgeAgentManifestResponse](data=data)


@router.get("/{agent_id}/ota/{release_id}/chunks/{index}")
async def chunk(agent_id: str, release_id: int, index: int = PathParam(ge=0), agent=Depends(_agent_auth), db: Session = Depends(db_module.get_db)):
    payload, version, chunk_size = service.chunk(db, agent, release_id, index)
    db.commit()
    return Response(content=payload, media_type="application/octet-stream", headers={"X-7GAI-Agent-Version": version, "X-7GAI-Chunk-Size": str(chunk_size), "X-7GAI-Chunk-Index": str(index)})


@router.post("/{agent_id}/ota/{release_id}/verify", response_model=OkResp[EdgeAgentDeploymentResponse])
async def verify(agent_id: str, release_id: int, cmd: VerifyEdgeAgentOtaCommand, agent=Depends(_agent_auth), db: Session = Depends(db_module.get_db)):
    data = service.verify(db, agent, release_id, cmd)
    db.commit()
    return OkResp[EdgeAgentDeploymentResponse](data=data)


@router.post("/{agent_id}/ota/{release_id}/activate", response_model=OkResp[EdgeAgentDeploymentResponse])
async def activate(agent_id: str, release_id: int, cmd: ActivateEdgeAgentOtaCommand, agent=Depends(_agent_auth), db: Session = Depends(db_module.get_db)):
    data = service.activate(db, agent, release_id, cmd)
    db.commit()
    return OkResp[EdgeAgentDeploymentResponse](data=data)


@router.post("/{agent_id}/ota/{release_id}/rollback", response_model=OkResp[EdgeAgentDeploymentResponse])
async def rollback(agent_id: str, release_id: int, cmd: RollbackEdgeAgentOtaCommand, agent=Depends(_agent_auth), db: Session = Depends(db_module.get_db)):
    data = service.rollback(db, agent, release_id, cmd)
    db.commit()
    return OkResp[EdgeAgentDeploymentResponse](data=data)


@router.get("", response_model=OkResp[list[EdgeAgentResponse]])
async def list_agents(
    project_id: int | None = None,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    return OkResp[list[EdgeAgentResponse]](
        data=service.list_agents(db, project_id, user=current_user)
    )


@router.get("/releases", response_model=OkResp[list[EdgeAgentReleaseResponse]])
async def list_releases(
    project_id: int | None = None,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    return OkResp[list[EdgeAgentReleaseResponse]](
        data=service.list_releases(db, project_id, user=current_user)
    )


@router.post("/releases", response_model=OkResp[EdgeAgentReleaseResponse], status_code=status.HTTP_201_CREATED)
async def create_release(
    cmd: CreateEdgeAgentReleaseCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_legacy_control_plane_write),
):
    data = service.create_release(db, cmd, current_user)
    db.commit()
    return OkResp[EdgeAgentReleaseResponse](data=data)


hide_router_from_openapi(router)
