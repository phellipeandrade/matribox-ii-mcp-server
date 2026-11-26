# Contributing

Contributions are welcome. Keep changes focused, use English for documentation and code, and include regression tests for protocol or preset-format changes.

Use Python 3.11 or later:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python -m build
python -m twine check dist/*
```

The offline tests do not require a pedal or the official editor. When reporting hardware issues, include the editor and firmware versions (if known), Windows version, the affected operation and sanitized logs.

Do not submit proprietary editor binaries, extracted code, vendor catalogs, third-party presets, personal data or credentials. Use synthetic test data and describe independently observed protocol behavior. For hardware changes, back up the target preset first and document the slots and operations tested.

By contributing, you agree to license your contributions under the project's MIT license.
