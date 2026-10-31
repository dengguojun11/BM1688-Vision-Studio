from typing import List

from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials

from labelu.internal.application.command.annotation_taxonomy import (
    ApplyGlobalTagsCommand,
    ApplyLabelPresetCommand,
    ApplyTaskLabelsCommand,
    CreateAnnotationLabelCommand,
    CreateDatasetCategoryCommand,
    CreateLabelAliasCommand,
    CreateGlobalTagDimensionCommand,
    CreateLabelPresetCommand,
    UpdateGlobalTagDimensionCommand,
    UpdateLabelPresetCommand,
    UpdateAnnotationLabelCommand,
    UpdateDatasetCategoryCommand,
    UpdateLabelAliasCommand,
    UpdateRuntimeTemplateCommand,
    UpsertRuntimeTemplateCommand,
    UpsertTrainingRecipeCommand,
)
from labelu.internal.application.response.annotation_taxonomy import (
    GlobalTagDimensionResponse,
    LabelPresetResponse,
    AnnotationLabelAliasResponse,
    AnnotationLabelResponse,
    DatasetCategoryResponse,
    RuntimePublishTemplateResponse,
    TaskAnnotationContractResponse,
    TrainingRecipeResponse,
)
from labelu.internal.application.response.base import CommonDataResp, MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.service import annotation_taxonomy as taxonomy_service
from labelu.internal.application.service import annotation_contract as contract_service
from labelu.internal.application.service import system_labels as system_label_service
from labelu.internal.application.service.algorithm import ensure_seeded
from labelu.internal.common.db import begin_transaction
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/annotation-taxonomy", tags=["annotation-taxonomy"])
catalog_router = APIRouter(tags=["annotation-taxonomy"])


@router.get("/labels", response_model=OkRespWithMeta[List[AnnotationLabelResponse]])
async def list_annotation_labels(
    page: int = Query(default=0, ge=0),
    size: int = Query(default=20, ge=1, le=500),
    q: str | None = Query(default=None, max_length=128),
    label_status: str | None = Query(
        default=None, alias="status", pattern="^(active|disabled)$"
    ),
    annotation_kind: str | None = Query(
        default=None, pattern="^(bbox|polygon)$"
    ),
    include_disabled: bool = Query(default=True),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data, total = system_label_service.list_labels(
        db,
        page=page,
        size=size,
        search=q,
        status_filter=label_status,
        annotation_kind=annotation_kind,
        include_disabled=include_disabled,
    )
    return OkRespWithMeta[List[AnnotationLabelResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@router.post("/labels", response_model=OkResp[AnnotationLabelResponse], status_code=status.HTTP_201_CREATED)
async def create_annotation_label(
    cmd: CreateAnnotationLabelCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    with begin_transaction(db):
        data = system_label_service.create_label(db, cmd, current_user)
    return OkResp[AnnotationLabelResponse](data=data)


@router.get("/labels/{label_id}", response_model=OkResp[AnnotationLabelResponse])
async def get_annotation_label(
    label_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[AnnotationLabelResponse](data=system_label_service.get_label(db, label_id))


@router.patch("/labels/{label_id}", response_model=OkResp[AnnotationLabelResponse])
async def update_annotation_label(
    label_id: int,
    cmd: UpdateAnnotationLabelCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    with begin_transaction(db):
        data = system_label_service.update_label(db, label_id, cmd, current_user)
    return OkResp[AnnotationLabelResponse](data=data)


@router.delete("/labels/{label_id}", response_model=OkResp[AnnotationLabelResponse])
async def disable_annotation_label(
    label_id: int,
    version: int = Query(..., ge=1),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    with begin_transaction(db):
        data = system_label_service.disable_label(db, label_id, version, current_user)
    return OkResp[AnnotationLabelResponse](data=data)


@router.get("/label-aliases", response_model=OkRespWithMeta[List[AnnotationLabelAliasResponse]])
async def list_label_aliases(
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    q: str | None = Query(default=None, max_length=128),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data, total = system_label_service.list_aliases_page(
        db, page=page, size=size, search=q
    )
    return OkRespWithMeta[List[AnnotationLabelAliasResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@router.post("/label-aliases", response_model=OkResp[AnnotationLabelAliasResponse], status_code=status.HTTP_201_CREATED)
async def create_label_alias(
    cmd: CreateLabelAliasCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    with begin_transaction(db): data = system_label_service.create_alias(db, cmd, current_user)
    return OkResp[AnnotationLabelAliasResponse](data=data)


@router.patch("/label-aliases/{alias_id}", response_model=OkResp[AnnotationLabelAliasResponse])
async def update_label_alias(
    alias_id: int,
    cmd: UpdateLabelAliasCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    with begin_transaction(db): data = system_label_service.update_alias(db, alias_id, cmd, current_user)
    return OkResp[AnnotationLabelAliasResponse](data=data)


@router.delete("/label-aliases/{alias_id}", response_model=OkResp[AnnotationLabelAliasResponse])
async def disable_label_alias(
    alias_id: int,
    version: int = Query(..., ge=1),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    with begin_transaction(db):
        data = system_label_service.disable_alias(db, alias_id, version, current_user)
    return OkResp[AnnotationLabelAliasResponse](data=data)


@router.get("/dataset-categories", response_model=OkRespWithMeta[List[DatasetCategoryResponse]])
async def list_dataset_categories(
    page: int = Query(default=0, ge=0),
    size: int = Query(default=20, ge=1, le=200),
    q: str | None = Query(default=None, max_length=128),
    category_status: str | None = Query(
        default=None, alias="status", pattern="^(active|disabled)$"
    ),
    include_disabled: bool = Query(default=True),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data, total = system_label_service.list_categories(
        db,
        page=page,
        size=size,
        search=q,
        status_filter=category_status,
        include_disabled=include_disabled,
        current_user=current_user,
    )
    return OkRespWithMeta[List[DatasetCategoryResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@router.post("/dataset-categories", response_model=OkResp[DatasetCategoryResponse], status_code=status.HTTP_201_CREATED)
async def create_dataset_category(
    cmd: CreateDatasetCategoryCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    with begin_transaction(db): data = system_label_service.create_category(db, cmd, current_user)
    return OkResp[DatasetCategoryResponse](data=data)


@router.patch("/dataset-categories/{category_id}", response_model=OkResp[DatasetCategoryResponse])
async def update_dataset_category(
    category_id: int,
    cmd: UpdateDatasetCategoryCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    with begin_transaction(db): data = system_label_service.update_category(db, category_id, cmd, current_user)
    return OkResp[DatasetCategoryResponse](data=data)


@router.delete("/dataset-categories/{category_id}", response_model=OkResp[DatasetCategoryResponse])
async def disable_dataset_category(
    category_id: int,
    version: int = Query(..., ge=1),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    with begin_transaction(db):
        data = system_label_service.delete_category(db, category_id, version, current_user)
    return OkResp[DatasetCategoryResponse](data=data)


@router.get("/training-recipes", response_model=OkRespWithMeta[List[TrainingRecipeResponse]])
async def list_training_recipes(
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    q: str | None = Query(default=None, max_length=128),
    authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)
):
    ensure_seeded(db)
    data, total = system_label_service.list_recipes_page(
        db, page=page, size=size, search=q
    )
    return OkRespWithMeta[List[TrainingRecipeResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@router.post("/training-recipes", response_model=OkResp[TrainingRecipeResponse], status_code=status.HTTP_201_CREATED)
async def create_training_recipe(
    cmd: UpsertTrainingRecipeCommand, authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)
):
    with begin_transaction(db): data = system_label_service.create_recipe(db, cmd, current_user)
    return OkResp[TrainingRecipeResponse](data=data)


@router.delete("/training-recipes/{recipe_id}", response_model=OkResp[TrainingRecipeResponse])
async def disable_training_recipe(
    recipe_id: int, authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)
):
    with begin_transaction(db): data = system_label_service.disable_recipe(db, recipe_id, current_user)
    return OkResp[TrainingRecipeResponse](data=data)


@router.get("/runtime-templates", response_model=OkRespWithMeta[List[RuntimePublishTemplateResponse]])
async def list_runtime_templates(
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    q: str | None = Query(default=None, max_length=128),
    authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)
):
    data, total = system_label_service.list_runtime_templates_page(
        db, page=page, size=size, search=q
    )
    return OkRespWithMeta[List[RuntimePublishTemplateResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@router.post("/runtime-templates", response_model=OkResp[RuntimePublishTemplateResponse], status_code=status.HTTP_201_CREATED)
async def create_runtime_template(
    cmd: UpsertRuntimeTemplateCommand, authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)
):
    with begin_transaction(db): data = system_label_service.create_runtime_template(db, cmd, current_user)
    return OkResp[RuntimePublishTemplateResponse](data=data)


@router.patch("/runtime-templates/{template_id}", response_model=OkResp[RuntimePublishTemplateResponse])
async def update_runtime_template(
    template_id: int,
    cmd: UpdateRuntimeTemplateCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    with begin_transaction(db):
        data = system_label_service.update_runtime_template(
            db, template_id, cmd, current_user
        )
    return OkResp[RuntimePublishTemplateResponse](data=data)


@router.delete("/runtime-templates/{template_id}", response_model=OkResp[RuntimePublishTemplateResponse])
async def disable_runtime_template(
    template_id: int, version: int = Query(..., ge=1), authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)
):
    with begin_transaction(db): data = system_label_service.disable_runtime_template(db, template_id, version, current_user)
    return OkResp[RuntimePublishTemplateResponse](data=data)


# --- 拉框预设 ---


@router.get(
    "/label-presets",
    response_model=OkRespWithMeta[List[LabelPresetResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_label_presets(
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    q: str | None = Query(default=None, max_length=128),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data, total = await taxonomy_service.label_preset_list(
        db, page=page, size=size, search=q
    )
    return OkRespWithMeta[List[LabelPresetResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)),
        data=data,
    )


@router.get(
    "/label-presets/{preset_id}",
    response_model=OkResp[LabelPresetResponse],
    status_code=status.HTTP_200_OK,
)
async def get_label_preset(
    preset_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = await taxonomy_service.label_preset_get(db, preset_id=preset_id)
    return OkResp[LabelPresetResponse](data=data)


@router.post(
    "/label-presets",
    response_model=OkResp[LabelPresetResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_label_preset(
    cmd: CreateLabelPresetCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    system_label_service.assert_taxonomy_admin(current_user)
    data = await taxonomy_service.label_preset_create(db, cmd=cmd)
    return OkResp[LabelPresetResponse](data=data)


@router.patch(
    "/label-presets/{preset_id}",
    response_model=OkResp[LabelPresetResponse],
    status_code=status.HTTP_200_OK,
)
async def update_label_preset(
    preset_id: int,
    cmd: UpdateLabelPresetCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    system_label_service.assert_taxonomy_admin(current_user)
    data = await taxonomy_service.label_preset_update(db, preset_id=preset_id, cmd=cmd)
    return OkResp[LabelPresetResponse](data=data)


@router.delete(
    "/label-presets/{preset_id}",
    response_model=OkResp[CommonDataResp],
    status_code=status.HTTP_200_OK,
)
async def delete_label_preset(
    preset_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    system_label_service.assert_taxonomy_admin(current_user)
    await taxonomy_service.label_preset_delete(db, preset_id=preset_id)
    return OkResp[CommonDataResp](data=CommonDataResp(ok=True))


# --- 全局属性维度 ---


@router.get(
    "/global-tag-dimensions",
    response_model=OkRespWithMeta[List[GlobalTagDimensionResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_global_dimensions(
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    q: str | None = Query(default=None, max_length=128),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data, total = await taxonomy_service.global_dimension_list(
        db, page=page, size=size, search=q
    )
    return OkRespWithMeta[List[GlobalTagDimensionResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)),
        data=data,
    )


@router.get(
    "/global-tag-dimensions/{dimension_id}",
    response_model=OkResp[GlobalTagDimensionResponse],
    status_code=status.HTTP_200_OK,
)
async def get_global_dimension(
    dimension_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = await taxonomy_service.global_dimension_get(db, dimension_id=dimension_id)
    return OkResp[GlobalTagDimensionResponse](data=data)


@router.post(
    "/global-tag-dimensions",
    response_model=OkResp[GlobalTagDimensionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_global_dimension(
    cmd: CreateGlobalTagDimensionCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    system_label_service.assert_taxonomy_admin(current_user)
    data = await taxonomy_service.global_dimension_create(db, cmd=cmd)
    return OkResp[GlobalTagDimensionResponse](data=data)


@router.patch(
    "/global-tag-dimensions/{dimension_id}",
    response_model=OkResp[GlobalTagDimensionResponse],
    status_code=status.HTTP_200_OK,
)
async def update_global_dimension(
    dimension_id: int,
    cmd: UpdateGlobalTagDimensionCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    system_label_service.assert_taxonomy_admin(current_user)
    data = await taxonomy_service.global_dimension_update(
        db, dimension_id=dimension_id, cmd=cmd
    )
    return OkResp[GlobalTagDimensionResponse](data=data)


@router.delete(
    "/global-tag-dimensions/{dimension_id}",
    response_model=OkResp[CommonDataResp],
    status_code=status.HTTP_200_OK,
)
async def delete_global_dimension(
    dimension_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    system_label_service.assert_taxonomy_admin(current_user)
    await taxonomy_service.global_dimension_delete(db, dimension_id=dimension_id)
    return OkResp[CommonDataResp](data=CommonDataResp(ok=True))


# Root-level catalog routes are the public contract documented for dataset and
# execution configuration. The namespaced routes remain available to existing
# extension pages during the compatibility window.
catalog_router.add_api_route(
    "/dataset-categories",
    list_dataset_categories,
    methods=["GET"],
    response_model=OkRespWithMeta[List[DatasetCategoryResponse]],
    name="list_dataset_categories_catalog",
)
catalog_router.add_api_route(
    "/dataset-categories",
    create_dataset_category,
    methods=["POST"],
    response_model=OkResp[DatasetCategoryResponse],
    status_code=status.HTTP_201_CREATED,
    name="create_dataset_category_catalog",
)
catalog_router.add_api_route(
    "/dataset-categories/{category_id}",
    update_dataset_category,
    methods=["PATCH"],
    response_model=OkResp[DatasetCategoryResponse],
    name="update_dataset_category_catalog",
)
catalog_router.add_api_route(
    "/dataset-categories/{category_id}",
    disable_dataset_category,
    methods=["DELETE"],
    response_model=OkResp[DatasetCategoryResponse],
    name="disable_dataset_category_catalog",
)
catalog_router.add_api_route(
    "/training-recipes",
    list_training_recipes,
    methods=["GET"],
    response_model=OkRespWithMeta[List[TrainingRecipeResponse]],
    name="list_training_recipes_catalog",
)
catalog_router.add_api_route(
    "/training-recipes",
    create_training_recipe,
    methods=["POST"],
    response_model=OkResp[TrainingRecipeResponse],
    status_code=status.HTTP_201_CREATED,
    name="create_training_recipe_catalog",
)
catalog_router.add_api_route(
    "/runtime-publish-templates",
    list_runtime_templates,
    methods=["GET"],
    response_model=OkRespWithMeta[List[RuntimePublishTemplateResponse]],
    name="list_runtime_publish_templates_catalog",
)
catalog_router.add_api_route(
    "/runtime-publish-templates",
    create_runtime_template,
    methods=["POST"],
    response_model=OkResp[RuntimePublishTemplateResponse],
    status_code=status.HTTP_201_CREATED,
    name="create_runtime_publish_template_catalog",
)
