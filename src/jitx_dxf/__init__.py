"""jitx-dxf: Convert between JITX board designs and DXF format."""

__version__ = "0.1.0"

from .dxf_reader import classify_entities, read_dxf
from .dxf_writer import convert
from .jitx_codegen import generate_board_code
from .models import BoardData
from .xml_parser import parse_xml

__all__ = [
    "BoardData",
    "classify_entities",
    "convert",
    "generate_board_code",
    "parse_xml",
    "read_dxf",
]
