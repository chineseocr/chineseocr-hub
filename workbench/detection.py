"""Multimodal document localization and an extension contract.

The running pipeline calls locate(), which uses the same multimodal provider as
OCR. Model-reported rectangles are unverified positions, with no invented score.
No third-party OCR engine or private detector weights are required.
"""

from typing import List, Protocol, Tuple

from pydantic import BaseModel, Field, model_validator

from .vision import locate as locate  # public running localization function


class Region(BaseModel):
    label: str
    box: Tuple[float, float, float, float]  # normalized x1,y1,x2,y2 in source page coordinates
    page: int = Field(ge=1)
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def ordered_box(self):
        """Reject inverted/out-of-page boxes before a future detector creates a crop."""
        x1, y1, x2, y2 = self.box
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError("检测框必须为有序的归一化坐标")
        return self


class Detector(Protocol):
    model_id: str
    license_id: str

    def detect(self, png: bytes, page: int) -> List[Region]:
        """Return real detections for one page; implementations must identify score and geometry provenance."""
        ...
