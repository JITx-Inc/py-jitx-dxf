"""Assemble disconnected LINE/ARC segments into closed paths.

Mechanical CAD DXF exports often represent the board outline as
disconnected LINE and ARC entities that share endpoints. This module
assembles them into closed paths by building an adjacency graph and
walking it to find closed loops.
"""

from __future__ import annotations

import math
from collections import defaultdict

from .models import (
    ArcPathSegment,
    ClosedPath,
    LinePathSegment,
    PathSegment,
    Point,
)

# Type alias for a hashable point key (rounded to tolerance grid)
type _PointKey = tuple[int, int]

# Precision multiplier for point hashing (1/tolerance)
_GRID_INV = 1000  # default: 0.001 mm tolerance


def _point_key(p: Point, grid_inv: int = _GRID_INV) -> _PointKey:
    """Hash a point to a grid cell for tolerance-based matching."""
    return (round(p.x * grid_inv), round(p.y * grid_inv))


def _segment_endpoints(seg: PathSegment) -> tuple[Point, Point]:
    """Return (start, end) points of a segment."""
    if isinstance(seg, LinePathSegment):
        return seg.start, seg.end
    if isinstance(seg, ArcPathSegment):
        return seg.start_point, seg.end_point
    msg = f"Unknown segment type: {type(seg)}"
    raise TypeError(msg)


def assemble_closed_paths(
    lines: list[tuple[Point, Point]],
    arcs: list[ArcPathSegment],
    tolerance: float = 0.001,
    source_layer: str = "",
) -> list[ClosedPath]:
    """Assemble disconnected LINE/ARC segments into closed paths.

    Algorithm:
    1. Convert all inputs to PathSegment objects
    2. Build adjacency graph by matching endpoints within tolerance
    3. Walk the graph to find closed loops
    4. Return list of ClosedPath objects

    Args:
        lines: List of (start, end) point tuples for LINE entities.
        arcs: List of ArcPathSegment objects for ARC entities.
        tolerance: Maximum distance between endpoints to consider connected (mm).
        source_layer: DXF layer name to tag on resulting paths.

    Returns:
        List of closed paths found.
    """
    grid_inv = round(1.0 / tolerance)

    # Build segment list
    segments: list[PathSegment] = []
    for start, end in lines:
        segments.append(LinePathSegment(start=start, end=end))
    segments.extend(arcs)

    if not segments:
        return []

    # Build adjacency: point_key -> list of (segment_index, is_start_endpoint)
    adjacency: dict[_PointKey, list[tuple[int, bool]]] = defaultdict(list)
    for i, seg in enumerate(segments):
        start, end = _segment_endpoints(seg)
        adjacency[_point_key(start, grid_inv)].append((i, True))
        adjacency[_point_key(end, grid_inv)].append((i, False))

    used = [False] * len(segments)
    paths: list[ClosedPath] = []

    for start_idx in range(len(segments)):
        if used[start_idx]:
            continue

        # Try to build a closed loop starting from this segment
        loop = _walk_loop(segments, adjacency, used, start_idx, grid_inv)
        if loop is not None:
            paths.append(ClosedPath(segments=loop, source_layer=source_layer))

    return paths


def _walk_loop(
    segments: list[PathSegment],
    adjacency: dict[_PointKey, list[tuple[int, bool]]],
    used: list[bool],
    start_idx: int,
    grid_inv: int,
) -> list[PathSegment] | None:
    """Walk from a starting segment to find a closed loop.

    Returns the list of segments forming the loop, or None if no loop found.
    """
    chain: list[PathSegment] = []
    current_idx = start_idx

    # We start at the start-point of the first segment
    seg = segments[current_idx]
    start_pt, end_pt = _segment_endpoints(seg)
    loop_start_key = _point_key(start_pt, grid_inv)

    chain.append(seg)
    used[current_idx] = True
    current_key = _point_key(end_pt, grid_inv)

    max_steps = len(segments)
    for _ in range(max_steps):
        if current_key == loop_start_key and len(chain) > 1:
            return chain  # Closed loop found!

        # Find next unused segment connected at current_key
        next_seg = _find_next(adjacency, used, current_key)
        if next_seg is None:
            # Dead end — mark segments as unused so they can be retried
            # from a different starting direction
            for s in chain:
                idx = segments.index(s)
                used[idx] = False
            return None

        seg_idx, entering_at_start = next_seg
        used[seg_idx] = True
        seg = segments[seg_idx]

        # Orient the segment: if we entered at the end, flip it
        if not entering_at_start:
            seg = _flip_segment(seg)

        chain.append(seg)
        _, end_pt = _segment_endpoints(seg)
        current_key = _point_key(end_pt, grid_inv)

    return None  # Exceeded max steps


def _find_next(
    adjacency: dict[_PointKey, list[tuple[int, bool]]],
    used: list[bool],
    key: _PointKey,
) -> tuple[int, bool] | None:
    """Find the next unused segment connected at the given point key.

    Returns (segment_index, entering_at_start) or None.
    """
    for seg_idx, is_start in adjacency.get(key, []):
        if not used[seg_idx]:
            return (seg_idx, is_start)
    return None


def _flip_segment(seg: PathSegment) -> PathSegment:
    """Return a new segment with start/end reversed."""
    if isinstance(seg, LinePathSegment):
        return LinePathSegment(start=seg.end, end=seg.start)
    if isinstance(seg, ArcPathSegment):
        return ArcPathSegment(
            center=seg.center,
            radius=seg.radius,
            start_angle=seg.end_angle,
            end_angle=seg.start_angle,
            start_point=seg.end_point,
            end_point=seg.start_point,
        )
    msg = f"Unknown segment type: {type(seg)}"
    raise TypeError(msg)


def lwpolyline_to_closed_path(
    points: list[tuple[float, float]],
    bulges: list[float],
    layer: str,
) -> ClosedPath:
    """Convert a closed LWPOLYLINE (with bulge values) to a ClosedPath.

    Args:
        points: List of (x, y) vertex positions.
        bulges: List of bulge values per vertex (0 = straight segment).
        layer: DXF layer name.

    Returns:
        A ClosedPath representing the polyline.
    """
    segments: list[PathSegment] = []
    n = len(points)

    for i in range(n):
        p1 = Point(points[i][0], points[i][1])
        p2 = Point(points[(i + 1) % n][0], points[(i + 1) % n][1])
        bulge = bulges[i] if i < len(bulges) else 0.0

        if abs(bulge) < 1e-10:
            segments.append(LinePathSegment(start=p1, end=p2))
        else:
            arc = _bulge_to_arc(p1, p2, bulge)
            segments.append(arc)

    return ClosedPath(segments=segments, source_layer=layer)


def _bulge_to_arc(p1: Point, p2: Point, bulge: float) -> ArcPathSegment:
    """Convert a DXF bulge value between two points to an arc segment.

    The bulge is the tangent of 1/4 of the included angle.
    Positive bulge = counterclockwise arc; negative = clockwise.
    """
    dx = p2.x - p1.x
    dy = p2.y - p1.y
    chord = math.hypot(dx, dy)

    if chord < 1e-12:
        return ArcPathSegment(
            center=p1, radius=0, start_angle=0, end_angle=0,
            start_point=p1, end_point=p2,
        )

    # Sagitta and radius
    s = bulge * chord / 2.0
    radius = abs((chord**2 / 4.0 + s**2) / (2.0 * s))

    # Midpoint of chord
    mx = (p1.x + p2.x) / 2.0
    my = (p1.y + p2.y) / 2.0

    # Unit normal to chord (pointing left of p1→p2)
    nx = -dy / chord
    ny = dx / chord

    # Distance from midpoint to center
    d = radius - abs(s)
    if bulge > 0:
        cx = mx + d * nx
        cy = my + d * ny
    else:
        cx = mx - d * nx
        cy = my - d * ny

    center = Point(cx, cy)

    start_angle = math.degrees(math.atan2(p1.y - cy, p1.x - cx))
    end_angle = math.degrees(math.atan2(p2.y - cy, p2.x - cx))

    return ArcPathSegment(
        center=center,
        radius=radius,
        start_angle=start_angle,
        end_angle=end_angle,
        start_point=p1,
        end_point=p2,
    )


def path_bounding_box(path: ClosedPath) -> tuple[Point, Point]:
    """Compute axis-aligned bounding box of a closed path.

    Returns (min_point, max_point).
    """
    xs: list[float] = []
    ys: list[float] = []

    for seg in path.segments:
        if isinstance(seg, LinePathSegment):
            xs.extend([seg.start.x, seg.end.x])
            ys.extend([seg.start.y, seg.end.y])
        elif isinstance(seg, ArcPathSegment):
            xs.extend([seg.start_point.x, seg.end_point.x])
            ys.extend([seg.start_point.y, seg.end_point.y])
            # Check if arc crosses any axis-aligned extremes
            _arc_bbox_extend(seg, xs, ys)

    if not xs:
        return (Point(0, 0), Point(0, 0))

    return (Point(min(xs), min(ys)), Point(max(xs), max(ys)))


def _arc_bbox_extend(arc: ArcPathSegment, xs: list[float], ys: list[float]) -> None:
    """Extend xs/ys lists with axis-aligned extreme points of an arc."""
    sa = arc.start_angle % 360
    ea = arc.end_angle % 360

    # Determine sweep direction from start_angle to end_angle
    # Check each cardinal direction (0°, 90°, 180°, 270°)
    for angle in [0.0, 90.0, 180.0, 270.0]:
        if _angle_in_arc(angle, sa, ea):
            rad = math.radians(angle)
            xs.append(arc.center.x + arc.radius * math.cos(rad))
            ys.append(arc.center.y + arc.radius * math.sin(rad))


def _angle_in_arc(angle: float, start: float, end: float) -> bool:
    """Check if an angle lies within the arc from start to end (CCW)."""
    angle = angle % 360
    start = start % 360
    end = end % 360

    if start <= end:
        return start <= angle <= end
    # Arc wraps around 360
    return angle >= start or angle <= end


def path_area(path: ClosedPath) -> float:
    """Compute signed area of a closed path (positive = CCW).

    Uses the shoelace formula for line segments and adds/subtracts
    circular segment areas for arcs.
    """
    area = 0.0

    for seg in path.segments:
        if isinstance(seg, LinePathSegment):
            # Shoelace term: (x1*y2 - x2*y1)
            area += seg.start.x * seg.end.y - seg.end.x * seg.start.y
        elif isinstance(seg, ArcPathSegment):
            # Shoelace for the chord
            area += (
                seg.start_point.x * seg.end_point.y
                - seg.end_point.x * seg.start_point.y
            )
            # Add circular segment area
            area += _arc_segment_area(seg)

    return area / 2.0


def _arc_segment_area(arc: ArcPathSegment) -> float:
    """Compute the signed area contribution of an arc's circular segment.

    This is the area between the arc and its chord, signed by direction.
    """
    if arc.radius < 1e-12:
        return 0.0

    # Sweep angle
    sweep = (arc.end_angle - arc.start_angle) % 360
    if sweep > 180:
        sweep -= 360

    sweep_rad = math.radians(sweep)

    # Circular segment area = r² * (θ - sin(θ)) / 2
    seg_area = arc.radius**2 * (sweep_rad - math.sin(sweep_rad)) / 2.0

    # Sign based on whether the arc bulges left (CCW) or right (CW)
    # relative to the chord direction
    return seg_area


def point_in_path(point: Point, path: ClosedPath) -> bool:
    """Test if a point lies inside a closed path using ray casting.

    Casts a ray in the +X direction and counts crossings with path edges.
    """
    crossings = 0
    px, py = point.x, point.y

    for seg in path.segments:
        if isinstance(seg, LinePathSegment):
            crossings += _ray_crosses_line(px, py, seg)
        elif isinstance(seg, ArcPathSegment):
            crossings += _ray_crosses_arc(px, py, seg)

    return crossings % 2 == 1


def _ray_crosses_line(px: float, py: float, seg: LinePathSegment) -> int:
    """Count ray (+X direction from px,py) crossings with a line segment."""
    y1, y2 = seg.start.y, seg.end.y
    x1, x2 = seg.start.x, seg.end.x

    # Check if ray's y-level intersects the segment's y-range
    if (y1 <= py < y2) or (y2 <= py < y1):
        # Compute x-coordinate of intersection
        t = (py - y1) / (y2 - y1)
        x_intersect = x1 + t * (x2 - x1)
        if x_intersect > px:
            return 1
    return 0


def _ray_crosses_arc(px: float, py: float, arc: ArcPathSegment) -> int:
    """Count ray (+X direction from px,py) crossings with an arc.

    Approximates the arc as line segments for the ray casting test.
    """
    # Approximate arc with line segments
    num_steps = max(8, int(abs(arc.end_angle - arc.start_angle) / 5))
    crossings = 0

    angles = []
    sweep = arc.end_angle - arc.start_angle
    for i in range(num_steps + 1):
        t = i / num_steps
        angle = math.radians(arc.start_angle + t * sweep)
        angles.append(angle)

    for i in range(len(angles) - 1):
        x1 = arc.center.x + arc.radius * math.cos(angles[i])
        y1 = arc.center.y + arc.radius * math.sin(angles[i])
        x2 = arc.center.x + arc.radius * math.cos(angles[i + 1])
        y2 = arc.center.y + arc.radius * math.sin(angles[i + 1])

        if (y1 <= py < y2) or (y2 <= py < y1):
            t = (py - y1) / (y2 - y1)
            x_intersect = x1 + t * (x2 - x1)
            if x_intersect > px:
                crossings += 1

    return crossings
