"""Conservative geometry assistance for one large, straight, ruled table.

This is pixel-line analysis, not text recognition. Text and header/body labels
still come solely from the vision model. A grid is used only when its row/column
counts agree with the model, its outer borders are present, and physical merged
cells do not combine conflicting nonempty model cells. Otherwise nothing changes.
"""

from PIL import ImageOps


def _centers(indices):
    groups = []
    for index in indices:
        if not groups or index > groups[-1][-1] + 2:
            groups.append([index])
        else:
            groups[-1].append(index)
    return [round(sum(group) / len(group)) for group in groups]


def assist_grid(data, image):
    if len(data.get("tables", [])) != 1:
        return
    table = data["tables"][0]
    if (
        not table["cells"]
        or table["row_count"] > 100
        or table["column_count"] > 30
        or table["row_count"] * table["column_count"] > 2000
    ):
        return
    gray = ImageOps.grayscale(image).point(lambda v: 0 if v < 100 else 255)
    width, height = gray.size
    raw = gray.tobytes()
    rows = [y for y in range(height) if raw[y * width : (y + 1) * width].count(b"\0") > width * 0.55]
    ys = _centers(rows)
    if len(ys) != table["row_count"] + 1 or len(ys) < 3:
        return
    xs = _centers(
        [
            x
            for x in range(width)
            if gray.crop((x, ys[0], x + 1, ys[-1] + 1)).histogram()[0] > (ys[-1] - ys[0]) * 0.35
        ]
    )
    if len(xs) != table["column_count"] + 1 or len(xs) < 3:
        return
    if min(b - a for a, b in zip(xs, xs[1:])) < 12 or min(b - a for a, b in zip(ys, ys[1:])) < 12:
        return

    def vertical(x, y1, y2):
        crop = gray.crop((max(0, x - 2), y1, min(width, x + 3), y2))
        return sum(
            any(v == 0 for v in crop.crop((0, y, crop.width, y + 1)).tobytes()) for y in range(crop.height)
        ) / max(1, crop.height)

    def horizontal(y, x1, x2):
        crop = gray.crop((x1, max(0, y - 2), x2, min(height, y + 3)))
        return sum(
            any(v == 0 for v in crop.crop((x, 0, x + 1, crop.height)).tobytes()) for x in range(crop.width)
        ) / max(1, crop.width)

    if (
        min(
            vertical(xs[0], ys[0], ys[-1]),
            vertical(xs[-1], ys[0], ys[-1]),
            horizontal(ys[0], xs[0], xs[-1]),
            horizontal(ys[-1], xs[0], xs[-1]),
        )
        < 0.9
    ):
        return
    # Components connect grid slots only across an actually absent separator.
    pending = {(r, c) for r in range(table["row_count"]) for c in range(table["column_count"])}
    components, missing = [], []
    while pending:
        seed = min(pending)
        pending.remove(seed)
        component = {seed}
        queue = [seed]
        while queue:
            r, c = queue.pop()
            neighbors = []
            if c + 1 < table["column_count"] and vertical(xs[c + 1], ys[r] + 4, ys[r + 1] - 4) < 0.15:
                neighbors.append((r, c + 1))
            if c > 0 and vertical(xs[c], ys[r] + 4, ys[r + 1] - 4) < 0.15:
                neighbors.append((r, c - 1))
            if r + 1 < table["row_count"] and horizontal(ys[r + 1], xs[c] + 4, xs[c + 1] - 4) < 0.15:
                neighbors.append((r + 1, c))
            if r > 0 and horizontal(ys[r], xs[c] + 4, xs[c + 1] - 4) < 0.15:
                neighbors.append((r - 1, c))
            for neighbor in neighbors:
                if neighbor in pending:
                    pending.remove(neighbor)
                    component.add(neighbor)
                    queue.append(neighbor)
        r1 = min(r for r, c in component)
        r2 = max(r for r, c in component) + 1
        c1 = min(c for r, c in component)
        c2 = max(c for r, c in component) + 1
        if len(component) != (r2 - r1) * (c2 - c1):
            return
        candidates = [cell for cell in table["cells"] if (cell["row"], cell["column"]) in component]
        nonempty = [cell for cell in candidates if cell["text"].strip()]
        if len(nonempty) > 1:
            return
        chosen = nonempty or candidates
        if not chosen:
            missing.append({"row": r1, "column": c1, "row_span": r2 - r1, "column_span": c2 - c1})
            continue
        cell = dict(chosen[0])
        cell.update(
            model_row=cell["row"],
            model_column=cell["column"],
            row=r1,
            column=c1,
            row_span=r2 - r1,
            column_span=c2 - c1,
            geometry_box=[
                xs[c1] * 1000 / width,
                ys[r1] * 1000 / height,
                xs[c2] * 1000 / width,
                ys[r2] * 1000 / height,
            ],
            position_source="image_grid_cell",
            geometry_source="pixel_lines",
        )
        components.append(cell)
    table["cells"] = sorted(components, key=lambda c: (c["row"], c["column"]))
    table["source_grid"] = {
        "x_lines": xs,
        "y_lines": ys,
        "coordinate_space": "inference_pixels",
        "method": "ruled-lines-v1",
    }
    table["structure_source"] = "model_text_and_pixel_grid"
    table["unrecognized_grid_cells"] = missing
