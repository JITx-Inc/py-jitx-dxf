"""Command-line interface for jitx-dxf."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .dxf_writer import convert, copper_layer_name, get_dxf_layer
from .xml_parser import parse_xml


def list_layers(xml_path: str) -> None:
    """Parse the XML and print all DXF layer names that would be generated."""
    data = parse_xml(xml_path)

    # Collect all layer names that would appear in the DXF
    layers: set[str] = set()

    # Static layers
    if data.boundary_lines or data.boundary_arcs:
        layers.add("BoardOutline")
    if data.vias:
        layers.update(["Vias", "Drill"])
    if data.instances:
        layers.add("Components")

    # Copper layers from stackup
    for idx in data.layer_names:
        name = data.layer_names[idx]
        layers.add(f"Copper_{name}")

    # Layers from tracks and fills (in case they reference layers not in stackup)
    for track in data.tracks:
        layers.add(copper_layer_name(track.layer_index, data.layer_names, "Copper"))
    for fill in data.fills:
        layers.add(copper_layer_name(fill.layer_index, data.layer_names, "Copper"))

    # Layers from packages (instantiated via INST)
    for inst in data.instances:
        pkg = data.packages.get(inst.package_name)
        if pkg is None:
            continue
        pad_layer = f"Pads_{inst.side}"
        if pkg.pads or pkg.rectangle_pads or pkg.polygon_pads:
            layers.add(pad_layer)
        if any(p.hole_radius > 0 for p in pkg.pads):
            layers.add("Drill")
        if any(p.hole_radius > 0 for p in pkg.rectangle_pads):
            layers.add("Drill")
        if any(p.hole_radius > 0 for p in pkg.polygon_pads):
            layers.add("Drill")
        for poly in pkg.polygons:
            layers.add(get_dxf_layer(poly.layer_name, poly.side))
        for ls in pkg.lines:
            layers.add(get_dxf_layer(ls.layer_name, ls.side))
        for ts in inst.shapes_text:
            layers.add(get_dxf_layer(ts.layer_name, ts.side))
        for poly in inst.shapes_polygon:
            layers.add(get_dxf_layer(poly.layer_name, poly.side))
        for ls in inst.shapes_line:
            layers.add(get_dxf_layer(ls.layer_name, ls.side))

    # Layers from board-level shapes
    for shape in data.board_shapes:
        layers.add(get_dxf_layer(shape.layer_name, shape.side))
    for ls in data.board_line_shapes:
        layers.add(get_dxf_layer(ls.layer_name, ls.side))

    print("Available DXF layers:")
    for name in sorted(layers):
        print(f"  {name}")


# --- Subcommand handlers ---


def _cmd_xml_to_dxf(args: argparse.Namespace) -> None:
    """Handle the xml-to-dxf subcommand."""
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    if args.list_layers:
        list_layers(str(input_path))
        return

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.with_suffix(".dxf")

    layer_filter = set(args.layers) if args.layers else None
    convert(str(input_path), str(output_path), layers=layer_filter)


def _cmd_inspect(args: argparse.Namespace) -> None:
    """Handle the inspect subcommand."""
    from .dxf_reader import read_dxf

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    inventory = read_dxf(str(input_path))

    print(f"DXF File: {inventory.filepath}")
    print(f"Version:  {inventory.dxf_version}")
    print(f"Units:    {inventory.units or 'not specified'}")
    print()

    if inventory.bounding_box:
        bb = inventory.bounding_box
        w = bb[1].x - bb[0].x
        h = bb[1].y - bb[0].y
        print(f"Bounding box: ({bb[0].x:.3f}, {bb[0].y:.3f}) to ({bb[1].x:.3f}, {bb[1].y:.3f})")
        print(f"Extent:       {w:.3f} x {h:.3f}")
        print()

    print("Layers:")
    for layer_name in sorted(inventory.layers.keys()):
        count = inventory.layers[layer_name]
        print(f"  {layer_name:30s} {count:5d} entities")
    print()

    print("Entity types:")
    for etype in sorted(inventory.entity_counts.keys()):
        count = inventory.entity_counts[etype]
        print(f"  {etype:20s} {count:5d}")
    print()

    total = sum(inventory.entity_counts.values())
    print(f"Total entities: {total}")


def _cmd_import(args: argparse.Namespace) -> None:
    """Handle the import subcommand."""
    from .dxf_reader import classify_entities
    from .jitx_codegen import (
        generate_board_code,
        generate_cutouts_snippet,
        generate_holes_snippet,
        generate_outline_snippet,
    )

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    # Parse layer map from KEY=VALUE arguments
    layer_map = None
    if args.layer_map:
        layer_map = {}
        for item in args.layer_map:
            if "=" not in item:
                print(f"Error: Invalid layer map entry (expected KEY=VALUE): {item}",
                      file=sys.stderr)
                sys.exit(1)
            key, value = item.split("=", 1)
            layer_map[key] = value

    recenter = not args.no_recenter

    classified = classify_entities(
        str(input_path),
        layer_map=layer_map,
        unit=args.unit,
    )

    # Print summary
    n_cutouts = len(classified.cutouts)
    n_holes = len(classified.holes)
    n_unclass = len(classified.unclassified_paths) + len(classified.unclassified_circles)

    print(f"Classified DXF entities from: {input_path.name}", file=sys.stderr)
    print(f"  Outline:      {'found' if classified.outline else 'not found'}", file=sys.stderr)
    print(f"  Cutouts:      {n_cutouts}", file=sys.stderr)
    print(f"  Holes:        {n_holes}", file=sys.stderr)
    print(f"  Unclassified: {n_unclass}", file=sys.stderr)

    if args.snippet:
        # Print snippet(s) to stdout
        print(generate_outline_snippet(classified, recenter=recenter))
        if classified.cutouts or classified.holes:
            print()
            print(generate_cutouts_snippet(classified, recenter=recenter))
    else:
        code = generate_board_code(
            classified,
            class_name=args.class_name,
            module_name=input_path.name,
            recenter=recenter,
        )

        if args.output:
            output_path = Path(args.output)
            output_path.write_text(code)
            print(f"  Written to:   {output_path}", file=sys.stderr)
        else:
            print(code)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="jitx-dxf",
        description="Convert between JITX board designs and DXF format.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # --- xml-to-dxf subcommand ---
    p_xml = subparsers.add_parser(
        "xml-to-dxf",
        help="Convert JITX XML board design to DXF format.",
    )
    p_xml.add_argument(
        "input",
        help="Path to the JITX XML file",
    )
    p_xml.add_argument(
        "-o", "--output",
        help="Output DXF file path (default: same directory as input, .dxf extension)",
    )
    p_xml.add_argument(
        "--layers",
        nargs="*",
        metavar="LAYER",
        help="Only include specific DXF layers (default: all). "
        "Use --list-layers to see available layers for a given XML file.",
    )
    p_xml.add_argument(
        "--list-layers",
        action="store_true",
        help="Parse the XML and print all DXF layer names that would be "
        "generated, then exit without writing a DXF file.",
    )
    p_xml.set_defaults(func=_cmd_xml_to_dxf)

    # --- import subcommand ---
    p_import = subparsers.add_parser(
        "import",
        help="Import a DXF file and generate JITX Python code.",
    )
    p_import.add_argument(
        "input",
        help="Path to the DXF file",
    )
    p_import.add_argument(
        "-o", "--output",
        help="Output Python file path (default: print to stdout)",
    )
    p_import.add_argument(
        "--class-name",
        default="ImportedBoard",
        help="Name of the generated Board class (default: ImportedBoard)",
    )
    p_import.add_argument(
        "--snippet",
        action="store_true",
        help="Output only shape expressions instead of a full Board class",
    )
    p_import.add_argument(
        "--layer-map",
        nargs="*",
        metavar="LAYER=ROLE",
        help="Map DXF layer names to PCB roles (outline, cutout, hole, keepout, soldermask)",
    )
    p_import.add_argument(
        "--unit",
        choices=["mm", "in", "mil"],
        help="Force unit interpretation (default: auto-detect)",
    )
    p_import.add_argument(
        "--no-recenter",
        action="store_true",
        help="Don't re-center the board outline to the origin",
    )
    p_import.set_defaults(func=_cmd_import)

    # --- inspect subcommand ---
    p_inspect = subparsers.add_parser(
        "inspect",
        help="Inspect a DXF file and print entity/layer summary.",
    )
    p_inspect.add_argument(
        "input",
        help="Path to the DXF file",
    )
    p_inspect.set_defaults(func=_cmd_inspect)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)
