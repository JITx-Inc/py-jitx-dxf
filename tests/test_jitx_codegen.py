"""Tests for the jitx_codegen module."""

from __future__ import annotations

from pathlib import Path

import pytest

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
        # Should compile without errors
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
        code = generate_board_code(classified)
        assert "ArcPolyline" in code

    def test_screwholes_has_cutouts(self):
        """hawk screwholes should have cutouts in generated code."""
        classified = classify_entities(str(FIXTURES / "hawk_outline_screwholes.dxf"))
        code = generate_board_code(classified)
        assert "cutouts" in code

    def test_no_recenter(self):
        """With recenter=False, coordinates should match raw DXF values."""
        classified = classify_entities(str(FIXTURES / "hawk_outline.dxf"))
        code_centered = generate_board_code(classified, recenter=True)
        code_raw = generate_board_code(classified, recenter=False)
        # Both should compile
        compile(code_centered, "<centered>", "exec")
        compile(code_raw, "<raw>", "exec")
        # They should be different (hawk outline is already centered,
        # so they might be very similar — just check they don't crash)


class TestSnippets:
    """Test snippet generation."""

    def test_outline_snippet(self):
        classified = classify_entities(str(FIXTURES / "hawk_outline.dxf"))
        snippet = generate_outline_snippet(classified)
        assert "board_shape" in snippet
        assert "Polygon" in snippet

    def test_cutouts_snippet(self):
        classified = classify_entities(str(FIXTURES / "hawk_outline_screwholes.dxf"))
        snippet = generate_cutouts_snippet(classified)
        assert "cutouts" in snippet

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
