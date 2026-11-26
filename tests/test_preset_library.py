import struct
import unittest
from unittest.mock import Mock, patch

from matribox_ii_mcp.library import upload_stored_preset
from matribox_ii_mcp.preset import decode_preset_messages, parse_preset, preset_label, resolve_preset_slot
from matribox_ii_mcp.preset_files import (
    decode_prst,
    encode_prst,
    neutral_preset,
    retarget_preset,
    same_preset_data,
)
from matribox_ii_mcp.protocol import build_preset_upload

from tests.test_preset import load_capture


class PresetLibraryTests(unittest.TestCase):
    def test_slots_round_trip(self) -> None:
        for slot, label in [(0, "01-A"), (102, "35-A"), (197, "66-C")]:
            self.assertEqual(resolve_preset_slot(label), slot)
            self.assertEqual(preset_label(slot), label)
        for invalid in ["0-A", "67-A", "35-AB", -1, 198]:
            with self.subTest(slot=invalid), self.assertRaises(ValueError):
                resolve_preset_slot(invalid)

    def test_neutral_preset_has_all_twelve_blocks(self) -> None:
        data = neutral_preset(102, "New Preset")
        result = parse_preset(data)

        self.assertEqual(result["name"], "New Preset")
        self.assertEqual(result["chain_order"], list(range(12)))
        self.assertEqual([e["category"] for e in result["effects"] if e["enabled"]], ["SND", "RTN", "VOL"])
        with self.assertRaises(ValueError):
            neutral_preset(102, "á" * 9)

    def test_prst_round_trip_preserves_stored_fields_and_rejects_bad_crc(self) -> None:
        original, _ = decode_preset_messages(load_capture(15))
        encoded = bytearray(encode_prst(original))

        self.assertEqual(len(encoded), 1259)
        self.assertTrue(same_preset_data(decode_prst(bytes(encoded)), original))
        encoded[75] ^= 1
        encoded[-4:] = sum(encoded[:-4]).to_bytes(4, "big")
        with self.assertRaisesRegex(ValueError, "parameter CRC-32"):
            decode_prst(bytes(encoded))

    def test_upload_targets_destination_and_has_exact_fragment_offsets(self) -> None:
        source, _ = decode_preset_messages(load_capture(15), expected_slot=15)
        target = retarget_preset(source, 102, "Copied")

        frames = build_preset_upload(102, target)
        payload, offsets = [], []
        for frame in frames:
            self.assertEqual(frame[8:11], bytes.fromhex("12 38 09"))
            offsets.append(frame[11] + (frame[12] << 7))
            payload.extend((high << 4) | low for high, low in zip(frame[13:-1:2], frame[14:-1:2]))
        self.assertEqual(offsets, list(range(0, 1208, 183)))
        self.assertEqual(bytes(payload[:16]), bytes.fromhex("00 00 04 00 01 00 66 00 01 00 04 00 66 00 66 00"))
        self.assertEqual(bytes(payload[16:]), target[8:])
        self.assertEqual(struct.unpack_from("<H", target, 104)[0], 102)
        self.assertEqual(target[120:], source[120:])

    def test_mismatched_upload_restores_previous_preset(self) -> None:
        device = Mock()
        device.expect_sysex.side_effect = [None, TimeoutError("Lost restoration acknowledgment")]
        original = neutral_preset(102, "Original")
        target = neutral_preset(102, "Target")
        invalid = neutral_preset(102, "Unexpected")

        with (
            patch("matribox_ii_mcp.library.read_stored_preset", side_effect=[invalid, original]),
            patch("matribox_ii_mcp.library.time.sleep"),
            self.assertRaisesRegex(RuntimeError, "previous preset was restored"),
        ):
            upload_stored_preset(device, 102, target, before=original)

        self.assertEqual(device.send_sysex.call_count, 14)
