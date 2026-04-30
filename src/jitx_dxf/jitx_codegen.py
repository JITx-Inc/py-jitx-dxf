"""Generate JITX Python code from classified DXF entities.

Produces a canonical JITX file containing a `Board` subclass for the outline,
an optional `Circuit` subclass for cutouts and mounting holes (each wrapped in
``jitx.feature.Cutout``), and a `Design` subclass that wires them together.
The structure mirrors the output of ``jitx-emn-importer``.
"""

from __future__ import annotations

import math

from .models import (
    ArcPathSegment,
    ClassifiedEntities,
    ClosedPath,
    LinePathSegment,
    Point,
)
from .path_assembler import path_bounding_box


def generate_board_code(
    classified: ClassifiedEntities,
    class_name: str = "ImportedBoard",
    module_name: str | None = None,
    recenter: bool = True,
) -> str:
    """Generate a complete JITX Python file from classified DXF entities.

    The output contains:

    * ``class <Board>(Board)`` with a ``shape`` attribute holding the outline.
    * ``class <Circuit>(Circuit)`` whose ``__init__`` assigns ``self.cutouts``
      to a list of ``Cutout(...)`` features (only emitted when the DXF has
      cutouts or mounting holes).
    * ``class <Design>(Design)`` instantiating the Board and Circuit (only
      emitted alongside the Circuit class).

    Args:
        classified: Classified DXF entities.
        class_name: Name of the generated Board class. The Circuit and Design
            class names are derived from this — a trailing ``Board`` suffix is
            stripped when present (``MyBoard`` → ``MyCircuit``/``MyDesign``);
            otherwise the suffix is appended (``Foo`` → ``FooCircuit``).
        module_name: Optional module name for the file header docstring.
        recenter: If True, re-center the board outline to the origin and apply
            the same offset to cutouts/holes.

    Returns:
        A string containing valid Python code.
    """
    offset = _recenter_offset(classified, recenter)
    board_name, circuit_name, design_name = _derive_class_names(class_name)
    has_features = bool(classified.cutouts or classified.holes)

    lines: list[str] = []

    # File header
    if module_name:
        lines.append(f'"""Board definition imported from {module_name}."""')
    else:
        lines.append('"""Board definition imported from DXF."""')
    lines.append("")

    # Imports
    lines.extend(_imports(classified, has_features))
    lines.append("")
    lines.append("")

    # Board class
    lines.extend(_board_class_lines(classified, board_name, offset))

    # Circuit + Design classes (only when there are features to place)
    if has_features:
        lines.append("")
        lines.append("")
        lines.extend(_circuit_class_lines(classified, circuit_name, offset))
        lines.append("")
        lines.append("")
        lines.extend(_design_class_lines(board_name, circuit_name, design_name))

    lines.append("")
    return "\n".join(lines)


def generate_outline_snippet(classified: ClassifiedEntities, recenter: bool = True) -> str:
    """Generate the ``shape = ...`` assignment for a Board subclass."""
    if not classified.outline:
        return "# No outline detected in DXF"

    offset = _recenter_offset(classified, recenter)
    return f"shape = {_outline_expression(classified.outline, offset, indent_level=0)}"


def generate_cutouts_snippet(classified: ClassifiedEntities, recenter: bool = True) -> str:
    """Generate a ``self.cutouts = [Cutout(...), ...]`` block for a Circuit ``__init__``.

    Combines explicit cutouts and mounting holes into a single list. Each
    geometry is wrapped in :class:`jitx.feature.Cutout`.
    """
    if not classified.cutouts and not classified.holes:
        return "# No cutouts or holes detected in DXF"

    offset = _recenter_offset(classified, recenter)

    parts: list[str] = ["self.cutouts = ["]
    for cutout in classified.cutouts:
        expr = _path_expression(cutout, offset, indent_level=1)
        parts.append(f"    Cutout({expr}),")
    for hole in classified.holes:
        parts.append(f"    Cutout({_circle_expression(hole, offset)}),")
    parts.append("]")
    return "\n".join(parts)


def generate_holes_snippet(classified: ClassifiedEntities, recenter: bool = True) -> str:
    """Generate one ``Cutout(Circle(...).at(x, y))`` line per detected hole."""
    if not classified.holes:
        return "# No holes detected in DXF"

    offset = _recenter_offset(classified, recenter)
    return "\n".join(
        f"Cutout({_circle_expression(hole, offset)})" for hole in classified.holes
    )


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _recenter_offset(classified: ClassifiedEntities, recenter: bool) -> Point:
    """Compute the offset that re-centers the outline to the origin (or zero)."""
    if not recenter or not classified.outline:
        return Point(0, 0)
    bb = path_bounding_box(classified.outline)
    return Point(
        -(bb[0].x + bb[1].x) / 2.0,
        -(bb[0].y + bb[1].y) / 2.0,
    )


def _derive_class_names(class_name: str) -> tuple[str, str, str]:
    """Derive Board/Circuit/Design class names from a single user-supplied name.

    A trailing ``Board`` suffix on ``class_name`` is stripped before appending
    ``Circuit`` and ``Design`` so common inputs like ``MyBoard`` produce clean
    names (``MyCircuit``, ``MyDesign``).
    """
    board_name = class_name
    prefix = class_name[:-5] if class_name.endswith("Board") else class_name
    if not prefix:
        prefix = class_name
    return board_name, f"{prefix}Circuit", f"{prefix}Design"


def _imports(classified: ClassifiedEntities, has_features: bool) -> list[str]:
    """Build the import block for the generated file."""
    needs_arc_polygon = False
    needs_polygon = False
    needs_circle = bool(classified.holes)

    if classified.outline:
        if any(isinstance(s, ArcPathSegment) for s in classified.outline.segments):
            needs_arc_polygon = True
        elif any(isinstance(s, LinePathSegment) for s in classified.outline.segments):
            needs_polygon = True

    for path in classified.cutouts:
        if any(isinstance(s, ArcPathSegment) for s in path.segments):
            needs_arc_polygon = True
        else:
            needs_polygon = True

    shape_imports: list[str] = []
    if needs_arc_polygon:
        shape_imports.append("Arc")
        shape_imports.append("ArcPolygon")
    if needs_circle:
        shape_imports.append("Circle")
    if needs_polygon:
        shape_imports.append("Polygon")

    lines = ["from jitx.board import Board"]
    if has_features:
        lines.append("from jitx.circuit import Circuit")
        lines.append("from jitx.design import Design")
        lines.append("from jitx.feature import Cutout")
    if shape_imports:
        lines.append(f"from jitx.shapes.primitive import {', '.join(shape_imports)}")
    return lines


def _board_class_lines(
    classified: ClassifiedEntities, board_name: str, offset: Point
) -> list[str]:
    """Render the Board subclass body."""
    lines = [f"class {board_name}(Board):"]
    if classified.outline:
        outline_expr = _outline_expression(classified.outline, offset, indent_level=1)
        lines.append(f"    shape = {outline_expr}")
    else:
        # The user must supply a real shape; jitx.Board.shape is required.
        lines.append("    shape = None  # No outline detected in DXF — fill in manually")
    return lines


def _circuit_class_lines(
    classified: ClassifiedEntities, circuit_name: str, offset: Point
) -> list[str]:
    """Render the Circuit subclass body that holds Cutout features."""
    lines = [
        f"class {circuit_name}(Circuit):",
        "    def __init__(self):",
        "        super().__init__()",
        "        self.cutouts = [",
    ]
    for cutout in classified.cutouts:
        expr = _path_expression(cutout, offset, indent_level=3)
        lines.append(f"            Cutout({expr}),")
    for hole in classified.holes:
        lines.append(f"            Cutout({_circle_expression(hole, offset)}),")
    lines.append("        ]")
    return lines


def _design_class_lines(board_name: str, circuit_name: str, design_name: str) -> list[str]:
    """Render a Design subclass that wires the Board and Circuit together."""
    return [
        f"class {design_name}(Design):",
        f"    board = {board_name}()",
        f"    circuit = {circuit_name}()",
    ]


def _circle_expression(hole, offset: Point) -> str:
    """Format a ``Circle(...).at(x, y)`` expression for a mounting hole."""
    cx = _fmt(hole.center.x + offset.x)
    cy = _fmt(hole.center.y + offset.y)
    r = _fmt(hole.radius)
    return f"Circle(radius={r}).at({cx}, {cy})"


def _outline_expression(path: ClosedPath, offset: Point, indent_level: int) -> str:
    """Generate a shape expression for the board outline."""
    bb = path_bounding_box(path)
    w = bb[1].x - bb[0].x
    h = bb[1].y - bb[0].y

    # Check if it's a simple rectangle (all line segments, axis-aligned)
    if _is_axis_aligned_rectangle(path):
        # NOTE: the rectangle branch always emits an origin-centered polygon and
        # ignores `offset`. When `recenter=False` is requested with a non-centered
        # outline, the rectangle short-cut is wrong; falling through to
        # `_polygon_expression` would handle it correctly. Tracked separately.
        return f"Polygon([({_fmt(-w/2)}, {_fmt(-h/2)}), ({_fmt(w/2)}, {_fmt(-h/2)}), ({_fmt(w/2)}, {_fmt(h/2)}), ({_fmt(-w/2)}, {_fmt(h/2)})])"

    has_arcs = any(isinstance(s, ArcPathSegment) for s in path.segments)
    if has_arcs:
        return _arc_polygon_expression(path, offset, indent_level)
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

    pad = "    " * (indent_level + 1)
    inner = f",\n{pad}".join(points)
    return f"Polygon([\n{pad}{inner},\n{'    ' * indent_level}])"


def _arc_polygon_expression(path: ClosedPath, offset: Point, indent_level: int) -> str:
    """Generate an ArcPolygon expression from a path with arcs."""
    pad = "    " * (indent_level + 1)
    elements: list[str] = []

    for seg in path.segments:
        if isinstance(seg, LinePathSegment):
            x = _fmt(seg.start.x + offset.x)
            y = _fmt(seg.start.y + offset.y)
            elements.append(f"({x}, {y})")
        elif isinstance(seg, ArcPathSegment):
            sx = _fmt(seg.start_point.x + offset.x)
            sy = _fmt(seg.start_point.y + offset.y)
            elements.append(f"({sx}, {sy})")

            cx = _fmt(seg.center.x + offset.x)
            cy = _fmt(seg.center.y + offset.y)
            r = _fmt(seg.radius)
            # jitx.shapes.primitive.Arc expects (start, arc_sweep) with
            # start in [0, 360); the path_assembler stores raw atan2 angles
            # that can be negative or exactly 360 after rounding. Normalize
            # both the start and the sweep before emission.
            sa_norm = _wrap_angle(seg.start_angle)
            sweep_norm = _wrap_angle(seg.end_angle - seg.start_angle)
            sa = _fmt(sa_norm)
            sw = _fmt(sweep_norm)
            elements.append(f"Arc(({cx}, {cy}), {r}, {sa}, {sw})")

    inner = f",\n{pad}".join(elements)
    return f"ArcPolygon([\n{pad}{inner},\n{'    ' * indent_level}])"


def _path_expression(path: ClosedPath, offset: Point, indent_level: int) -> str:
    """Generate a shape expression for a cutout path."""
    has_arcs = any(isinstance(s, ArcPathSegment) for s in path.segments)
    if has_arcs:
        return _arc_polygon_expression(path, offset, indent_level)
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


def _wrap_angle(angle: float) -> float:
    """Wrap an angle (degrees) to [0, 360). Defensive about float modulo edge
    cases where ``-1e-12 % 360.0`` returns a value that rounds up to 360.0
    after :func:`_fmt`'s 4-decimal rounding.
    """
    wrapped = angle - 360.0 * math.floor(angle / 360.0)
    if wrapped >= 360.0 - 1e-6:
        wrapped = 0.0
    return wrapped


def _fmt(value: float) -> str:
    """Format a float for code generation, trimming unnecessary decimals."""
    if abs(value) < 1e-6:
        return "0.0"
    rounded = round(value, 4)
    if rounded == int(rounded):
        return f"{int(rounded)}.0"
    s = f"{rounded:.4f}".rstrip("0")
    if s.endswith("."):
        s += "0"
    return s
