"""Tests for the dxf_reader module."""

from __future__ import annotations

from pathlib import Path

import pytest

from jitx_dxf.dxf_reader import classify_entities, read_dxf

FIXTURES = Path(__file__).parent / "fixtures"


class TestReadDxf:
    """Test DXF file inventory/inspection."""

    def test_hawk_outline(self):
        inv = read_dxf(str(FIXTURES / "hawk_outline.dxf"))
        assert inv.dxf_version == "AC1009"
        assert inv.entity_counts.get("LINE") == 32
        assert "0" in inv.layers
        assert inv.bounding_box is not None

    def test_hawk_outline_screwholes(self):
        inv = read_dxf(str(FIXTURES / "hawk_outline_screwholes.dxf"))
        assert inv.entity_counts.get("LINE") == 152
        assert inv.bounding_box is not None

    def test_beeper_flex(self):
        inv = read_dxf(str(FIXTURES / "beeper_flex_outline.dxf"))
        assert inv.units == "mm"
        assert inv.entity_counts.get("LWPOLYLINE") == 1
        assert inv.entity_counts.get("CIRCLE") == 2
        assert inv.entity_counts.get("LINE") == 3
        assert len(inv.layers) == 4  # OUTER_PROFILES, INTERIOR_PROFILES, BEND, BEND_EXTENT

    def test_ottercast_logo(self):
        inv = read_dxf(str(FIXTURES / "ottercast_logo.dxf"))
        assert inv.entity_counts.get("SPLINE", 0) > 0
        assert inv.entity_counts.get("LWPOLYLINE", 0) > 0
        assert inv.bounding_box is not None


class TestClassifyEntities:
    """Test DXF entity classification."""

    def test_hawk_outline_single_outline(self):
        """hawk_outline.dxf should classify as a single board outline."""
        result = classify_entities(str(FIXTURES / "hawk_outline.dxf"))
        assert result.outline is not None
        assert len(result.outline.segments) == 32
        assert len(result.cutouts) == 0
        assert len(result.holes) == 0
        assert len(result.unclassified_paths) == 0

    def test_hawk_screwholes_outline_and_cutouts(self):
        """hawk_outline_screwholes.dxf should have outline + 4 screw hole cutouts."""
        result = classify_entities(str(FIXTURES / "hawk_outline_screwholes.dxf"))
        assert result.outline is not None
        assert len(result.outline.segments) == 32  # outline is same shape
        assert len(result.cutouts) == 4  # 4 screw holes (each ~30 line segments)

    def test_beeper_flex_outline(self):
        """beeper_flex_outline.dxf should detect the LWPOLYLINE as outline."""
        result = classify_entities(str(FIXTURES / "beeper_flex_outline.dxf"))
        assert result.outline is not None
        # LWPOLYLINE with 20 vertices → 20 segments
        assert len(result.outline.segments) == 20

    def test_beeper_flex_units(self):
        """beeper_flex_outline.dxf is in mm; coordinates should be reasonable."""
        result = classify_entities(str(FIXTURES / "beeper_flex_outline.dxf"))
        assert result.unit_scale == 1.0  # mm

    def test_hawk_units_heuristic(self):
        """hawk_outline.dxf has wrong INSUNITS=meters, should fall back to mm."""
        result = classify_entities(str(FIXTURES / "hawk_outline.dxf"))
        assert result.unit_scale == 1.0  # should be mm, not meters

    def test_forced_unit(self):
        """Forcing unit should override auto-detection."""
        result = classify_entities(str(FIXTURES / "hawk_outline.dxf"), unit="in")
        assert result.unit_scale == 25.4

    def test_explicit_layer_map(self):
        """Using an explicit layer map should classify by layer name."""
        result = classify_entities(
            str(FIXTURES / "beeper_flex_outline.dxf"),
            layer_map={
                "OUTER_PROFILES": "outline",
                "INTERIOR_PROFILES": "hole",
            },
        )
        assert result.outline is not None
        assert len(result.holes) == 2  # 2 circles on INTERIOR_PROFILES

    def test_ottercast_graceful(self):
        """ottercast_logo.dxf (mostly SPLINEs) should not crash."""
        result = classify_entities(str(FIXTURES / "ottercast_logo.dxf"))
        # SPLINEs are skipped, but LWPOLYLINE paths should be found
        assert result is not None
