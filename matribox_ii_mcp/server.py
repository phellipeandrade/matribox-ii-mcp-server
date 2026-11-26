from __future__ import annotations

from functools import lru_cache
from threading import Lock
from typing import Any

from mcp.server.fastmcp import FastMCP

from .catalog import AlgorithmCatalog, SLOT_CATEGORIES
from .editing import write_effect_enabled, write_effect_model, write_effect_parameter
from .library import describe_preset, load_prst, read_stored_preset, save_prst, upload_stored_preset
from .preset import (
    PRESET_SLOT_COUNT,
    decode_preset_messages,
    parse_preset,
    resolve_preset_slot,
)
from .preset_files import neutral_preset
from .transport import WinMMMidiTransport, enumerate_midi_devices


mcp = FastMCP("Matribox II MCP Server")
_midi_lock = Lock()


@lru_cache(maxsize=1)
def _catalog() -> AlgorithmCatalog:
    return AlgorithmCatalog.from_xml()


@mcp.tool()
def list_midi_devices() -> dict[str, list[dict[str, Any]]]:
    """List Windows MIDI input and output devices visible to the computer."""
    devices = enumerate_midi_devices()
    return {
        "inputs": [device.to_dict() for device in devices["inputs"]],
        "outputs": [device.to_dict() for device in devices["outputs"]],
    }


@mcp.tool()
def list_effect_categories() -> list[str]:
    """List the 12 fixed effect slots in the Matribox II signal chain."""
    return list(SLOT_CATEGORIES)


@mcp.tool()
def list_algorithms(
    category: str | None = None,
    query: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Search the installed Matribox effect catalog by category or effect name."""
    catalog = _catalog()
    algorithms = catalog.list(category=category, query=query, limit=limit)
    return {
        "source": str(catalog.source),
        "total_catalog_entries": len(catalog.algorithms),
        "returned": len(algorithms),
        "algorithms": [algorithm.to_dict() for algorithm in algorithms],
    }


@mcp.tool()
def get_algorithm(
    category: str,
    algorithm_id: int | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Get an effect definition, including knob, switch and selector metadata."""
    if (algorithm_id is None) == (name is None):
        raise ValueError("Provide exactly one of algorithm_id or name")

    catalog = _catalog()
    if algorithm_id is not None:
        algorithm = catalog.get(category, algorithm_id)
    else:
        matches = [
            item
            for item in catalog.list(category=category, query=name, limit=500)
            if item.name.casefold() == (name or "").casefold()
        ]
        algorithm = matches[0] if matches else None

    if algorithm is None:
        raise ValueError(f"Effect not found in category {category}")
    return algorithm.to_dict()


@mcp.tool()
def read_preset(
    slot: int | str,
    device_name: str = "Matribox II Subdevice",
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    """Read a stored preset from the Matribox II over USB MIDI without changing it."""
    if not 0 < timeout_seconds <= 30:
        raise ValueError("timeout_seconds must be greater than 0 and at most 30")
    slot = resolve_preset_slot(slot)

    with _midi_lock, WinMMMidiTransport(device_name=device_name) as device:
        messages = device.read_preset_messages(slot, timeout_seconds=timeout_seconds)

    decoded, chunk_offsets = decode_preset_messages(messages, expected_slot=slot)
    result = parse_preset(decoded, catalog=_catalog())
    result["transport"] = {
        "device_name": device_name,
        "response_packets": len(chunk_offsets),
        "packet_offsets": [f"0x{offset:04X}" for offset in chunk_offsets],
    }
    return result


@mcp.tool()
def list_presets(
    start_slot: int | str = 0,
    limit: int = 24,
    device_name: str = "Matribox II Subdevice",
) -> dict[str, Any]:
    """List stored preset names without switching slots. Use next_slot for the next page."""
    start = resolve_preset_slot(start_slot)
    if not 1 <= limit <= 32:
        raise ValueError("limit must be between 1 and 32")
    end = min(start + limit, PRESET_SLOT_COUNT)
    presets = []
    with _midi_lock, WinMMMidiTransport(device_name=device_name) as device:
        device.enter_preset_mode()
        for slot in range(start, end):
            messages = device.request_preset_messages(slot, timeout_seconds=2.0)
            data, _ = decode_preset_messages(messages, expected_slot=slot)
            presets.append(describe_preset(slot, data))
    return {"presets": presets, "next_slot": end if end < PRESET_SLOT_COUNT else None}


@mcp.tool()
def select_preset(slot: int | str, device_name: str = "Matribox II Subdevice") -> dict[str, Any]:
    """Activate a stored preset by number or label. This discards unsaved edits in the edit buffer."""
    target = resolve_preset_slot(slot)
    with _midi_lock, WinMMMidiTransport(device_name=device_name) as device:
        data = read_stored_preset(device, target)
        device.select_preset(target)
    return describe_preset(target, data)


@mcp.tool()
def create_preset(slot: int | str, name: str, device_name: str = "Matribox II Subdevice") -> dict[str, Any]:
    """Overwrite a specific slot with a named neutral preset, then verify by rereading it."""
    target = resolve_preset_slot(slot)
    data = neutral_preset(target, name)
    with _midi_lock, WinMMMidiTransport(device_name=device_name) as device:
        return upload_stored_preset(device, target, data)


@mcp.tool()
def copy_preset(
    source_slot: int | str,
    destination_slot: int | str,
    name: str | None = None,
    device_name: str = "Matribox II Subdevice",
) -> dict[str, Any]:
    """Overwrite the destination with a stored copy of the source, optionally giving it a new name."""
    source = resolve_preset_slot(source_slot)
    target = resolve_preset_slot(destination_slot)
    with _midi_lock, WinMMMidiTransport(device_name=device_name) as device:
        data = read_stored_preset(device, source)
        before = data if source == target else None
        return upload_stored_preset(device, target, data, name, before=before)


@mcp.tool()
def rename_preset(slot: int | str, name: str, device_name: str = "Matribox II Subdevice") -> dict[str, Any]:
    """Rename the stored preset and verify that its other stored fields are preserved."""
    target = resolve_preset_slot(slot)
    with _midi_lock, WinMMMidiTransport(device_name=device_name) as device:
        data = read_stored_preset(device, target)
        return upload_stored_preset(device, target, data, name, before=data)


@mcp.tool()
def export_preset(slot: int | str, file_path: str, device_name: str = "Matribox II Subdevice") -> dict[str, Any]:
    """Write a new single-preset .prst backup from the pedal to the supplied local path."""
    source = resolve_preset_slot(slot)
    with _midi_lock, WinMMMidiTransport(device_name=device_name) as device:
        data = read_stored_preset(device, source)
    destination = save_prst(file_path, data)
    return {**describe_preset(source, data), "file_path": str(destination)}


@mcp.tool()
def import_preset(
    file_path: str,
    slot: int | str,
    name: str | None = None,
    device_name: str = "Matribox II Subdevice",
) -> dict[str, Any]:
    """Overwrite the selected destination with a standard Matribox II .prst without embedded assets."""
    target = resolve_preset_slot(slot)
    data = load_prst(file_path)
    with _midi_lock, WinMMMidiTransport(device_name=device_name) as device:
        return upload_stored_preset(device, target, data, name)


@mcp.tool()
def restore_preset(
    file_path: str,
    slot: int | str | None = None,
    device_name: str = "Matribox II Subdevice",
) -> dict[str, Any]:
    """Restore a .prst to its original slot or the specified destination and verify by rereading it."""
    data = load_prst(file_path)
    target = resolve_preset_slot(parse_preset(data)["slot"] if slot is None else slot)
    with _midi_lock, WinMMMidiTransport(device_name=device_name) as device:
        return upload_stored_preset(device, target, data)


@mcp.tool()
def set_effect_parameter(
    slot: int | str,
    category: str,
    parameter: str,
    value: float,
    device_name: str = "Matribox II Subdevice",
) -> dict[str, Any]:
    """Select a stored preset, set a named control in its current effect model, save, and reread it.

    Use the control name and numeric units returned by read_preset. Selecting the slot
    reloads its stored state and discards pending edits in the current edit buffer.
    """
    catalog = _catalog()
    slot = resolve_preset_slot(slot)
    with _midi_lock, WinMMMidiTransport(device_name=device_name) as device:
        return write_effect_parameter(device, catalog, slot, category, parameter, value)


@mcp.tool()
def set_effect_enabled(
    slot: int | str,
    category: str,
    enabled: bool,
    device_name: str = "Matribox II Subdevice",
) -> dict[str, Any]:
    """Select a stored preset, enable or bypass the effect block, save, and reread it.

    Selecting the slot reloads its stored state and discards pending edits in the current
    edit buffer.
    """
    slot = resolve_preset_slot(slot)
    with _midi_lock, WinMMMidiTransport(device_name=device_name) as device:
        return write_effect_enabled(device, slot, category, enabled)


@mcp.tool()
def set_effect_model(
    slot: int | str,
    category: str,
    algorithm_id: int,
    device_name: str = "Matribox II Subdevice",
) -> dict[str, Any]:
    """Select a stored preset, switch to a catalog amp, cab or effect model, and verify the save.

    Use list_algorithms to find the algorithm code. The pedal initializes controls for
    the selected model. Selecting the slot discards pending changes in the edit buffer.
    """
    catalog = _catalog()
    slot = resolve_preset_slot(slot)
    with _midi_lock, WinMMMidiTransport(device_name=device_name) as device:
        return write_effect_model(device, catalog, slot, category, algorithm_id)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
