# Matribox II preset protocol

Research recorded on November 26, 2026, using the Windows editor version 1.3.1 (x86-64, image base 0x140000000, SHA-256 818aa41c8d8d107308da306bc51f58644e5b15746942aaa5be20a841bd86970f). Observations were made using MIDI captures and static analysis with REA 6.10.1, Ghidra and Rizin. This document describes independently observed interoperability behavior. The published test fixtures contain synthetic data.

## Reading presets

All offsets are zero-based and include the F0 delimiter.

| Offset | Field | Observed value |
| --- | --- | --- |
| 0–7 | SysEx prefix | F0 21 25 7E 47 50 2D 32 |
| 8 | Transfer type | 12 |
| 9–10 | Decoded payload length | 30 09 (1,200 bytes) |
| 11–12 | Fragment offset | Two 7-bit bytes |
| 13 through penultimate byte | Fragment payload | High nibble followed by low nibble for each byte |
| Last | SysEx terminator | F7 |

Length and offset use the expression low OR (high shifted left by 7). The observed read offsets were 0, 185, 370, 555, 740, 925 and 1110. The first six fragments each carry 185 decoded bytes; the last carries 90.

The decoder in [preset.py](../matribox_ii_mcp/preset.py) validates the advertised size and continuous coverage before decoding. [Tests](../tests/test_preset.py) cover out-of-order delivery and inconsistent headers.

The 1,200-byte preset includes a name, slot identifiers, signal-chain order and twelve 72-byte effect blocks starting at offset 120. The final 216 bytes remain opaque.

## Saving the current preset

The editor's Save operation was observed as a 62-byte SysEx frame with type 0x12, length 24 and offset zero. The decoded record has the following layout.

| Record bytes | Field |
| --- | --- |
| 0–3 | 03 20 14 00 |
| 4–5 | Current preset slot, little-endian |
| 6–7 | Zero in the observed capture; full semantics unknown |
| 8–23 | Current preset name, 16 bytes |

The pedal returned a type 0x12 frame containing 11 10 04 00 01 00 00 00.

## Whole-preset transfer

The editor's Paste Preset operation uses the following format, confirmed by a MIDI capture on slot 35-A.

| Field | Observed value |
| --- | --- |
| Transfer type and total length | 0x12, 1,208 decoded bytes |
| Fragment offsets | 0, 183, 366, 549, 732, 915, 1098 |
| First record | 00 00 04 00 01 00 [slot: u16] |
| Second record | 01 00 04 00 [slot: u16] [slot: u16] |
| Remaining data | 1,192 bytes of stored parameters, starting with record 0x0002 |
| Acknowledgment | 10 20 04 00 00 00 00 00, followed by selection and state notifications |

The first six fragments contain 183 bytes each and the last contains 110. The destination slot also appears at offsets 12 and 104 of the read preset. The transfer writes the slot and ends at the final preset record. A hardware test wrote a new name and a control value in one transfer, reread the result and restored the original preset.

A distinct UploadOnePreset flow observed during static analysis uses CRC-32/ISO-HDLC over nibble-expanded data (reflected polynomial 0xEDB88320, initial and final XOR 0xFFFFFFFF; check vector 123456789 produces 0xCBF43926). The Paste Preset transfer implemented here sends records directly.

### Standard .prst file

The standard single-preset export from editor version 1.3.1 contains 1,259 bytes.

| Offset | Field |
| --- | --- |
| 0 | CUSTOM_FLAGTSRP magic |
| Header | Product identifier 2-PG |
| 43–46 | Parameter start offset, little-endian (55) |
| 47–50 | MRAP section length, little-endian (1,196) |
| 51–54 | MRAP identifier |
| 55–1246 | 1,192 bytes of stored parameters, excluding the first eight bytes of the MIDI read preset |
| 1247–1250 | CRC-32/ISO-HDLC of the parameter data, big-endian |
| 1251–1254 | Checksum position (1,251), little-endian |
| 1255–1258 | Sum of all preceding bytes, big-endian |

The [file codec](../matribox_ii_mcp/preset_files.py) checks both integrity values on import and computes them on export. Files containing embedded IR or NAM assets are outside the supported format.

## MIDI transport

The observed editor transmitter carries up to 183 decoded bytes per frame. Pedal read responses carry up to 185. Both use two 7-bit bytes for length and offset, and two nibbles per payload byte.

The Windows editor uses WinMM long MIDI messages. A 384-byte input frame was observed arriving as 256- and 128-byte pieces. The receiver must reassemble pieces until F7.

## Editing and saving

The following type 0x12 decoded records were observed on slot 35-A (decimal 102). Numeric record fields use little-endian encoding. The target record is 00 00 04 00 01 00 [slot: u16].

| Operation | Decoded record | Observed acknowledgment |
| --- | --- | --- |
| Select or reload | 0x1008, length 4, zero u16 and slot u16 | Echo |
| Set control | Target record followed by 05 00 0C 00 [block: u8] [control: u8] 00 00 [algorithm: u32] [value: f32] | 11 10 04 00 01 00 01 00 |
| Enable or bypass | Target record followed by 15 00 04 00 [block: u8] [state: u8] 00 00 | Matching block and state, followed by two variable bytes |
| Change model | Target record followed by 16 00 08 00 [block: u8] [selector: 3 bytes] [algorithm: u32] | 16 00 08 00 [block: u8] 00 [two variable bytes] [algorithm: u32] |
| Save | 24-byte record 0x2003 with slot and name | 11 10 04 00 01 00 00 00 |

The control index is the storage ID in the installed catalog. Observed indices were 1 for FX1 and CAB Volume, and 0 for FX1 Sustain and AMP Gain.

Previous hardware testing confirmed and reverted changes to FX1 Sustain, AMP Gain, CAB Volume and AMP bypass. Model switching was tested with FX1, AMP and CAB. The observed model selector was 7F 00 00 for FX1 and AMP, and FF FF FF for CAB. Using the FX1 selector for CAB produced no response.

The server selects, edits, saves, reloads and verifies each change. Sending two edits before saving left the second unacknowledged in testing. Changing models can retain unused parameter positions. Final verification matched the backup across the name, chain order and all 864 effect-block bytes. Some opaque header and trailing fields changed between consecutive reads.

## Preset library

The device exposes 198 slots, from 01-A to 66-C. Listing reads stored presets without changing the active slot and returns a pagination cursor. Selection checks the echoed command. Creation builds a neutral 12-block preset, copying updates the destination identifiers, and renaming changes the name. Import requires a destination; restoration defaults to the slot embedded in the file.

Whole-preset writes reread the destination and compare stable fields. If verification detects a mismatch, the server attempts to restore the previous preset and verify it. Recovery cannot be guaranteed if the device becomes unavailable.

Previous stdio hardware testing listed all 198 slots in order, selected 35-B and returned to 35-A. It also created, renamed, copied, imported and restored a preset in 35-A using both editor and server backups. The source slot remained unchanged and the final destination matched the original stable fields.
