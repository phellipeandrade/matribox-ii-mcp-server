import unittest

from matribox_ii_mcp.protocol import (
    build_effect_model_update,
    build_effect_state_update,
    build_parameter_update,
    build_save_preset_request,
    build_select_preset_request,
)


class EditorProtocolTests(unittest.TestCase):
    def test_parameter_update_matches_verified_amp_message(self) -> None:
        expected = bytes.fromhex(
            "f021257e47502d3212180000000000000000040000000100000606000000"
            "050000000c000000020000000000000001000000000007000000000f080401f7"
        )

        self.assertEqual(build_parameter_update(102, 2, 0, 0x07000001, 31), expected)

    def test_effect_state_update_and_reply_match_amp_capture(self) -> None:
        expected = bytes.fromhex(
            "f021257e47502d32121000000000000000000400000001000006060000"
            "01050000000400000002000100000000f7"
        )
        captured_reply = bytes.fromhex("f021257e47502d321208000000010500000004000000020001020e0800f7")

        packet, reply = build_effect_state_update(102, 2, True)

        self.assertEqual(packet, expected)
        self.assertTrue(reply.matches(captured_reply))

    def test_effect_model_update_and_reply_match_fx1_capture(self) -> None:
        expected = bytes.fromhex(
            "f021257e47502d32121400000000000000000400000001000006060000"
            "01060000000800000000070f000000000001000000000000f7"
        )
        captured_reply = bytes.fromhex(
            "f021257e47502d32120c000000010600000008000000000000000000000001000000000000f7"
        )

        packet, reply = build_effect_model_update(102, 0, 1)

        self.assertEqual(packet, expected)
        self.assertTrue(reply.matches(captured_reply))

    def test_cab_model_update_and_reply_match_capture(self) -> None:
        expected = bytes.fromhex(
            "f021257e47502d32121400000000000000000400000001000006060000"
            "010600000008000000060f0f0f0f0f0f000100000000000af7"
        )
        captured_reply = bytes.fromhex(
            "f021257e47502d32120c00000001060000000800000006000009080401000100000000000af7"
        )

        packet, reply = build_effect_model_update(102, 6, 0x0A000001)

        self.assertEqual(packet, expected)
        self.assertTrue(reply.matches(captured_reply))
        _, other_model_reply = build_effect_model_update(102, 6, 0x0A000002)
        self.assertFalse(other_model_reply.matches(captured_reply))

    def test_save_request_matches_editor_capture(self) -> None:
        expected = bytes.fromhex(
            "f021257e47502d32121800000000030200010400000606000000000000040d060107040702"
            "06090602060f070802000409040900000000000000000000f7"
        )

        self.assertEqual(build_save_preset_request(102, b"Matribox II".ljust(16, b"\0")), expected)

    def test_select_request_matches_editor_capture(self) -> None:
        expected = bytes.fromhex("f021257e47502d32120800000000080100000400000000000006060000f7")

        self.assertEqual(build_select_preset_request(102), expected)
