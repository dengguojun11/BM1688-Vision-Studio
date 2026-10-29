from __future__ import annotations

from typing import Sequence


def _torch_modules():
    import torch
    from torch import nn

    return torch, nn


def build_plate_classifier(class_count: int):
    _torch, nn = _torch_modules()

    class PlateClassifier(nn.Module):
        def __init__(self, output_count: int):
            super().__init__()
            self.features = nn.Sequential(
                nn.Conv2d(3, 32, 3, stride=2, padding=1),
                nn.BatchNorm2d(32),
                nn.SiLU(inplace=True),
                nn.Conv2d(32, 64, 3, stride=2, padding=1),
                nn.BatchNorm2d(64),
                nn.SiLU(inplace=True),
                nn.Conv2d(64, 128, 3, stride=2, padding=1),
                nn.BatchNorm2d(128),
                nn.SiLU(inplace=True),
                nn.AdaptiveAvgPool2d(1),
            )
            self.classifier = nn.Linear(128, output_count)

        def forward(self, images):
            features = self.features(images).flatten(1)
            return self.classifier(features)

    return PlateClassifier(int(class_count))


def build_plate_ocr_model(character_count: int):
    _torch, nn = _torch_modules()

    class PlateOCRNet(nn.Module):
        def __init__(self, output_count: int):
            super().__init__()
            self.backbone = nn.Sequential(
                nn.Conv2d(3, 32, 3, stride=2, padding=1),
                nn.BatchNorm2d(32),
                nn.SiLU(inplace=True),
                nn.Conv2d(32, 64, 3, stride=2, padding=1),
                nn.BatchNorm2d(64),
                nn.SiLU(inplace=True),
                nn.Conv2d(64, 128, 3, stride=(2, 1), padding=1),
                nn.BatchNorm2d(128),
                nn.SiLU(inplace=True),
                nn.Conv2d(128, 128, 3, stride=(2, 1), padding=1),
                nn.BatchNorm2d(128),
                nn.SiLU(inplace=True),
                nn.AdaptiveAvgPool2d((1, 24)),
            )
            self.head = nn.Conv2d(128, output_count, kernel_size=1)

        def forward(self, images):
            logits = self.head(self.backbone(images)).squeeze(2)
            return logits.transpose(1, 2)

    return PlateOCRNet(int(character_count))


def save_component_checkpoint(
    path,
    *,
    model,
    component: str,
    labels: Sequence[str],
    input_size: tuple[int, int],
) -> None:
    torch, _nn = _torch_modules()
    torch.save(
        {
            "format": "labelu_plate_recognition_v1",
            "component": component,
            "labels": list(labels),
            "input_size": list(input_size),
            "state_dict": model.state_dict(),
        },
        path,
    )
