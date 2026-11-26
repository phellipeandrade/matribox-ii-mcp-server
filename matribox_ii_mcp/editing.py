from __future__ import annotations

import math
import struct
from typing import Any

from .catalog import AlgorithmCatalog, SLOT_CATEGORIES
from .library import read_stored_preset
from .preset import (
    CHAIN_ORDER_START,
    EFFECT_ALGORITHM_OFFSET,
    EFFECT_BLOCK_SIZE,
    EFFECT_BLOCK_START,
    EFFECT_ENABLED_OFFSET,
    EFFECT_PARAMETER_OFFSET,
    PARAMETER_COUNT,
    TAIL_START,
    parse_preset,
)
from .protocol import (
    PARAMETER_ACK,
    ReplyPattern,
    PRESET_NAME_END,
    PRESET_NAME_START,
    SAVE_ACK,
    build_effect_model_update,
    build_effect_state_update,
    build_parameter_update,
    build_save_preset_request,
)
from .transport import WinMMMidiTransport


def _block_index(category: str) -> int:
    try:
        return SLOT_CATEGORIES.index(category.strip().upper())
    except ValueError as exc:
        raise ValueError(f"Unknown effect category {category!r}") from exc


def _save_edit(
    device: WinMMMidiTransport,
    slot: int,
    before: bytes,
    after: bytearray,
    command: bytes,
    acknowledgment: bytes | ReplyPattern,
    model_block: int | None = None,
) -> bytes | None:
    if before == after:
        return None

    name = before[PRESET_NAME_START:PRESET_NAME_END]
    device.select_preset(slot)
    try:
        device.exchange_sysex(command, acknowledgment, f"editing slot {slot}")
    except (RuntimeError, OSError):
        try:
            device.select_preset(slot)
        except (RuntimeError, OSError) as exc:
            raise RuntimeError(
                f"The edit to slot {slot} was interrupted before saving. Reload the slot to clear it."
            ) from exc
        raise

    save_error = None
    try:
        device.exchange_sysex(build_save_preset_request(slot, name), SAVE_ACK, f"saving slot {slot}")
    except (RuntimeError, OSError) as exc:
        # A lost save acknowledgment can still leave a committed edit. Check by rereading the slot.
        save_error = exc

    try:
        device.select_preset(slot)
        saved = read_stored_preset(device, slot)
    except (RuntimeError, OSError, ValueError) as exc:
        raise RuntimeError(f"Could not verify the resulting preset after attempting to save slot {slot}") from exc

    if model_block is not None:
        block_start = EFFECT_BLOCK_START + model_block * EFFECT_BLOCK_SIZE
        parameter_slice = slice(block_start + EFFECT_PARAMETER_OFFSET, block_start + EFFECT_BLOCK_SIZE)
        # The firmware chooses parameter values when it switches models.
        after[parameter_slice] = saved[parameter_slice]
    if (
        saved[PRESET_NAME_START:PRESET_NAME_END] != name
        or saved[CHAIN_ORDER_START:TAIL_START] != after[CHAIN_ORDER_START:TAIL_START]
    ):
        raise RuntimeError(f"Slot {slot} did not match the expected edit after saving and reloading") from save_error
    return saved


def write_effect_parameter(
    device: WinMMMidiTransport,
    catalog: AlgorithmCatalog,
    slot: int,
    category: str,
    parameter: str,
    value: float,
) -> dict[str, Any]:
    block = _block_index(category)
    if not math.isfinite(value):
        raise ValueError("Parameter value must be finite")

    before = read_stored_preset(device, slot)
    effect = parse_preset(before)["effects"][block]
    category = effect["category"]
    algorithm_id = effect["algorithm_id"]
    algorithm = catalog.get(category, algorithm_id)
    if algorithm is None:
        raise ValueError(f"The installed catalog has no {category} algorithm with ID {algorithm_id:#010x}")

    controls = [control for control in algorithm.controls if control.name.casefold() == parameter.strip().casefold()]
    if len(controls) != 1:
        raise ValueError(f"{parameter!r} does not identify one control on {category} {algorithm.name}")
    control = controls[0]
    index = control.storage_index
    if index is None or not 0 <= index < PARAMETER_COUNT:
        raise ValueError(f"{control.name!r} has no supported storage index")
    if control.menu_options and value not in {option.value for option in control.menu_options}:
        raise ValueError(f"{control.name!r} requires a value listed for that control in the catalog")
    if control.minimum is not None and value < control.minimum:
        raise ValueError(f"{control.name!r} has a minimum of {control.minimum:g}")
    if control.maximum is not None and value > control.maximum:
        raise ValueError(f"{control.name!r} has a maximum of {control.maximum:g}")

    try:
        parameter_bytes = struct.pack("<f", value)
    except (OverflowError, struct.error) as exc:
        raise ValueError("Parameter value is outside the device's 32-bit float range") from exc
    stored_value = struct.unpack("<f", parameter_bytes)[0]
    expected = bytearray(before)
    offset = EFFECT_BLOCK_START + block * EFFECT_BLOCK_SIZE + EFFECT_PARAMETER_OFFSET + index * 4
    expected[offset : offset + 4] = parameter_bytes
    command = build_parameter_update(slot, block, index, algorithm_id, stored_value)
    saved = _save_edit(device, slot, before, expected, command, PARAMETER_ACK)
    return {
        "slot": slot,
        "category": category,
        "algorithm": algorithm.name,
        "parameter": control.name,
        "previous_value": effect["parameters"][index],
        "value": stored_value,
        "saved": saved is not None,
    }


def write_effect_enabled(
    device: WinMMMidiTransport, slot: int, category: str, enabled: bool
) -> dict[str, Any]:
    block = _block_index(category)
    before = read_stored_preset(device, slot)
    effect = parse_preset(before)["effects"][block]
    expected = bytearray(before)
    expected[EFFECT_BLOCK_START + block * EFFECT_BLOCK_SIZE + EFFECT_ENABLED_OFFSET] = enabled
    command, reply = build_effect_state_update(slot, block, enabled)
    saved = _save_edit(device, slot, before, expected, command, reply)
    return {
        "slot": slot,
        "category": effect["category"],
        "previous_enabled": effect["enabled"],
        "enabled": enabled,
        "saved": saved is not None,
    }


def write_effect_model(
    device: WinMMMidiTransport,
    catalog: AlgorithmCatalog,
    slot: int,
    category: str,
    algorithm_id: int,
) -> dict[str, Any]:
    block = _block_index(category)
    category = SLOT_CATEGORIES[block]
    algorithm = catalog.get(category, algorithm_id)
    if algorithm is None:
        raise ValueError(f"The installed catalog has no {category} algorithm with ID {algorithm_id:#010x}")

    before = read_stored_preset(device, slot)
    previous = parse_preset(before, catalog)["effects"][block]
    expected = bytearray(before)
    offset = EFFECT_BLOCK_START + block * EFFECT_BLOCK_SIZE + EFFECT_ALGORITHM_OFFSET
    struct.pack_into("<I", expected, offset, algorithm_id)
    command, reply = build_effect_model_update(slot, block, algorithm_id)
    saved = _save_edit(device, slot, before, expected, command, reply, model_block=block)
    effect = parse_preset(saved, catalog)["effects"][block] if saved is not None else previous
    return {
        "slot": slot,
        "category": category,
        "previous_algorithm": previous["algorithm_name"],
        "previous_algorithm_id": previous["algorithm_id"],
        "algorithm": algorithm.name,
        "algorithm_id": algorithm_id,
        "controls": effect["controls"],
        "saved": saved is not None,
    }
