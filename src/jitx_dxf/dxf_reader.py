"""Read DXF files and produce intermediate representation for JITX code generation.

Uses ezdxf to parse DXF entities and classifies them by PCB role
(board outline, cutouts, mounting holes, keepouts, etc.).
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path

import ezdxf

from .models import (
    ArcPathSegment,
    ClassifiedEntities,
    ClosedPath,
    DxfCircle,
    DxfHatch,
    DxfInventory,
    DxfText,
    Point,
)
from .path_assembler import (
    assemble_closed_paths,
    lwpolyline_to_closed_path,
    path_area,
    path_bounding_box,
    point_in_path,
)

# ezdxf INSUNITS codes → unit names
_INSUNITS_MAP: dict[int, str] = {
    0: None,  # unitless
    1: "in",
    2: "ft",
    3: "mi",
    4: "mm",
    5: "cm",
    6: "m",
    8: "μin",
    9: "μm",
    10: "yd",
}

# Unit → mm conversion factors
_UNIT_TO_MM: dict[str, float] = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "in": 25.4,
    "ft": 304.8,
    "mil": 0.0254,
    "μin": 0.0000254,
    "μm": 0.001,
    "yd": 914.4,
}

# Layer name patterns for classification
_LAYER_PATTERNS: dict[str, list[str]] = {
    "outline": ["outline", "board", "boundary", "profile", "edge", "border"],
    "cutout": ["cutout", "route", "rout", "slot"],
    "hole": ["hole", "drill", "mount"],
    "keepout": ["keepout", "keep-out", "keep_out", "restrict"],
    "soldermask": ["mask", "soldermask", "solder"],
    "annotation": ["dim", "dimension", "note", "text", "anno"],
}


def read_dxf(dxf_path: str) -> DxfInventory:
    """Read a DXF file and return an inventory of its contents.

    Args:
        dxf_path: Path to the DXF file.

    Returns:
        DxfInventory with file metadata, layer counts, entity counts, and bounding box.
    """
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    # Collect layer entity counts
    layer_counts: dict[str, int] = defaultdict(int)
    entity_counts: dict[str, int] = defaultdict(int)

    all_x: list[float] = []
    all_y: list[float] = []

    for entity in msp:
        etype = entity.dxftype()
        layer = entity.dxf.layer
        layer_counts[layer] += 1
        entity_counts[etype] += 1

        # Collect coordinates for bounding box
        _collect_entity_coords(entity, all_x, all_y)

    bbox = None
    if all_x and all_y:
        bbox = (Point(min(all_x), min(all_y)), Point(max(all_x), max(all_y)))

    units = _detect_units(doc)

    return DxfInventory(
        filepath=dxf_path,
        dxf_version=doc.dxfversion,
        units=units,
        layers=dict(layer_counts),
        entity_counts=dict(entity_counts),
        bounding_box=bbox,
    )


def classify_entities(
    dxf_path: str,
    layer_map: dict[str, str] | None = None,
    unit: str | None = None,
) -> ClassifiedEntities:
    """Read a DXF file and classify entities by PCB role.

    Args:
        dxf_path: Path to the DXF file.
        layer_map: Optional mapping of DXF layer names to PCB roles
            ("outline", "cutout", "hole", "keepout", "soldermask", "annotation").
        unit: Force unit interpretation ("mm", "in", "mil"). Auto-detect if None.

    Returns:
        ClassifiedEntities with board outline, cutouts, holes, etc.
    """
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    # Determine unit scale
    unit_scale = _resolve_unit_scale(doc, unit, msp)

    # Parse all entities, grouped by layer
    layer_lines: dict[str, list[tuple[Point, Point]]] = defaultdict(list)
    layer_arcs: dict[str, list[ArcPathSegment]] = defaultdict(list)
    layer_lwpolys: dict[str, list[ClosedPath]] = defaultdict(list)
    circles: list[DxfCircle] = []
    texts: list[DxfText] = []
    hatches: list[DxfHatch] = []

    for entity in msp:
        etype = entity.dxftype()
        layer = entity.dxf.layer

        if etype == "LINE":
            p1 = Point(entity.dxf.start.x * unit_scale, entity.dxf.start.y * unit_scale)
            p2 = Point(entity.dxf.end.x * unit_scale, entity.dxf.end.y * unit_scale)
            layer_lines[layer].append((p1, p2))

        elif etype == "ARC":
            arc = _parse_arc_entity(entity, unit_scale)
            layer_arcs[layer].append(arc)

        elif etype == "LWPOLYLINE":
            path = _parse_lwpolyline(entity, unit_scale)
            if path is not None:
                layer_lwpolys[layer].append(path)

        elif etype == "CIRCLE":
            c = Point(entity.dxf.center.x * unit_scale, entity.dxf.center.y * unit_scale)
            circles.append(DxfCircle(center=c, radius=entity.dxf.radius * unit_scale, layer=layer))

        elif etype in ("TEXT", "MTEXT"):
            texts.append(_parse_text_entity(entity, unit_scale))

        elif etype == "HATCH":
            hatch = _parse_hatch_entity(entity, unit_scale)
            if hatch is not None:
                hatches.append(hatch)

        # SPLINE and other unsupported types are silently skipped

    # Assemble LINE/ARC segments into closed paths (per layer)
    all_paths: list[ClosedPath] = []
    all_layers = set(layer_lines.keys()) | set(layer_arcs.keys())
    for layer in all_layers:
        lines = layer_lines.get(layer, [])
        arcs = layer_arcs.get(layer, [])
        paths = assemble_closed_paths(lines, arcs, source_layer=layer)
        all_paths.extend(paths)

    # Add LWPOLYLINE closed paths
    for layer, polys in layer_lwpolys.items():
        all_paths.extend(polys)

    # Classify everything
    if layer_map:
        return _classify_by_layer_map(
            all_paths, circles, texts, hatches, layer_map, unit_scale
        )
    else:
        return _classify_by_heuristics(
            all_paths, circles, texts, hatches, unit_scale
        )


def _detect_units(doc: ezdxf.document.Drawing) -> str | None:
    """Detect the unit system from a DXF document."""
    try:
        insunits = doc.header.get("$INSUNITS", 0)
        if insunits in _INSUNITS_MAP:
            return _INSUNITS_MAP[insunits]
    except Exception:
        pass
    return None


def _resolve_unit_scale(
    doc: ezdxf.document.Drawing,
    forced_unit: str | None,
    msp,
) -> float:
    """Determine the unit-to-mm conversion factor.

    Priority:
    1. Forced unit from user
    2. DXF header $INSUNITS (with sanity check)
    3. Bounding box heuristic
    """
    if forced_unit:
        return _UNIT_TO_MM.get(forced_unit, 1.0)

    # Compute raw bounding box extent for heuristic validation
    xs: list[float] = []
    ys: list[float] = []
    for entity in msp:
        _collect_entity_coords(entity, xs, ys)

    raw_extent = 0.0
    if xs and ys:
        raw_extent = max(max(xs) - min(xs), max(ys) - min(ys))

    detected = _detect_units(doc)
    if detected and detected in _UNIT_TO_MM:
        scale = _UNIT_TO_MM[detected]
        # Sanity check: if scaled extent is unreasonable for a PCB (>5000mm = 5m),
        # the DXF header is likely wrong — fall through to heuristic
        if raw_extent * scale <= 5000:
            return scale

    # Heuristic: estimate from raw bounding box extent
    if raw_extent == 0:
        return 1.0  # assume mm

    if raw_extent > 500:
        return _UNIT_TO_MM["mil"]  # likely mils (typical PCB: 1000-20000 mil)
    # For extent 0-500, assume mm (typical PCB: 5-500mm)
    # Users with inch-based files can override with --unit in
    return 1.0


def _collect_entity_coords(entity, xs: list[float], ys: list[float]) -> None:
    """Extract representative coordinates from a DXF entity for bounding box."""
    etype = entity.dxftype()
    if etype == "LINE":
        xs.extend([entity.dxf.start.x, entity.dxf.end.x])
        ys.extend([entity.dxf.start.y, entity.dxf.end.y])
    elif etype == "CIRCLE":
        cx, cy = entity.dxf.center.x, entity.dxf.center.y
        r = entity.dxf.radius
        xs.extend([cx - r, cx + r])
        ys.extend([cy - r, cy + r])
    elif etype == "ARC":
        cx, cy = entity.dxf.center.x, entity.dxf.center.y
        r = entity.dxf.radius
        xs.extend([cx - r, cx + r])
        ys.extend([cy - r, cy + r])
    elif etype == "LWPOLYLINE":
        for x, y, *_ in entity.get_points(format="xyseb"):
            xs.append(x)
            ys.append(y)
    elif etype == "SPLINE":
        try:
            for pt in entity.control_points:
                xs.append(pt[0])
                ys.append(pt[1])
        except Exception:
            pass


def _parse_arc_entity(entity, unit_scale: float) -> ArcPathSegment:
    """Parse a DXF ARC entity into an ArcPathSegment."""
    cx = entity.dxf.center.x * unit_scale
    cy = entity.dxf.center.y * unit_scale
    r = entity.dxf.radius * unit_scale
    sa = entity.dxf.start_angle
    ea = entity.dxf.end_angle

    sp = Point(
        cx + r * math.cos(math.radians(sa)),
        cy + r * math.sin(math.radians(sa)),
    )
    ep = Point(
        cx + r * math.cos(math.radians(ea)),
        cy + r * math.sin(math.radians(ea)),
    )

    return ArcPathSegment(
        center=Point(cx, cy),
        radius=r,
        start_angle=sa,
        end_angle=ea,
        start_point=sp,
        end_point=ep,
    )


def _parse_lwpolyline(entity, unit_scale: float) -> ClosedPath | None:
    """Parse a DXF LWPOLYLINE entity. Returns a ClosedPath if closed, else None."""
    if not entity.closed:
        return None

    raw_points = list(entity.get_points(format="xyseb"))
    points = [(p[0] * unit_scale, p[1] * unit_scale) for p in raw_points]
    # Bulge is the 4th element in xyseb format (index 3 after x,y,start_width)
    # Actually in xyseb: x=0, y=1, s=start_width=2, e=end_width=3, b=bulge=4
    bulges = [p[4] if len(p) > 4 else 0.0 for p in raw_points]

    return lwpolyline_to_closed_path(points, bulges, entity.dxf.layer)


def _parse_text_entity(entity, unit_scale: float) -> DxfText:
    """Parse a DXF TEXT or MTEXT entity."""
    etype = entity.dxftype()

    if etype == "MTEXT":
        content = entity.text
        pos = entity.dxf.insert
        height = entity.dxf.char_height
        rotation = getattr(entity.dxf, "rotation", 0.0)
    else:
        content = entity.dxf.text
        pos = entity.dxf.insert
        height = entity.dxf.height
        rotation = getattr(entity.dxf, "rotation", 0.0)

    return DxfText(
        content=content,
        position=Point(pos.x * unit_scale, pos.y * unit_scale),
        height=height * unit_scale,
        rotation=rotation,
        layer=entity.dxf.layer,
    )


def _parse_hatch_entity(entity, unit_scale: float) -> DxfHatch | None:
    """Parse a DXF HATCH entity."""
    try:
        is_solid = entity.dxf.hatch_style == 0 or entity.dxf.pattern_name == "SOLID"
    except Exception:
        is_solid = False

    boundary_paths: list[ClosedPath] = []
    try:
        for bpath in entity.paths:
            segments = []
            if hasattr(bpath, "vertices"):
                # Polyline boundary
                verts = list(bpath.vertices)
                if len(verts) >= 3:
                    pts = [(v[0] * unit_scale, v[1] * unit_scale) for v in verts]
                    bulges = [v[2] if len(v) > 2 else 0.0 for v in verts]
                    boundary_paths.append(
                        lwpolyline_to_closed_path(pts, bulges, entity.dxf.layer)
                    )
            elif hasattr(bpath, "edges"):
                # Edge boundary — collect line/arc edges
                lines = []
                arcs = []
                for edge in bpath.edges:
                    if edge.EDGE_TYPE == "LineEdge":
                        p1 = Point(edge.start[0] * unit_scale, edge.start[1] * unit_scale)
                        p2 = Point(edge.end[0] * unit_scale, edge.end[1] * unit_scale)
                        lines.append((p1, p2))
                    elif edge.EDGE_TYPE == "ArcEdge":
                        cx = edge.center[0] * unit_scale
                        cy = edge.center[1] * unit_scale
                        r = edge.radius * unit_scale
                        sa = edge.start_angle
                        ea = edge.end_angle
                        sp = Point(
                            cx + r * math.cos(math.radians(sa)),
                            cy + r * math.sin(math.radians(sa)),
                        )
                        ep = Point(
                            cx + r * math.cos(math.radians(ea)),
                            cy + r * math.sin(math.radians(ea)),
                        )
                        arcs.append(ArcPathSegment(
                            center=Point(cx, cy), radius=r,
                            start_angle=sa, end_angle=ea,
                            start_point=sp, end_point=ep,
                        ))
                if lines or arcs:
                    paths = assemble_closed_paths(lines, arcs, source_layer=entity.dxf.layer)
                    boundary_paths.extend(paths)
    except Exception:
        return None

    if not boundary_paths:
        return None

    return DxfHatch(
        boundary_paths=boundary_paths,
        is_solid=is_solid,
        layer=entity.dxf.layer,
    )


def _classify_layer(layer_name: str) -> str | None:
    """Classify a DXF layer name by matching against known patterns.

    Returns a role string or None if no match.
    """
    lower = layer_name.lower()
    for role, patterns in _LAYER_PATTERNS.items():
        for pattern in patterns:
            if pattern in lower:
                return role
    return None


def _classify_by_layer_map(
    paths: list[ClosedPath],
    circles: list[DxfCircle],
    texts: list[DxfText],
    hatches: list[DxfHatch],
    layer_map: dict[str, str],
    unit_scale: float,
) -> ClassifiedEntities:
    """Classify entities using an explicit layer → role mapping."""
    result = ClassifiedEntities(unit_scale=unit_scale)

    # Classify paths
    outline_candidates: list[ClosedPath] = []
    for path in paths:
        role = layer_map.get(path.source_layer)
        if role == "outline":
            outline_candidates.append(path)
        elif role == "cutout":
            result.cutouts.append(path)
        elif role == "keepout":
            result.keepouts.append(path)
        elif role == "soldermask":
            result.soldermask_openings.append(path)
        else:
            result.unclassified_paths.append(path)

    # Pick largest outline candidate
    if outline_candidates:
        result.outline = max(outline_candidates, key=lambda p: abs(path_area(p)))

    # Classify circles
    for circle in circles:
        role = layer_map.get(circle.layer)
        if role == "hole":
            result.holes.append(circle)
        else:
            result.unclassified_circles.append(circle)

    result.texts = texts
    result.hatches = hatches
    return result


def _classify_by_heuristics(
    paths: list[ClosedPath],
    circles: list[DxfCircle],
    texts: list[DxfText],
    hatches: list[DxfHatch],
    unit_scale: float,
) -> ClassifiedEntities:
    """Classify entities using layer name heuristics and geometry analysis."""
    result = ClassifiedEntities(unit_scale=unit_scale)

    # First, try layer-name-based classification
    outline_candidates: list[ClosedPath] = []
    unresolved_paths: list[ClosedPath] = []
    unresolved_circles: list[DxfCircle] = []

    for path in paths:
        role = _classify_layer(path.source_layer)
        if role == "outline":
            outline_candidates.append(path)
        elif role == "cutout":
            result.cutouts.append(path)
        elif role == "keepout":
            result.keepouts.append(path)
        elif role == "soldermask":
            result.soldermask_openings.append(path)
        else:
            unresolved_paths.append(path)

    for circle in circles:
        role = _classify_layer(circle.layer)
        if role == "hole":
            result.holes.append(circle)
        else:
            unresolved_circles.append(circle)

    # Pick outline from candidates or largest unresolved path
    if outline_candidates:
        result.outline = max(outline_candidates, key=lambda p: abs(path_area(p)))
    elif unresolved_paths:
        # Largest closed path is the board outline
        largest_idx = max(range(len(unresolved_paths)),
                         key=lambda i: abs(path_area(unresolved_paths[i])))
        result.outline = unresolved_paths.pop(largest_idx)

    # Classify remaining items relative to outline
    if result.outline:
        for path in unresolved_paths:
            bb = path_bounding_box(path)
            center = Point((bb[0].x + bb[1].x) / 2, (bb[0].y + bb[1].y) / 2)
            if point_in_path(center, result.outline):
                result.cutouts.append(path)
            else:
                result.unclassified_paths.append(path)

        for circle in unresolved_circles:
            if point_in_path(circle.center, result.outline):
                result.holes.append(circle)
            else:
                result.unclassified_circles.append(circle)
    else:
        result.unclassified_paths = unresolved_paths
        result.unclassified_circles = unresolved_circles

    result.texts = texts
    result.hatches = hatches
    return result
