#!/usr/bin/env python3
"""Comprehensive audit of XML elements parsed vs dropped by xml_to_dxf.py converter.

Analyzes all three XML files and compares against what the converter handles.
"""

import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
import sys
import os


def short_name(path):
    """Return just the filename for display."""
    return os.path.basename(path)


def audit_file(xml_path):
    """Perform comprehensive audit of a single XML file."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    board = root.find("BOARD")
    schematic = root.find("SCHEMATIC")
    results = {}

    # ── 1. Count ALL direct children of BOARD by tag name ──
    board_children = Counter()
    for child in board:
        board_children[child.tag] += 1
    results["board_children"] = dict(board_children)

    # ── 2. Board-level SHAPE analysis ──
    board_shape_info = []
    for shape in board.findall("SHAPE"):
        info = {}
        layer_spec = shape.find("LAYER-SPECIFIER")
        layer_idx = shape.find("LAYER-INDEX")
        if layer_spec is not None:
            info["layer_type"] = "LAYER-SPECIFIER"
            info["layer_name"] = layer_spec.get("NAME", "?")
            info["layer_side"] = layer_spec.get("SIDE", "?")
            info["sub_name"] = layer_spec.get("SUB-NAME", None)
        elif layer_idx is not None:
            info["layer_type"] = "LAYER-INDEX"
            info["layer_index"] = layer_idx.get("INDEX", "?")
            info["layer_side"] = layer_idx.get("SIDE", "?")
        else:
            info["layer_type"] = "NONE"

        # Geometry children
        geom_types = []
        for child in shape:
            if child.tag not in ("LAYER-SPECIFIER", "LAYER-INDEX"):
                geom_types.append(child.tag)
        info["geometry"] = geom_types
        board_shape_info.append(info)

    # Summarize board shapes
    board_shape_by_layer = Counter()
    board_shape_geom = Counter()
    board_shape_layer_type = Counter()
    for s in board_shape_info:
        key = s.get("layer_name", s.get("layer_index", "UNKNOWN"))
        board_shape_by_layer[key] += 1
        board_shape_layer_type[s["layer_type"]] += 1
        for g in s["geometry"]:
            board_shape_geom[g] += 1

    results["board_shapes_by_layer"] = dict(board_shape_by_layer)
    results["board_shapes_geom_types"] = dict(board_shape_geom)
    results["board_shapes_layer_types"] = dict(board_shape_layer_type)

    # ── 3. PACKAGE analysis ──
    pkg_info = {}
    for pkg in board.findall("PACKAGE"):
        name = pkg.get("NAME")
        pad_shapes = Counter()
        pad_attribs = Counter()
        pad_children_other = Counter()
        for pad in pkg.findall("PAD"):
            # Count shape types
            has_shape = False
            for child in pad:
                if child.tag == "CIRCLE":
                    pad_shapes["CIRCLE"] += 1
                    has_shape = True
                elif child.tag == "RECTANGLE":
                    pad_shapes["RECTANGLE"] += 1
                    has_shape = True
                elif child.tag == "POLYGON":
                    pad_shapes["POLYGON"] += 1
                    has_shape = True
                elif child.tag in ("POSE", "LAYER-INDEX", "LAYER-SPECIFIER", "PAD-STACK", "HOLE"):
                    pass  # Known children
                else:
                    pad_children_other[child.tag] += 1
            if not has_shape:
                pad_shapes["NO_SHAPE"] += 1

            # Count pad attributes
            for attr in pad.attrib:
                pad_attribs[attr] += 1

        # SHAPE children of PACKAGE
        pkg_shape_by_layer = Counter()
        pkg_shape_geom = Counter()
        pkg_shape_layer_type = Counter()
        for shape in pkg.findall("SHAPE"):
            layer_spec = shape.find("LAYER-SPECIFIER")
            layer_idx = shape.find("LAYER-INDEX")
            if layer_spec is not None:
                pkg_shape_layer_type["LAYER-SPECIFIER"] += 1
                lname = layer_spec.get("NAME", "?")
                pkg_shape_by_layer[lname] += 1
            elif layer_idx is not None:
                pkg_shape_layer_type["LAYER-INDEX"] += 1
                pkg_shape_by_layer[f"conductor-idx-{layer_idx.get('INDEX', '?')}"] += 1
            else:
                pkg_shape_layer_type["NONE"] += 1
                pkg_shape_by_layer["UNKNOWN"] += 1

            for child in shape:
                if child.tag not in ("LAYER-SPECIFIER", "LAYER-INDEX"):
                    pkg_shape_geom[child.tag] += 1

        # Other PACKAGE children besides PAD and SHAPE
        pkg_other_children = Counter()
        for child in pkg:
            if child.tag not in ("PAD", "SHAPE"):
                pkg_other_children[child.tag] += 1

        pkg_info[name] = {
            "num_pads": len(pkg.findall("PAD")),
            "pad_shapes": dict(pad_shapes),
            "pad_attribs": dict(pad_attribs),
            "pad_children_other": dict(pad_children_other),
            "num_shapes": len(pkg.findall("SHAPE")),
            "shape_by_layer": dict(pkg_shape_by_layer),
            "shape_geom": dict(pkg_shape_geom),
            "shape_layer_type": dict(pkg_shape_layer_type),
            "other_children": dict(pkg_other_children),
        }
    results["packages"] = pkg_info

    # ── 4. INST analysis ──
    inst_children_other = Counter()
    inst_attribs = Counter()
    inst_count = 0
    for inst in board.findall("INST"):
        inst_count += 1
        for attr in inst.attrib:
            inst_attribs[attr] += 1
        for child in inst:
            if child.tag not in ("POSE", "DESIGNATOR-TEXT", "LAYER-INDEX"):
                inst_children_other[child.tag] += 1

    results["inst_count"] = inst_count
    results["inst_attribs"] = dict(inst_attribs)
    results["inst_children_other"] = dict(inst_children_other)

    # Check for PIN-NET inside INST
    pin_net_count = 0
    for inst in board.findall("INST"):
        for pn in inst.findall("PIN-NET"):
            pin_net_count += 1
    results["inst_pin_net_count"] = pin_net_count

    # ── 5. Attribute analysis for TRACK, FILL, VIA, PAD ──
    track_attribs = Counter()
    track_shape_count = 0
    track_shape_geom = Counter()
    for track in board.findall("TRACK"):
        for attr in track.attrib:
            track_attribs[attr] += 1
        for shape in track.findall("SHAPE"):
            track_shape_count += 1
            for child in shape:
                if child.tag not in ("LAYER-INDEX",):
                    track_shape_geom[child.tag] += 1
    results["track_count"] = len(board.findall("TRACK"))
    results["track_attribs"] = dict(track_attribs)
    results["track_shape_count"] = track_shape_count
    results["track_shape_geom"] = dict(track_shape_geom)

    fill_attribs = Counter()
    fill_children = Counter()
    fill_shape_geom = Counter()
    for fill in board.findall("FILL"):
        for attr in fill.attrib:
            fill_attribs[attr] += 1
        for child in fill:
            fill_children[child.tag] += 1
            if child.tag == "SHAPE":
                for sc in child:
                    if sc.tag not in ("LAYER-INDEX",):
                        fill_shape_geom[sc.tag] += 1
    results["fill_count"] = len(board.findall("FILL"))
    results["fill_attribs"] = dict(fill_attribs)
    results["fill_children"] = dict(fill_children)
    results["fill_shape_geom"] = dict(fill_shape_geom)

    via_attribs = Counter()
    via_children = Counter()
    for via in board.findall("VIA"):
        for attr in via.attrib:
            via_attribs[attr] += 1
        for child in via:
            via_children[child.tag] += 1
    results["via_count"] = len(board.findall("VIA"))
    results["via_attribs"] = dict(via_attribs)
    results["via_children"] = dict(via_children)

    # ── 6. Board-level LINE shapes (not inside POLYGON) ──
    # The converter only parses POLYGON from board-level SHAPEs; check for LINE
    board_shape_lines = 0
    board_shape_arcs = 0
    for shape in board.findall("SHAPE"):
        if shape.find("LINE") is not None:
            board_shape_lines += 1
        if shape.find("ARC") is not None:
            board_shape_arcs += 1
    results["board_shape_lines_count"] = board_shape_lines
    results["board_shape_arcs_count"] = board_shape_arcs

    # ── 7. SCHEMATIC section ──
    schematic_children = Counter()
    if schematic is not None:
        for child in schematic:
            schematic_children[child.tag] += 1
    results["schematic_children"] = dict(schematic_children)

    # ── Extra: NET elements at board level ──
    results["net_count"] = len(board.findall("NET"))

    # ── Extra: ANETCLASS elements ──
    results["anetclass_count"] = len(board.findall("ANETCLASS"))

    # ── Extra: MANUFACTURING-RULES ──
    mfg = board.find("MANUFACTURING-RULES")
    results["manufacturing_rules_attribs"] = dict(mfg.attrib) if mfg is not None else None

    # ── Extra: ALTIUM-RULES ──
    results["altium_rules_count"] = len(board.findall("ALTIUM-RULES"))

    # ── Extra: Root-level PROJECT attributes ──
    results["project_attribs"] = dict(root.attrib)

    # ── Extra: top-level children of PROJECT ──
    project_children = Counter()
    for child in root:
        project_children[child.tag] += 1
    results["project_children"] = dict(project_children)

    return results


def print_separator():
    print("=" * 120)


def print_audit(files):
    all_results = {}
    for f in files:
        print(f"Parsing: {f}")
        all_results[f] = audit_file(f)

    names = [short_name(f) for f in files]

    print()
    print_separator()
    print("COMPREHENSIVE XML AUDIT: Parsed vs Dropped Elements")
    print_separator()

    # ── Section 1: BOARD direct children ──
    print()
    print("1. BOARD DIRECT CHILDREN (tag counts)")
    print("-" * 120)
    all_tags = set()
    for r in all_results.values():
        all_tags.update(r["board_children"].keys())
    all_tags = sorted(all_tags)

    # Converter status for each tag
    converter_status = {
        "BOARD-BOUNDARY": "PARSED",
        "STACKUP": "PARSED",
        "PACKAGE": "PARSED",
        "INST": "PARSED",
        "SHAPE": "PARTIAL (polygons only, lines/arcs dropped)",
        "TRACK": "PARSED",
        "FILL": "PARSED (polygons only)",
        "VIA": "PARSED",
        "MANUFACTURING-RULES": "DROPPED",
        "ALTIUM-RULES": "DROPPED",
        "LAYER-INDEX": "DROPPED (board-level)",
        "ANETCLASS": "DROPPED",
        "NET": "DROPPED",
    }

    data_lost = {
        "BOARD-BOUNDARY": "None",
        "STACKUP": "None (layer names parsed)",
        "PACKAGE": "See detail below",
        "INST": "See detail below",
        "SHAPE": "Board-level LINE/ARC shapes (silkscreen/courtyard lines)",
        "TRACK": "None",
        "FILL": "Non-polygon fill shapes (if any)",
        "VIA": "None",
        "MANUFACTURING-RULES": "Min trace width, clearance, hole sizes, solder mask expansion",
        "ALTIUM-RULES": "Altium-specific DRC rules (polygon connect style, etc.)",
        "LAYER-INDEX": "Board-level layer index declarations",
        "ANETCLASS": "Net class definitions and membership",
        "NET": "Net name declarations",
    }

    print(f"{'Tag':<25} {'Converter Status':<50} ", end="")
    for n in names:
        print(f"{n[:20]:>20} ", end="")
    print()
    print(f"{'---':<25} {'---':<50} ", end="")
    for _ in names:
        print(f"{'---':>20} ", end="")
    print()

    for tag in all_tags:
        status = converter_status.get(tag, "DROPPED (unknown)")
        print(f"{tag:<25} {status:<50} ", end="")
        for f in files:
            count = all_results[f]["board_children"].get(tag, 0)
            print(f"{count:>20} ", end="")
        print()

    print()
    print("Data lost for DROPPED elements:")
    for tag in all_tags:
        if "DROPPED" in converter_status.get(tag, "DROPPED"):
            print(f"  {tag}: {data_lost.get(tag, 'All data in this element')}")

    # ── Section 2: Board-level SHAPE analysis ──
    print()
    print_separator()
    print("2. BOARD-LEVEL SHAPE ELEMENTS")
    print("-" * 120)

    print()
    print("2a. By LAYER-SPECIFIER NAME:")
    all_layer_names = set()
    for r in all_results.values():
        all_layer_names.update(r["board_shapes_by_layer"].keys())
    all_layer_names = sorted(all_layer_names)

    print(f"{'Layer Name':<30} ", end="")
    for n in names:
        print(f"{n[:20]:>20} ", end="")
    print()
    for ln in all_layer_names:
        print(f"{ln:<30} ", end="")
        for f in files:
            print(f"{all_results[f]['board_shapes_by_layer'].get(ln, 0):>20} ", end="")
        print()

    print()
    print("2b. Geometry types inside board-level SHAPEs:")
    all_geom = set()
    for r in all_results.values():
        all_geom.update(r["board_shapes_geom_types"].keys())
    all_geom = sorted(all_geom)

    geom_status = {
        "POLYGON": "PARSED",
        "LINE": "DROPPED - converter only parses POLYGON from board shapes",
        "ARC": "DROPPED - converter only parses POLYGON from board shapes",
    }

    print(f"{'Geom Type':<15} {'Converter Status':<60} ", end="")
    for n in names:
        print(f"{n[:20]:>20} ", end="")
    print()
    for g in all_geom:
        status = geom_status.get(g, "DROPPED (unknown geometry)")
        print(f"{g:<15} {status:<60} ", end="")
        for f in files:
            print(f"{all_results[f]['board_shapes_geom_types'].get(g, 0):>20} ", end="")
        print()

    print()
    print("2c. Layer specifier type (LAYER-SPECIFIER vs LAYER-INDEX):")
    all_lt = set()
    for r in all_results.values():
        all_lt.update(r["board_shapes_layer_types"].keys())

    print(f"{'Type':<20} ", end="")
    for n in names:
        print(f"{n[:20]:>20} ", end="")
    print()
    for lt in sorted(all_lt):
        conv_note = "PARSED" if lt == "LAYER-SPECIFIER" else "DROPPED (board shapes with LAYER-INDEX = copper shapes not handled)"
        print(f"{lt:<20} ", end="")
        for f in files:
            print(f"{all_results[f]['board_shapes_layer_types'].get(lt, 0):>20} ", end="")
        print(f"  <- {conv_note}")

    print()
    print(f"Board-level SHAPE elements containing LINE (dropped by converter):")
    print(f"{'File':<50} {'LINE count':>15} {'ARC count':>15}")
    for f in files:
        print(f"{short_name(f):<50} {all_results[f]['board_shape_lines_count']:>15} {all_results[f]['board_shape_arcs_count']:>15}")

    # ── Section 3: PACKAGE analysis ──
    print()
    print_separator()
    print("3. PACKAGE ELEMENT ANALYSIS")
    print("-" * 120)

    for f in files:
        pkgs = all_results[f]["packages"]
        if not pkgs:
            print(f"\n  {short_name(f)}: No packages")
            continue
        print(f"\n  {short_name(f)}: {len(pkgs)} package(s)")
        for pname, pinfo in pkgs.items():
            print(f"\n    Package '{pname}':")
            print(f"      PADs: {pinfo['num_pads']} total")
            print(f"        Shape types: {pinfo['pad_shapes']}")
            print(f"        PAD attributes found: {list(pinfo['pad_attribs'].keys())}")

            # Identify unparsed PAD attributes
            parsed_pad_attrs = {"NAME", "TYPE", "SIDE"}
            unparsed_pad_attrs = set(pinfo["pad_attribs"].keys()) - parsed_pad_attrs
            if unparsed_pad_attrs:
                print(f"        ** DROPPED PAD attributes: {sorted(unparsed_pad_attrs)}")
                for attr in sorted(unparsed_pad_attrs):
                    notes = {
                        "PASTE-EXPANSION": "Paste stencil expansion value",
                        "TOP-SOLDER-MASK": "Solder mask expansion on top",
                        "BOTTOM-SOLDER-MASK": "Solder mask expansion on bottom",
                        "PLATED": "Whether pad is plated (for TH pads)",
                    }
                    note = notes.get(attr, "Unknown purpose")
                    print(f"          {attr} (count={pinfo['pad_attribs'][attr]}): {note}")

            if pinfo["pad_children_other"]:
                print(f"        ** Unexpected PAD children (DROPPED): {pinfo['pad_children_other']}")

            print(f"      SHAPEs: {pinfo['num_shapes']} total")
            if pinfo["shape_by_layer"]:
                print(f"        By layer: {pinfo['shape_by_layer']}")
            if pinfo["shape_geom"]:
                print(f"        Geometry types: {pinfo['shape_geom']}")
            if pinfo["shape_layer_type"]:
                print(f"        Layer type: {pinfo['shape_layer_type']}")
                if "LAYER-INDEX" in pinfo["shape_layer_type"]:
                    print(f"        ** WARNING: {pinfo['shape_layer_type']['LAYER-INDEX']} SHAPE(s) use LAYER-INDEX (conductor layer) - DROPPED by converter!")

            if pinfo["other_children"]:
                print(f"      ** Other PACKAGE children (DROPPED): {pinfo['other_children']}")

    # ── Section 4: INST analysis ──
    print()
    print_separator()
    print("4. INST ELEMENT ANALYSIS")
    print("-" * 120)

    print(f"\n{'Attribute/Child':<30} {'Converter Status':<30} ", end="")
    for n in names:
        print(f"{n[:20]:>20} ", end="")
    print()

    inst_attrs_all = set()
    inst_children_all = set()
    for r in all_results.values():
        inst_attrs_all.update(r["inst_attribs"].keys())
        inst_children_all.update(r["inst_children_other"].keys())

    parsed_inst_attrs = {"DESIGNATOR", "PACKAGE", "SIDE"}
    for attr in sorted(inst_attrs_all):
        status = "PARSED" if attr in parsed_inst_attrs else "DROPPED"
        print(f"attr:{attr:<25} {status:<30} ", end="")
        for f in files:
            print(f"{all_results[f]['inst_attribs'].get(attr, 0):>20} ", end="")
        print()
        if status == "DROPPED":
            notes = {
                "HEIGHT": "Component height above board (useful for 3D/clearance)",
            }
            print(f"  -> Data lost: {notes.get(attr, 'Unknown')}")

    # Known parsed children
    known_inst_children = {"POSE", "DESIGNATOR-TEXT", "LAYER-INDEX"}
    print(f"\n  Parsed INST children: POSE, DESIGNATOR-TEXT, LAYER-INDEX")
    print(f"  Additional INST children found (DROPPED):")
    for child_tag in sorted(inst_children_all):
        print(f"    {child_tag:<25} ", end="")
        for f in files:
            print(f"{all_results[f]['inst_children_other'].get(child_tag, 0):>20} ", end="")
        print()
        notes = {
            "PIN-NET": "Pin-to-net mapping for the instance (net connectivity data)",
        }
        print(f"      -> Data lost: {notes.get(child_tag, 'Unknown child element')}")

    print(f"\n  PIN-NET elements inside INSTs:")
    for f in files:
        print(f"    {short_name(f)}: {all_results[f]['inst_pin_net_count']} PIN-NET elements")

    # ── Section 5: Unparsed attributes ──
    print()
    print_separator()
    print("5. TRACK / FILL / VIA ATTRIBUTE ANALYSIS")
    print("-" * 120)

    print("\n  TRACK attributes:")
    parsed_track_attrs = {"NET"}
    track_attrs_all = set()
    for r in all_results.values():
        track_attrs_all.update(r["track_attribs"].keys())
    for attr in sorted(track_attrs_all):
        status = "PARSED" if attr in parsed_track_attrs else "DROPPED"
        print(f"    {attr:<25} {status:<20} ", end="")
        for f in files:
            print(f"{all_results[f]['track_attribs'].get(attr, 0):>20} ", end="")
        print()

    print(f"\n  TRACK shape geometry types:")
    track_geom_all = set()
    for r in all_results.values():
        track_geom_all.update(r["track_shape_geom"].keys())
    for g in sorted(track_geom_all):
        print(f"    {g:<25} ", end="")
        for f in files:
            print(f"{all_results[f]['track_shape_geom'].get(g, 0):>20} ", end="")
        print()

    print("\n  FILL attributes:")
    parsed_fill_attrs = {"NET"}
    fill_attrs_all = set()
    for r in all_results.values():
        fill_attrs_all.update(r["fill_attribs"].keys())
    for attr in sorted(fill_attrs_all):
        status = "PARSED" if attr in parsed_fill_attrs else "DROPPED"
        print(f"    {attr:<25} {status:<20} ", end="")
        for f in files:
            print(f"{all_results[f]['fill_attribs'].get(attr, 0):>20} ", end="")
        print()
        if status == "DROPPED":
            notes = {
                "REMOVE-ISLANDS": "Whether to remove unconnected copper islands in pour",
                "ISOLATE": "Isolation/clearance value for the fill",
                "RANK": "Fill priority/ordering",
            }
            print(f"      -> Data lost: {notes.get(attr, 'Unknown fill attribute')}")

    print(f"\n  FILL children by tag:")
    fill_children_all = set()
    for r in all_results.values():
        fill_children_all.update(r["fill_children"].keys())
    for tag in sorted(fill_children_all):
        parsed = "PARSED" if tag == "SHAPE" else "DROPPED"
        print(f"    {tag:<25} {parsed:<20} ", end="")
        for f in files:
            print(f"{all_results[f]['fill_children'].get(tag, 0):>20} ", end="")
        print()

    print(f"\n  FILL shape geometry types:")
    fill_geom_all = set()
    for r in all_results.values():
        fill_geom_all.update(r["fill_shape_geom"].keys())
    for g in sorted(fill_geom_all):
        print(f"    {g:<25} ", end="")
        for f in files:
            print(f"{all_results[f]['fill_shape_geom'].get(g, 0):>20} ", end="")
        print()

    print("\n  VIA attributes:")
    parsed_via_attrs = {"DIAMETER", "HOLE-DIAMETER", "NET"}
    via_attrs_all = set()
    for r in all_results.values():
        via_attrs_all.update(r["via_attribs"].keys())
    for attr in sorted(via_attrs_all):
        status = "PARSED" if attr in parsed_via_attrs else "DROPPED"
        print(f"    {attr:<25} {status:<20} ", end="")
        for f in files:
            print(f"{all_results[f]['via_attribs'].get(attr, 0):>20} ", end="")
        print()
        if status == "DROPPED":
            notes = {
                "TYPE": "Via type (e.g., through, blind, buried)",
            }
            print(f"      -> Data lost: {notes.get(attr, 'Unknown via attribute')}")

    print(f"\n  VIA children by tag:")
    via_children_all = set()
    for r in all_results.values():
        via_children_all.update(r["via_children"].keys())
    parsed_via_children = {"POINT", "START-LAYER", "END-LAYER"}
    for tag in sorted(via_children_all):
        status = "PARSED" if tag in parsed_via_children else "DROPPED"
        print(f"    {tag:<25} {status:<20} ", end="")
        for f in files:
            print(f"{all_results[f]['via_children'].get(tag, 0):>20} ", end="")
        print()

    # ── Section 6: Board-level LINE shapes ──
    print()
    print_separator()
    print("6. BOARD-LEVEL LINE/ARC SHAPES (not inside POLYGON)")
    print("-" * 120)
    print("The converter's parse_board_shapes() only handles POLYGON geometry.")
    print("LINE and ARC geometry inside board-level SHAPE elements are silently dropped.")
    print()
    for f in files:
        r = all_results[f]
        total_shapes = r["board_children"].get("SHAPE", 0)
        poly_count = r["board_shapes_geom_types"].get("POLYGON", 0)
        line_count = r["board_shapes_geom_types"].get("LINE", 0)
        arc_count = r["board_shapes_geom_types"].get("ARC", 0)
        print(f"  {short_name(f)}:")
        print(f"    Total board SHAPEs: {total_shapes}")
        print(f"    With POLYGON (parsed): {poly_count}")
        print(f"    With LINE (DROPPED): {line_count}")
        print(f"    With ARC (DROPPED): {arc_count}")
        if line_count > 0 or arc_count > 0:
            print(f"    ** {line_count + arc_count} shapes lost! These are typically silkscreen/courtyard lines.")

    # ── Section 7: SCHEMATIC section ──
    print()
    print_separator()
    print("7. SCHEMATIC SECTION")
    print("-" * 120)
    print("The converter does NOT parse the SCHEMATIC section at all.")
    print()
    for f in files:
        r = all_results[f]
        sch = r["schematic_children"]
        if not sch:
            print(f"  {short_name(f)}: No SCHEMATIC section")
        else:
            print(f"  {short_name(f)}: SCHEMATIC children: {sch}")

    # ── Summary table ──
    print()
    print_separator()
    print("SUMMARY: Element Status Matrix")
    print_separator()
    print()

    rows = [
        ("BOARD/BOARD-BOUNDARY", "Parsed", "Board outline (LINE, ARC)", "None"),
        ("BOARD/STACKUP", "Parsed", "Layer name mapping", "None (THICKNESS, DIELECTRIC-CONSTANT, etc. dropped)"),
        ("BOARD/MANUFACTURING-RULES", "Dropped", "DRC constraints", "Min trace/clearance/hole/annular ring/solder mask values"),
        ("BOARD/ALTIUM-RULES", "Dropped", "Altium DRC rules", "Polygon connect style, packed rule settings"),
        ("BOARD/LAYER-INDEX", "Dropped", "Board-level layer decl", "Layer index-to-side mapping at board level"),
        ("BOARD/NET", "Dropped", "Net declarations", "All net names used in design"),
        ("BOARD/ANETCLASS", "Dropped", "Net class definitions", "Net class names and member nets"),
        ("BOARD/PACKAGE", "Parsed", "Component footprints", "See PAD/SHAPE details below"),
        ("BOARD/PACKAGE/PAD (CIRCLE)", "Parsed", "Round pads", "None"),
        ("BOARD/PACKAGE/PAD (RECTANGLE)", "Parsed", "Rectangular pads", "None"),
        ("BOARD/PACKAGE/PAD (POLYGON)", "Parsed", "Polygon pads", "None"),
        ("BOARD/PACKAGE/PAD attrs", "Partial", "PAD metadata", "PASTE-EXPANSION, TOP-SOLDER-MASK, TYPE dropped"),
        ("BOARD/PACKAGE/PAD/PAD-STACK", "Dropped", "Multi-layer pad def", "Pad shapes per layer for multi-layer pads"),
        ("BOARD/PACKAGE/PAD/LAYER-SPECIFIER", "Dropped", "Paste/mask layers", "Which paste/soldermask layers the pad uses"),
        ("BOARD/PACKAGE/SHAPE (POLYGON)", "Parsed", "Silkscreen/courtyard poly", "None"),
        ("BOARD/PACKAGE/SHAPE (LINE)", "Parsed", "Silkscreen/courtyard line", "None"),
        ("BOARD/PACKAGE/SHAPE (LAYER-INDEX)", "Dropped", "Copper shapes in pkg", "Package copper geometry on conductor layers"),
        ("BOARD/INST", "Parsed", "Component placement", "See detail"),
        ("BOARD/INST attr:HEIGHT", "Dropped", "Component height", "Height above board for 3D clearance"),
        ("BOARD/INST/PIN-NET", "Dropped", "Pin-net connectivity", "Which net each pin connects to"),
        ("BOARD/SHAPE (POLYGON)", "Parsed", "Board silkscreen/courtyard", "None"),
        ("BOARD/SHAPE (LINE)", "Dropped", "Board silkscreen/courtyard", "Line-based silkscreen/courtyard shapes"),
        ("BOARD/SHAPE (ARC)", "Dropped", "Board silkscreen/courtyard", "Arc-based silkscreen/courtyard shapes"),
        ("BOARD/TRACK", "Parsed", "Copper traces", "LINE, ARC, POLYGON all handled"),
        ("BOARD/FILL", "Partial", "Copper pours", "Only POLYGON; REMOVE-ISLANDS, ISOLATE, RANK attrs dropped"),
        ("BOARD/VIA", "Parsed", "Via holes", "TYPE attribute dropped"),
        ("SCHEMATIC/*", "Dropped", "Schematic data", "Symbol defs, sheets, wires, net connections"),
    ]

    print(f"{'Element Path':<40} {'Status':<10} {'Purpose':<30} ", end="")
    for n in names:
        print(f"{n[:15]:>15} ", end="")
    print(f"  {'Data Lost if Dropped'}")
    print("-" * 200)

    for path, status, purpose, lost in rows:
        print(f"{path:<40} {status:<10} {purpose:<30} ", end="")
        # Try to get counts
        for f in files:
            r = all_results[f]
            count = "-"
            tag = path.split("/")[-1].split(" ")[0].split(":")[0]
            if tag == "BOARD-BOUNDARY":
                count = str(r["board_children"].get("BOARD-BOUNDARY", 0))
            elif tag == "STACKUP":
                count = str(r["board_children"].get("STACKUP", 0))
            elif tag == "MANUFACTURING-RULES":
                count = str(r["board_children"].get("MANUFACTURING-RULES", 0))
            elif tag == "ALTIUM-RULES":
                count = str(r["board_children"].get("ALTIUM-RULES", 0))
            elif path == "BOARD/LAYER-INDEX":
                count = str(r["board_children"].get("LAYER-INDEX", 0))
            elif tag == "NET":
                count = str(r["net_count"])
            elif tag == "ANETCLASS":
                count = str(r["anetclass_count"])
            elif path == "BOARD/PACKAGE":
                count = str(r["board_children"].get("PACKAGE", 0))
            elif path == "BOARD/INST":
                count = str(r["inst_count"])
            elif path == "BOARD/INST/PIN-NET":
                count = str(r["inst_pin_net_count"])
            elif tag == "TRACK":
                count = str(r["track_count"])
            elif tag == "FILL":
                count = str(r["fill_count"])
            elif tag == "VIA":
                count = str(r["via_count"])
            elif "SHAPE" in path and "POLYGON" in path and "PACKAGE" not in path:
                count = str(r["board_shapes_geom_types"].get("POLYGON", 0))
            elif "SHAPE" in path and "LINE" in path and "PACKAGE" not in path:
                count = str(r["board_shapes_geom_types"].get("LINE", 0))
            elif "SHAPE" in path and "ARC" in path and "PACKAGE" not in path:
                count = str(r["board_shapes_geom_types"].get("ARC", 0))
            elif "SCHEMATIC" in path:
                count = str(sum(r["schematic_children"].values())) if r["schematic_children"] else "0"
            print(f"{count:>15} ", end="")
        print(f"  {lost}")


def main():
    files = [
        "/Users/bgupta/src/JITX/dummy-dir/test_py_comp/designs/test_py_comp.main.test_py_comp/xml/test_py_comp.main.test_py_comp.xml",
        "/Users/bgupta/src/JITX/dummy-dir/test_py_comp/designs/test_py_comp.main.test_py_reg/xml/test_py_comp.main.test_py_reg.xml",
        "/Users/bgupta/Downloads/All-GaN-HPA-Board.xml",
    ]
    for f in files:
        if not os.path.exists(f):
            print(f"ERROR: File not found: {f}", file=sys.stderr)
            sys.exit(1)

    print_audit(files)


if __name__ == "__main__":
    main()
