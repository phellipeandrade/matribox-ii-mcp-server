from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .preset import decode_preset_messages, preset_label, preset_name
from .preset_files import PRST_SIZE, decode_prst, encode_prst, retarget_preset, same_preset_data
from .protocol import UPLOAD_ACK, build_preset_upload
from .transport import WinMMMidiTransport


def read_stored_preset(device: WinMMMidiTransport, slot: int) -> bytes:
    data, _ = decode_preset_messages(device.read_preset_messages(slot), expected_slot=slot)
    return data


def describe_preset(slot: int, data: bytes) -> dict[str, Any]:
    return {"slot": slot, "label": preset_label(slot), "name": preset_name(data)}


def load_prst(path: str) -> bytes:
    with Path(path).expanduser().open("rb") as file:
        return decode_prst(file.read(PRST_SIZE + 1))


def save_prst(path: str, data: bytes) -> Path:
    destination = Path(path).expanduser().resolve()
    encoded = encode_prst(data)
    # Exclusive creation preserves existing backups at the chosen path.
    output = destination.open("xb")
    try:
        with output:
            output.write(encoded)
    except OSError:
        destination.unlink(missing_ok=True)
        raise
    return destination


def _send_upload(device: WinMMMidiTransport, slot: int, data: bytes) -> None:
    for packet in build_preset_upload(slot, data):
        device.send_sysex(packet)
        time.sleep(0.02)
    device.expect_sysex(UPLOAD_ACK, f"uploading slot {slot}", timeout_seconds=5.0)


def _upload_and_read(
    device: WinMMMidiTransport, slot: int, data: bytes
) -> tuple[bytes, Exception | None]:
    upload_error = None
    try:
        _send_upload(device, slot, data)
    except (OSError, RuntimeError) as exc:
        upload_error = exc
    # The editor's upload can reload the selected patch about a second after acknowledging it.
    time.sleep(1.2)
    return read_stored_preset(device, slot), upload_error


def upload_stored_preset(
    device: WinMMMidiTransport,
    slot: int,
    source: bytes,
    name: str | None = None,
    *,
    before: bytes | None = None,
) -> dict[str, Any]:
    target = retarget_preset(source, slot, name)
    if before is None:
        before = read_stored_preset(device, slot)
    if same_preset_data(before, target):
        return {**describe_preset(slot, before), "saved": False}

    try:
        current, upload_error = _upload_and_read(device, slot, target)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError(f"Could not verify slot {slot} after the upload attempt") from exc
    if same_preset_data(current, target):
        return {**describe_preset(slot, current), "saved": True, "acknowledged": upload_error is None}
    if same_preset_data(current, before):
        raise RuntimeError(f"Slot {slot} still contains its original preset after the upload attempt") from upload_error

    try:
        restored, restore_error = _upload_and_read(device, slot, before)
        if not same_preset_data(restored, before):
            raise RuntimeError("Restored preset differs from the original") from restore_error
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError(f"Upload of slot {slot} differed from the expected data and restoration could not be verified") from exc
    raise RuntimeError(f"Upload of slot {slot} differed from the expected data. The previous preset was restored.") from upload_error
