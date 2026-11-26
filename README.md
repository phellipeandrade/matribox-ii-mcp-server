# Matribox II MCP Server

**Independent, open-source MCP integration for the Sonicake Matribox II.**

MCP server for reading and editing stored presets on a Sonicake Matribox II over USB MIDI. It uses the effect catalog installed with the official Windows application.

## Current capabilities

- Enumerate Windows MIDI input and output devices.
- List the 198 stored presets by bank and slot, and select one by its label.
- Read a stored preset by slot without switching the active preset.
- Create a neutral preset, copy or rename a stored preset, and export, import or restore a standard `.prst`.
- Decode the 12 effect slots and the signal-chain order.
- Resolve each effect against the installed catalog, including knobs, switches, selectors, ranges and menu values.
- Switch amp, cab and effect models, set a named control in the installed model, or enable and bypass an effect block. The server saves the change and rereads the stored preset to verify it.
- Preserve the original header and trailing bytes in the returned preset data for future format work.

The decoder was verified against captures from the connected Matribox II. The write tools were tested over USB MIDI against slot 35-A alongside the installed Windows editor version 1.3.1. The preset payload is 1,200 bytes, with 12 blocks of 72 bytes beginning at offset 120. The trailing 216 bytes are retained as opaque data until their meaning is confirmed.

## Requirements

- Windows 10 or 11
- Python 3.11+
- Sonicake Matribox II connected over USB
- The official Matribox II application installed, to provide `algorithm.xml`

Close the official editor before connecting through this server because the Windows MIDI driver may grant exclusive access to the device.

## Install

```powershell
cd path\to\matribox-ii-mcp-server
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install .
```

The catalog loader searches the standard installation path. If the application is installed elsewhere, set one of these variables before starting the server.

```powershell
$env:MATRIBOX_ALGORITHM_XML = 'C:\path\to\algorithm.xml'
# or
$env:MATRIBOX_RESOURCE_DIR = 'C:\path\to\Matribox II\Resource\Matribox II\File'
```

## Configure Codex

Add this to `%USERPROFILE%\.codex\config.toml`, adjusting the project path and Python executable if needed.

```toml
[mcp_servers.matribox]
command = "C:\\path\\to\\matribox-ii-mcp-server\\.venv\\Scripts\\python.exe"
args = ["-m", "matribox_ii_mcp.server"]
cwd = "C:\\path\\to\\matribox-ii-mcp-server"
```

Restart Codex after changing its MCP configuration.

## Example prompts

Ask your MCP-compatible assistant to list your presets, inspect the available amp models, or help build a clean guitar tone in an explicitly chosen spare slot.

## Hardware and data safety

This project is experimental. Back up a destination preset before changing it. Review tool permissions in your MCP client: the server can overwrite pedal slots and access local preset files. Use a trusted local client and confirm which slot may be modified. Selecting presets can discard unsaved edits. A communication failure can leave the device state uncertain; reconnect and read the target slot before retrying.

## MCP tools

| Tool | Purpose |
|---|---|
| `list_midi_devices` | List MIDI input and output devices |
| `list_effect_categories` | List the 12 effect-chain categories |
| `list_algorithms` | Search the installed effect catalog |
| `get_algorithm` | Inspect an effect and all of its controls |
| `read_preset` | Read and decode a stored preset by slot |
| `list_presets` | List stored names and labels in pages without switching patches |
| `select_preset` | Activate a preset and verify the selection echoed by the pedal |
| `create_preset` | Overwrite a destination slot with a named neutral preset |
| `copy_preset` | Copy a stored preset to another slot, with an optional name |
| `rename_preset` | Rename a stored preset and verify its other fields |
| `export_preset` | Back up a preset to a new local `.prst` file |
| `import_preset` | Load a standard `.prst` into a specified slot |
| `restore_preset` | Load a `.prst` into its original slot or an explicit destination |
| `set_effect_parameter` | Set a control by category and name, then save and verify the stored preset |
| `set_effect_enabled` | Enable or bypass an effect block, then save and verify the stored preset |
| `set_effect_model` | Switch an effect block to a catalog algorithm ID, then save and verify the stored preset |

Example requests after connecting the MCP server:

- `list_midi_devices`
- `list_algorithms(category="AMP", query="Brit")`
- `get_algorithm(category="CAB", name="Brit MD 4x12")`
- `read_preset(slot=15)`
- `list_presets(start_slot="01-A", limit=24)`
- `select_preset(slot="35-A")`
- `export_preset(slot="35-A", file_path="presets/35-A.prst")`
- `create_preset(slot="35-A", name="My Preset")`
- `copy_preset(source_slot="06-A", destination_slot="35-A", name="Plexi Copy")`
- `rename_preset(slot="35-A", name="New Name")`
- `import_preset(file_path="presets/35-A.prst", slot="35-A")`
- `restore_preset(file_path="presets/35-A.prst")`
- `set_effect_parameter(slot=102, category="FX1", parameter="Sustain", value=25)`
- `set_effect_enabled(slot=102, category="AMP", enabled=true)`
- `set_effect_model(slot=102, category="CAB", algorithm_id=167772161)`

Effect editing tools select and reload the requested slot, which discards unsaved edits in the pedal's current edit buffer. Use `read_preset` to look up control names and numeric values, and `list_algorithms` to look up model IDs. The pedal initializes the controls when changing models and can retain values in positions the new model does not use. Slot numbers are zero-based, so `102` refers to `35-A` in the editor.

`list_presets` reads up to 32 slots per call. Use the returned `next_slot` to continue. The preset library and read tools accept labels such as `35-A` or zero-based numbers. Full-preset writes replace the target slot and confirm the stored contents by rereading it. File tools support the standard 1,259-byte single-preset `.prst`. Embedded IR and NAM asset sections require their own transfer and are outside this file format implementation.

## Development and tests

The offline tests use synthetic SysEx responses and a synthetic effect catalog. They do not require a pedal or the official editor.

```powershell
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python -m build
python -m twine check dist/*
```

## Protocol status

The verified read response starts with `F0 21 25 7E 47 50 2D 32 12`. The next four bytes encode the total size and fragment offset as two 7-bit bytes each, least significant first. The decoder sorts the fragments by offset, checks that they cover the full preset, and combines each pair of payload nibbles into a byte. The read request does not modify stored presets.

Model changes, control changes and effect bypass use the editor's individual update and save messages. Whole-preset writes use the editor's paste transfer, with seven fragments and a separate upload acknowledgment. The `.prst` file has a parameter CRC-32 and a file checksum. The seven MIDI frames carry the preset records directly.

See [the preset protocol research notes](research/preset-protocol.md) for the read captures and the write paths traced in the Windows editor.

## Project status and license

Version 0.1.0 is an alpha release. The Windows editor version 1.3.1 was used for protocol observations. Other firmware versions, other Matribox models, and non-Windows USB MIDI backends have not been validated.

This project is independent and is not affiliated with or endorsed by Sonicake. The original project code is available under the [MIT license](LICENSE). Product names identify compatible hardware. The official editor, its catalog, firmware and other vendor assets are not distributed with this project and remain subject to their respective licenses.

See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) and [CHANGELOG.md](CHANGELOG.md).
