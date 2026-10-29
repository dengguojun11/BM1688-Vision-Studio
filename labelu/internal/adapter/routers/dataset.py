from typing import List

from fastapi import APIRouter, Depends, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.dataset import (
    CreateDatasetCommand,
    CreateDatasetLabelContractCommand,
    CreateDatasetVersionCommand,
    CreateDatasetVersionFromTasksCommand,
    ReviewDatasetVersionCommand,
    UpdateDatasetCommand,
    UpdateDatasetVersionCommand,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.dataset import (
    DatasetResponse,
    DatasetLabelContractResponse,
    DatasetVersionResponse,
    DatasetVersionValidationResponse,
)
from labelu.internal.application.service import dataset as service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/datasets", tags=["datasets"])
version_router = APIRouter(prefix="/dataset-versions", tags=["datasets"])


@router.post(
    "", response_model=OkResp[DatasetResponse], status_code=status.HTTP_201_CREATED
)
async def create(
    cmd: CreateDatasetCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[DatasetResponse](data=await service.create(db, cmd, current_user))


@router.get("", response_model=OkRespWithMeta[List[DatasetResponse]])
async def list_all(
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    q: str | None = Query(default=None, max_length=128),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data, total = await service.list_by(db, current_user, page, size, search=q)
    return OkRespWithMeta[List[DatasetResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@router.get(
    "/{dataset_id}/label-contracts",
    response_model=OkResp[List[DatasetLabelContractResponse]],
)
async def list_label_contracts(
    dataset_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    return OkResp[List[DatasetLabelContractResponse]](
        data=await service.list_label_contracts(db, dataset_id, current_user)
    )


@router.get(
    "/{dataset_id}/label-contracts/current",
    response_model=OkResp[DatasetLabelContractResponse | None],
)
async def current_label_contract(
    dataset_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    return OkResp[DatasetLabelContractResponse | None](
        data=await service.current_label_contract(db, dataset_id, current_user)
    )


@router.post(
    "/{dataset_id}/label-contracts",
    response_model=OkResp[DatasetLabelContractResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_label_contract(
    dataset_id: int,
    cmd: CreateDatasetLabelContractCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    return OkResp[DatasetLabelContractResponse](
        data=await service.create_label_contract(
            db,
            dataset_id,
            cmd.label_ids,
            cmd.annotation_kind,
            current_user,
        )
    )


@router.post(
    "/{dataset_id}/label-contracts/{revision}/freeze",
    response_model=OkResp[DatasetLabelContractResponse],
)
async def freeze_label_contract_revision(
    dataset_id: int,
    revision: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    return OkResp[DatasetLabelContractResponse](
        data=await service.freeze_label_contract_revision(
            db, dataset_id, revision, current_user
        )
    )


@router.get("/{dataset_id}", response_model=OkResp[DatasetResponse])
async def get(
    dataset_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[DatasetResponse](data=await service.get(db, dataset_id, current_user))


@router.patch("/{dataset_id}", response_model=OkResp[DatasetResponse])
async def update(
    dataset_id: int,
    cmd: UpdateDatasetCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[DatasetResponse](
        data=await service.update(db, dataset_id, cmd, current_user)
    )


@router.post(
    "/{dataset_id}/versions",
    response_model=OkResp[DatasetVersionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_version(
    dataset_id: int,
    cmd: CreateDatasetVersionCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[DatasetVersionResponse](
        data=await service.create_version(db, dataset_id, cmd, current_user)
    )


@version_router.post(
    "/{version_id}/label-contract-freeze",
    response_model=OkResp[DatasetLabelContractResponse],
)
async def freeze_label_contract(
    version_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    return OkResp[DatasetLabelContractResponse](
        data=await service.freeze_label_contract(db, version_id, current_user)
    )


@router.post(
    "/{dataset_id}/versions/from-tasks",
    response_model=OkResp[DatasetVersionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_version_from_tasks(
    dataset_id: int,
    cmd: CreateDatasetVersionFromTasksCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate a reproducible training version from completed task samples."""
    return OkResp[DatasetVersionResponse](
        data=await service.create_version_from_tasks(db, dataset_id, cmd, current_user)
    )


@router.get(
    "/{dataset_id}/versions", response_model=OkRespWithMeta[List[DatasetVersionResponse]]
)
async def list_versions(
    dataset_id: int,
    page: int = Query(default=0, ge=0),
    size: int = Query(default=20, ge=1, le=500),
    q: str | None = Query(default=None, max_length=128),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    # Validate dataset access before applying the paged query. The shared
    # listing service then keeps the same visibility rules for collaborators.
    await service.get(db, dataset_id, current_user)
    data, total = await service.list_all_versions(
        db,
        current_user,
        page=page,
        size=size,
        search=q,
        dataset_id=dataset_id,
    )
    return OkRespWithMeta[List[DatasetVersionResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@version_router.get("", response_model=OkRespWithMeta[List[DatasetVersionResponse]])
async def list_all_versions(
    page: int = Query(default=0, ge=0),
    size: int = Query(default=20, ge=1, le=500),
    q: str | None = Query(default=None, max_length=128),
    active_learning_only: bool = Query(default=False),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Search and paginate versions across the user's visible datasets."""

    _ = authorization
    data, total = await service.list_all_versions(
        db,
        current_user,
        page,
        size,
        search=q,
        active_learning_only=active_learning_only,
    )
    return OkRespWithMeta[List[DatasetVersionResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@version_router.patch("/{version_id}", response_model=OkResp[DatasetVersionResponse])
async def update_version(
    version_id: int,
    cmd: UpdateDatasetVersionCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[DatasetVersionResponse](
        data=await service.update_version(db, version_id, cmd, current_user)
    )


@version_router.get("/{version_id}", response_model=OkResp[DatasetVersionResponse])
async def get_version(
    version_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[DatasetVersionResponse](
        data=await service.get_version_by_id(db, version_id, current_user)
    )


@version_router.post(
    "/{version_id}/review", response_model=OkResp[DatasetVersionResponse]
)
async def review_version(
    version_id: int,
    cmd: ReviewDatasetVersionCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[DatasetVersionResponse](
        data=await service.review_version(db, version_id, cmd, current_user)
    )


@version_router.post(
    "/{version_id}/freeze", response_model=OkResp[DatasetVersionResponse]
)
async def freeze_version(
    version_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[DatasetVersionResponse](
        data=await service.freeze_version(db, version_id, current_user)
    )


@version_router.get(
    "/{version_id}/validate", response_model=OkResp[DatasetVersionValidationResponse]
)
async def validate_version(
    version_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[DatasetVersionValidationResponse](
        data=await service.validate_version(db, version_id, current_user)
    )
