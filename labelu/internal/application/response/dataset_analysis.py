from typing import List, Optional

from pydantic import BaseModel, Field


class DatasetDistributionItem(BaseModel):
    value: str
    count: int = 0
    percentage: float = 0.0


class DatasetDimensionDistribution(BaseModel):
    key: str
    label: str
    total_images: int = 0
    covered_images: int = 0
    missing_images: int = 0
    items: List[DatasetDistributionItem] = Field(default_factory=list)
    expected_values: List[str] = Field(default_factory=list)
    missing_values: List[str] = Field(default_factory=list)


class DatasetLabelDistributionItem(BaseModel):
    label: str
    image_count: int = 0
    instance_count: int = 0
    percentage: float = 0.0
    target_count: int = 0
    gap_count: int = 0


class DatasetAnalysisTotals(BaseModel):
    task_count: int = 0
    image_task_count: int = 0
    total_images: int = 0
    done_images: int = 0
    pending_images: int = 0
    tagged_images: int = 0
    labeled_images: int = 0
    label_instance_count: int = 0


class DatasetAnalysisTaskRow(BaseModel):
    task_id: int
    task_name: str
    status: Optional[str] = None
    source: Optional[str] = None
    total_images: int = 0
    done_images: int = 0
    pending_images: int = 0
    labeled_images: int = 0
    tag_coverage: float = 0.0


class DatasetGapRecommendation(BaseModel):
    dimension: str
    dimension_label: str
    value: str
    current_count: int = 0
    target_count: int = 0
    priority: str = "warning"
    message: str


class DatasetAnalysisResponse(BaseModel):
    # Legacy category fields remain in the response for old clients. New
    # callers scope analysis by the logical dataset instead.
    category_id: int = 0
    category_key: str = ""
    category_name: str = ""
    dataset_id: Optional[int] = None
    dataset_name: Optional[str] = None
    generated_at: str
    totals: DatasetAnalysisTotals
    dimensions: List[DatasetDimensionDistribution] = Field(default_factory=list)
    labels: List[DatasetLabelDistributionItem] = Field(default_factory=list)
    tasks: List[DatasetAnalysisTaskRow] = Field(default_factory=list)
    recommendations: List[DatasetGapRecommendation] = Field(default_factory=list)
