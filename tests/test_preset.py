from __future__ import annotations

import json
import unittest
from pathlib import Path

from matribox_ii_mcp.catalog import AlgorithmCatalog
from matribox_ii_mcp.preset import decode_preset_messages, parse_preset


FIXTURES = Path(__file__).parent / "fixtures"


def load_capture(slot: int) -> list[bytes]:
    payload = json.loads((FIXTURES / f"slot{slot}_sysex.json").read_text(encoding="utf-8"))
    return [bytes.fromhex(message) for message in payload]


class PresetDecodeTests(unittest.TestCase):
    def test_slot_15_capture_decodes(self) -> None:
        decoded, offsets = decode_preset_messages(load_capture(15), expected_slot=15)

        self.assertEqual(len(decoded), 1200)
        self.assertEqual(offsets, [0, 185, 370, 555, 740, 925, 1110])
        self.assertEqual(decoded[28:44].split(b"\x00", 1)[0], b"Test Lead")

    def test_out_of_order_packets_decode_by_offset(self) -> None:
        messages = load_capture(15)
        ordered, offsets = decode_preset_messages(messages)
        decoded, received_offsets = decode_preset_messages(messages[::-1])

        self.assertEqual(decoded, ordered)
        self.assertEqual(received_offsets, offsets[::-1])

    def test_packet_offset_gap_is_rejected_at_full_payload_size(self) -> None:
        messages = load_capture(15)
        packet = bytearray(messages[1])
        packet[11] += 1
        messages[1] = bytes(packet)

        with self.assertRaisesRegex(ValueError, "offset 0x00B9, got 0x00BA"):
            decode_preset_messages(messages)

    def test_inconsistent_packet_total_is_rejected(self) -> None:
        messages = load_capture(15)
        packet = bytearray(messages[1])
        packet[10] -= 1
        messages[1] = bytes(packet)

        with self.assertRaisesRegex(ValueError, "packet advertises 1072"):
            decode_preset_messages(messages)

    def test_slot_14_capture_decodes(self) -> None:
        decoded, _ = decode_preset_messages(load_capture(14), expected_slot=14)
        preset = parse_preset(decoded)

        self.assertEqual(preset["name"], "Test Delay")
        self.assertEqual(preset["chain_order"], [9, 0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11])
        self.assertEqual(preset["effects"][9]["algorithm_id"], 0x0B000028)

    def test_slot_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "returned slot 15"):
            decode_preset_messages(load_capture(15), expected_slot=14)

    def test_unknown_messages_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "No Matribox II preset data packets"):
            decode_preset_messages([b"\xF0\x7D\x01\xF7"])

    def test_catalog_controls_use_storage_id(self) -> None:
        decoded, _ = decode_preset_messages(load_capture(15), expected_slot=15)
        catalog_path = FIXTURES / "algorithm.xml"

        preset = parse_preset(decoded, AlgorithmCatalog.from_xml(catalog_path))
        cab = preset["effects"][6]
        self.assertEqual(cab["algorithm_name"], "Test Cabinet")
        values = {control["name"]: control["value"] for control in cab["controls"]}
        self.assertEqual(values["Volume"], 50.0)
        self.assertEqual(values["Low Cut"], 19.0)
        self.assertEqual(values["High Cut"], 20001.0)


if __name__ == "__main__":
    unittest.main()
