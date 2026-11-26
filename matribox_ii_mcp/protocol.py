from __future__ import annotations

import struct
from dataclasses import dataclass


DEVICE_PREFIX = bytes.fromhex("F0 21 25 7E 47 50 2D 32")
ENTER_PRESET_MODE = DEVICE_PREFIX + bytes.fromhex("11 12 00 00 00 F7")
PRESET_NAME_START = 28
PRESET_NAME_END = 44


@dataclass(frozen=True)
class ReplyPattern:
    size: int
    prefix: bytes
    suffix: bytes

    def matches(self, message: bytes) -> bool:
        return len(message) == self.size and message.startswith(self.prefix) and message.endswith(self.suffix)


def build_read_preset_request(slot: int) -> bytes:
    if not 0 <= slot <= 0xFF:
        raise ValueError("Preset slot must be between 0 and 255")

    high_nibble = (slot >> 4) & 0x0F
    low_nibble = slot & 0x0F
    payload = [
        0x04,
        0x00,
        0x00,
        0x00,
        0x01,
        0x00,
        0x00,
        high_nibble,
        low_nibble,
        0x00,
        0x00,
        0x00,
        0x01,
        0x00,
        0x00,
        0x00,
        0x04,
        0x00,
        0x00,
        high_nibble,
        low_nibble,
        0x00,
        0x00,
        high_nibble,
        low_nibble,
        0x00,
        0x00,
        0xF7,
    ]
    return DEVICE_PREFIX + bytes([0x11, 0x10] + [0x00] * 8 + payload)


def _editor_frame(payload: bytes, total_size: int | None = None, offset: int = 0) -> bytes:
    size = len(payload) if total_size is None else total_size
    header = DEVICE_PREFIX + bytes((0x12, size & 0x7F, size >> 7, offset & 0x7F, offset >> 7))
    nibbles = bytes(nibble for value in payload for nibble in (value >> 4, value & 0x0F))
    return header + nibbles + b"\xF7"


PARAMETER_ACK = _editor_frame(bytes.fromhex("11 10 04 00 01 00 01 00"))
SAVE_ACK = _editor_frame(bytes.fromhex("11 10 04 00 01 00 00 00"))
UPLOAD_ACK = _editor_frame(bytes.fromhex("10 20 04 00 00 00 00 00"))
UPLOAD_FRAGMENT_SIZE = 183


def build_select_preset_request(slot: int) -> bytes:
    return _editor_frame(struct.pack("<HHHH", 0x1008, 4, 0, slot))


def _target_record(slot: int) -> bytes:
    return struct.pack("<HHHH", 0, 4, 1, slot)


def build_parameter_update(
    slot: int, block_index: int, parameter_index: int, algorithm_id: int, value: float
) -> bytes:
    update = struct.pack("<HHBB2xIf", 0x0005, 12, block_index, parameter_index, algorithm_id, value)
    return _editor_frame(_target_record(slot) + update)


def build_effect_state_update(slot: int, block_index: int, enabled: bool) -> tuple[bytes, ReplyPattern]:
    update = struct.pack("<HHBB2x", 0x0015, 4, block_index, enabled)
    # The device echoes the update with two additional bytes whose values vary.
    reply = _editor_frame(update)
    return _editor_frame(_target_record(slot) + update), ReplyPattern(len(reply), reply[:-5], b"\xF7")


def build_effect_model_update(slot: int, block_index: int, algorithm_id: int) -> tuple[bytes, ReplyPattern]:
    # The editor sends a different selector for the CAB block, which is slot 6.
    selector = b"\xFF\xFF\xFF" if block_index == 6 else b"\x7F\x00\x00"
    update = struct.pack("<HHB3sI", 0x0016, 8, block_index, selector, algorithm_id)
    reply = _editor_frame(struct.pack("<HHBB2xI", 0x0016, 8, block_index, 0, algorithm_id))
    # Accept the block and algorithm ID while ignoring the two variable bytes in between.
    return _editor_frame(_target_record(slot) + update), ReplyPattern(len(reply), reply[:25], reply[29:])


def build_save_preset_request(slot: int, preset_name: bytes) -> bytes:
    if len(preset_name) != PRESET_NAME_END - PRESET_NAME_START:
        raise ValueError("The saved preset name must occupy 16 bytes")
    return _editor_frame(struct.pack("<HHH2x16s", 0x2003, 20, slot, preset_name))


def build_preset_upload(slot: int, preset: bytes) -> tuple[bytes, ...]:
    # Replace the read-only slot record with the editor's second destination record.
    payload = _target_record(slot) + struct.pack("<HHHH", 1, 4, slot, slot) + preset[8:]
    return tuple(
        _editor_frame(payload[offset : offset + UPLOAD_FRAGMENT_SIZE], len(payload), offset)
        for offset in range(0, len(payload), UPLOAD_FRAGMENT_SIZE)
    )
