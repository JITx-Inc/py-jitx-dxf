"""Tests for the jitx_codegen module."""

from __future__ import annotations

from pathlib import Path

from jitx_dxf.dxf_reader import classify_entities
from jitx_dxf.jitx_codegen import (
    generate_board_code,
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

    def test_beeper_has_arc_polyline(self):
        """beeper flex outline should use ArcPolyline (has bulge arcs)."""
        classified = classify_entities(str(FIXTURES / "beeper_flex_outline.dxf"))
        code = generate_board_code(classified, class_name="BeeperBoard")
        assert "ArcPolyline" in code

    def test_outline_uses_canonical_shape_attribute(self):
        """Board class should use `shape =` (the canonical jitx.Board attribute)."""
        classified = classify_entities(str(FIXTURES / "hawk_outline.dxf"))
        code = generate_board_code(classified, class_name="HawkBoard")
        assert "    shape = " in code
        # The old `board_shape` attribute name must not appear.
        assert "board_shape" not in code

    def test_screwholes_emit_circuit_class(self):
        """When the DXF has cutouts/holes, a Circuit subclass should be emitted."""
        classified = classify_entities(str(FIXTURES / "hawk_outline_screwholes.dxf"))
        code = generate_board_code(classified, class_name="HawkBoard")
        assert "class HawkCircuit(Circuit):" in code
        # cutouts must be wrapped in Cutout(...) so jitx.feature.Cutout treats them as features.
        assert "Cutout(" in code
        assert "self.cutouts = [" in code

    def test_screwholes_emit_design_class(self):
        """When a Circuit is emitted, a Design class should also be emitted."""
        classified = classify_entities(str(FIXTURES / "hawk_outline_screwholes.dxf"))
        code = generate_board_code(classified, class_name="HawkBoard")
        assert "class HawkDesign(Design):" in code
        assert "board = HawkBoard()" in code
        assert "circuit = HawkCircuit()" in code

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
        """If class_name lacks a 'Board' suffix, derived names append 'Circuit'/'Design'."""
        classified = classify_entities(str(FIXTURES / "hawk_outline_screwholes.dxf"))
        code = generate_board_code(classified, class_name="Foo")
        assert "class Foo(Board):" in code
        assert "class FooCircuit(Circuit):" in code
        assert "class FooDesign(Design):" in code

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
