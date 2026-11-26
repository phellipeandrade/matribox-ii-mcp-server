from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from dataclasses import dataclass

from .protocol import (
    ENTER_PRESET_MODE,
    ReplyPattern,
    build_read_preset_request,
    build_select_preset_request,
)


MHDR_DONE = 0x00000001
MHDR_PREPARED = 0x00000002
MIDI_BUFFER_COUNT = 32
MIDI_BUFFER_SIZE = 1024


class MIDIHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_size_t),
        ("dwFlags", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_size_t),
        ("dwOffset", wintypes.DWORD),
        ("dwReserved", ctypes.c_size_t * 8),
    ]


class MIDIINCAPS(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.DWORD),
        ("szPname", wintypes.WCHAR * 32),
        ("dwSupport", wintypes.DWORD),
    ]


class MIDIOUTCAPS(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.DWORD),
        ("szPname", wintypes.WCHAR * 32),
        ("wTechnology", wintypes.WORD),
        ("wVoices", wintypes.WORD),
        ("wNotes", wintypes.WORD),
        ("wChannelMask", wintypes.WORD),
        ("dwSupport", wintypes.DWORD),
    ]


@dataclass(frozen=True)
class MidiDevice:
    index: int
    name: str

    def to_dict(self) -> dict[str, int | str]:
        return {"index": self.index, "name": self.name}


def _load_winmm() -> ctypes.WinDLL:
    if os.name != "nt":
        raise OSError("The Matribox II USB MIDI backend currently supports Windows only")

    library = ctypes.WinDLL("winmm")
    library.midiInGetNumDevs.restype = wintypes.UINT
    library.midiOutGetNumDevs.restype = wintypes.UINT
    library.midiInGetDevCapsW.argtypes = [
        ctypes.c_size_t,
        ctypes.POINTER(MIDIINCAPS),
        wintypes.UINT,
    ]
    library.midiOutGetDevCapsW.argtypes = [
        ctypes.c_size_t,
        ctypes.POINTER(MIDIOUTCAPS),
        wintypes.UINT,
    ]
    library.midiInOpen.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.c_size_t,
        wintypes.DWORD,
    ]
    library.midiOutOpen.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.c_size_t,
        wintypes.DWORD,
    ]
    for name in (
        "midiInPrepareHeader",
        "midiInAddBuffer",
        "midiInUnprepareHeader",
        "midiOutPrepareHeader",
        "midiOutUnprepareHeader",
    ):
        getattr(library, name).argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(MIDIHDR),
            wintypes.UINT,
        ]
    library.midiOutLongMsg.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(MIDIHDR),
        wintypes.UINT,
    ]
    for name in ("midiInStart", "midiInStop", "midiInReset", "midiInClose", "midiOutClose"):
        getattr(library, name).argtypes = [ctypes.c_void_p]
    return library


def enumerate_midi_devices() -> dict[str, list[MidiDevice]]:
    library = _load_winmm()
    inputs: list[MidiDevice] = []
    outputs: list[MidiDevice] = []

    for index in range(library.midiInGetNumDevs()):
        caps = MIDIINCAPS()
        result = library.midiInGetDevCapsW(index, ctypes.byref(caps), ctypes.sizeof(caps))
        if result == 0:
            inputs.append(MidiDevice(index=index, name=caps.szPname))

    for index in range(library.midiOutGetNumDevs()):
        caps = MIDIOUTCAPS()
        result = library.midiOutGetDevCapsW(index, ctypes.byref(caps), ctypes.sizeof(caps))
        if result == 0:
            outputs.append(MidiDevice(index=index, name=caps.szPname))

    return {"inputs": inputs, "outputs": outputs}


class WinMMMidiTransport:
    def __init__(
        self,
        device_name: str = "Matribox II Subdevice",
        buffer_count: int = MIDI_BUFFER_COUNT,
        buffer_size: int = MIDI_BUFFER_SIZE,
    ):
        self.library = _load_winmm()
        devices = enumerate_midi_devices()
        input_device = self._select_device(devices["inputs"], device_name, "input")
        output_device = self._select_device(devices["outputs"], device_name, "output")

        self.input_handle = ctypes.c_void_p()
        self.output_handle = ctypes.c_void_p()
        self.buffers: list[ctypes.Array] = []
        self.headers: list[MIDIHDR] = []
        self.partial_data = bytearray()
        self.closed = False

        self._check(
            self.library.midiOutOpen(
                ctypes.byref(self.output_handle), output_device.index, 0, 0, 0
            ),
            "midiOutOpen",
        )
        try:
            self._check(
                self.library.midiInOpen(
                    ctypes.byref(self.input_handle), input_device.index, 0, 0, 0
                ),
                "midiInOpen",
            )
            self._prepare_input_buffers(buffer_count, buffer_size)
            self._check(self.library.midiInStart(self.input_handle), "midiInStart")
        except Exception:
            self.close()
            raise

    @staticmethod
    def _select_device(
        devices: list[MidiDevice],
        requested_name: str,
        direction: str,
    ) -> MidiDevice:
        matches = [
            device
            for device in devices
            if requested_name.casefold() in device.name.casefold()
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            available = ", ".join(device.name for device in devices) or "none"
            raise RuntimeError(
                f"Matribox MIDI {direction} device '{requested_name}' not found. "
                f"Available devices: {available}"
            )
        raise RuntimeError(
            f"Multiple MIDI {direction} devices match '{requested_name}'. "
            "Use a more specific device name."
        )

    @staticmethod
    def _check(result: int, action: str) -> None:
        if result:
            raise RuntimeError(f"{action} failed with WinMM MMRESULT={result}")

    def _prepare_input_buffers(self, buffer_count: int, buffer_size: int) -> None:
        for _ in range(buffer_count):
            buffer = ctypes.create_string_buffer(buffer_size)
            header = MIDIHDR(
                ctypes.cast(buffer, ctypes.c_void_p),
                buffer_size,
                0,
                0,
                0,
                None,
                0,
                0,
                (ctypes.c_size_t * 8)(),
            )
            self._check(
                self.library.midiInPrepareHeader(
                    self.input_handle, ctypes.byref(header), ctypes.sizeof(header)
                ),
                "midiInPrepareHeader",
            )
            self._check(
                self.library.midiInAddBuffer(
                    self.input_handle, ctypes.byref(header), ctypes.sizeof(header)
                ),
                "midiInAddBuffer",
            )
            self.buffers.append(buffer)
            self.headers.append(header)

    def send_sysex(self, message: bytes, timeout_seconds: float = 3.0) -> None:
        if not message.startswith(b"\xF0") or not message.endswith(b"\xF7"):
            raise ValueError("SysEx messages must start with F0 and end with F7")

        buffer = ctypes.create_string_buffer(message, len(message))
        header = MIDIHDR(
            ctypes.cast(buffer, ctypes.c_void_p),
            len(message),
            len(message),
            0,
            0,
            None,
            0,
            0,
            (ctypes.c_size_t * 8)(),
        )
        self._check(
            self.library.midiOutPrepareHeader(
                self.output_handle, ctypes.byref(header), ctypes.sizeof(header)
            ),
            "midiOutPrepareHeader",
        )
        try:
            self._check(
                self.library.midiOutLongMsg(
                    self.output_handle, ctypes.byref(header), ctypes.sizeof(header)
                ),
                "midiOutLongMsg",
            )
            deadline = time.monotonic() + timeout_seconds
            while not header.dwFlags & MHDR_DONE:
                if time.monotonic() >= deadline:
                    raise TimeoutError("WinMM did not complete the SysEx output buffer")
                time.sleep(0.002)
        finally:
            if header.dwFlags & MHDR_DONE:
                self.library.midiOutUnprepareHeader(
                    self.output_handle, ctypes.byref(header), ctypes.sizeof(header)
                )

    def _collect_completed_buffers(self) -> list[bytes]:
        received: list[bytes] = []
        for header in self.headers:
            if not header.dwFlags & MHDR_DONE:
                continue

            if header.dwBytesRecorded:
                received.append(ctypes.string_at(header.lpData, header.dwBytesRecorded))

            self._check(
                self.library.midiInUnprepareHeader(
                    self.input_handle, ctypes.byref(header), ctypes.sizeof(header)
                ),
                "midiInUnprepareHeader",
            )
            header.dwFlags = 0
            header.dwBytesRecorded = 0
            self._check(
                self.library.midiInPrepareHeader(
                    self.input_handle, ctypes.byref(header), ctypes.sizeof(header)
                ),
                "midiInPrepareHeader(recycle)",
            )
            self._check(
                self.library.midiInAddBuffer(
                    self.input_handle, ctypes.byref(header), ctypes.sizeof(header)
                ),
                "midiInAddBuffer(recycle)",
            )
        return received

    def _extract_messages(self) -> list[bytes]:
        messages: list[bytes] = []
        while True:
            start = self.partial_data.find(0xF0)
            if start < 0:
                self.partial_data.clear()
                return messages
            if start:
                del self.partial_data[:start]

            end = self.partial_data.find(0xF7, 1)
            if end < 0:
                return messages

            messages.append(bytes(self.partial_data[: end + 1]))
            del self.partial_data[: end + 1]

    def receive_sysex(
        self,
        timeout_seconds: float,
        quiet_period_seconds: float = 0.25,
    ) -> list[bytes]:
        deadline = time.monotonic() + timeout_seconds
        last_message_at: float | None = None
        messages: list[bytes] = []

        while time.monotonic() < deadline:
            for buffer_data in self._collect_completed_buffers():
                self.partial_data.extend(buffer_data)
            new_messages = self._extract_messages()
            if new_messages:
                messages.extend(new_messages)
                last_message_at = time.monotonic()
            elif messages and last_message_at is not None:
                if time.monotonic() - last_message_at >= quiet_period_seconds:
                    break
            time.sleep(0.002)

        return messages

    def read_preset_messages(self, slot: int, timeout_seconds: float = 5.0) -> list[bytes]:
        request = build_read_preset_request(slot)
        self.enter_preset_mode()
        return self._request_preset_messages(request, timeout_seconds)

    def enter_preset_mode(self) -> None:
        self.receive_sysex(timeout_seconds=0.15, quiet_period_seconds=0.05)
        self.send_sysex(ENTER_PRESET_MODE)
        self.receive_sysex(timeout_seconds=0.2, quiet_period_seconds=0.05)

    def request_preset_messages(self, slot: int, timeout_seconds: float = 5.0) -> list[bytes]:
        return self._request_preset_messages(build_read_preset_request(slot), timeout_seconds)

    def _request_preset_messages(self, request: bytes, timeout_seconds: float) -> list[bytes]:
        self.send_sysex(request)
        return self.receive_sysex(timeout_seconds, quiet_period_seconds=0.3)

    def expect_sysex(
        self, expected: bytes | ReplyPattern, operation: str, timeout_seconds: float = 3.0
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        remaining = deadline - time.monotonic()
        while remaining > 0:
            replies = self.receive_sysex(remaining, quiet_period_seconds=0.25)
            if isinstance(expected, bytes):
                matched = expected in replies
            else:
                matched = any(expected.matches(reply) for reply in replies)
            if matched:
                return
            remaining = deadline - time.monotonic()
        raise TimeoutError(f"Matribox did not acknowledge {operation}")

    def exchange_sysex(self, message: bytes, expected: bytes | ReplyPattern, operation: str) -> None:
        self.send_sysex(message)
        self.expect_sysex(expected, operation)
        time.sleep(0.65)

    def select_preset(self, slot: int) -> None:
        if not 0 <= slot <= 0xFF:
            raise ValueError("Preset slot must be between 0 and 255")
        request = build_select_preset_request(slot)
        self.exchange_sysex(request, request, f"selecting slot {slot}")

    def close(self) -> None:
        if self.closed:
            return

        if self.input_handle:
            self.library.midiInStop(self.input_handle)
            self.library.midiInReset(self.input_handle)
            for header in self.headers:
                if header.dwFlags & MHDR_PREPARED:
                    self.library.midiInUnprepareHeader(
                        self.input_handle, ctypes.byref(header), ctypes.sizeof(header)
                    )
            self.library.midiInClose(self.input_handle)
            self.input_handle = ctypes.c_void_p()

        if self.output_handle:
            self.library.midiOutClose(self.output_handle)
            self.output_handle = ctypes.c_void_p()

        self.closed = True

    def __enter__(self) -> WinMMMidiTransport:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()
