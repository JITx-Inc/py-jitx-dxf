"""Tests that landpattern shapes flip to the correct DXF layer when a
component instance is placed on the Bottom side."""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import ezdxf

from jitx_dxf.dxf_writer import convert

FIXTURE = Path(__file__).parent / "fixtures" / "side_flip_test.xml"


def _layer_counts(dxf_path: str) -> dict[str, int]:
    """Return a Counter of {layer_name: entity_count} for the DXF."""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    counts: dict[str, int] = Counter()
    for e in msp:
        counts[e.dxf.layer] += 1
    return dict(counts)


def test_top_instance_keeps_top_layers(tmp_path: Path) -> None:
    """U1 is on Top — silkscreen/courtyard/custom shapes stay on Top layers."""
    out = str(tmp_path / "out.dxf")
    # Only emit layers relevant to U1 (Top-side)
    convert(str(FIXTURE), out, layers={
        "Pads_Top", "Silkscreen_Top", "Courtyard_Top", "FINISH_Top",
    })
    counts = _layer_counts(out)

    # Pads: 2 circles for U1 (U2 is Bottom, filtered out)
    assert counts.get("Pads_Top", 0) == 2
    # Silkscreen_Top: 1 polygon + 1 line from U1
    assert counts.get("Silkscreen_Top", 0) == 2
    # Courtyard_Top: 1 polygon from U1
    assert counts.get("Courtyard_Top", 0) == 1
    # Custom layer on Top: 1 polygon from U1
    assert counts.get("FINISH_Top", 0) == 1

    # Nothing should appear on Bottom layers
    assert counts.get("Silkscreen_Bottom", 0) == 0
    assert counts.get("Courtyard_Bottom", 0) == 0
    assert counts.get("FINISH_Bottom", 0) == 0


def test_bottom_instance_flips_to_bottom_layers(tmp_path: Path) -> None:
    """U2 is on Bottom — silkscreen/courtyard/custom shapes must flip."""
    out = str(tmp_path / "out.dxf")
    # Only emit layers relevant to U2 (Bottom-side)
    convert(str(FIXTURE), out, layers={
        "Pads_Bottom", "Silkscreen_Bottom", "Courtyard_Bottom", "FINISH_Bottom",
    })
    counts = _layer_counts(out)

    # Pads: 2 circles for U2
    assert counts.get("Pads_Bottom", 0) == 2
    # Silkscreen_Bottom: 1 polygon + 1 line from U2 (flipped from Top)
    assert counts.get("Silkscreen_Bottom", 0) == 2
    # Courtyard_Bottom: 1 polygon from U2 (flipped from Top)
    assert counts.get("Courtyard_Bottom", 0) == 1
    # Custom layer on Bottom: 1 polygon from U2 (flipped from Top)
    assert counts.get("FINISH_Bottom", 0) == 1

    # Nothing from U2 should land on Top layers
    assert counts.get("Silkscreen_Top", 0) == 0
    assert counts.get("Courtyard_Top", 0) == 0
    assert counts.get("FINISH_Top", 0) == 0


def test_both_instances_unfiltered(tmp_path: Path) -> None:
    """Full conversion — Top instance on Top layers, Bottom on Bottom layers."""
    out = str(tmp_path / "out.dxf")
    convert(str(FIXTURE), out)
    counts = _layer_counts(out)

    # Each instance contributes 2 pads
    assert counts.get("Pads_Top", 0) == 2
    assert counts.get("Pads_Bottom", 0) == 2

    # Each instance contributes 1 silkscreen polygon + 1 silkscreen line
    assert counts.get("Silkscreen_Top", 0) == 2
    assert counts.get("Silkscreen_Bottom", 0) == 2

    # Each instance contributes 1 courtyard polygon
    assert counts.get("Courtyard_Top", 0) == 1
    assert counts.get("Courtyard_Bottom", 0) == 1

    # Each instance contributes 1 custom-layer polygon
    assert counts.get("FINISH_Top", 0) == 1
    assert counts.get("FINISH_Bottom", 0) == 1
