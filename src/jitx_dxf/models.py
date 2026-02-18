"""Data classes for JITX XML board design elements."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Point:
    x: float
    y: float


@dataclass
class Pose:
    x: float
    y: float
    angle: float  # degrees
    flip_x: bool


@dataclass
class LineSegment:
    p1: Point
    p2: Point
    width: float


@dataclass
class ArcSegment:
    center: Point
    radius: float
    start_angle: float  # degrees
    end_angle: float  # degrees
    width: float


@dataclass
class CirclePad:
    name: str
    center: Point  # local coordinates within package
    radius: float
    side: str  # "Top" or "Bottom"
    hole_radius: float = 0.0  # drill hole radius for TH pads (0 = SMD)


@dataclass
class RectanglePad:
    name: str
    width: float
    height: float
    rect_pose: Pose  # RECTANGLE's inner pose (offset/rotation within pad)
    pad_pose: Pose  # pad position/rotation within package
    side: str  # "Top" or "Bottom"
    hole_radius: float = 0.0  # drill hole radius for TH pads (0 = SMD)


@dataclass
class PolygonPad:
    name: str
    points: list[Point]  # pad shape vertices in pad-local coordinates
    pose: Pose  # pad position/rotation within package
    side: str  # "Top" or "Bottom"
    hole_radius: float = 0.0  # drill hole radius for TH pads (0 = SMD)


@dataclass
class PolygonShape:
    points: list[Point]
    layer_name: str  # "SILKSCREEN" or "COURTYARD"
    side: str  # "Top" or "Bottom"


@dataclass
class LineShape:
    line: LineSegment
    layer_name: str
    side: str


@dataclass
class TextShape:
    string: str
    size: float
    pose: Pose  # in board coordinates
    layer_name: str
    side: str


@dataclass
class Package:
    name: str
    pads: list[CirclePad]
    rectangle_pads: list[RectanglePad]
    polygon_pads: list[PolygonPad]
    polygons: list[PolygonShape]
    lines: list[LineShape]


@dataclass
class DesignatorText:
    string: str
    size: float
    anchor: str
    pose: Pose  # relative to instance pose


@dataclass
class Instance:
    designator: str
    package_name: str
    side: str
    pose: Pose
    designator_text: DesignatorText | None
    shapes_text: list[TextShape] = field(default_factory=list)
    shapes_polygon: list[PolygonShape] = field(default_factory=list)
    shapes_line: list[LineShape] = field(default_factory=list)


@dataclass
class CopperShape:
    """A copper shape on a conductor layer, identified by layer index."""
    layer_index: int  # stackup layer index (0=top, N=bottom)
    net: str


@dataclass
class CopperLine(CopperShape):
    p1: Point = field(default_factory=lambda: Point(0, 0))
    p2: Point = field(default_factory=lambda: Point(0, 0))
    width: float = 0.0


@dataclass
class CopperArc(CopperShape):
    center: Point = field(default_factory=lambda: Point(0, 0))
    radius: float = 0.0
    start_angle: float = 0.0
    end_angle: float = 0.0
    width: float = 0.0


@dataclass
class CopperPolygon(CopperShape):
    points: list[Point] = field(default_factory=list)


@dataclass
class Via:
    center: Point
    diameter: float  # pad diameter
    hole_diameter: float  # drill hole diameter
    net: str  # net name, may be empty
    start_side: str  # "Top" or "Bottom"
    end_side: str  # "Top" or "Bottom"


@dataclass
class BoardData:
    boundary_lines: list[LineSegment]
    boundary_arcs: list[ArcSegment]
    packages: dict[str, Package]
    instances: list[Instance]
    board_shapes: list[PolygonShape]
    board_line_shapes: list[LineShape]
    tracks: list[CopperShape]
    fills: list[CopperPolygon]
    vias: list[Via]
    layer_names: dict[int, str]  # maps layer index -> stackup layer name


# --- DXF Reader intermediate types ---


@dataclass
class PathSegment:
    """A segment of a path: either a line or an arc."""


@dataclass
class LinePathSegment(PathSegment):
    start: Point = field(default_factory=lambda: Point(0, 0))
    end: Point = field(default_factory=lambda: Point(0, 0))


@dataclass
class ArcPathSegment(PathSegment):
    center: Point = field(default_factory=lambda: Point(0, 0))
    radius: float = 0.0
    start_angle: float = 0.0  # degrees
    end_angle: float = 0.0  # degrees
    start_point: Point = field(default_factory=lambda: Point(0, 0))
    end_point: Point = field(default_factory=lambda: Point(0, 0))


@dataclass
class ClosedPath:
    """A closed path assembled from line/arc segments."""
    segments: list[PathSegment] = field(default_factory=list)
    source_layer: str = ""


@dataclass
class DxfCircle:
    center: Point = field(default_factory=lambda: Point(0, 0))
    radius: float = 0.0
    layer: str = ""


@dataclass
class DxfText:
    content: str = ""
    position: Point = field(default_factory=lambda: Point(0, 0))
    height: float = 0.0
    rotation: float = 0.0  # degrees
    layer: str = ""


@dataclass
class DxfHatch:
    boundary_paths: list[ClosedPath] = field(default_factory=list)
    is_solid: bool = False
    layer: str = ""


@dataclass
class DxfInventory:
    """Summary of what's in a DXF file, for the inspect command."""
    filepath: str = ""
    dxf_version: str = ""
    units: str | None = None
    layers: dict[str, int] = field(default_factory=dict)
    entity_counts: dict[str, int] = field(default_factory=dict)
    bounding_box: tuple[Point, Point] | None = None


@dataclass
class ClassifiedEntities:
    """DXF entities classified by PCB role."""
    outline: ClosedPath | None = None
    cutouts: list[ClosedPath] = field(default_factory=list)
    holes: list[DxfCircle] = field(default_factory=list)
    keepouts: list[ClosedPath] = field(default_factory=list)
    soldermask_openings: list[ClosedPath] = field(default_factory=list)
    texts: list[DxfText] = field(default_factory=list)
    hatches: list[DxfHatch] = field(default_factory=list)
    unclassified_paths: list[ClosedPath] = field(default_factory=list)
    unclassified_circles: list[DxfCircle] = field(default_factory=list)
    unit_scale: float = 1.0  # multiplier to convert to mm
