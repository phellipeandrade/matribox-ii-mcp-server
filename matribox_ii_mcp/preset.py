from __future__ import annotations

import math
import struct
from typing import Any, Sequence

from .catalog import AlgorithmCatalog, SLOT_CATEGORIES
from .protocol import PRESET_NAME_END, PRESET_NAME_START


RESPONSE_PREFIX = bytes.fromhex("F0 21 25 7E 47 50 2D 32 12 30")
PRESET_SIZE = 1200
PRESET_BANKS = 66
PRESET_POSITIONS = "ABC"
PRESET_SLOT_COUNT = PRESET_BANKS * len(PRESET_POSITIONS)
HEADER_SIZE = 120
CHAIN_ORDER_START = 108
EFFECT_BLOCK_START = 120
EFFECT_BLOCK_SIZE = 72
EFFECT_SLOT_COUNT = 12
EFFECT_ENABLED_OFFSET = 5
EFFECT_ALGORITHM_OFFSET = 8
EFFECT_PARAMETER_OFFSET = 12
PARAMETER_COUNT = 15
EFFECT_BLOCK_MARKER = bytes.fromhex("14 00 44 00")
TAIL_START = EFFECT_BLOCK_START + EFFECT_BLOCK_SIZE * EFFECT_SLOT_COUNT


class PresetFormatError(ValueError):
    """Raised when a SysEx response cannot be decoded as a known preset."""


def decode_preset_messages(
    messages: Sequence[bytes],
    expected_slot: int | None = None,
) -> tuple[bytes, list[int]]:
    chunks: list[tuple[int, bytes]] = []
    for message in messages:
        if not message.startswith(RESPONSE_PREFIX):
            continue
        if len(message) < 14 or message[-1] != 0xF7:
            raise PresetFormatError("Malformed Matribox preset response packet")

        size_low, size_high, offset_low, offset_high = message[9:13]
        if max(size_high, offset_low, offset_high) > 0x7F:
            raise PresetFormatError("Preset packet header contains non-7-bit data")
        total_size = size_low | (size_high << 7)
        if total_size != PRESET_SIZE:
            raise PresetFormatError(
                f"Expected a {PRESET_SIZE}-byte preset, packet advertises {total_size}"
            )
        offset = offset_low | (offset_high << 7)
        nibble_data = message[13:-1]
        if not nibble_data or len(nibble_data) % 2:
            raise PresetFormatError(f"Packet at offset 0x{offset:04X} must contain full bytes")
        if any(value > 0x0F for value in nibble_data):
            raise PresetFormatError(f"Packet at offset 0x{offset:04X} contains non-nibble data")
        chunks.append((offset, nibble_data))

    if not chunks:
        raise PresetFormatError("No Matribox II preset data packets were received")

    offsets = [offset for offset, _ in chunks]
    if len(offsets) != len(set(offsets)):
        raise PresetFormatError("Duplicate packet offsets in preset response")

    chunks.sort(key=lambda item: item[0])
    expected_offset = 0
    for offset, payload in chunks:
        if offset != expected_offset:
            raise PresetFormatError(
                f"Expected packet at offset 0x{expected_offset:04X}, got 0x{offset:04X}"
            )
        expected_offset += len(payload) // 2

    nibbles = b"".join(payload for _, payload in chunks)
    decoded = bytes(
        (nibbles[index] << 4) | nibbles[index + 1]
        for index in range(0, len(nibbles), 2)
    )
    if len(decoded) != PRESET_SIZE:
        raise PresetFormatError(
            f"Expected {PRESET_SIZE} decoded bytes, received {len(decoded)}"
        )

    slot = struct.unpack_from("<H", decoded, 6)[0]
    if expected_slot is not None and slot != expected_slot:
        raise PresetFormatError(
            f"Requested preset slot {expected_slot}, device returned slot {slot}"
        )

    return decoded, offsets


def _decode_name(raw_name: bytes) -> str:
    return raw_name.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()


def resolve_preset_slot(slot: int | str) -> int:
    if isinstance(slot, str):
        label = slot.strip().upper()
        bank, separator, letter = label.partition("-")
        if label.isascii() and label.isdecimal():
            slot = int(label)
        elif separator and bank.isascii() and bank.isdecimal() and len(letter) == 1 and letter in PRESET_POSITIONS:
            slot = (int(bank) - 1) * len(PRESET_POSITIONS) + PRESET_POSITIONS.index(letter)
    if type(slot) is not int or not 0 <= slot < PRESET_SLOT_COUNT:
        raise ValueError(f"Use a preset slot from 0 to {PRESET_SLOT_COUNT - 1} or a label from 01-A to 66-C")
    return slot


def preset_label(slot: int) -> str:
    bank, index = divmod(slot, len(PRESET_POSITIONS))
    return f"{bank + 1:02d}-{PRESET_POSITIONS[index]}"


def preset_name(data: bytes) -> str:
    return _decode_name(data[PRESET_NAME_START:PRESET_NAME_END])


def parse_preset(data: bytes, catalog: AlgorithmCatalog | None = None) -> dict[str, Any]:
    if len(data) != PRESET_SIZE:
        raise PresetFormatError(f"Expected a {PRESET_SIZE}-byte preset, received {len(data)}")

    slot = struct.unpack_from("<H", data, 6)[0]
    repeated_slots = [
        struct.unpack_from("<H", data, offset)[0]
        for offset in (6, 12, 104)
    ]
    if any(value != slot for value in repeated_slots):
        raise PresetFormatError(f"Inconsistent preset slot fields in header: {repeated_slots}")

    chain_order = list(data[CHAIN_ORDER_START:HEADER_SIZE])
    if sorted(chain_order) != list(range(EFFECT_SLOT_COUNT)):
        raise PresetFormatError(f"Invalid effect chain order: {chain_order}")

    effects: list[dict[str, Any]] = []
    for slot_index, category in enumerate(SLOT_CATEGORIES):
        base = EFFECT_BLOCK_START + slot_index * EFFECT_BLOCK_SIZE
        marker = data[base : base + 4]
        stored_index = data[base + 4]
        enabled = data[base + EFFECT_ENABLED_OFFSET]
        parameter_count = struct.unpack_from("<H", data, base + 6)[0]
        algorithm_id = struct.unpack_from("<I", data, base + EFFECT_ALGORITHM_OFFSET)[0]
        parameters = struct.unpack_from("<15f", data, base + EFFECT_PARAMETER_OFFSET)

        if marker != EFFECT_BLOCK_MARKER:
            raise PresetFormatError(
                f"Unknown effect block marker in slot {slot_index}: {marker.hex()}"
            )
        if stored_index != slot_index:
            raise PresetFormatError(
                f"Effect block {slot_index} contains slot index {stored_index}"
            )
        if enabled not in (0, 1):
            raise PresetFormatError(f"Invalid enabled flag in effect slot {slot_index}: {enabled}")
        if parameter_count != PARAMETER_COUNT:
            raise PresetFormatError(
                f"Effect slot {slot_index} reports {parameter_count} parameters"
            )
        if not all(math.isfinite(value) for value in parameters):
            raise PresetFormatError(f"Non-finite parameter value in effect slot {slot_index}")

        algorithm = catalog.get(category, algorithm_id) if catalog else None
        effect: dict[str, Any] = {
            "slot": slot_index,
            "category": category,
            "enabled": bool(enabled),
            "algorithm_id": algorithm_id,
            "algorithm_id_hex": f"0x{algorithm_id:08X}",
            "algorithm_name": algorithm.name if algorithm else None,
            "parameters": list(parameters),
            "block_marker_hex": marker.hex(),
        }
        if algorithm:
            effect["controls"] = algorithm.control_values(parameters)
        effects.append(effect)

    return {
        "slot": slot,
        "name": preset_name(data),
        "chain_order": chain_order,
        "chain_categories": [SLOT_CATEGORIES[index] for index in chain_order],
        "effects": effects,
        "header_raw_hex": data[:HEADER_SIZE].hex(),
        "tail_raw_hex": data[TAIL_START:].hex(),
        "raw_data_hex": data.hex(),
        "format": {
            "decoded_bytes": len(data),
            "effect_slots": EFFECT_SLOT_COUNT,
            "parameters_per_slot": PARAMETER_COUNT,
            "tail_bytes": len(data) - TAIL_START,
        },
    }
