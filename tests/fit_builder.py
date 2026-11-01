"""A minimal FIT file writer, for tests that need real FIT bytes.

Writes definition + data messages with little-endian fields and the FIT CRCs,
so fitparse decodes the result exactly as it decodes a watch's file. Values
in tests are synthetic.
"""
import struct
from typing import Iterable, List, Sequence, Tuple

# FIT base types: (type byte, struct format)
ENUM = (0x00, "B")
SINT8 = (0x01, "b")
UINT8 = (0x02, "B")
SINT16 = (0x83, "h")
UINT16 = (0x84, "H")
SINT32 = (0x85, "i")
UINT32 = (0x86, "I")

# One field: (field definition number, base type, raw value)
Field = Tuple[int, Tuple[int, str], int]

FIT_EPOCH = 631065600  # 1989-12-31T00:00:00Z as Unix time
SEMICIRCLES_PER_DEGREE = 2 ** 31 / 180

_CRC_TABLE = (0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
              0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400)


def _crc(data: bytes, crc: int = 0) -> int:
    for byte in data:
        tmp = _CRC_TABLE[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ _CRC_TABLE[byte & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc


def fit_time(unix_seconds: int) -> int:
    """A Unix time as a FIT timestamp."""
    return unix_seconds - FIT_EPOCH


def semicircles(degrees: float) -> int:
    return int(round(degrees * SEMICIRCLES_PER_DEGREE))


def build_fit(messages: Iterable[Tuple[int, Sequence[Field]]]) -> bytes:
    """FIT bytes for (global message number, fields) pairs, in order.

    A message whose layout (message number and field list) has been seen
    reuses its local definition; a new layout gets a new definition.
    """
    body = bytearray()
    layouts: List[Tuple[int, Tuple[Tuple[int, int, str], ...]]] = []
    for global_num, fields in messages:
        layout = (global_num, tuple((num, base[0], base[1]) for num, base, _ in fields))
        if layout in layouts:
            local = layouts.index(layout) % 16
        else:
            layouts.append(layout)
            local = (len(layouts) - 1) % 16
            body += struct.pack("<BBBHB", 0x40 | local, 0, 0, global_num, len(fields))
            for num, type_byte, fmt in layout[1]:
                body += struct.pack("<BBB", num, struct.calcsize(fmt), type_byte)
        body += struct.pack("<B", local)
        for _num, base, value in fields:
            body += struct.pack("<" + base[1], value)
    header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(body), b".FIT")
    header += struct.pack("<H", _crc(header))
    data = header + bytes(body)
    return data + struct.pack("<H", _crc(data))
