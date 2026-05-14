"""Tests for the jitx_codegen module."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from jitx_dxf.cli import _cmd_import
from jitx_dxf.dxf_reader import classify_entities
from jitx_dxf.jitx_codegen import (
    generate_board_code,
    generate_circuit_code,
    generate_cutouts_snippet,
    generate_holes_snippet,
    generate_outline_snippet,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestGenerateBoardCode:
    """Test full board code generation."""

    def test_hawk_compiles(self):
        """Generated code for hawk_outline.dxf should be valid Python."""
        classified = classify_entities(str(FIXTURES / "hawk_outline.dxf"))
        code = generate_board_code(classified, class_name="HawkBoard")
        compile(code, "<hawk_board>", "exec")

    def test_hawk_screwholes_compiles(self):
        """Generated code for hawk_outline_screwholes.dxf should be valid Python."""
        classified = classify_entities(str(FIXTURES / "hawk_outline_screwholes.dxf"))
        code = generate_board_code(classified, class_name="HawkBoard")
        compile(code, "<hawk_screwholes>", "exec")

    def test_beeper_compiles(self):
        """Generated code for beeper_flex_outline.dxf should be valid Python."""
        classified = classify_entities(str(FIXTURES / "beeper_flex_outline.dxf"))
        code = generate_board_code(classified, class_name="BeeperBoard")
        compile(code, "<beeper_board>", "exec")

    def test_class_name_in_output(self):
        """The class name should appear in the generated code."""
        classified = classify_entities(str(FIXTURES / "hawk_outline.dxf"))
        code = generate_board_code(classified, class_name="MyCustomBoard")
        assert "class MyCustomBoard(Board):" in code

    def test_module_name_in_docstring(self):
        """The module name should appear in the file docstring."""
        classified = classify_entities(str(FIXTURES / "hawk_outline.dxf"))
        code = generate_board_code(classified, module_name="test.dxf")
        assert "test.dxf" in code

    def test_hawk_has_polygon(self):
        """hawk outline should use Polygon (line-only segments)."""
        classified = classify_entities(str(FIXTURES / "hawk_outline.dxf"))
        code = generate_board_code(classified)
        assert "Polygon" in code
        assert "ArcPolyline" not in code

    def test_beeper_has_arc_polygon(self):
        """beeper flex outline should use ArcPolygon (closed shape with bulge arcs)."""
        classified = classify_entities(str(FIXTURES / "beeper_flex_outline.dxf"))
        code = generate_board_code(classified, class_name="BeeperBoard")
        assert "ArcPolygon" in code
        # ArcPolyline (which would require a width arg) must NOT appear.
        assert "ArcPolyline" not in code

    def test_beeper_imports_arc_when_emitting_arcs(self):
        """When the codegen emits Arc(...) inside an ArcPolyline, it must
        also import Arc — otherwise the generated file fails at class-body
        evaluation with NameError."""
        classified = classify_entities(str(FIXTURES / "beeper_flex_outline.dxf"))
        code = generate_board_code(classified, class_name="BeeperBoard")
        assert "Arc(" in code, "fixture should produce at least one Arc(...) literal"
        assert "import Arc" in code or ", Arc" in code or "Arc," in code, (
            "Arc must be imported alongside ArcPolyline"
        )
        # And the file should compile.
        compile(code, "<beeper_arc_imports>", "exec")

    def test_arc_start_angle_is_normalized(self):
        """jitx.shapes.primitive.Arc requires start in [0, 360); the
        path_assembler emits raw atan2 angles that can be negative.
        Verify the codegen does the wrap before emission."""

        classified = classify_entities(str(FIXTURES / "beeper_flex_outline.dxf"))
        code = generate_board_code(classified, class_name="BeeperBoard")
        # Extract every Arc(...) literal — track parentheses by hand so we
        # skip past the nested (cx, cy) tuple correctly.
        def iter_arc_literals(text: str):
            i = 0
            while True:
                idx = text.find("Arc(", i)
                if idx < 0:
                    return
                depth = 0
                j = idx + len("Arc(")
                while j < len(text):
                    ch = text[j]
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        if depth == 0:
                            yield text[idx : j + 1]
                            i = j + 1
                            break
                        depth -= 1
                    j += 1
                else:
                    return

        for literal in iter_arc_literals(code):
            args = literal[len("Arc("):-1]
            # Skip the (cx, cy) tuple
            depth = 0
            tail = ""
            for i, ch in enumerate(args):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                elif ch == "," and depth == 0:
                    tail = args[i + 1 :].strip()
                    break
            # tail now starts with the radius — split out start (3rd positional)
            parts = [p.strip() for p in tail.split(",")]
            assert len(parts) >= 3, f"unexpected Arc literal shape: {literal}"
            start = float(parts[1])
            sweep = float(parts[2])
            assert 0.0 <= start < 360.0, (
                f"Arc start must be in [0, 360); got {start} in {literal}"
            )
            assert -360.0 <= sweep <= 360.0, (
                f"Arc sweep must be in [-360, 360]; got {sweep} in {literal}"
            )

    def test_outline_uses_canonical_shape_attribute(self):
        """Board class should use `shape =` (the canonical jitx.Board attribute)."""
        classified = classify_entities(str(FIXTURES / "hawk_outline.dxf"))
        code = generate_board_code(classified, class_name="HawkBoard")
        assert "    shape = " in code
        # The old `board_shape` attribute name must not appear.
        assert "board_shape" not in code

    def test_screwholes_emit_board_holes(self):
        """When the DXF has cutouts/holes, Board.shape should carry holes."""
        classified = classify_entities(str(FIXTURES / "hawk_outline_screwholes.dxf"))
        code = generate_board_code(classified, class_name="HawkBoard")
        assert "class HawkCircuit" not in code
        assert "from jitx.circuit import Circuit" not in code
        assert "from jitx.feature import Cutout" not in code
        assert "Cutout(" not in code
        assert "holes=[" in code
        assert "non-plated board geometry" in code
        assert "separately generated companion Circuit file" in code
        assert "feature_ports" in code

    def test_screwholes_emit_companion_circuit_code(self):
        """Cutouts/holes should also have a removable companion Circuit template."""
        classified = classify_entities(str(FIXTURES / "hawk_outline_screwholes.dxf"))
        code = generate_circuit_code(classified, board_class_name="HawkBoard")
        assert "from jitx.circuit import Circuit" in code
        assert "from jitx.feature import Cutout" in code
        assert "from jitx.net import Port, port_array" in code
        assert "class HawkCircuit(Circuit):" in code
        assert "self.feature_ports = port_array(4, ptype=Port)" in code
        assert "Cutout(" in code
        assert "plated = HawkCircuit()" in code
        assert "chassis = Port()" in code
        assert "plated_feature_nets = [chassis + p for p in plated.feature_ports]" in code
        compile(code, "<hawk_screwholes_circuit>", "exec")

    def test_board_can_suppress_holes_for_companion_circuit(self):
        """Board output can omit features when a companion Circuit owns them."""
        classified = classify_entities(str(FIXTURES / "hawk_outline_screwholes.dxf"))
        code = generate_board_code(
            classified,
            class_name="HawkBoard",
            include_features_in_board=False,
        )
        assert "class HawkBoard(Board):" in code
        assert "holes=[" not in code
        assert "Cutout(" not in code
        assert "companion Circuit file" in code
        compile(code, "<hawk_suppressed_board>", "exec")

    def test_screwholes_omit_design_class(self):
        """Generated output should not create a Design subclass."""
        classified = classify_entities(str(FIXTURES / "hawk_outline_screwholes.dxf"))
        code = generate_board_code(classified, class_name="HawkBoard")
        assert "from jitx.design import Design" not in code
        assert "class HawkDesign" not in code
        assert "board = HawkBoard()" not in code
        assert "circuit = HawkCircuit()" not in code

    def test_outline_only_omits_circuit_and_design(self):
        """A DXF with only an outline should produce just a Board class."""
        classified = classify_entities(str(FIXTURES / "hawk_outline.dxf"))
        code = generate_board_code(classified, class_name="HawkBoard")
        assert "class HawkBoard(Board):" in code
        # No features → no Circuit/Design emitted.
        assert "Circuit" not in code
        assert "Design" not in code
        assert "Cutout" not in code

    def test_class_name_without_board_suffix(self):
        """If class_name lacks a 'Board' suffix, use it directly as the Board name."""
        classified = classify_entities(str(FIXTURES / "hawk_outline_screwholes.dxf"))
        code = generate_board_code(classified, class_name="Foo")
        circuit_code = generate_circuit_code(classified, board_class_name="Foo")
        assert "class Foo(Board):" in code
        assert "class FooCircuit" not in code
        assert "class FooDesign" not in code
        assert "class FooCircuit(Circuit):" in circuit_code
        assert "class FooDesign" not in circuit_code

    def test_circle_holes_are_board_holes(self):
        """DxfCircle holes should be approximated into Board.shape holes."""
        from jitx_dxf.models import (
            ClassifiedEntities,
            ClosedPath,
            DxfCircle,
            LinePathSegment,
            Point,
        )

        outline = ClosedPath(
            [
                LinePathSegment(Point(0, 0), Point(10, 0)),
                LinePathSegment(Point(10, 0), Point(10, 5)),
                LinePathSegment(Point(10, 5), Point(0, 5)),
                LinePathSegment(Point(0, 5), Point(0, 0)),
            ]
        )
        classified = ClassifiedEntities(
            outline=outline,
            holes=[DxfCircle(center=Point(2.0, 3.0), radius=0.5, layer="DRILL")],
        )

        code = generate_board_code(classified, class_name="DemoBoard")

        assert "holes=[" in code
        assert "Circle(" not in code
        assert "Cutout(" not in code
        assert "class DemoCircuit" not in code
        assert "(-2.5, 0.5)" in code

        circuit_code = generate_circuit_code(classified, board_class_name="DemoBoard")
        assert "from jitx.shapes.primitive import Circle" in circuit_code
        assert "self.feature_ports = port_array(1, ptype=Port)" in circuit_code
        assert "Cutout(Circle(radius=0.5).at(-3.0, 0.5))" in circuit_code

    def test_no_recenter(self):
        """With recenter=False, generated code should still compile."""
        classified = classify_entities(str(FIXTURES / "hawk_outline.dxf"))
        code_centered = generate_board_code(classified, recenter=True)
        code_raw = generate_board_code(classified, recenter=False)
        compile(code_centered, "<centered>", "exec")
        compile(code_raw, "<raw>", "exec")


class TestSnippets:
    """Test snippet generation."""

    def test_outline_snippet_uses_shape_attribute(self):
        classified = classify_entities(str(FIXTURES / "hawk_outline.dxf"))
        snippet = generate_outline_snippet(classified)
        assert snippet.startswith("shape = ")
        assert "Polygon" in snippet
        assert "board_shape" not in snippet

    def test_cutouts_snippet_wraps_in_cutout(self):
        classified = classify_entities(str(FIXTURES / "hawk_outline_screwholes.dxf"))
        snippet = generate_cutouts_snippet(classified)
        assert "self.cutouts = [" in snippet
        assert "Cutout(" in snippet

    def test_holes_snippet_wraps_in_cutout(self):
        # The bundled fixtures classify circles as cutout paths (polygon
        # approximations), not as DxfCircle holes, so build one directly.
        from jitx_dxf.models import ClassifiedEntities, DxfCircle, Point

        classified = ClassifiedEntities(
            holes=[DxfCircle(center=Point(1.0, 2.0), radius=0.8, layer="DRILL")]
        )
        snippet = generate_holes_snippet(classified)
        assert "Cutout(Circle(radius=0.8).at(1.0, 2.0))" in snippet

    def test_holes_snippet_no_holes(self):
        classified = classify_entities(str(FIXTURES / "hawk_outline.dxf"))
        snippet = generate_holes_snippet(classified)
        assert "No holes" in snippet

    def test_no_outline(self):
        """If no outline detected, snippet should say so."""
        from jitx_dxf.models import ClassifiedEntities
        empty = ClassifiedEntities()
        snippet = generate_outline_snippet(empty)
        assert "No outline" in snippet


class TestImportCli:
    """Test CLI import output behavior."""

    def test_output_default_keeps_features_in_board_only(self, tmp_path):
        board_path = tmp_path / "hawk_board.py"
        args = argparse.Namespace(
            input=str(FIXTURES / "hawk_outline_screwholes.dxf"),
            output=str(board_path),
            class_name="HawkBoard",
            snippet=False,
            layer_map=None,
            unit=None,
            no_recenter=False,
            plated_features_circuit=False,
        )

        _cmd_import(args)

        circuit_path = tmp_path / "hawk_board_circuit.py"
        assert board_path.exists()
        assert not circuit_path.exists()
        board_code = board_path.read_text()
        assert "class HawkBoard(Board):" in board_code
        assert "holes=[" in board_code
        assert "class HawkCircuit" not in board_code

    def test_output_flag_writes_companion_circuit_and_suppresses_board_features(
        self, tmp_path
    ):
        board_path = tmp_path / "hawk_board.py"
        args = argparse.Namespace(
            input=str(FIXTURES / "hawk_outline_screwholes.dxf"),
            output=str(board_path),
            class_name="HawkBoard",
            snippet=False,
            layer_map=None,
            unit=None,
            no_recenter=False,
            plated_features_circuit=True,
        )

        _cmd_import(args)

        circuit_path = tmp_path / "hawk_board_circuit.py"
        assert board_path.exists()
        assert circuit_path.exists()
        board_code = board_path.read_text()
        circuit_code = circuit_path.read_text()
        assert "class HawkBoard(Board):" in board_code
        assert "holes=[" not in board_code
        assert "companion Circuit file" in board_code
        assert "class HawkCircuit(Circuit):" in circuit_code
        assert "self.feature_ports = port_array(4, ptype=Port)" in circuit_code

    def test_plated_features_circuit_requires_output(self):
        args = argparse.Namespace(
            input=str(FIXTURES / "hawk_outline_screwholes.dxf"),
            output=None,
            class_name="HawkBoard",
            snippet=False,
            layer_map=None,
            unit=None,
            no_recenter=False,
            plated_features_circuit=True,
        )

        with pytest.raises(SystemExit):
            _cmd_import(args)
