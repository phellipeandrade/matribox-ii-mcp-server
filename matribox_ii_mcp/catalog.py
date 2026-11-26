from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


SLOT_CATEGORIES = (
    "FX1",
    "FX2",
    "AMP",
    "SND",
    "RTN",
    "NR",
    "CAB",
    "EQ",
    "MOD",
    "DLY",
    "RVB",
    "VOL",
)


def default_algorithm_xml_path() -> Path:
    configured_path = os.getenv("MATRIBOX_ALGORITHM_XML")
    if configured_path:
        return Path(configured_path).expanduser()

    resource_directory = os.getenv("MATRIBOX_RESOURCE_DIR")
    if resource_directory:
        return Path(resource_directory).expanduser() / "algorithm.xml"

    program_files = Path(os.getenv("ProgramFiles", r"C:\Program Files"))
    return (
        program_files
        / "Sonicake"
        / "Matribox II"
        / "Resource"
        / "Matribox II"
        / "File"
        / "algorithm.xml"
    )


def _parse_decimal(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(Decimal(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_integer(value: str | None) -> int | None:
    number = _parse_decimal(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


@dataclass(frozen=True)
class MenuOption:
    name: str
    value: int

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value}


@dataclass(frozen=True)
class ControlDefinition:
    kind: str
    name: str
    ui_index: int | None
    storage_index: int | None
    default: float | None
    minimum: float | None
    maximum: float | None
    step: float | None
    suffix: str | None
    value_type: str | None
    binding: str | None
    menu_options: tuple[MenuOption, ...]
    attributes: dict[str, str]

    @classmethod
    def from_xml(cls, element: ET.Element) -> ControlDefinition:
        kind_by_tag = {"Knob": "knob", "Switch": "switch", "Combox": "select"}
        options = tuple(
            MenuOption(name=menu.attrib.get("Name", ""), value=int(menu.attrib["ID"]))
            for menu in element.findall("Menu")
            if menu.attrib.get("ID", "").lstrip("-").isdigit()
        )

        return cls(
            kind=kind_by_tag[element.tag],
            name=element.attrib.get("Name", "Unnamed control"),
            ui_index=_parse_integer(element.attrib.get("idx")),
            storage_index=_parse_integer(element.attrib.get("ID")),
            default=_parse_decimal(element.attrib.get("default")),
            minimum=_parse_decimal(element.attrib.get("Dmin")),
            maximum=_parse_decimal(element.attrib.get("Dmax")),
            step=_parse_decimal(element.attrib.get("step")),
            suffix=element.attrib.get("Suffix"),
            value_type=element.attrib.get("valueType"),
            binding=element.attrib.get("bind"),
            menu_options=options,
            attributes=dict(element.attrib),
        )

    def describe_value(self, parameters: tuple[float, ...]) -> dict[str, Any]:
        value = None
        if self.storage_index is not None and 0 <= self.storage_index < len(parameters):
            value = parameters[self.storage_index]

        display_value: str | None = None
        if value is not None and self.menu_options:
            matching_option = next(
                (option for option in self.menu_options if abs(value - option.value) < 0.001),
                None,
            )
            if matching_option is not None:
                display_value = matching_option.name
        elif value is not None and self.suffix:
            display_value = f"{value:g} {self.suffix}"

        return {
            **self.to_dict(),
            "value": value,
            "display_value": display_value,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "ui_index": self.ui_index,
            "storage_index": self.storage_index,
            "default": self.default,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "step": self.step,
            "suffix": self.suffix,
            "value_type": self.value_type,
            "binding": self.binding,
            "menu_options": [option.to_dict() for option in self.menu_options],
            "attributes": self.attributes,
        }


@dataclass(frozen=True)
class AlgorithmDefinition:
    category: str
    name: str
    code: int
    index: int | None
    cab_code: int | None
    controls: tuple[ControlDefinition, ...]
    attributes: dict[str, str]

    @classmethod
    def from_xml(cls, category: str, element: ET.Element) -> AlgorithmDefinition:
        return cls(
            category=category,
            name=element.attrib.get("Name", "Unnamed algorithm"),
            code=int(element.attrib["Code"], 10),
            index=_parse_integer(element.attrib.get("Index")),
            cab_code=_parse_integer(element.attrib.get("CABCode")),
            controls=tuple(
                ControlDefinition.from_xml(control)
                for control in element
                if control.tag in {"Knob", "Switch", "Combox"}
            ),
            attributes=dict(element.attrib),
        )

    def control_values(self, parameters: tuple[float, ...]) -> list[dict[str, Any]]:
        return [control.describe_value(parameters) for control in self.controls]

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "name": self.name,
            "code": self.code,
            "code_hex": f"0x{self.code:08X}",
            "index": self.index,
            "cab_code": self.cab_code,
            "controls": [control.to_dict() for control in self.controls],
            "attributes": self.attributes,
        }


class AlgorithmCatalog:
    def __init__(self, algorithms: list[AlgorithmDefinition], source: Path | None = None):
        self.algorithms = tuple(algorithms)
        self.source = source
        self._by_key = {(item.category, item.code): item for item in algorithms}

    @classmethod
    def from_xml(cls, path: str | Path | None = None) -> AlgorithmCatalog:
        source = Path(path) if path is not None else default_algorithm_xml_path()
        if not source.is_file():
            raise FileNotFoundError(
                f"algorithm.xml not found at {source}. Set MATRIBOX_ALGORITHM_XML "
                "or MATRIBOX_RESOURCE_DIR to the Matribox II resource directory."
            )

        root = ET.parse(source).getroot()
        algorithms = [
            AlgorithmDefinition.from_xml(category.attrib["Name"], element)
            for category in root.findall("Catalog")
            for element in category.findall("Alg")
        ]
        return cls(algorithms, source=source)

    def get(self, category: str, code: int) -> AlgorithmDefinition | None:
        return self._by_key.get((category.upper(), code))

    def list(
        self,
        category: str | None = None,
        query: str | None = None,
        limit: int = 100,
    ) -> list[AlgorithmDefinition]:
        category_filter = category.upper() if category else None
        query_filter = query.casefold().strip() if query else None
        matches = [
            item
            for item in self.algorithms
            if (category_filter is None or item.category == category_filter)
            and (
                query_filter is None
                or query_filter in item.name.casefold()
                or query_filter in item.category.casefold()
            )
        ]
        return matches[: max(1, min(limit, 500))]
