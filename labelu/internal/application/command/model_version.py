from typing import Any, Literal

from pydantic import BaseModel, Field

GateType = Literal["training", "generalization", "conversion", "edge", "release"]
GateStatus = Literal["PENDING", "PASS", "FAIL"]


class CreateModelVersionCommand(BaseModel):
    algorithm_id: int = Field(gt=0)
    dataset_version_id: int = Field(gt=0)
    training_job_id: int | None = Field(default=None, gt=0)
    series_id: int | None = Field(default=None, gt=0)
    variant_id: int | None = Field(default=None, gt=0)
    branch_key: str | None = Field(default=None, max_length=64)
    selection_contract: dict[str, Any] | None = None
    inheritance_mode: str | None = Field(default=None, max_length=32)
    selection_contract_sha256: str | None = Field(default=None, max_length=64)
    parent_model_version_id: int | None = Field(default=None, gt=0)
    parent_training_job_id: int | None = Field(default=None, gt=0)
    display_name: str = Field(min_length=1, max_length=256)
    chip_type: str | None = Field(default=None, max_length=64)
    precision: str | None = Field(default=None, max_length=16)
    input_contract: dict[str, Any] | None = None
    output_contract: dict[str, Any] | None = None
    preprocess_contract: dict[str, Any] | None = None
    postprocess_contract: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    manifest: dict[str, Any] | None = None

    class Config:
        extra = "forbid"


class RegisterTrainingJobModelCommand(BaseModel):
    """Promote a completed platform training job into the model repository.

    The training job is the source of truth for the project, frozen dataset
    version, algorithm, metrics, and weight artifact.  Keeping the command
    small prevents the UI from asking operators to retype lineage or hashes.
    """

    training_job_id: int = Field(gt=0)
    display_name: str | None = Field(default=None, max_length=256)

    class Config:
        extra = "forbid"


class ImportExternalModelCommand(BaseModel):
    """Register an already uploaded external weight in the canonical repository.

    External weights only declare which logical dataset contract they support.
    A frozen dataset version is created later only when evaluation or training
    needs an immutable sample baseline.
    """

    algorithm_id: int = Field(gt=0)
    dataset_id: int = Field(gt=0)
    imported_model_id: int = Field(gt=0)
    display_name: str | None = Field(default=None, max_length=256)
    chip_type: str | None = Field(default=None, max_length=64)
    precision: str | None = Field(default=None, max_length=16)
    manifest: dict[str, Any] | None = None

    class Config:
        extra = "forbid"


class CreateModelArtifactCommand(BaseModel):
    # 一期边端范围固定为 BM1688；RKNN 属于未开放的其他芯片链路。
    artifact_type: Literal["pt", "onnx", "bmodel", "manifest"]
    relative_path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(min_length=64, max_length=64)
    size_bytes: int | None = Field(default=None, ge=0)
    chip_type: str | None = Field(default=None, max_length=64)
    precision: str | None = Field(default=None, max_length=16)
    metadata: dict[str, Any] | None = None


class CreateModelQualityEvidenceCommand(BaseModel):
    gate_type: GateType
    status: GateStatus
    revision: int = Field(default=1, ge=1)
    metrics: dict[str, Any] | None = None
    evidence_uri: str | None = Field(default=None, max_length=512)
    evidence_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    evaluator: str | None = Field(default=None, max_length=128)
    reason: str | None = Field(default=None, max_length=4000)


class PublishModelVersionCommand(BaseModel):
    published_catalog_id: int | None = Field(default=None, gt=0)
    # AICM publishes the stable model id used by expert-feedback imports.
    # Supplying it lets the canonical model-center action create/update the
    # local download catalog in the same transaction.  Omitting it keeps the
    # historical internal-only publish contract compatible.
    aicm_model_set_id: int | None = Field(default=None, gt=0)
    aicm_environment_id: str | None = Field(default=None, max_length=64)
    package_path: str | None = Field(default=None, max_length=1024)
