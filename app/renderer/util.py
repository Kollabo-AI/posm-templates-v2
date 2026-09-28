from __future__ import annotations


def calculate_coverage_area(boxes: list[tuple[float, float, float, float]], image_width: int, image_height: int) -> float:
    """How much area is covered by the boxes, as a ratio of the image area.

    Args:
        boxes (list[tuple[float, float, float, float]]): List of bounding boxes in (x, y, w, h) format.
        image_width (int): Width of the image.
        image_height (int): Height of the image.
    Returns:
        float: The coverage ratio (0.0 to 1.0)."""
    if not boxes or image_width <= 0 or image_height <= 0:
        return 0.0

    rects = []
    for b in boxes:
        x, y, w, h = b
        x1 = max(0, x)
        y1 = max(0, y)
        x2 = min(image_width, x + w)
        y2 = min(image_height, y + h)

        if x1 < x2 and y1 < y2:
            rects.append((x1, y1, x2, y2))

    if not rects:
        return 0.0

    x_coords = set()
    y_coords = set()
    for x1, y1, x2, y2 in rects:
        x_coords.add(x1)
        x_coords.add(x2)
        y_coords.add(y1)
        y_coords.add(y2)

    sorted_x = sorted(list(x_coords))
    sorted_y = sorted(list(y_coords))

    x_map = {x: i for i, x in enumerate(sorted_x)}
    y_map = {y: i for i, y in enumerate(sorted_y)}

    covered_grid = [[False for _ in range(len(sorted_x) - 1)] for _ in range(len(sorted_y) - 1)]

    for x1, y1, x2, y2 in rects:
        x_start_idx = x_map[x1]
        x_end_idx = x_map[x2]
        y_start_idx = y_map[y1]
        y_end_idx = y_map[y2]

        for i in range(y_start_idx, y_end_idx):
            for j in range(x_start_idx, x_end_idx):
                covered_grid[i][j] = True

    union_area = 0.0
    for i in range(len(sorted_y) - 1):
        for j in range(len(sorted_x) - 1):
            if covered_grid[i][j]:
                cell_w = sorted_x[j + 1] - sorted_x[j]
                cell_h = sorted_y[i + 1] - sorted_y[i]
                union_area += cell_w * cell_h

    return union_area


def validate_box(
    left: float,
    top: float,
    width: float,
    height: float,
    parent_width: float | None = None,
    parent_height: float | None = None,
) -> None:
    """
    Validate that a bounding box lies within image bounds.

    Rules:
    - All values must be >= 0.
    - If parent dimensions are provided, the box must fit entirely within them.

    Raises ValueError if any condition is violated.
    """
    if left < 0:
        raise ValueError(f"Box x coordinate must be >= 0, got: {left}")
    if top < 0:
        raise ValueError(f"Box y coordinate must be >= 0, got: {top}")
    if width < 0:
        raise ValueError(f"Box width must be >= 0, got: {width}")
    if height < 0:
        raise ValueError(f"Box height must be >= 0, got: {height}")

    if parent_width is not None:
        if left >= parent_width:
            raise ValueError(
                f"Box x coordinate {left} is outside parent width {parent_width}"
            )
        if left + width > parent_width:
            raise ValueError(
                f"Box extends past parent width. Right edge: {left + width}, Parent width: {parent_width}"
            )

    if parent_height is not None:
        if top >= parent_height:
            raise ValueError(
                f"Box y coordinate {top} is outside parent height {parent_height}"
            )
        if top + height > parent_height:
            raise ValueError(
                f"Box extends past parent height. Bottom edge: {top + height}, Parent height: {parent_height}"
            )
