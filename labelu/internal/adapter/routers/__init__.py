from fastapi import FastAPI

from labelu.internal.common.api_route_logging import instrument_router
from labelu.internal.common.config import settings
from labelu.internal.adapter.routers import user
from labelu.internal.adapter.routers import task
from labelu.internal.adapter.routers import sample
from labelu.internal.adapter.routers import attachment
from labelu.internal.adapter.routers import pre_annotation
from labelu.internal.adapter.routers import datasource
from labelu.internal.adapter.routers import annotation_taxonomy
from labelu.internal.adapter.routers import yolo_train
from labelu.internal.adapter.routers import yolo_eval_prompts
from labelu.internal.adapter.routers import ai_labeling_settings
from labelu.internal.adapter.routers import board_agent
from labelu.internal.adapter.routers import edge_device_job
from labelu.internal.adapter.routers import tagtool_preannotate
from labelu.internal.adapter.routers import yolo_aicm_publish
from labelu.internal.adapter.routers import event_review
from labelu.internal.adapter.routers import experiment_dataset_import
from labelu.internal.adapter.routers import evolution_imports
from labelu.internal.adapter.routers import strategy_task_integration
from labelu.internal.adapter.routers import strategy_integration_s3_config
from labelu.internal.adapter.routers import strategy_evolution
from labelu.internal.adapter.routers import local_dev
from labelu.internal.adapter.routers import auto_training
from labelu.internal.adapter.routers import third_party_calls
from labelu.internal.adapter.routers import published_model_catalog
from labelu.internal.adapter.routers import health
from labelu.internal.adapter.routers import project
from labelu.internal.adapter.routers import organization
from labelu.internal.adapter.routers import dataset
from labelu.internal.adapter.routers import algorithm
from labelu.internal.adapter.routers import model_version
from labelu.internal.adapter.routers import deployment
from labelu.internal.adapter.routers import workflow
from labelu.internal.adapter.routers import workspace
from labelu.internal.adapter.routers import edge_feedback
from labelu.internal.adapter.routers import annotation_review
from labelu.internal.adapter.routers import active_learning
from labelu.internal.adapter.routers import data_governance
from labelu.internal.adapter.routers import edge_agent
from labelu.internal.adapter.routers import provider_registry
from labelu.internal.adapter.routers import model_access_config
from labelu.internal.adapter.routers import provider_job
from labelu.internal.adapter.routers import ingestion
from labelu.internal.adapter.routers import project_ownership
from labelu.internal.adapter.routers import rule
from labelu.internal.adapter.routers import training
from labelu.internal.adapter.routers import evolution_screen
from labelu.internal.adapter.routers import model_series
from labelu.internal.adapter.routers import annotation_prompt


def _include(app: FastAPI, router, prefix: str | None = None) -> None:
    instrument_router(router)
    app.include_router(router, prefix=prefix if prefix is not None else settings.API_V1_STR)


def add_router(app: FastAPI):
    # Health probes are intentionally root-level and unauthenticated so load
    # balancers do not depend on the versioned API or user database state.
    _include(app, health.router, prefix="")
    _include(app, project.router)
    _include(app, organization.router)
    _include(app, organization.scene_router)
    _include(app, dataset.router)
    _include(app, dataset.version_router)
    _include(app, algorithm.groups_router)
    _include(app, algorithm.router)
    _include(app, model_version.router)
    _include(app, deployment.router)
    _include(app, model_version.compat_router)
    _include(app, model_series.router)
    _include(app, annotation_prompt.router)
    _include(app, workflow.router)
    _include(app, workspace.router)
    _include(app, edge_feedback.router)
    _include(app, annotation_review.router)
    _include(app, active_learning.router)
    _include(app, data_governance.router)
    _include(app, edge_agent.router)
    _include(app, provider_registry.router)
    _include(app, model_access_config.router)
    _include(app, provider_job.router)
    _include(app, ingestion.router)
    _include(app, project_ownership.router)
    _include(app, rule.router)
    _include(app, training.router)
    _include(app, evolution_screen.router)
    _include(app, evolution_screen.metrics_router)
    _include(app, user.router)
    _include(app, task.router)
    _include(app, attachment.router)
    _include(app, sample.router)
    _include(app, pre_annotation.router)
    _include(app, datasource.router)
    _include(app, annotation_taxonomy.router)
    _include(app, annotation_taxonomy.catalog_router)
    _include(app, yolo_train.router)
    _include(app, yolo_train.global_jobs_router)
    _include(app, yolo_train.eval_reports_router)
    _include(app, yolo_train.trained_models_router)
    _include(app, yolo_train.imported_models_router)
    _include(app, yolo_train.train_meta_router)
    _include(app, yolo_train.training_tasks_router)
    _include(app, yolo_train.bm1688_exports_router)
    _include(app, yolo_train.edge_devices_router)
    _include(app, yolo_train.yolo_edge_validation_router)
    _include(app, yolo_train.quality_policy_router)
    _include(app, yolo_aicm_publish.router)
    _include(app, board_agent.router)
    _include(app, yolo_eval_prompts.router)
    _include(app, ai_labeling_settings.router)
    _include(app, edge_device_job.router)
    _include(app, tagtool_preannotate.models_router)
    _include(app, tagtool_preannotate.tasks_router)
    _include(app, event_review.router)
    _include(app, experiment_dataset_import.router, prefix="")
    _include(app, evolution_imports.router, prefix="")
    _include(app, strategy_evolution.internal_router, prefix="")
    _include(app, strategy_evolution.callback_router, prefix="")
    _include(app, strategy_evolution.public_router, prefix="")
    _include(app, strategy_task_integration.router)
    _include(app, strategy_integration_s3_config.router)
    _include(app, local_dev.router)
    _include(app, auto_training.plans_router)
    _include(app, auto_training.rounds_router)
    _include(app, auto_training.reviews_router)
    _include(app, auto_training.evaluation_router)
    _include(app, auto_training.quality_approvals_router)
    _include(app, auto_training.evaluation_jobs_router)
    _include(app, auto_training.settings_router)
    _include(app, third_party_calls.router)
    _include(app, published_model_catalog.router)
    _include(app, published_model_catalog.public_router)
