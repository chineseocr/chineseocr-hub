"""Image preparation and reversible geometry, without another OCR engine.

Deskew uses only image projections. It does not read text, infer document type,
load weights, or claim to fix perspective. All matrices map pixel-edge coordinates
from the actual model canvas back to the EXIF-normalized page/crop canvas.
"""

import math

from PIL import Image, ImageOps

IDENTITY = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]


def point(matrix, x, y):
    a, b, c, d, e, f = matrix
    return [a * x + b * y + c, d * x + e * y + f]


def compose(outer, inner):
    """Compose inverse transforms: apply inner first, then outer."""
    a, b, c, d, e, f = outer
    g, h, i, j, k, tail = inner
    return [
        a * g + b * j,
        a * h + b * k,
        a * i + b * tail + c,
        d * g + e * j,
        d * h + e * k,
        d * i + e * tail + f,
    ]


def rotate(image, angle):
    """Rotate counterclockwise and derive the inverse from both actual canvases.

    Using the expanded output size is essential: rotation around the old center
    alone omits the new canvas translation and shifts every returned box.
    """
    result = image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white")
    radians = math.radians(angle)
    a, b, d, e = [
        round(value, 15)
        for value in (math.cos(radians), -math.sin(radians), math.sin(radians), math.cos(radians))
    ]
    inverse = [
        a,
        b,
        image.width / 2 - a * result.width / 2 - b * result.height / 2,
        d,
        e,
        image.height / 2 - d * result.width / 2 - e * result.height / 2,
    ]
    return result, inverse


def estimate_deskew(image):
    """Conservatively search +/-8 degrees using horizontal ink projections.

    Blank/dark images, boundary optima and weak improvements are left unchanged.
    This heuristic is suitable for near-upright scans, not a confidence score or
    automatic 90-degree orientation detector. Work is bounded to a 512px image.
    """
    sample = ImageOps.grayscale(image)
    sample.thumbnail((512, 512))
    ink = sample.point(lambda value: 255 if value < 160 else 0)
    total = sum(ink.histogram()[1:])
    if total < 150 or total > sample.width * sample.height * 0.45:
        return 0.0

    def score(angle):
        rotated = ink.rotate(angle, expand=True, fillcolor=0)
        raw = rotated.tobytes()
        rows = [sum(raw[y * rotated.width : (y + 1) * rotated.width]) / 255 for y in range(rotated.height)]
        return sum((b - a) ** 2 for a, b in zip(rows, rows[1:]))

    baseline = score(0)
    coarse = [(score(angle), angle) for angle in range(-8, 9)]
    best_score, best = max(coarse)
    if abs(best) == 8:
        return 0.0
    fine = [(score(best + step / 4), best + step / 4) for step in range(-3, 4)]
    best_score, best = max(fine)
    if abs(best) < 0.25 or best_score < max(1, baseline) * 1.15:
        return 0.0
    return round(best, 2)


def prepare_image(image, *, rotation=0, deskew=False):
    """Return the actual inference image and serializable inverse-transform metadata."""
    original = ImageOps.exif_transpose(image).convert("RGB")
    prepared, inverse = rotate(original, -rotation) if rotation else (original.copy(), IDENTITY[:])
    angle = estimate_deskew(prepared) if deskew else 0.0
    if angle:
        prepared, correction = rotate(prepared, angle)
        inverse = compose(inverse, correction)
    return prepared, {
        "source_width": original.width,
        "source_height": original.height,
        "inference_width": prepared.width,
        "inference_height": prepared.height,
        "rotation": rotation,
        "deskew_requested": deskew,
        "deskew_angle_ccw": angle,
        "inverse_matrix": inverse,
        "algorithm": "projection-v1",
        "perspective_corrected": False,
    }


def map_box(box, transform):
    """Normalize in the model canvas, inverse-map every corner, then bound on source.

    Return both polygon and enclosing rectangle. Never reconstruct a rotated
    rectangle from only two corners; that loses extrema after deskew.
    """
    if box is None:
        return None, None
    width, height = transform["inference_width"], transform["inference_height"]
    x1, y1, x2, y2 = box
    polygon = [
        point(transform["inverse_matrix"], x * width / 1000, y * height / 1000)
        for x, y in ((x1, y1), (x2, y1), (x2, y2), (x1, y2))
    ]
    polygon = [
        [max(0, min(transform["source_width"], x)), max(0, min(transform["source_height"], y))]
        for x, y in polygon
    ]
    bounds = [
        min(p[0] for p in polygon),
        min(p[1] for p in polygon),
        max(p[0] for p in polygon),
        max(p[1] for p in polygon),
    ]
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        return None, None
    return bounds, polygon
