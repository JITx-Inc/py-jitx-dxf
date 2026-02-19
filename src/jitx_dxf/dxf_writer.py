"""DXF layer setup, geometry emission, and conversion orchestration."""

from __future__ import annotations

import math
import sys

from ezdxf import units as ezdxf_units
from ezdxf.document import Drawing
from ezdxf.filemanagement import new as ezdxf_new
from ezdxf.layouts.layout import Modelspace

from .models import (
    BoardData,
    CirclePad,
    CopperArc,
    CopperLine,
    CopperPolygon,
    CopperShape,
    Instance,
    LineShape,
    Package,
    Point,
    PolygonPad,
    PolygonShape,
    Pose,
    RectanglePad,
    Via,
)
from .transforms import transform_angle, transform_point
from .xml_parser import parse_xml


# ─── DXF Layer Setup ─────────────────────────────────────────────────────

LAYER_DEFS = {
    "BoardOutline": {"color": 7},
    "Pads_Top": {"color": 1},
    "Pads_Bottom": {"color": 5},
    "Vias": {"color": 8},
    "Drill": {"color": 8},
    "Silkscreen_Top": {"color": 3},
    "Silkscreen_Bottom": {"color": 4},
    "Courtyard_Top": {"color": 6},
    "Courtyard_Bottom": {"color": 2},
    "Components": {"color": 7},
}

# Colors for copper layers by index (cycling if more than available)
COPPER_COLORS = [30, 140, 170, 200, 50, 110]


def copper_layer_name(layer_index: int, layer_names: dict[int, str], prefix: str = "Copper") -> str:
    """Generate a DXF layer name for a copper layer index."""
    stackup_name = layer_names.get(layer_index, f"L{layer_index}")
    return f"{prefix}_{stackup_name}"


def get_dxf_layer(layer_name: str, side: str) -> str:
    mapping = {
        "SILKSCREEN": f"Silkscreen_{side}",
        "COURTYARD": f"Courtyard_{side}",
    }
    return mapping.get(layer_name, f"{layer_name}_{side}")


def _flip_side(side: str) -> str:
    """Return the opposite board side."""
    return "Bottom" if side == "Top" else "Top"


def resolve_side(shape_side: str, inst_side: str) -> str:
    """Resolve a shape's layer side for a given instance placement.

    Package shapes are defined relative to a Top-side placement.  When the
    instance is on the Bottom, every Top shape must flip to Bottom and vice
    versa.
    """
    if inst_side == "Bottom":
        return _flip_side(shape_side)
    return shape_side


def setup_layers(doc: Drawing, layer_names: dict[int, str]) -> None:
    for layer_name, props in LAYER_DEFS.items():
        doc.layers.add(layer_name, color=props["color"])
    # Create a single copper layer per conductor layer in the stackup
    for idx, name in layer_names.items():
        color = COPPER_COLORS[idx % len(COPPER_COLORS)]
        doc.layers.add(f"Copper_{name}", color=color)


# ─── DXF Emission Helpers ─────────────────────────────────────────────────


def _add_wide_line(
    msp: Modelspace,
    p1: tuple[float, float],
    p2: tuple[float, float],
    width: float,
    layer: str,
) -> None:
    """Emit a line with physical width as a 2-vertex LWPOLYLINE.

    Uses per-vertex start/end width (xyseb format) for maximum viewer
    compatibility — some DXF viewers ignore const_width.
    """
    # xyseb format: x, y, start_width, end_width, bulge
    msp.add_lwpolyline(
        [(p1[0], p1[1], width, width, 0.0), (p2[0], p2[1], width, width, 0.0)],
        dxfattribs={"layer": layer},
        format="xyseb",
    )


def _add_wide_arc(
    msp: Modelspace,
    center: tuple[float, float],
    radius: float,
    start_angle: float,
    end_angle: float,
    width: float,
    layer: str,
) -> None:
    """Emit an arc with physical width as a 2-vertex LWPOLYLINE with bulge."""
    sa_rad = math.radians(start_angle)
    ea_rad = math.radians(end_angle)
    p1x = center[0] + radius * math.cos(sa_rad)
    p1y = center[1] + radius * math.sin(sa_rad)
    p2x = center[0] + radius * math.cos(ea_rad)
    p2y = center[1] + radius * math.sin(ea_rad)
    # Included angle (CCW from start to end)
    included = end_angle - start_angle
    if included <= 0:
        included += 360.0
    # Bulge = tan(included_angle / 4), positive for CCW
    bulge = math.tan(math.radians(included) / 4.0)
    # xyseb format: x, y, start_width, end_width, bulge
    msp.add_lwpolyline(
        [(p1x, p1y, width, width, bulge), (p2x, p2y, width, width, 0.0)],
        dxfattribs={"layer": layer},
        format="xyseb",
    )


# ─── DXF Emission ────────────────────────────────────────────────────────


def emit_board_outline(msp: Modelspace, data: BoardData) -> None:
    for line in data.boundary_lines:
        msp.add_line(
            start=(line.p1.x, line.p1.y),
            end=(line.p2.x, line.p2.y),
            dxfattribs={"layer": "BoardOutline"},
        )
    for arc in data.boundary_arcs:
        msp.add_arc(
            center=(arc.center.x, arc.center.y),
            radius=arc.radius,
            start_angle=arc.start_angle,
            end_angle=arc.end_angle,
            dxfattribs={"layer": "BoardOutline"},
        )


def _emit_drill_hole(
    msp: Modelspace, center: Point, hole_radius: float, inst_pose: Pose
) -> None:
    """Emit a drill hole circle on the Drill layer if hole_radius > 0."""
    if hole_radius <= 0.0:
        return
    board_pt = transform_point(center, inst_pose)
    msp.add_circle(
        center=(board_pt.x, board_pt.y),
        radius=hole_radius,
        dxfattribs={"layer": "Drill"},
    )


def emit_pads(
    msp: Modelspace, pads: list[CirclePad], pose: Pose, side: str
) -> None:
    layer = f"Pads_{side}"
    for pad in pads:
        board_pt = transform_point(pad.center, pose)
        msp.add_circle(
            center=(board_pt.x, board_pt.y),
            radius=pad.radius,
            dxfattribs={"layer": layer},
        )
        _emit_drill_hole(msp, pad.center, pad.hole_radius, pose)


def emit_rectangle_pads(
    msp: Modelspace,
    rectangle_pads: list[RectanglePad],
    inst_pose: Pose,
    side: str,
) -> None:
    layer = f"Pads_{side}"
    for pad in rectangle_pads:
        # Build rectangle corners in rect-local coordinates
        hw = pad.width / 2.0
        hh = pad.height / 2.0
        corners = [
            Point(-hw, -hh),
            Point(hw, -hh),
            Point(hw, hh),
            Point(-hw, hh),
        ]
        # Transform: rect-local -> pad-local (via rect_pose) -> package-local (via pad_pose) -> board (via inst_pose)
        transformed = []
        for pt in corners:
            pad_pt = transform_point(pt, pad.rect_pose)
            pkg_pt = transform_point(pad_pt, pad.pad_pose)
            board_pt = transform_point(pkg_pt, inst_pose)
            transformed.append((board_pt.x, board_pt.y))
        msp.add_lwpolyline(transformed, close=True, dxfattribs={"layer": layer})
        # Drill hole at pad center
        _emit_drill_hole(msp, Point(pad.pad_pose.x, pad.pad_pose.y), pad.hole_radius, inst_pose)


def emit_polygon_pads(
    msp: Modelspace,
    polygon_pads: list[PolygonPad],
    inst_pose: Pose,
    side: str,
) -> None:
    layer = f"Pads_{side}"
    for pad in polygon_pads:
        transformed = []
        for pt in pad.points:
            pkg_pt = transform_point(pt, pad.pose)
            board_pt = transform_point(pkg_pt, inst_pose)
            transformed.append((board_pt.x, board_pt.y))
        if len(transformed) < 2:
            continue
        msp.add_lwpolyline(
            transformed,
            close=True,
            dxfattribs={"layer": layer},
        )
        # Drill hole at pad center
        _emit_drill_hole(msp, Point(pad.pose.x, pad.pose.y), pad.hole_radius, inst_pose)


def emit_polygon(
    msp: Modelspace,
    points: list[Point],
    layer: str,
    pose: Pose | None = None,
) -> None:
    transformed = []
    for pt in points:
        if pose is not None:
            bpt = transform_point(pt, pose)
            transformed.append((bpt.x, bpt.y))
        else:
            transformed.append((pt.x, pt.y))
    if len(transformed) < 2:
        return
    msp.add_lwpolyline(
        transformed,
        close=True,
        dxfattribs={"layer": layer},
    )


def emit_line_shape(
    msp: Modelspace, line_shape: LineShape, pose: Pose | None = None, layer: str | None = None,
) -> None:
    if layer is None:
        layer = get_dxf_layer(line_shape.layer_name, line_shape.side)
    p1 = line_shape.line.p1
    p2 = line_shape.line.p2
    if pose is not None:
        p1 = transform_point(p1, pose)
        p2 = transform_point(p2, pose)
    _add_wide_line(msp, (p1.x, p1.y), (p2.x, p2.y), line_shape.line.width, layer)


def emit_instance(
    msp: Modelspace,
    inst: Instance,
    packages: dict[str, Package],
    layer_filter: set[str] | None,
) -> None:
    pkg = packages.get(inst.package_name)
    if pkg is None:
        print(
            f"Warning: Package '{inst.package_name}' not found for "
            f"instance '{inst.designator}'",
            file=sys.stderr,
        )
        return

    if layer_filter is None or "Components" in layer_filter:
        msp.add_point(
            location=(inst.pose.x, inst.pose.y),
            dxfattribs={"layer": "Components"},
        )

    pad_layer = f"Pads_{inst.side}"
    if layer_filter is None or pad_layer in layer_filter or "Drill" in (layer_filter or set()):
        emit_pads(msp, pkg.pads, inst.pose, inst.side)
        emit_rectangle_pads(msp, pkg.rectangle_pads, inst.pose, inst.side)
        emit_polygon_pads(msp, pkg.polygon_pads, inst.pose, inst.side)

    for poly in pkg.polygons:
        resolved_side = resolve_side(poly.side, inst.side)
        layer = get_dxf_layer(poly.layer_name, resolved_side)
        if layer_filter is None or layer in layer_filter:
            emit_polygon(msp, poly.points, layer, inst.pose)

    for line_shape in pkg.lines:
        resolved_side = resolve_side(line_shape.side, inst.side)
        layer = get_dxf_layer(line_shape.layer_name, resolved_side)
        if layer_filter is None or layer in layer_filter:
            emit_line_shape(msp, line_shape, inst.pose, layer)

    if layer_filter is None or "Components" in layer_filter:
        if inst.designator_text is not None:
            dt = inst.designator_text
            text_board_pt = transform_point(
                Point(x=dt.pose.x, y=dt.pose.y), inst.pose
            )
            text_rotation = transform_angle(dt.pose.angle, inst.pose)
            msp.add_text(
                dt.string,
                height=dt.size,
                dxfattribs={
                    "layer": "Components",
                    "rotation": text_rotation,
                    "insert": (text_board_pt.x, text_board_pt.y),
                },
            )

    # Instance-level shapes (value labels, custom geometry) — already in board coords
    for ts in inst.shapes_text:
        layer = get_dxf_layer(ts.layer_name, ts.side)
        if layer_filter is not None and layer not in layer_filter:
            continue
        msp.add_text(
            ts.string,
            height=ts.size,
            dxfattribs={
                "layer": layer,
                "rotation": ts.pose.angle,
                "insert": (ts.pose.x, ts.pose.y),
            },
        )
    for poly in inst.shapes_polygon:
        layer = get_dxf_layer(poly.layer_name, poly.side)
        if layer_filter is None or layer in layer_filter:
            emit_polygon(msp, poly.points, layer)
    for ls in inst.shapes_line:
        layer = get_dxf_layer(ls.layer_name, ls.side)
        if layer_filter is None or layer in layer_filter:
            _add_wide_line(
                msp, (ls.line.p1.x, ls.line.p1.y), (ls.line.p2.x, ls.line.p2.y),
                ls.line.width, layer,
            )


def emit_tracks(
    msp: Modelspace,
    tracks: list[CopperShape],
    layer_names: dict[int, str],
    layer_filter: set[str] | None,
) -> None:
    for track in tracks:
        layer = copper_layer_name(track.layer_index, layer_names, "Copper")
        if layer_filter is not None and layer not in layer_filter:
            continue
        if isinstance(track, CopperPolygon):
            pts = [(p.x, p.y) for p in track.points]
            if len(pts) < 2:
                continue
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})
        elif isinstance(track, CopperLine):
            _add_wide_line(
                msp, (track.p1.x, track.p1.y), (track.p2.x, track.p2.y),
                track.width, layer,
            )
        elif isinstance(track, CopperArc):
            _add_wide_arc(
                msp, (track.center.x, track.center.y), track.radius,
                track.start_angle, track.end_angle, track.width, layer,
            )


def emit_fills(
    msp: Modelspace,
    fills: list[CopperPolygon],
    layer_names: dict[int, str],
    layer_filter: set[str] | None,
) -> None:
    for fill in fills:
        layer = copper_layer_name(fill.layer_index, layer_names, "Copper")
        if layer_filter is not None and layer not in layer_filter:
            continue
        pts = [(p.x, p.y) for p in fill.points]
        if len(pts) < 2:
            continue
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})


def emit_vias(
    msp: Modelspace,
    vias: list[Via],
    layer_filter: set[str] | None,
) -> None:
    for via in vias:
        # Via pad (annular ring) on Vias layer
        if layer_filter is None or "Vias" in layer_filter:
            msp.add_circle(
                center=(via.center.x, via.center.y),
                radius=via.diameter / 2.0,
                dxfattribs={"layer": "Vias"},
            )
        # Drill hole on Drill layer
        if layer_filter is None or "Drill" in layer_filter:
            msp.add_circle(
                center=(via.center.x, via.center.y),
                radius=via.hole_diameter / 2.0,
                dxfattribs={"layer": "Drill"},
            )


def emit_board_shapes(
    msp: Modelspace,
    shapes: list[PolygonShape],
    layer_filter: set[str] | None,
) -> None:
    for shape in shapes:
        layer = get_dxf_layer(shape.layer_name, shape.side)
        if layer_filter is None or layer in layer_filter:
            emit_polygon(msp, shape.points, layer)


def emit_board_line_shapes(
    msp: Modelspace,
    line_shapes: list[LineShape],
    layer_filter: set[str] | None,
) -> None:
    for ls in line_shapes:
        layer = get_dxf_layer(ls.layer_name, ls.side)
        if layer_filter is None or layer in layer_filter:
            _add_wide_line(
                msp, (ls.line.p1.x, ls.line.p1.y), (ls.line.p2.x, ls.line.p2.y),
                ls.line.width, layer,
            )


# ─── Main Conversion ─────────────────────────────────────────────────────


def convert(
    xml_path: str,
    dxf_path: str,
    layers: set[str] | None = None,
) -> None:
    data = parse_xml(xml_path)

    n_track_lines = sum(1 for t in data.tracks if isinstance(t, CopperLine))
    n_track_arcs = sum(1 for t in data.tracks if isinstance(t, CopperArc))
    n_track_polys = sum(1 for t in data.tracks if isinstance(t, CopperPolygon))
    print(f"Parsed: {len(data.boundary_lines)} boundary lines, "
          f"{len(data.boundary_arcs)} boundary arcs, "
          f"{len(data.packages)} packages, "
          f"{len(data.instances)} instances, "
          f"{len(data.board_shapes)} board polygon shapes, "
          f"{len(data.board_line_shapes)} board line shapes, "
          f"{len(data.tracks)} tracks ({n_track_lines} line, {n_track_arcs} arc, {n_track_polys} polygon), "
          f"{len(data.fills)} fills, "
          f"{len(data.vias)} vias")

    for pkg_name, pkg in data.packages.items():
        print(f"  Package '{pkg_name}': {len(pkg.pads)} circle, "
              f"{len(pkg.rectangle_pads)} rect, "
              f"{len(pkg.polygon_pads)} polygon pads, "
              f"{len(pkg.polygons)} shapes, {len(pkg.lines)} lines")

    for inst in data.instances:
        print(f"  Instance '{inst.designator}': package='{inst.package_name}', "
              f"pose=({inst.pose.x}, {inst.pose.y}, {inst.pose.angle}deg)")

    if data.layer_names:
        print(f"  Stackup layers: {data.layer_names}")

    doc = ezdxf_new("R2010")
    doc.units = ezdxf_units.MM
    setup_layers(doc, data.layer_names)
    msp = doc.modelspace()

    if layers is None or "BoardOutline" in layers:
        emit_board_outline(msp, data)

    for inst in data.instances:
        emit_instance(msp, inst, data.packages, layers)

    emit_tracks(msp, data.tracks, data.layer_names, layers)
    emit_fills(msp, data.fills, data.layer_names, layers)
    emit_vias(msp, data.vias, layers)
    emit_board_shapes(msp, data.board_shapes, layers)
    emit_board_line_shapes(msp, data.board_line_shapes, layers)

    doc.saveas(dxf_path)
    print(f"Written: {dxf_path}")
