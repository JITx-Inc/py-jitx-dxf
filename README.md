# jitx-dxf

Convert between JITX board designs and DXF format.

## Installation

```bash
pip install -e .

# With dev dependencies (pytest)
pip install -e ".[dev]"
```

After installation, the `jitx-dxf` command is available. Alternatively, you can
run directly from source without installing:

```bash
python -m jitx_dxf <subcommand> [options]
```

## Usage

### Export: JITX XML to DXF

```bash
# Convert XML to DXF (output alongside input with .dxf extension)
jitx-dxf xml-to-dxf board.xml

# Specify output path
jitx-dxf xml-to-dxf board.xml -o output.dxf

# List available layers
jitx-dxf xml-to-dxf board.xml --list-layers

# Export only specific layers
jitx-dxf xml-to-dxf board.xml --layers BoardOutline Pads_Top Silkscreen_Top
```

### Import: DXF to JITX Python

Import a mechanical DXF file (from SolidWorks, Fusion 360, etc.) and generate JITX Board class code:

```bash
# Generate a Board class to stdout
jitx-dxf import outline.dxf

# Write to a file with a custom class name
jitx-dxf import outline.dxf -o my_board.py --class-name MyBoard

# Output only shape expressions (no class wrapper)
jitx-dxf import outline.dxf --snippet

# Override unit detection (auto-detects mm/in/mil by default)
jitx-dxf import outline.dxf --unit mm

# Map DXF layers to PCB roles explicitly
jitx-dxf import board.dxf --layer-map OUTER_PROFILES=outline HOLES=hole

# Keep original DXF coordinates (don't re-center to origin)
jitx-dxf import outline.dxf --no-recenter
```

### Inspect a DXF file

```bash
# Print layers, entity types, bounding box, and unit info
jitx-dxf inspect outline.dxf
```

### Python API

```python
from jitx_dxf import convert, parse_xml, read_dxf, classify_entities, generate_board_code

# --- Export: XML to DXF ---
data = parse_xml("board.xml")
convert("board.xml", "board.dxf")
convert("board.xml", "board.dxf", layers={"BoardOutline", "Pads_Top"})

# --- Import: DXF to JITX ---
# Inspect a DXF file
inventory = read_dxf("outline.dxf")
print(inventory.layers, inventory.entity_counts)

# Classify entities by PCB role
classified = classify_entities("outline.dxf")
print(f"Outline: {classified.outline is not None}")
print(f"Holes: {len(classified.holes)}")

# Generate JITX Python code
code = generate_board_code(classified, class_name="MyBoard")
print(code)
```

## DXF Import Details

The importer handles DXF files commonly exported from mechanical CAD tools:

**Supported entity types:**
- `LINE` — assembled into closed paths for board outlines and cutouts
- `ARC` — assembled into closed paths (mixed with lines)
- `LWPOLYLINE` (closed) — board outlines, cutouts (with bulge arcs)
- `CIRCLE` — mounting holes, through-holes
- `TEXT` / `MTEXT` — annotations
- `HATCH` — filled regions

**Classification heuristics** (when no `--layer-map` is provided):
- Layer names containing "outline", "board", "boundary", "profile", "edge" → board outline
- Layer names containing "cutout", "route", "rout" → cutouts
- Layer names containing "hole", "drill", "mount" → holes
- If no layer name matches: largest closed path = outline, circles inside = holes, smaller paths inside = cutouts

**Unit auto-detection:**
1. DXF `$INSUNITS` header (with sanity check for unreasonable values)
2. Bounding box heuristic: extent > 500 → mils, otherwise → mm
3. Override with `--unit` flag

## Testing

```bash
pytest tests/ -v
```
