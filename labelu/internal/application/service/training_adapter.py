"""Public training-adapter SPI used by orchestration and worker code.

The concrete registry lives in ``common.algorithm_registry`` so it can also
be consumed by startup diagnostics without importing a service that owns DB
transactions.  This module is the stable application-layer import path.
"""

from labelu.internal.common.algorithm_registry import (
    ClassificationAdapter,
    CustomContainerAdapter,
    PoseAdapter,
    SegmentationAdapter,
    TrainingAdapter,
    YoloDetectionAdapter,
    adapter_capabilities,
    get_training_adapter,
)
from labelu.internal.common.training_worker import (
    TRAINING_PROTOCOL_VERSION,
    TRAINING_STAGES,
    ContainerTrainingWorker,
    PlaceholderTrainingWorker,
    TrainingWorker,
    make_command,
    result_envelope,
    validate_command,
)

__all__ = [
    "TrainingAdapter",
    "YoloDetectionAdapter",
    "ClassificationAdapter",
    "SegmentationAdapter",
    "PoseAdapter",
    "CustomContainerAdapter",
    "adapter_capabilities",
    "get_training_adapter",
    "TRAINING_PROTOCOL_VERSION",
    "TRAINING_STAGES",
    "TrainingWorker",
    "ContainerTrainingWorker",
    "PlaceholderTrainingWorker",
    "make_command",
    "validate_command",
    "result_envelope",
]
