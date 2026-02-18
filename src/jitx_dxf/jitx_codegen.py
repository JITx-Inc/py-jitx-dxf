"""Generate JITX Python code from classified DXF entities.

Produces valid JITX Board class definitions with board outlines,
cutouts, mounting holes, keepouts, and annotations.
"""

from __future__ import annotations

import math
from textwrap import dedent, indent

from .models import (
    ArcPathSegment,
    ClassifiedEntities,
    ClosedPath,
    DxfCircle,
    LinePathSegment,
    Point,
)
from .path_assembler import path_area, path_bounding_box


def generate_board_code(
    classified: ClassifiedEntities,
    class_name: str = "ImportedBoard",
    module_name: str | None = None,
    recenter: bool = True,
) -> str:
    """Generate a complete JITX Board class Python file.

    Args:
        classified: Classified DXF entities.
        class_name: Name of the generated Board class.
        module_name: Optional module name for the file header comment.
        recenter: If True, re-center the board outline to the origin.

    Returns:
        A string containing valid Python code.
    """
    offset = Point(0, 0)
    if recenter and classified.outline:
        bb = path_bounding_box(classified.outline)
        offset = Point(
            -(bb[0].x + bb[1].x) / 2.0,
            -(bb[0].y + bb[1].y) / 2.0,
        )

    lines: list[str] = []

    # File header
    if module_name:
        lines.append(f'"""Board definition imported from {module_name}."""')
    else:
        lines.append('"""Board definition imported from DXF."""')
    lines.append("")

    # Imports
    lines.append("from jitx.board import Board")

    needs_arc_polygon = False
    needs_polygon = False
    needs_circle = False

    if classified.outline:
        has_arcs = any(isinstance(s, ArcPathSegment) for s in classified.outline.segments)
        has_lines = any(isinstance(s, LinePathSegment) for s in classified.outline.segments)
        if has_arcs:
            needs_arc_polygon = True
        elif has_lines:
            needs_polygon = True

    if classified.cutouts or classified.holes:
        needs_circle = any(True for _ in classified.holes)

    for path in classified.cutouts:
        if any(isinstance(s, ArcPathSegment) for s in path.segments):
            needs_arc_polygon = True
        else:
            needs_polygon = True

    shape_imports: list[str] = []
    if needs_polygon:
        shape_imports.append("Polygon")
    if needs_arc_polygon:
        shape_imports.append("ArcPolyline")
    if needs_circle or classified.holes:
        shape_imports.append("Circle")

    if shape_imports:
        lines.append(f"from jitx.shapes.primitive import {', '.join(sorted(shape_imports))}")

    lines.append("")
    lines.append("")

    # Class definition
    lines.append(f"class {class_name}(Board):")

    # Board outline
    if classified.outline:
        outline_expr = _outline_expression(classified.outline, offset, indent_level=1)
        lines.append(f"    board_shape = {outline_expr}")
    else:
        lines.append("    board_shape = None  # No outline detected in DXF")

    # Cutouts
    if classified.cutouts:
        lines.append("")
        lines.append("    cutouts = [")
        for cutout in classified.cutouts:
            expr = _path_expression(cutout, offset, indent_level=2)
            lines.append(f"        {expr},")
        lines.append("    ]")

    # Holes as cutouts
    if classified.holes:
        if not classified.cutouts:
            lines.append("")
            lines.append("    cutouts = [")
        else:
            # Extend existing cutouts list — re-open before the closing bracket
            # Actually, just add holes to the cutouts list above
            pass

        # If cutouts list wasn't opened yet
        if not classified.cutouts:
            for hole in classified.holes:
                cx = _fmt(hole.center.x + offset.x)
                cy = _fmt(hole.center.y + offset.y)
                r = _fmt(hole.radius)
                lines.append(f"        Circle(radius={r}).at({cx}, {cy}),")
            lines.append("    ]")
        else:
            # Insert holes before the closing bracket
            # Remove the last "]" line and re-add
            last = lines.pop()  # removes "    ]"
            for hole in classified.holes:
                cx = _fmt(hole.center.x + offset.x)
                cy = _fmt(hole.center.y + offset.y)
                r = _fmt(hole.radius)
                lines.append(f"        Circle(radius={r}).at({cx}, {cy}),")
            lines.append(last)  # re-add "    ]"

    lines.append("")

    return "\n".join(lines)


def generate_outline_snippet(classified: ClassifiedEntities, recenter: bool = True) -> str:
    """Generate just the board outline shape expression."""
    if not classified.outline:
        return "# No outline detected in DXF"

    offset = Point(0, 0)
    if recenter:
        bb = path_bounding_box(classified.outline)
        offset = Point(
            -(bb[0].x + bb[1].x) / 2.0,
            -(bb[0].y + bb[1].y) / 2.0,
        )

    return f"board_shape = {_outline_expression(classified.outline, offset, indent_level=0)}"


def generate_cutouts_snippet(classified: ClassifiedEntities, recenter: bool = True) -> str:
    """Generate cutout shape expressions."""
    if not classified.cutouts and not classified.holes:
        return "# No cutouts or holes detected in DXF"

    offset = Point(0, 0)
    if recenter and classified.outline:
        bb = path_bounding_box(classified.outline)
        offset = Point(
            -(bb[0].x + bb[1].x) / 2.0,
            -(bb[0].y + bb[1].y) / 2.0,
        )

    parts: list[str] = []
    parts.append("cutouts = [")
    for cutout in classified.cutouts:
        expr = _path_expression(cutout, offset, indent_level=1)
        parts.append(f"    {expr},")
    for hole in classified.holes:
        cx = _fmt(hole.center.x + offset.x)
        cy = _fmt(hole.center.y + offset.y)
        r = _fmt(hole.radius)
        parts.append(f"    Circle(radius={r}).at({cx}, {cy}),")
    parts.append("]")
    return "\n".join(parts)


def generate_holes_snippet(classified: ClassifiedEntities, recenter: bool = True) -> str:
    """Generate hole/mounting point expressions."""
    if not classified.holes:
        return "# No holes detected in DXF"

    offset = Point(0, 0)
    if recenter and classified.outline:
        bb = path_bounding_box(classified.outline)
        offset = Point(
            -(bb[0].x + bb[1].x) / 2.0,
            -(bb[0].y + bb[1].y) / 2.0,
        )

    parts: list[str] = []
    for hole in classified.holes:
        cx = _fmt(hole.center.x + offset.x)
        cy = _fmt(hole.center.y + offset.y)
        r = _fmt(hole.radius)
        parts.append(f"Circle(radius={r}).at({cx}, {cy})")
    return "\n".join(parts)


def _outline_expression(path: ClosedPath, offset: Point, indent_level: int) -> str:
    """Generate a shape expression for the board outline."""
    bb = path_bounding_box(path)
    w = bb[1].x - bb[0].x
    h = bb[1].y - bb[0].y

    # Check if it's a simple rectangle (all line segments, axis-aligned)
    if _is_axis_aligned_rectangle(path):
        cx = _fmt((bb[0].x + bb[1].x) / 2.0 + offset.x)
        cy = _fmt((bb[0].y + bb[1].y) / 2.0 + offset.y)
        return f"Polygon([({_fmt(-w/2)}, {_fmt(-h/2)}), ({_fmt(w/2)}, {_fmt(-h/2)}), ({_fmt(w/2)}, {_fmt(h/2)}), ({_fmt(-w/2)}, {_fmt(h/2)})])"

    has_arcs = any(isinstance(s, ArcPathSegment) for s in path.segments)

    if has_arcs:
        return _arc_polygon_expression(path, offset, indent_level)
    else:
        return _polygon_expression(path, offset, indent_level)


def _polygon_expression(path: ClosedPath, offset: Point, indent_level: int) -> str:
    """Generate a Polygon expression from a path with only line segments."""
    points: list[str] = []
    for seg in path.segments:
        if isinstance(seg, LinePathSegment):
            x = _fmt(seg.start.x + offset.x)
            y = _fmt(seg.start.y + offset.y)
            points.append(f"({x}, {y})")

    if len(points) <= 6:
        return f"Polygon([{', '.join(points)}])"

    # Multi-line for many points
    pad = "    " * (indent_level + 1)
    inner = f",\n{pad}".join(points)
    return f"Polygon([\n{pad}{inner},\n{'    ' * indent_level}])"


def _arc_polygon_expression(path: ClosedPath, offset: Point, indent_level: int) -> str:
    """Generate an ArcPolyline expression from a path with arcs."""
    pad = "    " * (indent_level + 1)
    elements: list[str] = []

    for seg in path.segments:
        if isinstance(seg, LinePathSegment):
            x = _fmt(seg.start.x + offset.x)
            y = _fmt(seg.start.y + offset.y)
            elements.append(f"({x}, {y})")
        elif isinstance(seg, ArcPathSegment):
            # Emit the start point, then the arc
            sx = _fmt(seg.start_point.x + offset.x)
            sy = _fmt(seg.start_point.y + offset.y)
            elements.append(f"({sx}, {sy})")

            cx = _fmt(seg.center.x + offset.x)
            cy = _fmt(seg.center.y + offset.y)
            r = _fmt(seg.radius)
            sa = _fmt(seg.start_angle)
            ea = _fmt(seg.end_angle)
            elements.append(f"Arc(({cx}, {cy}), {r}, {sa}, {ea})")

    inner = f",\n{pad}".join(elements)
    return f"ArcPolyline([\n{pad}{inner},\n{'    ' * indent_level}])"


def _path_expression(path: ClosedPath, offset: Point, indent_level: int) -> str:
    """Generate a shape expression for a cutout or other path."""
    has_arcs = any(isinstance(s, ArcPathSegment) for s in path.segments)
    if has_arcs:
        return _arc_polygon_expression(path, offset, indent_level)
    else:
        return _polygon_expression(path, offset, indent_level)


def _is_axis_aligned_rectangle(path: ClosedPath) -> bool:
    """Check if a path is an axis-aligned rectangle (4 line segments, 90° turns)."""
    if len(path.segments) != 4:
        return False
    if not all(isinstance(s, LinePathSegment) for s in path.segments):
        return False

    for seg in path.segments:
        assert isinstance(seg, LinePathSegment)
        dx = abs(seg.end.x - seg.start.x)
        dy = abs(seg.end.y - seg.start.y)
        if dx > 1e-6 and dy > 1e-6:
            return False  # Diagonal line
    return True


def _fmt(value: float) -> str:
    """Format a float for code generation, trimming unnecessary decimals."""
    if abs(value) < 1e-6:
        return "0.0"
    # Round to 4 decimal places (0.1 μm precision)
    rounded = round(value, 4)
    if rounded == int(rounded):
        return f"{int(rounded)}.0"
    # Strip trailing zeros but keep at least one decimal
    s = f"{rounded:.4f}".rstrip("0")
    if s.endswith("."):
        s += "0"
    return s
