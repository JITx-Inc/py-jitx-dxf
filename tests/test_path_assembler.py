"""Tests for the path_assembler module."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from jitx_dxf.models import ArcPathSegment, ClosedPath, LinePathSegment, Point
from jitx_dxf.path_assembler import (
    assemble_closed_paths,
    lwpolyline_to_closed_path,
    path_area,
    path_bounding_box,
    point_in_path,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestAssembleClosedPaths:
    """Test assembling disconnected LINE/ARC segments into closed paths."""

    def test_simple_rectangle(self):
        """4 lines forming a rectangle should produce 1 closed path."""
        lines = [
            (Point(0, 0), Point(10, 0)),
            (Point(10, 0), Point(10, 5)),
            (Point(10, 5), Point(0, 5)),
            (Point(0, 5), Point(0, 0)),
        ]
        paths = assemble_closed_paths(lines, [])
        assert len(paths) == 1
        assert len(paths[0].segments) == 4

    def test_shuffled_segments(self):
        """Segments in random order should still assemble into a closed path."""
        lines = [
            (Point(10, 5), Point(0, 5)),
            (Point(0, 0), Point(10, 0)),
            (Point(0, 5), Point(0, 0)),
            (Point(10, 0), Point(10, 5)),
        ]
        paths = assemble_closed_paths(lines, [])
        assert len(paths) == 1
        assert len(paths[0].segments) == 4

    def test_two_separate_paths(self):
        """Two disconnected rectangles should produce 2 closed paths."""
        lines = [
            # First rectangle
            (Point(0, 0), Point(5, 0)),
            (Point(5, 0), Point(5, 5)),
            (Point(5, 5), Point(0, 5)),
            (Point(0, 5), Point(0, 0)),
            # Second rectangle (offset)
            (Point(20, 20), Point(25, 20)),
            (Point(25, 20), Point(25, 25)),
            (Point(25, 25), Point(20, 25)),
            (Point(20, 25), Point(20, 20)),
        ]
        paths = assemble_closed_paths(lines, [])
        assert len(paths) == 2

    def test_empty_input(self):
        """Empty input should produce no paths."""
        paths = assemble_closed_paths([], [])
        assert paths == []

    def test_tolerance_matching(self):
        """Endpoints within tolerance should be considered connected."""
        lines = [
            (Point(0, 0), Point(10, 0)),
            (Point(10.0005, 0), Point(10, 5)),  # slightly off
            (Point(10, 5), Point(0, 5)),
            (Point(0, 5), Point(0, 0.0003)),  # slightly off
        ]
        paths = assemble_closed_paths(lines, [], tolerance=0.001)
        assert len(paths) == 1

    def test_hawk_outline_from_dxf(self):
        """hawk_outline.dxf has 32 LINE entities forming 1 closed path."""
        import ezdxf

        doc = ezdxf.readfile(str(FIXTURES / "hawk_outline.dxf"))
        msp = doc.modelspace()

        lines = []
        for entity in msp:
            if entity.dxftype() == "LINE":
                p1 = Point(entity.dxf.start.x, entity.dxf.start.y)
                p2 = Point(entity.dxf.end.x, entity.dxf.end.y)
                lines.append((p1, p2))

        assert len(lines) == 32
        paths = assemble_closed_paths(lines, [])
        assert len(paths) == 1
        assert len(paths[0].segments) == 32


class TestLwpolylineToClosedPath:
    """Test LWPOLYLINE conversion to closed paths."""

    def test_simple_square(self):
        """A square polyline with no bulge should have all line segments."""
        points = [(0, 0), (10, 0), (10, 10), (0, 10)]
        bulges = [0, 0, 0, 0]
        path = lwpolyline_to_closed_path(points, bulges, "test")
        assert len(path.segments) == 4
        assert all(isinstance(s, LinePathSegment) for s in path.segments)
        assert path.source_layer == "test"

    def test_bulge_creates_arc(self):
        """A non-zero bulge should create an arc segment."""
        points = [(0, 0), (10, 0), (10, 10), (0, 10)]
        bulges = [0.5, 0, 0, 0]  # First segment has bulge
        path = lwpolyline_to_closed_path(points, bulges, "test")
        assert isinstance(path.segments[0], ArcPathSegment)
        assert isinstance(path.segments[1], LinePathSegment)


class TestPathBoundingBox:
    """Test bounding box computation."""

    def test_rectangle_bbox(self):
        """Bounding box of a rectangle path."""
        path = ClosedPath(
            segments=[
                LinePathSegment(start=Point(1, 2), end=Point(5, 2)),
                LinePathSegment(start=Point(5, 2), end=Point(5, 8)),
                LinePathSegment(start=Point(5, 8), end=Point(1, 8)),
                LinePathSegment(start=Point(1, 8), end=Point(1, 2)),
            ],
            source_layer="",
        )
        bb_min, bb_max = path_bounding_box(path)
        assert bb_min.x == pytest.approx(1.0)
        assert bb_min.y == pytest.approx(2.0)
        assert bb_max.x == pytest.approx(5.0)
        assert bb_max.y == pytest.approx(8.0)


class TestPathArea:
    """Test area computation."""

    def test_unit_square_ccw(self):
        """CCW unit square should have positive area."""
        path = ClosedPath(
            segments=[
                LinePathSegment(start=Point(0, 0), end=Point(1, 0)),
                LinePathSegment(start=Point(1, 0), end=Point(1, 1)),
                LinePathSegment(start=Point(1, 1), end=Point(0, 1)),
                LinePathSegment(start=Point(0, 1), end=Point(0, 0)),
            ],
            source_layer="",
        )
        area = path_area(path)
        assert area == pytest.approx(1.0)

    def test_unit_square_cw(self):
        """CW unit square should have negative area."""
        path = ClosedPath(
            segments=[
                LinePathSegment(start=Point(0, 0), end=Point(0, 1)),
                LinePathSegment(start=Point(0, 1), end=Point(1, 1)),
                LinePathSegment(start=Point(1, 1), end=Point(1, 0)),
                LinePathSegment(start=Point(1, 0), end=Point(0, 0)),
            ],
            source_layer="",
        )
        area = path_area(path)
        assert area == pytest.approx(-1.0)

    def test_rectangle_area(self):
        """A 4x3 rectangle should have area 12."""
        path = ClosedPath(
            segments=[
                LinePathSegment(start=Point(0, 0), end=Point(4, 0)),
                LinePathSegment(start=Point(4, 0), end=Point(4, 3)),
                LinePathSegment(start=Point(4, 3), end=Point(0, 3)),
                LinePathSegment(start=Point(0, 3), end=Point(0, 0)),
            ],
            source_layer="",
        )
        assert abs(path_area(path)) == pytest.approx(12.0)


class TestPointInPath:
    """Test point-in-path testing."""

    def test_inside_rectangle(self):
        path = ClosedPath(
            segments=[
                LinePathSegment(start=Point(0, 0), end=Point(10, 0)),
                LinePathSegment(start=Point(10, 0), end=Point(10, 10)),
                LinePathSegment(start=Point(10, 10), end=Point(0, 10)),
                LinePathSegment(start=Point(0, 10), end=Point(0, 0)),
            ],
            source_layer="",
        )
        assert point_in_path(Point(5, 5), path) is True

    def test_outside_rectangle(self):
        path = ClosedPath(
            segments=[
                LinePathSegment(start=Point(0, 0), end=Point(10, 0)),
                LinePathSegment(start=Point(10, 0), end=Point(10, 10)),
                LinePathSegment(start=Point(10, 10), end=Point(0, 10)),
                LinePathSegment(start=Point(0, 10), end=Point(0, 0)),
            ],
            source_layer="",
        )
        assert point_in_path(Point(15, 5), path) is False
        assert point_in_path(Point(-1, 5), path) is False
