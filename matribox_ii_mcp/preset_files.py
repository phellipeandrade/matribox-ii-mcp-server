from __future__ import annotations

import struct
from zlib import crc32

from .preset import (
    EFFECT_BLOCK_SIZE,
    EFFECT_BLOCK_START,
    EFFECT_PARAMETER_OFFSET,
    EFFECT_SLOT_COUNT,
    PARAMETER_COUNT,
    PRESET_SIZE,
    parse_preset,
)
from .protocol import PRESET_NAME_END, PRESET_NAME_START


_PRESET_SLOT_OFFSETS = (6, 12, 104)
_VARIABLE_FIELDS = (
    slice(22, 28), slice(44, 100), slice(106, 108),
    *(slice(offset, offset + 2) for offset in range(1158, PRESET_SIZE, 12)),
)
_RECORD_LAYOUT = (
    (0, 4), (2, 88), (8, 16),
    *((0x14, 68),) * EFFECT_SLOT_COUNT,
    *((0x0C, 12),) * 9,
    *((0x10, 4),) * 3,
    *((0x0F, 8),) * 4,
)

_PRST_MAGIC = b"CUSTOM_FLAGTSRP"
_PRST_PRODUCT = b"2-PG"
_PRST_HEADER = (
    _PRST_MAGIC + bytes.fromhex("00 00 00 00 00 00 00 03 00 00 00 00")
    + _PRST_PRODUCT + bytes.fromhex("00 01 01 00") + bytes(8)
)
_PRST_PARAMETER_OFFSET = 55
_PRST_PARAMETER_SIZE = PRESET_SIZE - 8
_PRST_CRC_OFFSET = _PRST_PARAMETER_OFFSET + _PRST_PARAMETER_SIZE
PRST_SIZE = _PRST_CRC_OFFSET + 12

_NEUTRAL_MODELS = (
    (0x00000000, False, (20.0, 50.0)),
    (0x03000000, False, (40.0, 70.0, 50.0)),
    (0x07000001, False, (30.0, 50.0, 50.0)),
    (0x06000001, True, ()),
    (0x06000002, True, ()),
    (0x0000001B, False, (20.0,)),
    (0x0A000000, False, (0.0, 50.0, 0.0, 0.0, 0.0, 19.0, 20001.0)),
    (0x01000035, False, (0.0, 0.0, 0.0, 0.0, 0.0, 50.0)),
    (0x04000001, False, (50.0, 0.5, 50.0)),
    (0x0B000001, False, (20.0, 500.0, 50.0)),
    (0x0C00000B, False, (25.0, 0.0, 50.0, 50.0, 50.0, 50.0)),
    (0x06000003, True, (100.0,)),
)


def _validate_records(data: bytes) -> None:
    parse_preset(data)
    offset = 0
    for record in _RECORD_LAYOUT:
        if struct.unpack_from("<HH", data, offset) != record:
            raise ValueError(f"Unsupported preset record at offset {offset}")
        offset += 4 + record[1]


def _encoded_name(name: str) -> bytes:
    name = name.strip()
    value = name.encode("utf-8")
    size = PRESET_NAME_END - PRESET_NAME_START
    if not name.isprintable() or len(value) > size:
        raise ValueError(f"Preset name must contain between 1 and {size} printable UTF-8 bytes")
    return value.ljust(size, b"\0")


def retarget_preset(data: bytes, slot: int, name: str | None = None) -> bytes:
    _validate_records(data)
    target = bytearray(data)
    for offset in _PRESET_SLOT_OFFSETS:
        struct.pack_into("<H", target, offset, slot)
    if name is not None:
        target[PRESET_NAME_START:PRESET_NAME_END] = _encoded_name(name)
    return bytes(target)


def neutral_preset(slot: int, name: str) -> bytes:
    data = bytearray(PRESET_SIZE)
    struct.pack_into("<11H", data, 0, 0, 4, 1, slot, 2, 88, slot, 120, 50, 0, 0)
    data[PRESET_NAME_START:PRESET_NAME_END] = _encoded_name(name)
    struct.pack_into("<HHH2x12B", data, 100, 8, 16, slot, *range(EFFECT_SLOT_COUNT))
    for index, (algorithm, enabled, controls) in enumerate(_NEUTRAL_MODELS):
        offset = EFFECT_BLOCK_START + index * EFFECT_BLOCK_SIZE
        struct.pack_into("<HHBBHI", data, offset, 0x14, 68, index, enabled, PARAMETER_COUNT, algorithm)
        struct.pack_into(f"<{len(controls)}f", data, offset + EFFECT_PARAMETER_OFFSET, *controls)

    groups = (0x00, 0x01, 0x02, 0x10, 0x11, 0x12, 0x20, 0x21, 0x22)
    for offset, group in zip(range(984, 1128, 16), groups):
        assignment = 0x0B if group == 0 else 0xFF
        struct.pack_into("<HHBB2xf4x", data, offset, 0x0C, 12, group, assignment, 100.0)
    for offset, index, assignment in zip(range(1128, 1152, 8), range(3), (0xFF, 0xFF, 0x0C)):
        struct.pack_into("<HHBB2x", data, offset, 0x10, 4, index, assignment)
    for index, offset in enumerate(range(1152, PRESET_SIZE, 12)):
        struct.pack_into("<HHH6x", data, offset, 0x0F, 8, index)
    return bytes(data)


def _without_variable_fields(data: bytes) -> bytes:
    result = bytearray(data)
    for field in _VARIABLE_FIELDS:
        result[field] = bytes(field.stop - field.start)
    return bytes(result)


def same_preset_data(left: bytes, right: bytes) -> bool:
    return _without_variable_fields(left) == _without_variable_fields(right)


def decode_prst(file: bytes) -> bytes:
    if len(file) != PRST_SIZE or not file.startswith(_PRST_MAGIC) or file[27:31] != _PRST_PRODUCT:
        raise ValueError("Expected a standard single-preset Matribox II .prst without embedded assets")
    if (
        struct.unpack_from("<II", file, 43) != (_PRST_PARAMETER_OFFSET, _PRST_PARAMETER_SIZE + 4)
        or file[51:55] != b"MRAP"
        or struct.unpack_from("<I", file, _PRST_CRC_OFFSET + 4)[0] != _PRST_CRC_OFFSET + 4
    ):
        raise ValueError("Unsupported .prst parameter section")
    if int.from_bytes(file[-4:], "big") != sum(file[:-4]):
        raise ValueError("Invalid .prst file checksum")
    parameters = file[_PRST_PARAMETER_OFFSET:_PRST_CRC_OFFSET]
    if struct.unpack_from(">I", file, _PRST_CRC_OFFSET)[0] != crc32(parameters):
        raise ValueError("Invalid .prst parameter CRC-32")
    slot = struct.unpack_from("<H", parameters, 4)[0]
    data = struct.pack("<HHHH", 0, 4, 1, slot) + parameters
    _validate_records(data)
    return data


def encode_prst(data: bytes) -> bytes:
    _validate_records(data)
    parameters = data[8:]
    section = struct.pack("<II", _PRST_PARAMETER_OFFSET, len(parameters) + 4) + b"MRAP" + parameters
    file = _PRST_HEADER + section + struct.pack(">I", crc32(parameters))
    file += struct.pack("<I", len(file))
    return file + struct.pack(">I", sum(file))
