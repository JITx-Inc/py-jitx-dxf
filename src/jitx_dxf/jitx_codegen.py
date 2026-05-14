"""Generate JITX Python code from classified DXF entities.

Produces a canonical JITX file containing a `Board` subclass whose ``shape``
contains the outline plus any detected cutouts and mounting holes as board
geometry. A separate companion `Circuit` file can be generated for plated or
electrically connected features.
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

_ARC_APPROXIMATION_DEGREES = 10.0
_CIRCLE_HOLE_SEGMENTS = 32


def generate_board_code(
    classified: ClassifiedEntities,
    class_name: str = "ImportedBoard",
    module_name: str | None = None,
    recenter: bool = True,
    include_features_in_board: bool = True,
) -> str:
    """Generate a complete JITX Python file from classified DXF entities.

    The output contains:

    * ``class <Board>(Board)`` with a ``shape`` attribute holding the outline,
      cutouts, and mounting holes. Cutouts and holes are emitted as non-plated
      board geometry.

    Args:
        classified: Classified DXF entities.
        class_name: Name of the generated Board class.
        module_name: Optional module name for the file header docstring.
        recenter: If True, re-center the board outline to the origin and apply
            the same offset to cutouts/holes.
        include_features_in_board: If True, detected cutouts/holes are emitted
            as non-plated board geometry. If False, they are omitted from
            ``Board.shape`` so a companion Circuit can own them instead.

    Returns:
        A string containing valid Python code.
    """
    offset = _recenter_offset(classified, recenter)

    lines: list[str] = []

    # File header
    if module_name:
        lines.append(f'"""Board definition imported from {module_name}."""')
    else:
        lines.append('"""Board definition imported from DXF."""')
    lines.append("")

    # Imports
    lines.extend(_board_imports(classified, include_features_in_board))
    lines.append("")
    lines.append("")

    # Board class
    lines.extend(
        _board_class_lines(classified, class_name, offset, include_features_in_board)
    )

    lines.append("")
    return "\n".join(lines)


def generate_circuit_code(
    classified: ClassifiedEntities,
    board_class_name: str = "ImportedBoard",
    module_name: str | None = None,
    recenter: bool = True,
) -> str:
    """Generate a companion Circuit file for plated/electrical cutouts and holes."""
    if not classified.cutouts and not classified.holes:
        return "# No cutouts or holes detected in DXF"

    offset = _recenter_offset(classified, recenter)
    _board_name, circuit_name = _derive_class_names(board_class_name)

    lines: list[str] = []
    if module_name:
        lines.append(f'"""Optional plated-feature circuit imported from {module_name}."""')
    else:
        lines.append('"""Optional plated-feature circuit imported from DXF."""')
    lines.append("")
    lines.extend(_circuit_imports(classified))
    lines.append("")
    lines.append("")
    lines.extend(_circuit_class_lines(classified, circuit_name, offset))
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


def _derive_class_names(class_name: str) -> tuple[str, str]:
    """Derive Board/Circuit class names from a single user-supplied name.

    A trailing ``Board`` suffix on ``class_name`` is stripped before appending
    ``Circuit`` so common inputs like ``MyBoard`` produce clean names
    (``MyCircuit``).
    """
    board_name = class_name
    prefix = class_name[:-5] if class_name.endswith("Board") else class_name
    if not prefix:
        prefix = class_name
    return board_name, f"{prefix}Circuit"


def _board_imports(
    classified: ClassifiedEntities, include_features_in_board: bool
) -> list[str]:
    """Build the import block for the generated Board file."""
    needs_arc_polygon = False
    needs_polygon = False
    has_board_holes = bool(
        include_features_in_board
        and classified.outline
        and (classified.cutouts or classified.holes)
    )

    if classified.outline:
        if has_board_holes:
            needs_polygon = True
        elif any(isinstance(s, ArcPathSegment) for s in classified.outline.segments):
            needs_arc_polygon = True
        elif any(isinstance(s, LinePathSegment) for s in classified.outline.segments):
            needs_polygon = True

    shape_imports: list[str] = []
    if needs_arc_polygon:
        shape_imports.append("Arc")
        shape_imports.append("ArcPolygon")
    if needs_polygon:
        shape_imports.append("Polygon")

    lines = ["from jitx.board import Board"]
    if shape_imports:
        lines.append(f"from jitx.shapes.primitive import {', '.join(shape_imports)}")
    return lines


def _circuit_imports(classified: ClassifiedEntities) -> list[str]:
    """Build the import block for the optional companion Circuit file."""
    needs_arc_polygon = False
    needs_polygon = False
    needs_circle = bool(classified.holes)

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

    lines = [
        "from jitx.circuit import Circuit",
        "from jitx.feature import Cutout",
        "from jitx.net import Port, port_array",
    ]
    if shape_imports:
        lines.append(f"from jitx.shapes.primitive import {', '.join(shape_imports)}")
    return lines


def _board_class_lines(
    classified: ClassifiedEntities,
    board_name: str,
    offset: Point,
    include_features_in_board: bool,
) -> list[str]:
    """Render the Board subclass body."""
    lines = [f"class {board_name}(Board):"]
    if classified.outline:
        if include_features_in_board and (classified.cutouts or classified.holes):
            lines.extend(_board_hole_comment_lines())
        elif classified.cutouts or classified.holes:
            lines.extend(_suppressed_board_feature_comment_lines())
        outline_expr = _board_shape_expression(
            classified,
            offset,
            indent_level=1,
            include_features_in_board=include_features_in_board,
        )
        lines.append(f"    shape = {outline_expr}")
    else:
        # The user must supply a real shape; jitx.Board.shape is required.
        lines.append("    shape = None  # No outline detected in DXF — fill in manually")
    return lines


def _board_hole_comment_lines() -> list[str]:
    """Render the note that board holes are non-electrical geometry."""
    return [
        "    # Cutouts and holes below are emitted as non-plated board geometry.",
        "    # If any feature is plated or should connect electrically, use the",
        "    # separately generated companion Circuit file and connect its",
        "    # feature_ports.",
    ]


def _suppressed_board_feature_comment_lines() -> list[str]:
    """Render the note used when a companion Circuit owns the features."""
    return [
        "    # Cutouts and holes are omitted from Board.shape because they are",
        "    # emitted in the companion Circuit file for plated/electrical use.",
    ]


def _circuit_class_lines(
    classified: ClassifiedEntities, circuit_name: str, offset: Point
) -> list[str]:
    """Render the optional Circuit template for plated/electrical features."""
    feature_count = len(classified.cutouts) + len(classified.holes)
    lines = [
        f"class {circuit_name}(Circuit):",
        "    # Electrical-use template for the cutouts/holes above.",
        "    # Keep using Board.shape for non-plated mechanical geometry.",
        "    # If these features are plated or electrically connected, instantiate",
        "    # this Circuit in your design and net feature_ports to the intended",
        "    # electrical ports, for example:",
        f"    #     plated = {circuit_name}()",
        "    #     chassis = Port()",
        "    #     plated_feature_nets = [chassis + p for p in plated.feature_ports]",
        "    def __init__(self):",
        "        super().__init__()",
        f"        self.feature_ports = port_array({feature_count}, ptype=Port)",
        "        self.cutouts = [",
    ]
    for cutout in classified.cutouts:
        expr = _path_expression(cutout, offset, indent_level=3)
        lines.append(f"            Cutout({expr}),")
    for hole in classified.holes:
        lines.append(f"            Cutout({_circle_expression(hole, offset)}),")
    lines.append("        ]")
    return lines


def _circle_expression(hole, offset: Point) -> str:
    """Format a ``Circle(...).at(x, y)`` expression for a mounting hole."""
    cx = _fmt(hole.center.x + offset.x)
    cy = _fmt(hole.center.y + offset.y)
    r = _fmt(hole.radius)
    return f"Circle(radius={r}).at({cx}, {cy})"


def _board_shape_expression(
    classified: ClassifiedEntities,
    offset: Point,
    indent_level: int,
    include_features_in_board: bool,
) -> str:
    """Generate the Board.shape expression, including non-plated holes."""
    assert classified.outline is not None
    if (
        not include_features_in_board
        or (not classified.cutouts and not classified.holes)
    ):
        return _outline_expression(classified.outline, offset, indent_level)
    return _polygon_with_holes_expression(classified, offset, indent_level)


def _polygon_with_holes_expression(
    classified: ClassifiedEntities, offset: Point, indent_level: int
) -> str:
    """Generate a Polygon(..., holes=[...]) expression for board geometry."""
    assert classified.outline is not None

    outer_points = _path_points(classified.outline, offset)
    hole_points = _board_hole_points(classified, offset)

    arg_indent = indent_level + 1
    pad = "    " * indent_level
    outer_expr = _point_list_expression(outer_points, arg_indent)
    holes_expr = _holes_argument_expression(hole_points, arg_indent)
    return f"Polygon(\n{outer_expr},\n{holes_expr},\n{pad})"


def _board_hole_points(classified: ClassifiedEntities, offset: Point) -> list[list[Point]]:
    """Return point-list boundaries for every board cutout and mounting hole."""
    holes: list[list[Point]] = []
    for cutout in classified.cutouts:
        points = _path_points(cutout, offset)
        if len(points) >= 3:
            holes.append(points)
    for hole in classified.holes:
        holes.append(_circle_points(hole, offset))
    return holes


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
    points = [_point_expression(point) for point in _path_points(path, offset)]

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


def _path_points(path: ClosedPath, offset: Point) -> list[Point]:
    """Generate point vertices from a path, approximating arcs when needed."""
    points: list[Point] = []
    for seg in path.segments:
        if isinstance(seg, LinePathSegment):
            points.append(Point(seg.start.x + offset.x, seg.start.y + offset.y))
        elif isinstance(seg, ArcPathSegment):
            points.append(
                Point(seg.start_point.x + offset.x, seg.start_point.y + offset.y)
            )
            points.extend(_arc_intermediate_points(seg, offset))
    return points


def _arc_intermediate_points(seg: ArcPathSegment, offset: Point) -> list[Point]:
    """Sample intermediate points along an arc for Polygon hole boundaries."""
    sweep = seg.end_angle - seg.start_angle
    if abs(sweep) < 1e-9:
        return []

    steps = max(1, math.ceil(abs(sweep) / _ARC_APPROXIMATION_DEGREES))
    points: list[Point] = []
    for i in range(1, steps):
        angle = math.radians(seg.start_angle + sweep * i / steps)
        points.append(
            Point(
                seg.center.x + offset.x + seg.radius * math.cos(angle),
                seg.center.y + offset.y + seg.radius * math.sin(angle),
            )
        )
    return points


def _circle_points(hole, offset: Point) -> list[Point]:
    """Approximate a circular board hole as a polygon boundary."""
    points: list[Point] = []
    for i in range(_CIRCLE_HOLE_SEGMENTS):
        angle = 2.0 * math.pi * i / _CIRCLE_HOLE_SEGMENTS
        points.append(
            Point(
                hole.center.x + offset.x + hole.radius * math.cos(angle),
                hole.center.y + offset.y + hole.radius * math.sin(angle),
            )
        )
    return points


def _holes_argument_expression(hole_points: list[list[Point]], indent_level: int) -> str:
    """Format the holes= argument for Polygon(...)."""
    pad = "    " * indent_level
    lines = [f"{pad}holes=["]
    for points in hole_points:
        lines.append(f"{_point_list_expression(points, indent_level + 1)},")
    lines.append(f"{pad}]")
    return "\n".join(lines)


def _point_list_expression(points: list[Point], indent_level: int) -> str:
    """Format a Python list of point tuples."""
    point_exprs = [_point_expression(point) for point in points]
    pad = "    " * indent_level
    if len(point_exprs) <= 6:
        return f"{pad}[{', '.join(point_exprs)}]"

    inner_pad = "    " * (indent_level + 1)
    inner = f",\n{inner_pad}".join(point_exprs)
    return f"{pad}[\n{inner_pad}{inner},\n{pad}]"


def _point_expression(point: Point) -> str:
    """Format a single point tuple."""
    return f"({_fmt(point.x)}, {_fmt(point.y)})"


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
