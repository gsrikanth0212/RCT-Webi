#!/usr/bin/env python3
"""
SAP BusinessObjects Web Intelligence (WebI) → Power BI Migration Engine
===========================================================================
Single-file build of the WebI → Power BI migration engine. This exists
alongside — and does not modify, import from, or depend on —
`tableau_pbi_server.py`, which continues to own the Tableau → Power BI path
unchanged.

This file is a merged, workable combination of the following originally
separate modules (kept as a modular multi-file layout elsewhere in this
repo for readability; this file trades that separation for single-file
portability). Section banners below mark where each one starts:

    webi_ir.py                   WebI Intermediate Representation
    webi_input_detector.py       Input format detection + adapter registry
    webi_data_provider_parser.py Data Provider / Result Object / Query Filter parsing
    webi_universe_parser.py      Universe reconstruction
    webi_query_parser.py         Prompt extraction/classification
    webi_parser.py               .wid.xml -> WebIDocument adapter
    webi_formula_parser.py       WebI formula tokenizer + AST parser
    webi_dax_converter.py        AST -> DAX conversion, dependency graph, cycle detection
    webi_semantic_model.py       Power BI tables/columns/measures/relationships
    webi_field_resolver.py       Per-visual field-well resolution
    webi_visual_mapper.py        Block/chartType -> Power BI visualType classification
    webi_layout_mapper.py        WebI report/section/block -> Power BI page/visual layout
    webi_powerbi_generator.py    DataModelSchema / Report-Layout / DiagramLayout + .pbit packaging
    webi_validator.py            Static artifact + semantic validation
    webi_migration_audit.py      Migration audit + quality scoring
    webi_pbi_server.py           Entry point: HTTP+SSE server / CLI, 17-stage pipeline

Pipeline (17 progress stages, reported by process_file() below):

    WebI Input -> Input Detection -> WebI Parsing -> Normalized WebI IR
      -> Semantic Model / Universe / Data Provider / Query Reconstruction
      -> Power BI Data Model Generation -> Formula -> DAX Conversion
      -> Filter / Prompt Conversion -> Visual Classification
      -> Field-Well Resolution -> Power BI Generation -> Layout Reconstruction
      -> Artifact + Semantic Validation -> Migration Audit -> Finalization

Usage:
    python webi_to_powerbi_server.py [--port 8600] [--source ./input] [--target ./output]
        Starts the HTTP/SSE server (same request/response shape as
        tableau_pbi_server.py: /start, /stop, /convert_one, /state, /events).

    python webi_to_powerbi_server.py --once --source ./input --target ./output
        Runs one batch conversion synchronously and exits — no server.

    python webi_to_powerbi_server.py --once --file input/01_simple_table_report.wid.xml --target ./output
        Converts a single file synchronously and exits.

See WEBI_MIGRATION_ARCHITECTURE.md, WEBI_TO_POWERBI_MAPPING.md,
WEBI_SUPPORTED_FEATURES.md, WEBI_UNSUPPORTED_FEATURES.md,
WEBI_MIGRATION_TEST_SUITE.md, and WEBI_MIGRATION_TROUBLESHOOTING.md for the
full design writeup — this file's structure mirrors those docs exactly.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import queue
import re
import sys
import tempfile
import threading
import time
import traceback
import uuid
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field as dc_field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional, Union
from urllib.parse import urlparse


def _guid() -> str:
    return str(uuid.uuid4())


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — WebI Intermediate Representation  (from webi_ir.py)
# ═══════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────
# Universe / Data Provider / Query layer
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class WebIObject:
    """A Universe/query result object: a Dimension, Measure or Detail.

    `id` is the identity used for reference resolution (columns, formulas,
    filters, axes all point at objects by id or by name — WebI is
    inconsistent about which, so the field registry built in
    webi_semantic_model.py indexes both).
    """
    id: str
    name: str
    kind: str = "Dimension"          # Dimension | Measure | Detail
    data_type: str = "Character"     # Character | Number | Date | DateTime | Boolean
    aggregation: str = ""            # Sum | Average | Min | Max | Count | DistinctCount | Median | ...
    qualification: str = ""          # raw WebI qualification attribute, if present
    data_provider_id: str = ""
    universe: str = ""
    format: str = ""                 # display format string, if declared at object level


@dataclass
class WebIQueryFilter:
    object_ref: str
    operator: str = "Equal"          # Equal | NotEqual | InList | NotInList | Between | GreaterThan | LessThan | IsNull | IsNotNull
    values: list = dc_field(default_factory=list)
    is_prompt: bool = False
    prompt_text: str = ""
    prompt_mandatory: bool = True
    prompt_multi_value: bool = False
    scope: str = "Query"             # Query | Report | Section | Block


@dataclass
class WebIDataProvider:
    id: str
    name: str
    type: str = "Universe"           # Universe | FreeHandSQL | OLAP | Other
    universe: str = ""
    connection: str = ""
    sql: str = ""
    objects: list = dc_field(default_factory=list)      # list[WebIObject]
    filters: list = dc_field(default_factory=list)       # list[WebIQueryFilter]

    def object_by_id(self, obj_id: str) -> Optional[WebIObject]:
        for o in self.objects:
            if o.id == obj_id:
                return o
        return None


@dataclass
class WebIUniverse:
    """Reconstructed Universe — derived from the objects observed across
    every Universe-type Data Provider that declares this universe name.
    Real universe metadata (classes, joins, contexts) is not present in
    WebI report exports; when it is available from a separate universe
    export it should be merged in by a future dedicated adapter."""
    name: str
    connection: str = ""
    objects: list = dc_field(default_factory=list)       # list[WebIObject], de-duplicated by name
    data_provider_ids: list = dc_field(default_factory=list)


@dataclass
class WebIMergedDimensionSource:
    data_provider_id: str
    object_ref: str


@dataclass
class WebIMergedDimension:
    name: str
    sources: list = dc_field(default_factory=list)       # list[WebIMergedDimensionSource]


# ─────────────────────────────────────────────────────────────────────────
# Calculations
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class WebIVariable:
    """A WebI report/document-level Variable (calculated object)."""
    id: str
    name: str
    data_type: str = "Character"
    formula: str = ""
    scope: str = "Document"          # Document | Report | Section | Block | DataProvider

    # populated by webi_dax_converter.py
    dependencies: list = dc_field(default_factory=list)     # referenced object/variable refs
    dax_expression: str = ""
    dax_target: str = "measure"      # measure | calculated_column
    dax_status: str = "PENDING"      # SUCCESS | PARTIAL | APPROXIMATED | UNSUPPORTED | FAILED | PENDING
    dax_confidence: float = 0.0
    dax_notes: list = dc_field(default_factory=list)


@dataclass
class WebIInputControl:
    id: str
    type: str = "ComboBox"           # ComboBox | DateRangeSlider | CheckboxList | RadioButtons | ...
    bound_object: str = ""
    label: str = ""


# ─────────────────────────────────────────────────────────────────────────
# Report structure: sections, blocks, columns, charts
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class WebIColumnRef:
    """A resolved-ish reference to a field used in a block/axis/sort/etc."""
    ref: str                          # raw ref token: object id, "DPID.objid", merged-dim name, or variable id
    ref_kind: str = "object"          # object | dp_object | merged | variable
    header: str = ""
    format: str = ""


@dataclass
class WebISort:
    ref: WebIColumnRef
    direction: str = "Ascending"      # Ascending | Descending


@dataclass
class WebIAggregate:
    ref: WebIColumnRef
    function: str = "Sum"


@dataclass
class WebIConditionalFormat:
    applies_to: str
    condition: str
    style: str = ""


@dataclass
class WebIChartAxis:
    role: str                         # Category | Value | SecondaryValue | X | Y | Size
    ref: Optional[WebIColumnRef] = None
    chart_element: str = ""           # Bar | Line — combo-chart axis role


@dataclass
class WebISectionFooterCell:
    ref: WebIColumnRef
    function: str = ""
    label: str = ""


@dataclass
class WebIBlock:
    id: str
    type: str                         # Table | CrossTab | Chart | KPI | FreeCell
    name: str = ""
    chart_type: str = ""              # Line | StackedBar | Pie | ComboBarLine | ...
    position: dict = dc_field(default_factory=dict)   # {'x':int,'y':int,'w':int,'h':int} if declared

    # Table
    columns: list = dc_field(default_factory=list)     # list[WebIColumnRef]
    sorts: list = dc_field(default_factory=list)        # list[WebISort]
    footer_aggregates: list = dc_field(default_factory=list)  # list[WebIAggregate]
    conditional_formats: list = dc_field(default_factory=list)  # list[WebIConditionalFormat]

    # CrossTab
    row_axis: Optional[WebIColumnRef] = None
    column_axis: Optional[WebIColumnRef] = None
    body: Optional[WebIAggregate] = None

    # Chart
    axes: list = dc_field(default_factory=list)          # list[WebIChartAxis]
    series_by: Optional[WebIColumnRef] = None
    slice_ref: Optional[WebIColumnRef] = None          # Pie
    value_ref: Optional[WebIColumnRef] = None          # Pie
    legend_position: str = ""
    data_labels: str = ""

    # KPI
    metric: Optional[WebIAggregate] = None
    comparison_ref: Optional[WebIColumnRef] = None
    comparison_style: str = ""

    # FreeCell
    content: str = ""


@dataclass
class WebISection:
    id: str
    break_on: str = "none"            # "none" or an object/variable ref
    new_page_per_break: bool = False
    header_cells: list = dc_field(default_factory=list)   # list[WebIColumnRef]
    footer_cells: list = dc_field(default_factory=list)    # list[WebISectionFooterCell]
    blocks: list = dc_field(default_factory=list)          # list[WebIBlock]
    subsections: list = dc_field(default_factory=list)     # list[WebISection] — nested breaks


@dataclass
class WebIPageSetup:
    orientation: str = "Landscape"
    size: str = "A4"


@dataclass
class WebIReport:
    name: str
    tab_order: int = 1
    page_setup: WebIPageSetup = dc_field(default_factory=WebIPageSetup)
    sections: list = dc_field(default_factory=list)        # list[WebISection] (top-level)


@dataclass
class WebIDocument:
    name: str
    cuid: str = ""
    last_refresh: str = ""

    data_providers: list = dc_field(default_factory=list)      # list[WebIDataProvider]
    universes: list = dc_field(default_factory=list)            # list[WebIUniverse]
    merged_dimensions: list = dc_field(default_factory=list)    # list[WebIMergedDimension]
    variables: list = dc_field(default_factory=list)            # list[WebIVariable]
    input_controls: list = dc_field(default_factory=list)       # list[WebIInputControl]
    reports: list = dc_field(default_factory=list)              # list[WebIReport]

    source_path: str = ""
    source_format: str = ""

    def all_blocks(self):
        """Yield (report, section, block) for every block in the document,
        walking nested subsections depth-first."""
        for report in self.reports:
            def _walk(section):
                for b in section.blocks:
                    yield section, b
                for sub in section.subsections:
                    yield from _walk(sub)
            for section in report.sections:
                for sec, blk in _walk(section):
                    yield report, sec, blk

    def all_sections(self):
        for report in self.reports:
            def _walk(section):
                yield section
                for sub in section.subsections:
                    yield from _walk(sub)
            for section in report.sections:
                yield from _walk(section)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — WebI Input Detection  (from webi_input_detector.py)
# ═══════════════════════════════════════════════════════════════════════════

class WebIInputError(Exception):
    """Raised when input cannot be classified or read. Callers should
    surface this as an INPUT_ERROR (see webi_pbi_server.py error taxonomy)."""


@dataclass
class DetectedInput:
    path: Path
    format: str            # e.g. "wid_xml"
    root_tag: str = ""


def _sniff_root_tag(path: Path) -> Optional[str]:
    """Read only enough of the file to find the root element tag, so
    detection stays cheap even for large exports."""
    try:
        for event, elem in ET.iterparse(str(path), events=("start",)):
            tag = elem.tag.split("}")[-1]  # strip namespace if present
            return tag
    except ET.ParseError:
        return None
    except OSError:
        return None
    return None


# Root-tag → format-id. A given WebI export format is identified by its XML
# document root element, not by file extension (extensions like .wid.xml
# are a convention, not a guarantee).
_ROOT_TAG_FORMATS = {
    "WebIDocument": "wid_xml",
}


def detect_file(path: Path) -> DetectedInput:
    if not path.exists():
        raise WebIInputError(f"Input path does not exist: {path}")
    if path.is_dir():
        raise WebIInputError(f"detect_file() called on a directory: {path}")

    suffix = path.suffix.lower()
    if suffix not in (".xml",) and not path.name.lower().endswith(".wid.xml"):
        raise WebIInputError(
            f"Unrecognized WebI input extension for {path.name} "
            f"(expected .wid.xml or .xml)"
        )

    root_tag = _sniff_root_tag(path)
    if root_tag is None:
        raise WebIInputError(f"Could not parse XML root of {path.name} — not valid XML")

    fmt = _ROOT_TAG_FORMATS.get(root_tag)
    if fmt is None:
        raise WebIInputError(
            f"{path.name}: root element <{root_tag}> is not a recognized "
            f"WebI input format. Recognized roots: {sorted(_ROOT_TAG_FORMATS)}"
        )
    return DetectedInput(path=path, format=fmt, root_tag=root_tag)


def discover_inputs(source_dir: Path) -> list:
    """Scan a source folder for every file that looks like a WebI input,
    skipping anything that doesn't classify (logged by the caller, not
    fatal for the whole batch — mirrors the existing Tableau engine's
    tolerant folder-scan behavior)."""
    found = []
    if not source_dir.is_dir():
        raise WebIInputError(f"Source folder not found: {source_dir}")
    candidates = sorted(
        [p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() == ".xml"]
    )
    for p in candidates:
        try:
            found.append(detect_file(p))
        except WebIInputError:
            continue
    return found


# ─── Adapter registry ──────────────────────────────────────────────────────
# Populated once, right after parse_wid_xml() is defined further down in
# this file (single-file build — no import-cycle concern here, unlike the
# original multi-module layout this was merged from).
_ADAPTERS: dict = {}


def get_adapter(fmt: str) -> Callable[[Path], "object"]:
    adapter = _ADAPTERS.get(fmt)
    if adapter is None:
        raise WebIInputError(f"No parser adapter registered for format '{fmt}'")
    return adapter


def parse_input(path: Path):
    """End-to-end: detect + parse a single WebI input file into a WebIDocument."""
    detected = detect_file(path)
    adapter = get_adapter(detected.format)
    doc = adapter(detected.path)
    doc.source_path = str(detected.path)
    doc.source_format = detected.format
    return doc


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — Data Provider / Query Parsing  (from webi_data_provider_parser.py)
# ═══════════════════════════════════════════════════════════════════════════
_PROMPT_PREFIX = "@Prompt("


def _classify_kind(obj_el: ET.Element) -> str:
    explicit = obj_el.get("qualification") or obj_el.get("type")
    if explicit in ("Dimension", "Measure", "Detail"):
        return explicit
    data_type = obj_el.get("dataType", "Character")
    has_agg = bool(obj_el.get("aggregation"))
    if has_agg and data_type in ("Number",):
        return "Measure"
    return "Dimension"


def _parse_result_objects(dp_el: ET.Element, dp_id: str, universe: str) -> list:
    objects = []
    ro_el = dp_el.find("ResultObjects")
    if ro_el is None:
        return objects
    for obj_el in ro_el.findall("Object"):
        objects.append(WebIObject(
            id=obj_el.get("id", obj_el.get("name", "")),
            name=obj_el.get("name", ""),
            kind=_classify_kind(obj_el),
            data_type=obj_el.get("dataType", "Character"),
            aggregation=obj_el.get("aggregation", ""),
            qualification=obj_el.get("qualification", ""),
            data_provider_id=dp_id,
            universe=universe,
            format=obj_el.get("format", ""),
        ))
    return objects


def _parse_prompt(value_text: str):
    """Detect WebI `@Prompt('text';'type';...;mono|multi;...)` syntax inside
    a filter value. Real WebI prompt syntax is positional and fairly
    consistent, e.g.:
        @Prompt('Select Year','N','Year',mono,free)
    We extract best-effort: prompt text (first arg) and multi/mono flag."""
    inner = value_text[len(_PROMPT_PREFIX):].rstrip(")")
    parts = [p.strip().strip("'\"") for p in inner.split(",")]
    prompt_text = parts[0] if parts else value_text
    multi = any(p.lower() == "multi" for p in parts)
    mandatory = not any(p.lower() == "optional" for p in parts)
    return prompt_text, multi, mandatory


def _parse_query_filters(dp_el: ET.Element) -> list:
    filters = []
    qf_el = dp_el.find("QueryFilters")
    if qf_el is None:
        return filters
    for filt_el in qf_el.findall("Filter"):
        values = [v.text or "" for v in filt_el.findall("Value")]
        is_prompt = False
        prompt_text, multi, mandatory = "", False, True
        if len(values) == 1 and values[0].strip().startswith(_PROMPT_PREFIX):
            is_prompt = True
            prompt_text, multi, mandatory = _parse_prompt(values[0].strip())
        filters.append(WebIQueryFilter(
            object_ref=filt_el.get("object", ""),
            operator=filt_el.get("operator", "Equal"),
            values=values,
            is_prompt=is_prompt,
            prompt_text=prompt_text,
            prompt_multi_value=multi,
            prompt_mandatory=mandatory,
            scope="Query",
        ))
    return filters


def parse_data_providers(doc_el: ET.Element) -> list:
    """Parse the document's <DataProviders> into a list[WebIDataProvider].
    Each <DataProvider> is treated as exactly one Query (this schema does
    not separate multi-query data providers; when a future input format
    does, extend this into a dedicated Query loop per provider)."""
    providers = []
    dps_el = doc_el.find("DataProviders")
    if dps_el is None:
        return providers
    for dp_el in dps_el.findall("DataProvider"):
        dp_id = dp_el.get("id", dp_el.get("name", ""))
        universe = dp_el.get("universe", "")
        dp = WebIDataProvider(
            id=dp_id,
            name=dp_el.get("name", dp_id),
            type=dp_el.get("type", "Universe"),
            universe=universe,
            connection=dp_el.get("connection", ""),
            objects=_parse_result_objects(dp_el, dp_id, universe),
            filters=_parse_query_filters(dp_el),
        )
        sql_el = dp_el.find("SQL")
        if sql_el is not None and sql_el.text:
            dp.sql = sql_el.text.strip()
        providers.append(dp)
    return providers


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — Universe Reconstruction  (from webi_universe_parser.py)
# ═══════════════════════════════════════════════════════════════════════════

def reconstruct_universes(data_providers: list) -> list:
    registry: dict = {}   # universe name -> WebIUniverse
    for dp in data_providers:
        if dp.type != "Universe" or not dp.universe:
            continue
        uni = registry.get(dp.universe)
        if uni is None:
            uni = WebIUniverse(name=dp.universe, connection=dp.connection)
            registry[dp.universe] = uni
        if dp.id not in uni.data_provider_ids:
            uni.data_provider_ids.append(dp.id)
        existing_names = {o.name for o in uni.objects}
        for obj in dp.objects:
            if obj.name not in existing_names:
                uni.objects.append(obj)
                existing_names.add(obj.name)
    return list(registry.values())


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5 — Prompt Extraction  (from webi_query_parser.py)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class WebIPrompt:
    object_ref: str
    text: str
    data_provider_ids: list = dc_field(default_factory=list)
    mandatory: bool = True
    multi_value: bool = False
    has_input_control: bool = False
    input_control_type: str = ""


def extract_prompts(document) -> list:
    prompts: dict = {}   # object_ref -> WebIPrompt
    for dp in document.data_providers:
        for filt in dp.filters:
            if not filt.is_prompt:
                continue
            key = filt.object_ref
            p = prompts.get(key)
            if p is None:
                p = WebIPrompt(
                    object_ref=key,
                    text=filt.prompt_text or key,
                    mandatory=filt.prompt_mandatory,
                    multi_value=filt.prompt_multi_value,
                )
                prompts[key] = p
            if dp.id not in p.data_provider_ids:
                p.data_provider_ids.append(dp.id)

    for ic in document.input_controls:
        p = prompts.get(ic.bound_object)
        if p is not None:
            p.has_input_control = True
            p.input_control_type = ic.type

    return list(prompts.values())


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6 — WebI Parser: .wid.xml -> WebIDocument  (from webi_parser.py)
# ═══════════════════════════════════════════════════════════════════════════

def _parse_column_ref(el: ET.Element, ref_attr_names=("object", "mergedDimension")) -> WebIColumnRef:
    for attr in ref_attr_names:
        val = el.get(attr)
        if val:
            if attr == "mergedDimension":
                return WebIColumnRef(ref=val, ref_kind="merged",
                                      header=el.get("header", el.get("label", "")),
                                      format=el.get("format", ""))
            if "." in val and not val.startswith("VAR_"):
                return WebIColumnRef(ref=val, ref_kind="dp_object",
                                      header=el.get("header", el.get("label", "")),
                                      format=el.get("format", ""))
            kind = "variable" if val.startswith("VAR_") else "object"
            return WebIColumnRef(ref=val, ref_kind=kind,
                                  header=el.get("header", el.get("label", "")),
                                  format=el.get("format", ""))
    return WebIColumnRef(ref="", ref_kind="object")


def _parse_merged_dimensions(doc_el: ET.Element) -> list:
    merges = []
    md_el = doc_el.find("MergedDimensions")
    if md_el is None:
        return merges
    for merge_el in md_el.findall("Merge"):
        sources = [
            WebIMergedDimensionSource(data_provider_id=src.get("dataProvider", ""),
                                       object_ref=src.get("object", ""))
            for src in merge_el.findall("Source")
        ]
        merges.append(WebIMergedDimension(name=merge_el.get("name", ""), sources=sources))
    return merges


def _parse_variables(doc_el: ET.Element) -> list:
    variables = []
    vars_el = doc_el.find("Variables")
    if vars_el is None:
        return variables
    for var_el in vars_el.findall("Variable"):
        formula_el = var_el.find("Formula")
        variables.append(WebIVariable(
            id=var_el.get("id", var_el.get("name", "")),
            name=var_el.get("name", ""),
            data_type=var_el.get("dataType", "Character"),
            formula=(formula_el.text or "").strip() if formula_el is not None else "",
        ))
    return variables


def _parse_input_controls(doc_el: ET.Element) -> list:
    controls = []
    ics_el = doc_el.find("InputControls")
    if ics_el is None:
        return controls
    for ic_el in ics_el.findall("InputControl"):
        controls.append(WebIInputControl(
            id=ic_el.get("id", ""),
            type=ic_el.get("type", "ComboBox"),
            bound_object=ic_el.get("boundObject", ""),
            label=ic_el.get("label", ""),
        ))
    return controls


def _parse_table_block(block_el: ET.Element, block: WebIBlock) -> None:
    cols_el = block_el.find("Columns")
    if cols_el is not None:
        for col_el in cols_el.findall("Column"):
            block.columns.append(_parse_column_ref(col_el))

    sort_el = block_el.find("SortOrder")
    if sort_el is not None:
        for s_el in sort_el.findall("Sort"):
            block.sorts.append(WebISort(ref=_parse_column_ref(s_el),
                                         direction=s_el.get("direction", "Ascending")))

    footer_el = block_el.find("Footer")
    if footer_el is not None:
        for agg_el in footer_el.findall("Aggregate"):
            ref = WebIColumnRef(ref=agg_el.get("column", ""),
                                 ref_kind="variable" if agg_el.get("column", "").startswith("VAR_") else "object")
            block.footer_aggregates.append(WebIAggregate(ref=ref, function=agg_el.get("function", "Sum")))

    cf_el = block_el.find("ConditionalFormatting")
    if cf_el is not None:
        for rule_el in cf_el.findall("Rule"):
            block.conditional_formats.append(WebIConditionalFormat(
                applies_to=rule_el.get("appliesTo", ""),
                condition=rule_el.get("condition", ""),
                style=rule_el.get("style", ""),
            ))


def _parse_crosstab_block(block_el: ET.Element, block: WebIBlock) -> None:
    row_el = block_el.find("RowAxis")
    if row_el is not None:
        block.row_axis = _parse_column_ref(row_el)
    col_el = block_el.find("ColumnAxis")
    if col_el is not None:
        block.column_axis = _parse_column_ref(col_el)
    body_el = block_el.find("Body")
    if body_el is not None:
        body_ref = _parse_column_ref(body_el)
        body_ref.format = body_el.get("format", body_ref.format)
        block.body = WebIAggregate(ref=body_ref, function=body_el.get("function", "Sum"))
    cf_el = block_el.find("ConditionalFormatting")
    if cf_el is not None:
        for rule_el in cf_el.findall("Rule"):
            block.conditional_formats.append(WebIConditionalFormat(
                applies_to=rule_el.get("appliesTo", ""),
                condition=rule_el.get("condition", ""),
                style=rule_el.get("style", rule_el.get("min", "")),
            ))


def _parse_chart_block(block_el: ET.Element, block: WebIBlock) -> None:
    block.chart_type = block_el.get("chartType", "")
    for axis_el in block_el.findall("Axis"):
        block.axes.append(WebIChartAxis(
            role=axis_el.get("role", "Value"),
            ref=_parse_column_ref(axis_el),
            chart_element=axis_el.get("chartElement", ""),
        ))
    series_el = block_el.find("SeriesBy")
    if series_el is not None:
        block.series_by = _parse_column_ref(series_el)
    slice_el = block_el.find("Slice")
    if slice_el is not None:
        block.slice_ref = _parse_column_ref(slice_el)
    value_el = block_el.find("Value")
    if value_el is not None:
        block.value_ref = _parse_column_ref(value_el)
    legend_el = block_el.find("Legend")
    if legend_el is not None:
        block.legend_position = legend_el.get("position", "")
    labels_el = block_el.find("DataLabels")
    if labels_el is not None:
        block.data_labels = labels_el.get("show", "")


def _parse_kpi_block(block_el: ET.Element, block: WebIBlock) -> None:
    metric_el = block_el.find("Metric")
    if metric_el is not None:
        metric_ref = _parse_column_ref(metric_el)
        metric_ref.format = metric_el.get("format", metric_ref.format)
        block.metric = WebIAggregate(ref=metric_ref, function=metric_el.get("function", "Sum"))
    comp_el = block_el.find("Comparison")
    if comp_el is not None:
        block.comparison_ref = _parse_column_ref(comp_el)
        block.comparison_style = comp_el.get("style", "")


def _parse_position(pos_str: str) -> dict:
    """Parse `position="x=0,y=110,w=630,h=250"` into a dict of ints."""
    out = {}
    if not pos_str:
        return out
    for part in pos_str.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                out[k.strip()] = int(v.strip())
            except ValueError:
                pass
    return out


def _parse_block(block_el: ET.Element) -> WebIBlock:
    block = WebIBlock(
        id=block_el.get("id", ""),
        type=block_el.get("type", "Table"),
        name=block_el.get("name", ""),
        position=_parse_position(block_el.get("position", "")),
    )
    if block.type == "Table":
        _parse_table_block(block_el, block)
    elif block.type == "CrossTab":
        _parse_crosstab_block(block_el, block)
    elif block.type == "Chart":
        _parse_chart_block(block_el, block)
    elif block.type == "KPI":
        _parse_kpi_block(block_el, block)
    elif block.type == "FreeCell":
        content_el = block_el.find("Content")
        block.content = (content_el.text or "").strip() if content_el is not None else ""
    return block


def _parse_section(sec_el: ET.Element) -> WebISection:
    section = WebISection(
        id=sec_el.get("id", ""),
        break_on=sec_el.get("breakOn", "none"),
        new_page_per_break=sec_el.get("newPagePerBreak", "false").lower() == "true",
    )
    header_el = sec_el.find("SectionHeader")
    if header_el is not None:
        for cell_el in header_el.findall("Cell"):
            ref = _parse_column_ref(cell_el)
            ref.header = cell_el.get("label", ref.header)
            section.header_cells.append(ref)

    footer_el = sec_el.find("SectionFooter")
    if footer_el is not None:
        for cell_el in footer_el.findall("Cell"):
            section.footer_cells.append(WebISectionFooterCell(
                ref=_parse_column_ref(cell_el),
                function=cell_el.get("function", ""),
                label=cell_el.get("label", ""),
            ))

    blocks_el = sec_el.find("Blocks")
    if blocks_el is not None:
        for block_el in blocks_el.findall("Block"):
            section.blocks.append(_parse_block(block_el))

    subsections_el = sec_el.find("SubSections")
    if subsections_el is not None:
        for sub_el in subsections_el.findall("Section"):
            section.subsections.append(_parse_section(sub_el))

    return section


def _parse_report(report_el: ET.Element) -> WebIReport:
    page_el = report_el.find("PageSetup")
    page_setup = WebIPageSetup(
        orientation=page_el.get("orientation", "Landscape") if page_el is not None else "Landscape",
        size=page_el.get("size", "A4") if page_el is not None else "A4",
    )
    report = WebIReport(
        name=report_el.get("name", ""),
        tab_order=int(report_el.get("tabOrder", "1") or "1"),
        page_setup=page_setup,
    )
    sections_el = report_el.find("Sections")
    if sections_el is not None:
        for sec_el in sections_el.findall("Section"):
            report.sections.append(_parse_section(sec_el))
    return report


def parse_wid_xml(path: Path) -> WebIDocument:
    tree = ET.parse(str(path))
    root = tree.getroot()
    if root.tag != "WebIDocument":
        raise ValueError(f"Expected <WebIDocument> root, got <{root.tag}>")

    doc = WebIDocument(
        name=root.get("name", path.stem),
        cuid=root.get("cuid", ""),
        last_refresh=root.get("lastRefresh", ""),
    )
    doc.data_providers = parse_data_providers(root)
    doc.universes = reconstruct_universes(doc.data_providers)
    doc.merged_dimensions = _parse_merged_dimensions(root)
    doc.variables = _parse_variables(root)
    doc.input_controls = _parse_input_controls(root)

    reports_el = root.find("Reports")
    if reports_el is not None:
        for report_el in reports_el.findall("Report"):
            doc.reports.append(_parse_report(report_el))

    return doc


_ADAPTERS["wid_xml"] = parse_wid_xml


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7 — WebI Formula Parser  (from webi_formula_parser.py)
# ═══════════════════════════════════════════════════════════════════════════

class FormulaParseError(Exception):
    pass


# ─── AST ────────────────────────────────────────────────────────────────

@dataclass
class Literal:
    value: object
    kind: str   # "number" | "string"


@dataclass
class FieldRef:
    name: str


@dataclass
class BinaryOp:
    op: str
    left: object
    right: object


@dataclass
class UnaryOp:
    op: str
    operand: object


@dataclass
class FuncCall:
    name: str
    args: list = dc_field(default_factory=list)


ASTNode = Union[Literal, FieldRef, BinaryOp, UnaryOp, FuncCall]


# ─── Tokenizer ──────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"""
    (?P<WS>\s+)
  | (?P<FIELD>\[[^\]]*\])
  | (?P<STRING>"(?:[^"\\]|\\.)*")
  | (?P<NUMBER>\d+\.\d+|\d+)
  | (?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<GE>>=) | (?P<LE><=) | (?P<NE><>)
  | (?P<OP>[+\-*/();,><=])
""", re.VERBOSE)


@dataclass
class Token:
    kind: str
    value: str


def tokenize(formula: str) -> list:
    tokens = []
    pos = 0
    n = len(formula)
    while pos < n:
        m = _TOKEN_RE.match(formula, pos)
        if not m:
            raise FormulaParseError(f"Unexpected character {formula[pos]!r} at position {pos}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "WS":
            continue
        tokens.append(Token(kind=kind, value=m.group()))
    return tokens


# ─── Recursive-descent parser ──────────────────────────────────────────
# Grammar (lowest to highest precedence):
#   expr       := comparison
#   comparison := additive ( ('>'|GE|'<'|LE|'='|NE) additive )*
#   additive   := term ( ('+'|'-') term )*
#   term       := postfix ( ('*'|'/') postfix )*
#   postfix    := primary ( IDENT=='ForEach' '(' arglist ')' )*
#   primary    := NUMBER | STRING | FIELD | funccall | '(' expr ')' | '-' primary
#   funccall   := IDENT '(' arglist? ')'
#   arglist    := expr ( ';' expr )*

_COMPARISON_OPS = {">", ">=", "<", "<=", "=", "<>"}


class _Parser:
    def __init__(self, tokens: list):
        self.tokens = tokens
        self.i = 0

    def _peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def _advance(self):
        tok = self._peek()
        self.i += 1
        return tok

    def _expect_op(self, value: str):
        tok = self._peek()
        if tok is None or tok.value != value:
            raise FormulaParseError(f"Expected {value!r}, got {tok.value if tok else 'EOF'}")
        return self._advance()

    def parse(self) -> ASTNode:
        node = self.expr()
        if self._peek() is not None:
            raise FormulaParseError(f"Unexpected trailing token {self._peek().value!r}")
        return node

    def expr(self):
        return self.comparison()

    def comparison(self):
        left = self.additive()
        while True:
            tok = self._peek()
            op = None
            if tok and tok.kind in ("GE", "LE", "NE"):
                op = {"GE": ">=", "LE": "<=", "NE": "<>"}[tok.kind]
            elif tok and tok.kind == "OP" and tok.value in (">", "<", "="):
                op = tok.value
            if op is None:
                break
            self._advance()
            right = self.additive()
            left = BinaryOp(op=op, left=left, right=right)
        return left

    def additive(self):
        left = self.term()
        while True:
            tok = self._peek()
            if tok and tok.kind == "OP" and tok.value in ("+", "-"):
                self._advance()
                right = self.term()
                left = BinaryOp(op=tok.value, left=left, right=right)
            else:
                break
        return left

    def term(self):
        left = self.postfix()
        while True:
            tok = self._peek()
            if tok and tok.kind == "OP" and tok.value in ("*", "/"):
                self._advance()
                right = self.postfix()
                left = BinaryOp(op=tok.value, left=left, right=right)
            else:
                break
        return left

    def postfix(self):
        node = self.primary()
        while True:
            tok = self._peek()
            if tok and tok.kind == "IDENT" and tok.value == "ForEach":
                self._advance()
                self._expect_op("(")
                args = self.arglist()
                self._expect_op(")")
                node = FuncCall(name="ForEach", args=[node] + args)
            else:
                break
        return node

    def primary(self):
        tok = self._peek()
        if tok is None:
            raise FormulaParseError("Unexpected end of formula")

        if tok.kind == "OP" and tok.value == "-":
            self._advance()
            return UnaryOp(op="-", operand=self.primary())

        if tok.kind == "NUMBER":
            self._advance()
            val = float(tok.value) if "." in tok.value else int(tok.value)
            return Literal(value=val, kind="number")

        if tok.kind == "STRING":
            self._advance()
            return Literal(value=tok.value[1:-1].replace('\\"', '"'), kind="string")

        if tok.kind == "FIELD":
            self._advance()
            return FieldRef(name=tok.value[1:-1])

        if tok.kind == "OP" and tok.value == "(":
            self._advance()
            node = self.expr()
            self._expect_op(")")
            return node

        if tok.kind == "IDENT":
            self._advance()
            next_tok = self._peek()
            if next_tok and next_tok.kind == "OP" and next_tok.value == "(":
                self._advance()
                args = self.arglist()
                self._expect_op(")")
                return FuncCall(name=tok.value, args=args)
            # Bare identifier with no call — treat as a field reference
            # (some WebI exports omit brackets around simple names).
            return FieldRef(name=tok.value)

        raise FormulaParseError(f"Unexpected token {tok.value!r}")

    def arglist(self):
        tok = self._peek()
        if tok and tok.kind == "OP" and tok.value == ")":
            return []
        args = [self.expr()]
        while True:
            tok = self._peek()
            if tok and tok.kind == "OP" and tok.value in (";", ","):
                self._advance()
                args.append(self.expr())
            else:
                break
        return args


def parse_formula(formula: str) -> ASTNode:
    """Parse a WebI formula string (with or without leading '=') into an AST."""
    text = formula.strip()
    if text.startswith("="):
        text = text[1:]
    tokens = tokenize(text)
    if not tokens:
        raise FormulaParseError("Empty formula")
    return _Parser(tokens).parse()


def extract_field_refs(node: ASTNode) -> set:
    """Walk the AST collecting every FieldRef name (used to build the
    variable dependency graph in webi_dax_converter.py)."""
    refs: set = set()

    def _walk(n):
        if isinstance(n, FieldRef):
            refs.add(n.name)
        elif isinstance(n, BinaryOp):
            _walk(n.left)
            _walk(n.right)
        elif isinstance(n, UnaryOp):
            _walk(n.operand)
        elif isinstance(n, FuncCall):
            for a in n.args:
                _walk(a)

    _walk(node)
    return refs


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 8 — Formula -> DAX Conversion  (from webi_dax_converter.py)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ResolvedField:
    table: str
    column: str
    is_measure: bool
    data_type: str = "string"


@dataclass
class ConversionContext:
    unresolved: set = dc_field(default_factory=set)
    notes: list = dc_field(default_factory=list)
    approximated: bool = False
    unsupported: bool = False


_COMPARISON_DAX = {">": ">", ">=": ">=", "<": "<", "<=": "<=", "=": "=", "<>": "<>"}

# Functions that map 1:1 onto a DAX function of the same arity/semantics.
_DIRECT_FUNCS = {
    "year": "YEAR", "month": "MONTH", "day": "DAY", "quarter": "QUARTER",
    "weekday": "WEEKDAY", "hour": "HOUR", "minute": "MINUTE", "second": "SECOND",
    "left": "LEFT", "right": "RIGHT", "mid": "MID", "trim": "TRIM",
    "uppercase": "UPPER", "lowercase": "LOWER", "length": "LEN", "len": "LEN",
    "round": "ROUND", "abs": "ABS", "int": "INT", "truncate": "TRUNC",
    "sqrt": "SQRT", "power": "POWER",
}

_AGG_FUNCS = {
    "sum": "SUM", "average": "AVERAGE", "min": "MIN", "max": "MAX",
    "count": "COUNT", "distinctcount": "DISTINCTCOUNT",
    "median": "MEDIAN", "stdev": "STDEV.P", "var": "VAR.P",
}


def _literal_number(node, default=0):
    """Best-effort extraction of a numeric literal, including a unary-minus
    wrapped one (WebI's `-12` parses as UnaryOp('-', Literal(12)))."""
    if isinstance(node, Literal) and node.kind == "number":
        return node.value
    if isinstance(node, UnaryOp) and node.op == "-" and isinstance(node.operand, Literal):
        return -node.operand.value
    return default


def _dax_string_literal(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def _resolve_ref(name: str, resolver, var_lookup: dict, ctx: ConversionContext, active_stack: set) -> str:
    # Prefer variable-to-variable resolution (already-converted sibling variable).
    var = var_lookup.get(name)
    if var is not None:
        if name in active_stack:
            ctx.notes.append(f"Circular reference through '{name}' — treated as unresolved")
            ctx.unresolved.add(name)
            return f"/* CIRCULAR:{name} */"
        if var.dax_status in ("SUCCESS", "PARTIAL", "APPROXIMATED") and var.dax_expression:
            return f"[{_safe_measure_name(var.name)}]"
        ctx.notes.append(f"Referenced variable '{var.name}' has no usable DAX yet (status={var.dax_status})")
        ctx.unresolved.add(name)
        return f"/* UNRESOLVED:{name} */"

    resolved = resolver.resolve(name) if resolver else None
    if resolved is None:
        ctx.unresolved.add(name)
        return f"/* UNRESOLVED:{name} */"
    if resolved.is_measure:
        return f"[{resolved.column}]"
    return f"'{resolved.table}'[{resolved.column}]"


def _safe_measure_name(name: str) -> str:
    return name.replace("[", "(").replace("]", ")")


def _looks_textual(node, resolver, var_lookup) -> bool:
    if isinstance(node, Literal):
        return node.kind == "string"
    if isinstance(node, FieldRef):
        var = var_lookup.get(node.name)
        if var is not None:
            return var.data_type == "Character"
        resolved = resolver.resolve(node.name) if resolver else None
        return bool(resolved and resolved.data_type == "Character")
    return False


def _convert(node, resolver, var_lookup: dict, ctx: ConversionContext, active_stack: set) -> str:
    if isinstance(node, Literal):
        if node.kind == "string":
            return _dax_string_literal(node.value)
        return str(node.value)

    if isinstance(node, FieldRef):
        return _resolve_ref(node.name, resolver, var_lookup, ctx, active_stack)

    if isinstance(node, UnaryOp):
        return f"-{_convert(node.operand, resolver, var_lookup, ctx, active_stack)}"

    if isinstance(node, BinaryOp):
        left = _convert(node.left, resolver, var_lookup, ctx, active_stack)
        right = _convert(node.right, resolver, var_lookup, ctx, active_stack)
        if node.op in _COMPARISON_DAX:
            return f"({left} {_COMPARISON_DAX[node.op]} {right})"
        if node.op == "+":
            textual = _looks_textual(node.left, resolver, var_lookup) or _looks_textual(node.right, resolver, var_lookup)
            return f"({left} & {right})" if textual else f"({left} + {right})"
        return f"({left} {node.op} {right})"

    if isinstance(node, FuncCall):
        return _convert_func(node, resolver, var_lookup, ctx, active_stack)

    ctx.unsupported = True
    ctx.notes.append(f"Unrecognized AST node {type(node).__name__}")
    return "BLANK()"


def _convert_func(node: FuncCall, resolver, var_lookup, ctx: ConversionContext, active_stack: set) -> str:
    name = node.name
    lname = name.lower()
    args_dax = [_convert(a, resolver, var_lookup, ctx, active_stack) for a in node.args]

    if lname == "if":
        if len(args_dax) not in (2, 3):
            ctx.unsupported = True
            ctx.notes.append(f"If() expects 2-3 args, got {len(args_dax)}")
            return "BLANK()"
        return f"IF({', '.join(args_dax)})"

    if lname == "isnull":
        return f"ISBLANK({args_dax[0]})"

    if lname == "not":
        return f"NOT({args_dax[0]})"

    if lname in ("and", "or") and len(args_dax) >= 2:
        fn = "AND" if lname == "and" else "OR"
        acc = args_dax[0]
        for a in args_dax[1:]:
            acc = f"{fn}({acc}, {a})"
        return acc

    if lname in _DIRECT_FUNCS:
        return f"{_DIRECT_FUNCS[lname]}({', '.join(args_dax)})"

    if lname in _AGG_FUNCS:
        return f"{_AGG_FUNCS[lname]}({args_dax[0]})" if args_dax else "BLANK()"

    if lname == "foreach":
        # node.args[0] = base expression already converted (args_dax[0]).
        # Remaining args are context-shifting terms, e.g. RelativeDate(-12)
        # paired with the preceding dimension field. Exact WebI ForEach
        # context-transition semantics (which axis is shifted vs. fixed)
        # cannot be reproduced exactly with a metadata-only migration —
        # emit the closest CALCULATE()-based approximation and flag it.
        ctx.approximated = True
        base = args_dax[0]
        dim_dax = args_dax[1] if len(args_dax) > 1 else None
        if dim_dax and len(node.args) > 2 and isinstance(node.args[2], FuncCall) and node.args[2].name.lower() == "relativedate":
            offset = _literal_number(node.args[2].args[0]) if node.args[2].args else 0
            ctx.notes.append(
                "ForEach(...; RelativeDate(n)) approximated as CALCULATE(..., DATEADD(...)); "
                "offset unit assumed MONTH — verify against the source Universe's date grain."
            )
            return f"CALCULATE({base}, DATEADD({dim_dax}, {offset}, MONTH))"
        ctx.notes.append("ForEach(...) context transition approximated with CALCULATE(); verify manually.")
        return f"CALCULATE({base}, {', '.join(args_dax[1:])})" if len(args_dax) > 1 else f"CALCULATE({base})"

    if lname == "relativedate":
        ctx.approximated = True
        ctx.notes.append("RelativeDate() outside ForEach() has no direct DAX equivalent; passed through as literal offset.")
        return args_dax[0] if args_dax else "0"

    if lname in ("previous",):
        ctx.approximated = True
        ctx.notes.append(
            "Previous() has no row-context equivalent in a metadata-only migration; "
            "approximated as the same-context value. Rebuild with EARLIER()/window "
            "functions once the underlying table's true row order is known."
        )
        return args_dax[0] if args_dax else "BLANK()"

    if lname in ("runningsum", "runningcount", "runningaverage"):
        ctx.approximated = True
        agg = {"runningsum": "SUM", "runningcount": "COUNT", "runningaverage": "AVERAGE"}[lname]
        ctx.notes.append(
            f"{name}() approximated as a plain {agg}(); a true running total needs an "
            f"explicit sort column and CALCULATE(...,FILTER(ALL(...))) — add manually."
        )
        return f"{agg}({args_dax[0]})" if args_dax else "BLANK()"

    if lname in ("forall", "where"):
        ctx.approximated = True
        ctx.notes.append(f"{name}() approximated via CALCULATE()/ALL(); verify filter context manually.")
        return f"CALCULATE({args_dax[0]})" if args_dax else "BLANK()"

    if lname == "case":
        ctx.unsupported = True
        ctx.notes.append("Case() formula syntax not recognized by this parser — needs manual conversion to SWITCH().")
        return "BLANK()"

    ctx.unsupported = True
    ctx.notes.append(f"Unknown WebI function '{name}()' — no DAX mapping available.")
    return "BLANK()"


def convert_variable(variable, resolver, var_lookup: dict, active_stack: Optional[set] = None) -> None:
    """Convert one WebIVariable's formula to DAX in place. `var_lookup` maps
    both variable id and variable name to the WebIVariable objects already
    processed earlier in dependency order (see build_dependency_graph)."""
    active_stack = active_stack or set()
    active_stack.add(variable.id)
    active_stack.add(variable.name)

    ctx = ConversionContext()
    try:
        ast = parse_formula(variable.formula)
    except FormulaParseError as e:
        variable.dax_status = "FAILED"
        variable.dax_notes = [f"Parse error: {e}"]
        variable.dax_expression = ""
        variable.dependencies = []
        return

    variable.dependencies = sorted(extract_field_refs(ast))
    dax = _convert(ast, resolver, var_lookup, ctx, active_stack)

    if ctx.unresolved:
        variable.dax_status = "FAILED"
        variable.dax_expression = ""
        variable.dax_notes = ctx.notes + [f"Unresolved reference(s): {sorted(ctx.unresolved)}"]
        variable.dax_confidence = 0.0
        return

    variable.dax_expression = dax
    variable.dax_notes = ctx.notes
    if ctx.unsupported:
        variable.dax_status = "UNSUPPORTED"
        variable.dax_confidence = 0.3
    elif ctx.approximated:
        variable.dax_status = "APPROXIMATED"
        variable.dax_confidence = 0.7
    else:
        variable.dax_status = "SUCCESS"
        variable.dax_confidence = 1.0


def build_dependency_graph(variables: list):
    """Topologically order variables by their formula dependencies on other
    variables (object/column references are leaves, not part of this
    graph). Returns (order: list[WebIVariable], cycles: list[list[str]])
    — cycle members are excluded from `order` and reported separately so
    a circular calculation never gets silently converted (master §13:
    'circular dependency detection')."""
    by_key: dict = {}
    for v in variables:
        by_key[v.id] = v
        by_key[v.name] = v

    adjacency: dict = {v.id: set() for v in variables}
    for v in variables:
        try:
            ast = parse_formula(v.formula)
            refs = extract_field_refs(ast)
        except FormulaParseError:
            refs = set()
        for r in refs:
            target = by_key.get(r)
            if target is not None and target.id != v.id:
                adjacency[v.id].add(target.id)

    WHITE, GRAY, BLACK = 0, 1, 2
    color = {v.id: WHITE for v in variables}
    order: list = []
    cycles: list = []
    id_to_var = {v.id: v for v in variables}

    def dfs(node_id, stack):
        color[node_id] = GRAY
        stack.append(node_id)
        for dep in adjacency[node_id]:
            if color[dep] == WHITE:
                dfs(dep, stack)
            elif color[dep] == GRAY:
                cycle_start = stack.index(dep)
                cycles.append(stack[cycle_start:] + [dep])
        stack.pop()
        color[node_id] = BLACK
        order.append(node_id)

    for v in variables:
        if color[v.id] == WHITE:
            dfs(v.id, [])

    cyclic_ids = {node for cyc in cycles for node in cyc}
    ordered_vars = [id_to_var[i] for i in order if i not in cyclic_ids]
    return ordered_vars, cycles


def convert_all_variables(variables: list, resolver) -> None:
    """Convert every variable in the document to DAX, in dependency order,
    so a variable referencing another variable sees its already-converted
    DAX measure name. Variables involved in a circular dependency are
    marked FAILED without attempting conversion."""
    ordered, cycles = build_dependency_graph(variables)
    by_key = {v.id: v for v in variables}
    by_key.update({v.name: v for v in variables})

    cyclic_ids = {node for cyc in cycles for node in cyc}
    for v in variables:
        if v.id in cyclic_ids:
            v.dax_status = "FAILED"
            v.dax_expression = ""
            v.dax_notes = [f"Circular dependency detected: {' -> '.join(cyc)}" for cyc in cycles if v.id in cyc]
            v.dax_confidence = 0.0

    for v in ordered:
        convert_variable(v, resolver, by_key)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 9 — Power BI Semantic Model  (from webi_semantic_model.py)
# ═══════════════════════════════════════════════════════════════════════════
CALCULATIONS_TABLE = "Calculations"

_AGG_MAP = {
    "sum": "SUM", "average": "AVERAGE", "min": "MIN", "max": "MAX",
    "count": "COUNT", "distinctcount": "DISTINCTCOUNT", "median": "MEDIAN",
    "stddev": "STDEV.P", "variance": "VAR.P",
}

# A measure can never share its table's column name in a Tabular model, so
# the DAX measure gets a semantic prefix (matching common Power BI naming
# convention) rather than an arbitrary " 2" disambiguation suffix.
_AGG_PREFIX = {
    "SUM": "Total ", "AVERAGE": "Average ", "MIN": "Min ", "MAX": "Max ",
    "COUNT": "Count of ", "DISTINCTCOUNT": "Distinct Count of ",
    "MEDIAN": "Median ", "STDEV.P": "Std Dev of ", "VAR.P": "Variance of ",
}

_DATATYPE_MAP = {
    "Number": "double", "Character": "string", "Date": "dateTime",
    "DateTime": "dateTime", "Boolean": "boolean",
}

_M_TYPE_MAP = {
    "double": "number", "string": "text", "dateTime": "datetime", "boolean": "logical",
}

_INVALID_NAME_CHARS = re.compile(r"[\[\]'\"\r\n\t]")
_M_SIMPLE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_pbi_name(name: str, fallback: str = "Field") -> str:
    cleaned = _INVALID_NAME_CHARS.sub(" ", name or "").strip()
    return cleaned or fallback


def _m_quote_identifier(name: str) -> str:
    """M language identifiers containing a space or any character outside
    [A-Za-z0-9_] (or starting with a digit) MUST be quoted as #"Name" —
    an unquoted `Product Line = text` inside a `type table [...]`
    declaration is a syntax error that Power BI Desktop's Mashup/Power
    Query engine rejects outright (surfaces as "This file is corrupted"
    when opening the .pbit). Every WebI object name with a space — which
    is the normal case — needs this."""
    if _M_SIMPLE_IDENTIFIER.match(name):
        return name
    return '#"' + name.replace('"', '""') + '"'


def _unique_name(base: str, seen: set) -> str:
    name = base
    n = 2
    while name in seen:
        name = f"{base} {n}"
        n += 1
    seen.add(name)
    return name


def _pbi_datatype(webi_datatype: str) -> str:
    return _DATATYPE_MAP.get(webi_datatype, "string")


def _empty_m_partition(table_name: str, columns: list) -> dict:
    """Empty, correctly-typed Power Query source — see module docstring
    "Scope note" for why this migration does not fabricate row data."""
    if columns:
        type_decl = ", ".join(
            f"{_m_quote_identifier(c['name'])} = {_M_TYPE_MAP.get(c['dataType'], 'text')}.Type"
            for c in columns
        )
        source = f"#table(type table [{type_decl}], {{}})"
    else:
        source = "#table(type table [_Placeholder = text.Type], {})"
    return {
        "name": table_name,
        "mode": "import",
        "source": {
            "type": "m",
            "expression": ["let", f"    Source = {source}", "in", "    Source"],
        },
    }


class FieldRegistry:
    """Object/variable ref → ResolvedField lookup. Satisfies the
    `resolver.resolve(name)` protocol expected by webi_dax_converter.py
    and reused by webi_field_resolver.py to place fields into visuals."""

    def __init__(self):
        self._index: dict = {}
        self.ambiguous: list = []

    def register(self, key: str, resolved: ResolvedField, allow_overwrite: bool = False) -> None:
        if not key:
            return
        if key in self._index and not allow_overwrite:
            existing = self._index[key]
            if (existing.table, existing.column) != (resolved.table, resolved.column):
                self.ambiguous.append(key)
            return
        self._index[key] = resolved

    def resolve(self, name: str):
        return self._index.get(name)


@dataclass
class SemanticModel:
    tables: list = dc_field(default_factory=list)
    relationships: list = dc_field(default_factory=list)
    registry: FieldRegistry = None
    notes: list = dc_field(default_factory=list)
    warnings: list = dc_field(default_factory=list)


def _build_object_column(obj, seen_col_names: set) -> dict:
    col_name = _unique_name(_safe_pbi_name(obj.name, obj.id), seen_col_names)
    return {
        "name": col_name,
        "lineageTag": _guid(),
        "dataType": _pbi_datatype(obj.data_type),
        "sourceColumn": col_name,
        "summarizeBy": "none",
        "annotations": [{"name": "SummarizationSetBy", "value": "Automatic"}],
    }


def _build_dp_table(dp, seen_table_names: set, registry: FieldRegistry, warnings: list) -> dict:
    table_name = _unique_name(_safe_pbi_name(dp.name, dp.id), seen_table_names)
    columns = []
    measures = []
    seen_col_names: set = set()

    for obj in dp.objects:
        col = _build_object_column(obj, seen_col_names)
        columns.append(col)

        if obj.kind == "Measure":
            agg_key = (obj.aggregation or "sum").lower()
            dax_agg = _AGG_MAP.get(agg_key)
            if dax_agg is None:
                warnings.append(
                    f"Data Provider '{dp.name}': measure '{obj.name}' has unrecognized "
                    f"aggregation '{obj.aggregation}' — defaulted to SUM."
                )
                dax_agg = "SUM"
            measure_base = f"{_AGG_PREFIX.get(dax_agg, '')}{col['name']}".strip()
            measure_name = _unique_name(measure_base, seen_col_names | {m["name"] for m in measures})
            measure = {
                "name": measure_name,
                "lineageTag": _guid(),
                "expression": f"{dax_agg}('{table_name}'[{col['name']}])",
            }
            if obj.format:
                measure["formatString"] = obj.format
            measures.append(measure)
            resolved = ResolvedField(table=table_name, column=measure_name, is_measure=True,
                                      data_type=obj.data_type)
        else:
            resolved = ResolvedField(table=table_name, column=col["name"], is_measure=False,
                                      data_type=obj.data_type)

        registry.register(obj.id, resolved)
        registry.register(obj.name, resolved)
        registry.register(f"{dp.id}.{obj.id}", resolved, allow_overwrite=True)

    table = {
        "name": table_name,
        "lineageTag": _guid(),
        "columns": columns,
        "partitions": [_empty_m_partition(table_name, columns)],
        "annotations": [
            {"name": "WebI_DataProviderId", "value": dp.id},
            {"name": "WebI_DataProviderType", "value": dp.type},
        ],
    }
    if measures:
        table["measures"] = measures
    return table, table_name


def _build_merged_dimension_bridge(merge, dp_table_names: dict, registry: FieldRegistry,
                                    seen_table_names: set, notes: list, warnings: list):
    col_name = _safe_pbi_name(merge.name)
    table_name = _unique_name(col_name, seen_table_names)

    data_type = "string"
    relationships = []
    resolved_sources = 0
    for src in merge.sources:
        dp_table = dp_table_names.get(src.data_provider_id)
        if dp_table is None:
            warnings.append(
                f"Merged dimension '{merge.name}': source Data Provider "
                f"'{src.data_provider_id}' not found — this source is skipped."
            )
            continue
        src_field = registry.resolve(f"{src.data_provider_id}.{src.object_ref}") or registry.resolve(src.object_ref)
        if src_field is None:
            warnings.append(
                f"Merged dimension '{merge.name}': source object '{src.object_ref}' "
                f"in Data Provider '{src.data_provider_id}' could not be resolved — this source is skipped."
            )
            continue
        data_type = src_field.data_type
        relationships.append({
            "name": _guid(),
            "fromTable": dp_table,
            "fromColumn": src_field.column,
            "toTable": table_name,
            "toColumn": col_name,
            "fromCardinality": "many",
            "toCardinality": "one",
            "crossFilteringBehavior": "single",
            "isActive": True,
        })
        resolved_sources += 1

    columns = [{
        "name": col_name,
        "lineageTag": _guid(),
        "dataType": _pbi_datatype(data_type),
        "sourceColumn": col_name,
        "summarizeBy": "none",
    }]
    table = {
        "name": table_name,
        "lineageTag": _guid(),
        "columns": columns,
        "partitions": [_empty_m_partition(table_name, columns)],
        "annotations": [{"name": "WebI_MergedDimension", "value": merge.name}],
    }

    notes.append(
        f"Merged dimension '{merge.name}' → bridge table '{table_name}' with "
        f"{resolved_sources}/{len(merge.sources)} source relationship(s) "
        f"(many-to-one, single-direction filtering from the bridge). This is the "
        f"closest valid Power BI model for a WebI cross-Data-Provider merge; if the "
        f"real merge used non-matching key granularity, review the relationships."
    )
    if resolved_sources < len(merge.sources):
        notes.append(
            f"Merged dimension '{merge.name}': {len(merge.sources) - resolved_sources} "
            f"source(s) could not be linked — see warnings."
        )

    resolved = ResolvedField(table=table_name, column=col_name, is_measure=False, data_type=data_type)
    registry.register(merge.name, resolved, allow_overwrite=True)
    return table, relationships


def _build_calculations_table(document, registry: FieldRegistry, notes: list) -> dict:
    convert_all_variables(document.variables, registry)

    measures = []
    seen_names: set = set()
    for var in document.variables:
        if var.dax_status in ("SUCCESS", "PARTIAL", "APPROXIMATED") and var.dax_expression:
            m_name = _unique_name(_safe_pbi_name(var.name, var.id), seen_names)
            measure = {
                "name": m_name,
                "lineageTag": _guid(),
                "expression": var.dax_expression,
                "annotations": [{"name": "WebI_Variable", "value": var.id}],
            }
            measures.append(measure)
            registry.register(var.id, ResolvedField(table=CALCULATIONS_TABLE, column=m_name,
                                                      is_measure=True, data_type=var.data_type),
                               allow_overwrite=True)
            registry.register(var.name, ResolvedField(table=CALCULATIONS_TABLE, column=m_name,
                                                        is_measure=True, data_type=var.data_type),
                               allow_overwrite=True)
            notes.append(f"Variable '{var.name}' → measure [{m_name}] "
                         f"(status={var.dax_status}, confidence={var.dax_confidence:.1f})")
        else:
            notes.append(
                f"Variable '{var.name}' NOT migrated to a Power BI measure "
                f"(status={var.dax_status}): {'; '.join(var.dax_notes) or 'see dax_notes'}"
            )

    columns = [{
        "name": "_Placeholder",
        "lineageTag": _guid(),
        "dataType": "string",
        "sourceColumn": "_Placeholder",
        "summarizeBy": "none",
        "isHidden": True,
    }]
    table = {
        "name": CALCULATIONS_TABLE,
        "lineageTag": _guid(),
        "columns": columns,
        "partitions": [_empty_m_partition(CALCULATIONS_TABLE, columns)],
        "annotations": [{"name": "WebI_Role", "value": "CalculatedVariables"}],
    }
    if measures:
        table["measures"] = measures
    return table


def build_semantic_model(document) -> SemanticModel:
    registry = FieldRegistry()
    notes: list = []
    warnings: list = []
    seen_table_names: set = set()
    tables = []
    dp_table_names: dict = {}

    for dp in document.data_providers:
        table, table_name = _build_dp_table(dp, seen_table_names, registry, warnings)
        tables.append(table)
        dp_table_names[dp.id] = table_name

    relationships = []
    for merge in document.merged_dimensions:
        bridge_table, rels = _build_merged_dimension_bridge(
            merge, dp_table_names, registry, seen_table_names, notes, warnings)
        tables.append(bridge_table)
        relationships.extend(rels)

    if document.variables:
        tables.append(_build_calculations_table(document, registry, notes))

    if registry.ambiguous:
        warnings.append(
            f"{len(registry.ambiguous)} field reference(s) had ambiguous names across "
            f"Data Providers and were resolved to their first occurrence: "
            f"{sorted(set(registry.ambiguous))}. Use a Data-Provider-qualified reference "
            f"(DP_ID.OBJ_ID) or a merged dimension to disambiguate."
        )

    return SemanticModel(tables=tables, relationships=relationships, registry=registry,
                          notes=notes, warnings=warnings)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 10 — Visual Field-Well Resolution  (from webi_field_resolver.py)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class FieldPlacement:
    label: str
    table: str
    column: str
    is_measure: bool
    data_type: str = "string"
    format: str = ""
    aggregate_function: str = ""


@dataclass
class BlockFieldResolution:
    wells: dict = dc_field(default_factory=dict)      # role -> list[FieldPlacement]
    unresolved: list = dc_field(default_factory=list)  # raw refs that failed resolution
    notes: list = dc_field(default_factory=list)


def _resolve_field_ref(colref, registry, unresolved: list, header_override: str = ""):
    if colref is None or not colref.ref:
        return None
    resolved = registry.resolve(colref.ref)
    if resolved is None:
        unresolved.append(colref.ref)
        return None
    return FieldPlacement(
        label=header_override or colref.header or resolved.column,
        table=resolved.table,
        column=resolved.column,
        is_measure=resolved.is_measure,
        data_type=resolved.data_type,
        format=colref.format,
    )


def _resolve_agg(agg, registry, unresolved: list):
    if agg is None:
        return None
    placement = _resolve_field_ref(agg.ref, registry, unresolved)
    if placement is not None:
        placement.aggregate_function = agg.function
    return placement


def resolve_block(block, registry) -> BlockFieldResolution:
    res = BlockFieldResolution()

    if block.type == "Table":
        values = []
        for colref in block.columns:
            placement = _resolve_field_ref(colref, registry, res.unresolved)
            if placement is not None:
                values.append(placement)
        if values:
            res.wells["Values"] = values
        if res.unresolved:
            res.notes.append(
                f"{len(res.unresolved)} column reference(s) could not be resolved and were "
                f"omitted from the generated table: {res.unresolved}"
            )
        return res

    if block.type == "CrossTab":
        row = _resolve_field_ref(block.row_axis, registry, res.unresolved)
        col = _resolve_field_ref(block.column_axis, registry, res.unresolved)
        body = _resolve_agg(block.body, registry, res.unresolved)
        if row is not None:
            res.wells["Rows"] = [row]
        if col is not None:
            res.wells["Columns"] = [col]
        if body is not None:
            res.wells["Values"] = [body]
        return res

    if block.type == "Chart":
        if block.chart_type in ("Pie", "Doughnut"):
            legend = _resolve_field_ref(block.slice_ref, registry, res.unresolved)
            values = _resolve_field_ref(block.value_ref, registry, res.unresolved)
            if legend is not None:
                res.wells["Legend"] = [legend]
            if values is not None:
                res.wells["Values"] = [values]
            return res

        role_to_well = {
            "Category": "Category", "Value": "Values", "SecondaryValue": "SecondaryValues",
            "X": "X", "Y": "Y", "Size": "Size",
        }
        for axis in block.axes:
            well_name = role_to_well.get(axis.role)
            if well_name is None:
                res.notes.append(f"Unrecognized chart axis role '{axis.role}' — skipped.")
                continue
            placement = _resolve_field_ref(axis.ref, registry, res.unresolved)
            if placement is not None:
                res.wells.setdefault(well_name, []).append(placement)

        series = _resolve_field_ref(block.series_by, registry, res.unresolved)
        if series is not None:
            res.wells["Legend"] = [series]
        return res

    if block.type == "KPI":
        metric = _resolve_agg(block.metric, registry, res.unresolved)
        if metric is not None:
            res.wells["Values"] = [metric]
        return res

    if block.type == "FreeCell":
        return res

    return res


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 11 — Visual Classification  (from webi_visual_mapper.py)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class VisualClassification:
    visual_type: str
    status: str          # SUPPORTED | APPROXIMATED | UNSUPPORTED
    confidence: float
    notes: list = dc_field(default_factory=list)


_CHART_TYPE_MAP = {
    "Line": ("lineChart", "SUPPORTED", 1.0, []),
    "MultiLine": ("lineChart", "SUPPORTED", 1.0, []),
    "Bar": ("clusteredBarChart", "SUPPORTED", 1.0, []),
    "Column": ("clusteredColumnChart", "SUPPORTED", 1.0, []),
    "ClusteredBar": ("clusteredBarChart", "SUPPORTED", 1.0, []),
    "ClusteredColumn": ("clusteredColumnChart", "SUPPORTED", 1.0, []),
    "StackedBar": ("barChart", "SUPPORTED", 1.0, []),
    "StackedColumn": ("columnChart", "SUPPORTED", 1.0, []),
    "100PercentStackedBar": ("hundredPercentStackedBarChart", "SUPPORTED", 1.0, []),
    "100PercentStackedColumn": ("hundredPercentStackedColumnChart", "SUPPORTED", 1.0, []),
    "Area": ("areaChart", "SUPPORTED", 1.0, []),
    "StackedArea": ("stackedAreaChart", "SUPPORTED", 1.0, []),
    "Pie": ("pieChart", "SUPPORTED", 1.0, []),
    "Doughnut": ("donutChart", "SUPPORTED", 1.0, []),
    "Scatter": ("scatterChart", "SUPPORTED", 1.0, []),
    "Bubble": ("scatterChart", "APPROXIMATED", 0.8,
               ["Bubble chart approximated as scatterChart with Size field well populated; "
                "verify bubble sizing scale in Power BI."]),
    "Treemap": ("treemap", "SUPPORTED", 1.0, []),
    "Waterfall": ("waterfallChart", "SUPPORTED", 1.0, []),
    "Ribbon": ("ribbonChart", "SUPPORTED", 1.0, []),
    "Funnel": ("funnel", "SUPPORTED", 1.0, []),
    "Gauge": ("gauge", "SUPPORTED", 1.0, []),
    "Map": ("map", "SUPPORTED", 1.0, []),
    "FilledMap": ("filledMap", "SUPPORTED", 1.0, []),
    "Histogram": ("columnChart", "APPROXIMATED", 0.6,
                  ["Power BI has no native histogram visual; approximated as a column chart. "
                   "Re-bin the underlying measure manually (e.g. via a grouping column) for a "
                   "true histogram."]),
    "Bullet": ("barChart", "APPROXIMATED", 0.5,
               ["Power BI has no native bullet chart visual; approximated as a bar chart. "
                "Target/threshold bands are not reproduced — add manually or use a certified "
                "custom visual."]),
    "ComboBarLine": ("lineClusteredColumnComboChart", "APPROXIMATED", 0.8,
                      ["Combo chart stacking (stacked vs clustered column) is not distinguishable "
                       "from WebI metadata alone; defaulted to clustered. Verify against the "
                       "original report."]),
    "ComboStackedBarLine": ("lineStackedColumnComboChart", "SUPPORTED", 1.0, []),
}


def classify_block(block) -> VisualClassification:
    if block.type == "Table":
        return VisualClassification("tableEx", "SUPPORTED", 1.0)

    if block.type == "CrossTab":
        notes = []
        for cf in block.conditional_formats:
            if "colorscale" in cf.condition.lower():
                notes.append(
                    "Source cross-tab used a color-scale conditional format (heat-map effect). "
                    "The matrix visual is generated; apply a 'Background color scale' conditional "
                    "formatting rule on the value field in Power BI to reproduce it — this is not "
                    "auto-generated into the .pbit."
                )
        status = "APPROXIMATED" if notes else "SUPPORTED"
        return VisualClassification("pivotTable", status, 0.85 if notes else 1.0, notes)

    if block.type == "Chart":
        mapped = _CHART_TYPE_MAP.get(block.chart_type)
        if mapped is None:
            return VisualClassification(
                "tableEx", "UNSUPPORTED", 0.2,
                [f"Chart type '{block.chart_type}' is not recognized — defaulted to a table so "
                 f"data is not lost; rebuild the visualization manually in Power BI."]
            )
        visual_type, status, confidence, notes = mapped
        return VisualClassification(visual_type, status, confidence, list(notes))

    if block.type == "KPI":
        notes = []
        if block.comparison_ref is not None:
            notes.append(
                "KPI trend/comparison arrow approximated as a plain Card visual (Power BI's "
                "native 'kpi' visual type requires a time-series trend measure, which a "
                "metadata-only migration cannot construct). Recreate as a KPI visual once "
                "connected to real historical data."
            )
        return VisualClassification("card", "APPROXIMATED" if notes else "SUPPORTED",
                                     0.75 if notes else 1.0, notes)

    if block.type == "FreeCell":
        return VisualClassification("textbox", "SUPPORTED", 1.0)

    return VisualClassification(
        "tableEx", "UNSUPPORTED", 0.1,
        [f"Block type '{block.type}' is not recognized — defaulted to a table."]
    )


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 12 — Report Layout Reconstruction  (from webi_layout_mapper.py)
# ═══════════════════════════════════════════════════════════════════════════
PAGE_WIDTH = 1280
PAGE_HEIGHT = 720
MARGIN = 24
INDENT_PER_LEVEL = 28
TEXTBOX_HEIGHT = 32
DEFAULT_BLOCK_WIDTH = 1180
DEFAULT_BLOCK_HEIGHT = 220
FREECELL_HEIGHT = 44


@dataclass
class PlacedVisual:
    block: object            # WebIBlock
    x: int
    y: int
    w: int
    h: int
    is_textbox: bool = False
    text: str = ""


@dataclass
class PageLayout:
    name: str
    width: int
    height: int
    visuals: list = dc_field(default_factory=list)   # list[PlacedVisual]
    notes: list = dc_field(default_factory=list)


def _section_header_text(section) -> str:
    labels = [c.header for c in section.header_cells if c.header]
    return " ".join(labels).strip() or f"[{section.break_on}]"


def _section_footer_text(section) -> str:
    parts = [c.label for c in section.footer_cells if c.label]
    return " ".join(parts).strip()


def _block_dims(block) -> tuple:
    pos = block.position or {}
    if all(k in pos for k in ("x", "y", "w", "h")):
        return pos["x"], pos["y"], pos["w"], pos["h"]
    h = FREECELL_HEIGHT if block.type == "FreeCell" else DEFAULT_BLOCK_HEIGHT
    return None, None, DEFAULT_BLOCK_WIDTH, h


def _flatten_section(section, depth: int, cursor: dict, visuals: list, notes: list) -> None:
    indent = depth * INDENT_PER_LEVEL
    x0 = MARGIN + indent

    if section.header_cells and section.break_on != "none":
        visuals.append(PlacedVisual(block=None, x=x0, y=cursor["y"], w=DEFAULT_BLOCK_WIDTH - indent,
                                     h=TEXTBOX_HEIGHT, is_textbox=True, text=_section_header_text(section)))
        cursor["y"] += TEXTBOX_HEIGHT + MARGIN // 2
        notes.append(
            f"Section break on '{section.break_on}' rendered as a static header label, not a "
            f"repeating per-value section (see WEBI_UNSUPPORTED_FEATURES.md)."
        )

    for block in section.blocks:
        bx, by, bw, bh = _block_dims(block)
        x = bx if bx is not None else x0
        y = by if by is not None else cursor["y"]
        visuals.append(PlacedVisual(block=block, x=x, y=y, w=bw, h=bh))
        cursor["y"] = max(cursor["y"], y + bh + MARGIN)

    for sub in section.subsections:
        _flatten_section(sub, depth + 1, cursor, visuals, notes)

    if section.footer_cells:
        footer_text = _section_footer_text(section)
        if footer_text:
            visuals.append(PlacedVisual(block=None, x=x0, y=cursor["y"], w=DEFAULT_BLOCK_WIDTH - indent,
                                         h=TEXTBOX_HEIGHT, is_textbox=True, text=footer_text))
            cursor["y"] += TEXTBOX_HEIGHT + MARGIN // 2


def layout_report(report) -> PageLayout:
    cursor = {"y": MARGIN}
    visuals: list = []
    notes: list = []
    for section in report.sections:
        _flatten_section(section, 0, cursor, visuals, notes)

    max_x = max([v.x + v.w for v in visuals], default=0)
    width = max(PAGE_WIDTH, max_x + MARGIN)
    height = max(PAGE_HEIGHT, cursor["y"] + MARGIN)

    return PageLayout(name=report.name, width=width, height=height, visuals=visuals, notes=notes)


def layout_document(document) -> list:
    return [layout_report(report) for report in document.reports]


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 13 — Power BI Artifact Generator  (from webi_powerbi_generator.py)
# ═══════════════════════════════════════════════════════════════════════════
SLICER_WIDTH = 200
SLICER_HEIGHT = 90


def _jstr(obj) -> str:
    return json.dumps(obj, separators=(",", ":"))


def _pbi_literal_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


# Abstract field-well role (webi_field_resolver.py) → Power BI internal
# projection role name, per visual type. Visual types not listed here use
# the identity mapping (tableEx/pivotTable/card already use PBI's literal
# role names: Values, Rows, Columns).
_ROLE_ALIASES = {
    "lineChart":        {"Category": "Category", "Legend": "Series", "Values": "Y", "SecondaryValues": "Y2"},
    "areaChart":        {"Category": "Category", "Legend": "Series", "Values": "Y"},
    "stackedAreaChart": {"Category": "Category", "Legend": "Series", "Values": "Y"},
    "barChart":         {"Category": "Category", "Legend": "Series", "Values": "Y"},
    "columnChart":      {"Category": "Category", "Legend": "Series", "Values": "Y"},
    "clusteredBarChart":    {"Category": "Category", "Legend": "Series", "Values": "Y"},
    "clusteredColumnChart": {"Category": "Category", "Legend": "Series", "Values": "Y"},
    "hundredPercentStackedBarChart":    {"Category": "Category", "Legend": "Series", "Values": "Y"},
    "hundredPercentStackedColumnChart": {"Category": "Category", "Legend": "Series", "Values": "Y"},
    "pieChart":  {"Legend": "Category", "Values": "Y"},
    "donutChart": {"Legend": "Category", "Values": "Y"},
    "scatterChart": {"X": "X", "Y": "Y", "Size": "Size", "Legend": "Category", "Category": "Details"},
    "treemap":   {"Category": "Group", "Values": "Values"},
    "waterfallChart": {"Category": "Category", "Values": "Y"},
    "ribbonChart":    {"Category": "Category", "Legend": "Series", "Values": "Y"},
    "funnel":    {"Category": "Category", "Values": "Values"},
    "gauge":     {"Values": "Y"},
    "map":       {"Category": "Category", "Values": "Size"},
    "filledMap": {"Category": "Category", "Values": "Size"},
    "lineClusteredColumnComboChart": {"Category": "Category", "Legend": "Series", "Values": "Y", "SecondaryValues": "Y2"},
    "lineStackedColumnComboChart":   {"Category": "Category", "Legend": "Series", "Values": "Y", "SecondaryValues": "Y2"},
}


def _build_query_and_projections(wells: dict, role_aliases: dict):
    froms: dict = {}
    selects: list = []
    projections: dict = {}
    seen_names: set = set()

    for well_name, placements in wells.items():
        pbi_role = role_aliases.get(well_name, well_name)
        for placement in placements:
            entity = placement.table
            froms[entity] = True
            base_name = f"{entity}.{placement.column}"
            select_name = base_name
            n = 2
            while select_name in seen_names:
                select_name = f"{base_name} ({n})"
                n += 1
            seen_names.add(select_name)

            node_key = "Measure" if placement.is_measure else "Column"
            selects.append({
                node_key: {"Expression": {"SourceRef": {"Entity": entity}}, "Property": placement.column},
                "Name": select_name,
            })
            projections.setdefault(pbi_role, []).append({"queryRef": select_name, "active": True})

    from_list = [{"Name": t, "Entity": t, "Type": 0} for t in froms]
    prototype_query = {"Version": 2, "From": from_list, "Select": selects, "OrderBy": []}
    return prototype_query, projections


def _build_data_visual_container(placed, classification, resolution, z_index: int) -> dict:
    role_aliases = _ROLE_ALIASES.get(classification.visual_type, {})
    prototype_query, projections = _build_query_and_projections(resolution.wells, role_aliases)

    objects = {}
    if placed.block.name:
        objects["title"] = [{
            "properties": {"text": {"expr": {"Literal": {"Value": _pbi_literal_str(placed.block.name)}}}}
        }]

    single_visual = {
        "visualType": classification.visual_type,
        "projections": projections,
        "prototypeQuery": prototype_query,
        "drillFilterOtherVisuals": True,
        "objects": objects,
    }
    config = {
        "name": _guid(),
        "layouts": [{"id": 0, "position": {"x": placed.x, "y": placed.y, "z": z_index,
                                            "width": placed.w, "height": placed.h, "tabOrder": z_index}}],
        "singleVisual": single_visual,
    }
    return {"x": placed.x, "y": placed.y, "z": z_index, "width": placed.w, "height": placed.h,
            "config": _jstr(config)}


def _build_textbox_container(x: int, y: int, w: int, h: int, text: str, z_index: int) -> dict:
    paragraph = {"textRuns": [{"value": text, "textStyle": {"fontSize": "11pt"}}],
                 "horizontalTextAlignment": "left"}
    single_visual = {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": [paragraph]}}]}}
    config = {
        "name": _guid(),
        "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": z_index, "width": w, "height": h,
                                            "tabOrder": z_index}}],
        "singleVisual": single_visual,
    }
    return {"x": x, "y": y, "z": z_index, "width": w, "height": h, "config": _jstr(config)}


def _filter_field_expr(resolved) -> dict:
    node_key = "Measure" if resolved.is_measure else "Column"
    return {node_key: {"Expression": {"SourceRef": {"Entity": resolved.table}}, "Property": resolved.column}}


def _literal(value: str) -> dict:
    stripped = value.strip()
    try:
        float(stripped)
        return {"Literal": {"Value": stripped if "." in stripped else f"{stripped}L"}}
    except ValueError:
        return {"Literal": {"Value": _pbi_literal_str(value)}}


def _condition_for_filter(operator: str, values: list):
    """Master prompt §15: only emit a Power BI filter condition for
    operators this module can translate with confidence (Equal/InList and
    their negations). Anything else is reported, not guessed at, per §36
    ('Detect + Audit + Recommend instead of Guess + Corrupt')."""
    if operator in ("Equal", "InList") and values:
        return {"In": {"Values": [[_literal(v)] for v in values]}}
    if operator in ("NotEqual", "NotInList") and values:
        return {"Not": {"Expression": {"In": {"Values": [[_literal(v)] for v in values]}}}}
    return None


def _build_report_filters(document, registry):
    """Convert non-prompt Query Filters (§15) into Power BI report-level
    filters, applied uniformly across every page of the document. (A
    per-page/per-Data-Provider scope would require tracing which Data
    Providers feed which report — not attempted for a multi-report
    document; see WEBI_UNSUPPORTED_FEATURES.md.)"""
    filters: list = []
    notes: list = []
    for dp in document.data_providers:
        for qf in dp.filters:
            if qf.is_prompt:
                continue
            resolved = registry.resolve(qf.object_ref)
            if resolved is None:
                notes.append(f"Query filter on '{qf.object_ref}' (Data Provider '{dp.name}') "
                              f"could not be resolved to a field — not applied.")
                continue
            condition = _condition_for_filter(qf.operator, qf.values)
            if condition is None:
                notes.append(f"Query filter operator '{qf.operator}' on "
                              f"'{resolved.table}[{resolved.column}]' has no direct Power BI filter "
                              f"mapping — recreate manually as a report filter.")
                continue
            filters.append({
                "name": _guid(),
                "expression": _filter_field_expr(resolved),
                "filterType": 1,
                "type": "Categorical",
                "howCreated": 1,
                "condition": condition,
            })
    return filters, notes


def _build_slicer_container(resolved, x: int, y: int, w: int, h: int, z: int) -> dict:
    select_name = f"{resolved.table}.{resolved.column}"
    node_key = "Measure" if resolved.is_measure else "Column"
    prototype_query = {
        "Version": 2,
        "From": [{"Name": resolved.table, "Entity": resolved.table, "Type": 0}],
        "Select": [{node_key: {"Expression": {"SourceRef": {"Entity": resolved.table}}, "Property": resolved.column},
                     "Name": select_name}],
        "OrderBy": [],
    }
    single_visual = {
        "visualType": "slicer",
        "projections": {"Values": [{"queryRef": select_name, "active": True}]},
        "prototypeQuery": prototype_query,
        "objects": {},
    }
    config = {
        "name": _guid(),
        "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": z, "width": w, "height": h, "tabOrder": z}}],
        "singleVisual": single_visual,
    }
    return {"x": x, "y": y, "z": z, "width": w, "height": h, "config": _jstr(config)}


def build_report_layout(document, semantic_model, pages: list):
    """Returns (layout_dict, generation_notes). `generation_notes` is a
    list of per-block dicts (block id/type, visual type, status,
    confidence, field-resolution notes) consumed by webi_migration_audit.py
    — computed here, once, rather than re-classifying blocks elsewhere."""
    sections = []
    generation_notes: list = []

    report_filters, filter_notes = _build_report_filters(document, semantic_model.registry)
    for n in filter_notes:
        generation_notes.append({"block_id": "", "block_type": "QueryFilter", "visual_type": "",
                                  "status": "UNSUPPORTED", "confidence": 0.0, "notes": [n]})
    filters_json = _jstr(report_filters)

    prompts = extract_prompts(document)
    prompts_by_object = {p.object_ref: p for p in prompts}

    for ordinal, page in enumerate(pages):
        visual_containers = []
        for z, placed in enumerate(page.visuals):
            if placed.is_textbox:
                visual_containers.append(_build_textbox_container(placed.x, placed.y, placed.w, placed.h,
                                                                     placed.text, z))
                continue

            block = placed.block
            if block.type == "FreeCell":
                visual_containers.append(_build_textbox_container(placed.x, placed.y, placed.w, placed.h,
                                                                     block.content or block.name, z))
                generation_notes.append({
                    "block_id": block.id, "block_type": block.type, "visual_type": "textbox",
                    "status": "SUPPORTED", "confidence": 1.0, "notes": [],
                })
                continue

            classification = classify_block(block)
            resolution = resolve_block(block, semantic_model.registry)
            visual_containers.append(_build_data_visual_container(placed, classification, resolution, z))
            generation_notes.append({
                "block_id": block.id, "block_type": block.type, "chart_type": block.chart_type,
                "visual_type": classification.visual_type, "status": classification.status,
                "confidence": classification.confidence,
                "notes": list(classification.notes) + list(resolution.notes),
                "unresolved_fields": resolution.unresolved,
                "field_wells": {k: [p.label for p in v] for k, v in resolution.wells.items()},
            })

        page_height = page.height
        if ordinal == 0 and document.input_controls:
            slicer_y = page_height
            placed_any = False
            for i, ic in enumerate(document.input_controls):
                resolved = semantic_model.registry.resolve(ic.bound_object)
                if resolved is None:
                    generation_notes.append({
                        "block_id": ic.id, "block_type": "InputControl", "visual_type": "",
                        "status": "UNSUPPORTED", "confidence": 0.0,
                        "notes": [f"Input Control '{ic.label or ic.id}' bound to '{ic.bound_object}' "
                                  f"could not be resolved to a field — no slicer generated."],
                    })
                    continue
                x = MARGIN + i * (SLICER_WIDTH + MARGIN)
                visual_containers.append(_build_slicer_container(
                    resolved, x, slicer_y, SLICER_WIDTH, SLICER_HEIGHT, len(visual_containers)))
                placed_any = True
                slicer_notes = [f"WebI Input Control (type '{ic.type}') mapped to a Power BI Slicer."]
                prompt = prompts_by_object.get(ic.bound_object)
                if prompt is not None:
                    slicer_notes.append(
                        f"Also bound to a query prompt ({'multi' if prompt.multi_value else 'single'}-value, "
                        f"{'mandatory' if prompt.mandatory else 'optional'}); mandatory-prompt enforcement "
                        f"and cascading-prompt dependencies are not reproduced by a plain slicer — configure "
                        f"manually if the source relied on them."
                    )
                generation_notes.append({
                    "block_id": ic.id, "block_type": "InputControl", "visual_type": "slicer",
                    "status": "APPROXIMATED", "confidence": 0.8, "notes": slicer_notes,
                })
            if placed_any:
                page_height = slicer_y + SLICER_HEIGHT + MARGIN

        for p in prompts:
            if not p.has_input_control:
                generation_notes.append({
                    "block_id": p.object_ref, "block_type": "Prompt", "visual_type": "",
                    "status": "UNSUPPORTED", "confidence": 0.2,
                    "notes": [f"Query prompt '{p.text}' on '{p.object_ref}' has no associated Input "
                              f"Control in the source document — WebI would have shown it as a modal "
                              f"refresh-time dialog. No persistent slicer was generated; add one "
                              f"manually, or add an Input Control to the source for automatic mapping."],
                })

        sections.append({
            "name": _guid(),
            "displayName": (page.name or f"Page {ordinal + 1}")[:80],
            "ordinal": ordinal,
            "visualContainers": visual_containers,
            "config": _jstr({"objects": {}}),
            "filters": filters_json,
            "height": page_height,
            "width": page.width,
        })
        for n in page.notes:
            generation_notes.append({"block_id": "", "block_type": "Section", "visual_type": "",
                                      "status": "APPROXIMATED", "confidence": 0.5, "notes": [n]})

    layout = {
        "id": 0,
        "resourcePackages": [],
        "config": _jstr({"version": "5.43", "activeSectionIndex": 0,
                          "settings": {"useStylableVisualContainerHeader": True}}),
        "layoutOptimization": 0,
        "publicCustomVisuals": [],
        "sections": sections,
    }
    return layout, generation_notes


def build_data_model_schema(document, semantic_model) -> dict:
    return {
        "name": "SemanticModel",
        "compatibilityLevel": 1567,
        "model": {
            "culture": "en-US",
            "dataAccessOptions": {"legacyRedirects": True, "returnErrorValuesAsNull": True},
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "sourceQueryCulture": "en-US",
            "tables": semantic_model.tables,
            "relationships": semantic_model.relationships,
            "annotations": [
                {"name": "PBI_QueryOrder", "value": _jstr([t["name"] for t in semantic_model.tables])},
                {"name": "__PBI_TimeIntelligenceEnabled", "value": "0"},
                {"name": "PBIDesktopVersion", "value": "WebI-Migration-Engine 1.0"},
            ],
        },
    }


def build_diagram_layout(semantic_model) -> dict:
    nodes = []
    for i, t in enumerate(semantic_model.tables):
        nodes.append({
            "location": {"x": (i % 4) * 260, "y": (i // 4) * 200},
            "nodeIndex": t["name"],
            "nodeLineageTag": t.get("lineageTag", _guid()),
            "size": {"width": 220, "height": 150},
            "zIndex": i,
        })
    return {
        "version": "1.1.0",
        "diagrams": [{
            "ordinal": 0,
            "scrollPosition": {"x": 0, "y": 0},
            "nodes": nodes,
            "name": "All tables",
            "zoomValue": 100,
            "pinKeyFieldsToTop": False,
            "showDataFor": [t["name"] for t in semantic_model.tables],
            "hideKeyFieldsWhenCollapsed": True,
        }],
    }


def write_pbit(document, semantic_model, output_path: Path):
    """End-to-end: build layout from the IR, assemble the three JSON
    parts, and write a `.pbit` zip at `output_path`.

    Returns (generation_notes, schema, layout, diagram) so callers
    (webi_pbi_server.py) can run validation and write the audit without
    re-parsing the artifact just written."""
    pages = layout_document(document)
    layout, generation_notes = build_report_layout(document, semantic_model, pages)
    schema = build_data_model_schema(document, semantic_model)
    diagram = build_diagram_layout(semantic_model)

    content_types = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="json" ContentType=""/>'
        '<Default Extension="xml" ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/>'
        '<Override PartName="/Version" ContentType=""/>'
        '<Override PartName="/DataModelSchema" ContentType=""/>'
        '<Override PartName="/DiagramLayout" ContentType=""/>'
        '<Override PartName="/Report/Layout" ContentType=""/>'
        '<Override PartName="/Settings" ContentType="application/json"/>'
        '<Override PartName="/Metadata" ContentType="application/json"/>'
        '</Types>'
    )
    version_str = "1.21"
    metadata = _jstr({"Version": 5, "AutoCreatedRelationships": [],
                       "CreatedFrom": "WebI Migration Engine", "CreatedFromRelease": "1.0"})
    settings = _jstr({"Version": 4, "ReportSettings": {},
                       "QueriesSettings": {"TypeDetectionEnabled": True, "RelationshipImportEnabled": True}})

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".pbit.tmp", dir=output_path.parent)
    try:
        os.close(tmp_fd)
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", content_types.encode("utf-8"))
            zf.writestr("Version", version_str.encode("utf-16-le"))
            zf.writestr("Metadata", metadata.encode("utf-16-le"))
            zf.writestr("Settings", settings.encode("utf-16-le"))
            zf.writestr("DataModelSchema", _jstr(schema).encode("utf-16-le"))
            zf.writestr("DiagramLayout", _jstr(diagram).encode("utf-16-le"))
            zf.writestr("Report/Layout", _jstr(layout).encode("utf-16-le"))
        os.replace(tmp_path, output_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return generation_notes, schema, layout, diagram


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 14 — Artifact & Semantic Validation  (from webi_validator.py)
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ValidationIssue:
    severity: str   # ERROR | WARNING | INFO
    code: str
    message: str


def has_errors(issues: list) -> bool:
    return any(i.severity == "ERROR" for i in issues)


def _check_guid(tag, ctx, seen_tags: set, issues: list) -> None:
    if not tag:
        issues.append(ValidationIssue("ERROR", "MISSING_GUID", f"Missing lineageTag for {ctx}"))
        return
    if tag in seen_tags:
        issues.append(ValidationIssue("ERROR", "DUPLICATE_GUID", f"Duplicate lineageTag '{tag}' for {ctx}"))
    seen_tags.add(tag)


def validate_artifact(schema: dict, layout: dict) -> list:
    issues: list = []
    model = schema.get("model", {})
    tables = model.get("tables", [])
    table_index = {t["name"]: t for t in tables}

    seen_table_names: set = set()
    seen_tags: set = set()
    for t in tables:
        if t["name"] in seen_table_names:
            issues.append(ValidationIssue("ERROR", "DUPLICATE_TABLE", f"Duplicate table name '{t['name']}'"))
        seen_table_names.add(t["name"])
        _check_guid(t.get("lineageTag"), f"table '{t['name']}'", seen_tags, issues)

        names_in_table: set = set()
        for c in t.get("columns", []):
            _check_guid(c.get("lineageTag"), f"column '{t['name']}[{c['name']}]'", seen_tags, issues)
            if c["name"] in names_in_table:
                issues.append(ValidationIssue("ERROR", "DUPLICATE_FIELD_NAME",
                                               f"Duplicate field name '{c['name']}' in table '{t['name']}'"))
            names_in_table.add(c["name"])
        for m in t.get("measures", []):
            _check_guid(m.get("lineageTag"), f"measure '{t['name']}[{m['name']}]'", seen_tags, issues)
            if m["name"] in names_in_table:
                issues.append(ValidationIssue("ERROR", "DUPLICATE_FIELD_NAME",
                                               f"Measure '{m['name']}' collides with an existing field "
                                               f"name in table '{t['name']}'"))
            names_in_table.add(m["name"])

    rel_seen: set = set()
    for r in model.get("relationships", []):
        for side_table, side_column in (("fromTable", "fromColumn"), ("toTable", "toColumn")):
            tname = r.get(side_table)
            table = table_index.get(tname)
            if table is None:
                issues.append(ValidationIssue("ERROR", "DANGLING_RELATIONSHIP",
                                               f"Relationship references missing table '{tname}'"))
                continue
            col = r.get(side_column)
            if col not in {c["name"] for c in table.get("columns", [])}:
                issues.append(ValidationIssue("ERROR", "DANGLING_RELATIONSHIP",
                                               f"Relationship references missing column '{tname}[{col}]'"))
        key = (r.get("fromTable"), r.get("fromColumn"), r.get("toTable"), r.get("toColumn"))
        if key in rel_seen:
            issues.append(ValidationIssue("WARNING", "DUPLICATE_RELATIONSHIP", f"Duplicate relationship {key}"))
        rel_seen.add(key)

    visual_guids: set = set()
    for section in layout.get("sections", []):
        for vc in section.get("visualContainers", []):
            try:
                config = json.loads(vc["config"])
            except (KeyError, ValueError) as e:
                issues.append(ValidationIssue("ERROR", "INVALID_JSON",
                                               f"visualContainer config is not valid JSON: {e}"))
                continue

            name = config.get("name")
            if name in visual_guids:
                issues.append(ValidationIssue("ERROR", "DUPLICATE_GUID", f"Duplicate visual GUID '{name}'"))
            visual_guids.add(name)

            sv = config.get("singleVisual", {})
            pq = sv.get("prototypeQuery")
            if pq is None:
                continue  # textbox and other non-data visuals

            froms = {f["Name"] for f in pq.get("From", [])}
            select_names: set = set()
            for sel in pq.get("Select", []):
                sname = sel.get("Name")
                select_names.add(sname)
                node = sel.get("Column") or sel.get("Measure")
                if node is None:
                    issues.append(ValidationIssue("ERROR", "INVALID_SELECT",
                                                   f"Visual '{name}' Select entry '{sname}' has neither Column nor Measure"))
                    continue
                entity = node.get("Expression", {}).get("SourceRef", {}).get("Entity")
                prop = node.get("Property")
                if entity not in froms:
                    issues.append(ValidationIssue("ERROR", "MISSING_FROM",
                                                   f"Visual '{name}' Select '{sname}' uses entity '{entity}' not declared in From"))
                table = table_index.get(entity)
                if table is None:
                    issues.append(ValidationIssue("ERROR", "DANGLING_TABLE_REF",
                                                   f"Visual '{name}' Select references missing table '{entity}'"))
                    continue
                names_in_table = ({c["name"] for c in table.get("columns", [])} |
                                   {m["name"] for m in table.get("measures", [])})
                if prop not in names_in_table:
                    issues.append(ValidationIssue("ERROR", "DANGLING_FIELD_REF",
                                                   f"Visual '{name}' Select references missing field '{entity}[{prop}]'"))

            for role, refs in sv.get("projections", {}).items():
                for ref in refs:
                    if ref.get("queryRef") not in select_names:
                        issues.append(ValidationIssue("ERROR", "DANGLING_QUERYREF",
                                                       f"Visual '{name}' role '{role}' references missing "
                                                       f"queryRef '{ref.get('queryRef')}'"))

            if pq.get("Select") and not sv.get("projections"):
                issues.append(ValidationIssue("WARNING", "EMPTY_PROJECTIONS",
                                               f"Visual '{name}' ({sv.get('visualType')}) has fields selected "
                                               f"but no field wells populated"))

    return issues


def validate_semantic(document, semantic_model) -> list:
    issues: list = []
    for w in semantic_model.warnings:
        issues.append(ValidationIssue("WARNING", "SEMANTIC_MODEL", w))

    for v in document.variables:
        if v.dax_status == "FAILED":
            issues.append(ValidationIssue("ERROR", "VARIABLE_FAILED",
                                           f"Variable '{v.name}' did not convert to DAX: {'; '.join(v.dax_notes)}"))
        elif v.dax_status == "UNSUPPORTED":
            issues.append(ValidationIssue("WARNING", "VARIABLE_UNSUPPORTED",
                                           f"Variable '{v.name}' used an unsupported function: {'; '.join(v.dax_notes)}"))
        elif v.dax_status == "APPROXIMATED":
            issues.append(ValidationIssue("INFO", "VARIABLE_APPROXIMATED",
                                           f"Variable '{v.name}' converted with an approximation: {'; '.join(v.dax_notes)}"))

    if not document.data_providers:
        issues.append(ValidationIssue("ERROR", "NO_DATA_PROVIDERS", "Document has no Data Providers — nothing to model."))
    if not document.reports:
        issues.append(ValidationIssue("WARNING", "NO_REPORTS", "Document has no Reports — no pages will be generated."))

    return issues


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 15 — Migration Audit & Quality Score  (from webi_migration_audit.py)
# ═══════════════════════════════════════════════════════════════════════════
# Score weight per dimension (must sum to 1.0). Formatting/Interactions
# carry lower weight because this engine's coverage of them is
# deliberately shallow (see WEBI_UNSUPPORTED_FEATURES.md) — a low score
# there should not dominate an otherwise-clean data model + calc migration.
_WEIGHTS = {
    "data_model": 0.20,
    "semantic": 0.10,
    "calculations": 0.20,
    "filters": 0.10,
    "prompts": 0.05,
    "visuals": 0.20,
    "field_mapping": 0.10,
    "layout": 0.05,
}

_STATUS_SCORE = {
    "SUCCESS": 1.0, "SUPPORTED": 1.0,
    "PARTIAL": 0.7, "APPROXIMATED": 0.7,
    "UNSUPPORTED": 0.3,
    "FAILED": 0.0,
}


@dataclass
class ScoreDimension:
    name: str
    score: float               # 0-100
    basis: str                 # MEASURED | HEURISTIC | MANUAL_VERIFICATION_REQUIRED
    detail: str = ""


@dataclass
class MigrationAudit:
    document: object
    semantic_model: object
    generation_notes: list
    artifact_issues: list
    semantic_issues: list

    dimensions: list = dc_field(default_factory=list)
    overall_score: float = 0.0
    summary: dict = dc_field(default_factory=dict)

    def compute(self) -> "MigrationAudit":
        doc = self.document
        sm = self.semantic_model
        notes = self.generation_notes

        n_reports = len(doc.reports)
        n_sections = sum(1 for _ in doc.all_sections())
        n_blocks = sum(1 for _ in doc.all_blocks())
        n_dps = len(doc.data_providers)
        n_objects = sum(len(dp.objects) for dp in doc.data_providers)
        n_vars = len(doc.variables)
        n_merges = len(doc.merged_dimensions)

        self.summary = {
            "document": doc.name,
            "reports": n_reports, "sections": n_sections, "blocks": n_blocks,
            "data_providers": n_dps, "universe_objects": n_objects,
            "variables": n_vars, "merged_dimensions": n_merges,
            "input_controls": len(doc.input_controls),
            "tables_generated": len(sm.tables),
            "relationships_generated": len(sm.relationships),
        }

        # ── Data Model Match ────────────────────────────────────────────
        errors = [i for i in self.artifact_issues if i.severity == "ERROR"]
        dm_score = 100.0 if not errors else max(0.0, 100.0 - 15 * len(errors))
        self.dimensions.append(ScoreDimension(
            "Data Model Match", dm_score, "MEASURED",
            f"{len(sm.tables)} table(s), {len(sm.relationships)} relationship(s), "
            f"{len(errors)} artifact validation error(s)."
        ))

        # ── Semantic Match ──────────────────────────────────────────────
        ambiguous = len(getattr(sm.registry, "ambiguous", []))
        sem_score = 100.0 if n_objects == 0 else max(0.0, 100.0 - (ambiguous / max(n_objects, 1)) * 100)
        self.dimensions.append(ScoreDimension(
            "Semantic Match", sem_score, "HEURISTIC",
            f"{ambiguous} ambiguous field name reference(s) out of {n_objects} universe object(s)."
        ))

        # ── Calculation Match ───────────────────────────────────────────
        if n_vars:
            calc_score = 100.0 * sum(_STATUS_SCORE.get(v.dax_status, 0.0) for v in doc.variables) / n_vars
        else:
            calc_score = 100.0
        self.dimensions.append(ScoreDimension(
            "Calculation Match", calc_score, "MEASURED",
            f"{sum(1 for v in doc.variables if v.dax_status == 'SUCCESS')}/{n_vars} variable(s) "
            f"converted to DAX with SUCCESS status."
        ))

        # ── Filter / Prompt Match ───────────────────────────────────────
        filter_notes = [n for n in notes if n["block_type"] == "QueryFilter"]
        total_filters = sum(1 for dp in doc.data_providers for f in dp.filters if not f.is_prompt)
        unresolved_filters = len(filter_notes)
        filt_score = 100.0 if total_filters == 0 else max(
            0.0, 100.0 * (total_filters - unresolved_filters) / total_filters)
        self.dimensions.append(ScoreDimension(
            "Filter Match", filt_score, "MEASURED",
            f"{total_filters - unresolved_filters}/{total_filters} query filter(s) applied as "
            f"Power BI report filters."
        ))

        prompt_notes = [n for n in notes if n["block_type"] in ("Prompt", "InputControl")]
        total_prompts = (len(doc.input_controls) +
                          sum(1 for dp in doc.data_providers for f in dp.filters if f.is_prompt))
        if prompt_notes:
            prompt_score = 100.0 * sum(
                _STATUS_SCORE.get(n["status"], 0.0) for n in prompt_notes) / len(prompt_notes)
        else:
            prompt_score = 100.0
        self.dimensions.append(ScoreDimension(
            "Prompt Match", prompt_score, "HEURISTIC",
            f"{sum(1 for n in prompt_notes if n['status'] != 'UNSUPPORTED')}/{total_prompts} "
            f"prompt(s)/input control(s) mapped to Slicer visuals (approximated — see notes)."
        ))

        # ── Visual / Field Mapping Match ────────────────────────────────
        visual_notes = [n for n in notes if n["block_type"] not in ("Section", "QueryFilter", "Prompt")]
        if visual_notes:
            vis_score = 100.0 * sum(_STATUS_SCORE.get(n["status"], 0.0) for n in visual_notes) / len(visual_notes)
        else:
            vis_score = 100.0
        self.dimensions.append(ScoreDimension(
            "Visual Match", vis_score, "MEASURED",
            f"{n_blocks} block(s) classified into Power BI visuals; "
            f"{sum(1 for n in visual_notes if n['status'] == 'SUPPORTED')} exact, "
            f"{sum(1 for n in visual_notes if n['status'] == 'APPROXIMATED')} approximated, "
            f"{sum(1 for n in visual_notes if n['status'] == 'UNSUPPORTED')} unsupported."
        ))

        n_unresolved_fields = sum(len(n.get("unresolved_fields", [])) for n in visual_notes)
        n_total_field_refs = n_unresolved_fields + sum(
            len(f) for n in visual_notes for f in n.get("field_wells", {}).values())
        field_score = 100.0 if n_total_field_refs == 0 else max(
            0.0, 100.0 * (n_total_field_refs - n_unresolved_fields) / n_total_field_refs)
        self.dimensions.append(ScoreDimension(
            "Field Mapping Match", field_score, "MEASURED",
            f"{n_unresolved_fields} unresolved field reference(s) out of {n_total_field_refs} "
            f"placed into visual field wells."
        ))

        # ── Layout Match ─────────────────────────────────────────────────
        section_approx = sum(1 for n in notes if n["block_type"] == "Section")
        layout_score = 100.0 if n_sections == 0 else max(0.0, 100.0 - (section_approx / max(n_sections, 1)) * 40)
        self.dimensions.append(ScoreDimension(
            "Layout Match", layout_score, "HEURISTIC",
            f"{section_approx}/{n_sections} section break(s) rendered as a static label instead of "
            f"a repeating per-value section (see WEBI_UNSUPPORTED_FEATURES.md)."
        ))

        self.overall_score = self._weighted_overall()
        return self

    def _weighted_overall(self) -> float:
        by_name = {d.name: d.score for d in self.dimensions}
        name_map = {
            "data_model": "Data Model Match", "semantic": "Semantic Match",
            "calculations": "Calculation Match", "filters": "Filter Match",
            "prompts": "Prompt Match", "visuals": "Visual Match",
            "field_mapping": "Field Mapping Match", "layout": "Layout Match",
        }
        total = sum(by_name.get(name_map[k], 0.0) * w for k, w in _WEIGHTS.items())
        return round(total, 1)

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "overall_score": self.overall_score,
            "dimensions": [
                {"name": d.name, "score": round(d.score, 1), "basis": d.basis, "detail": d.detail}
                for d in self.dimensions
            ],
            "artifact_issues": [
                {"severity": i.severity, "code": i.code, "message": i.message} for i in self.artifact_issues
            ],
            "semantic_issues": [
                {"severity": i.severity, "code": i.code, "message": i.message} for i in self.semantic_issues
            ],
            "generation_notes": self.generation_notes,
            "variables": [
                {"name": v.name, "formula": v.formula, "dax": v.dax_expression,
                 "status": v.dax_status, "confidence": v.dax_confidence, "notes": v.dax_notes}
                for v in self.document.variables
            ],
        }

    def _render_markdown(self) -> str:
        d = self.to_dict()
        lines = [
            f"# WebI Migration Audit — {self.document.name}",
            "",
            f"**Overall quality score: {self.overall_score} / 100**",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|---|---|",
        ]
        for k, v in self.summary.items():
            lines.append(f"| {k.replace('_', ' ').title()} | {v} |")

        lines += ["", "## Quality Score Breakdown", "", "| Dimension | Score | Basis | Detail |", "|---|---|---|---|"]
        for dim in self.dimensions:
            lines.append(f"| {dim.name} | {dim.score:.1f} | {dim.basis} | {dim.detail} |")

        lines += ["", "## Calculations (Variables → DAX)", "", "| Variable | Status | Confidence | DAX |", "|---|---|---|---|"]
        for v in self.document.variables:
            dax = (v.dax_expression or "(none)").replace("|", "\\|")
            lines.append(f"| {v.name} | {v.dax_status} | {v.dax_confidence:.1f} | `{dax}` |")
            for note in v.dax_notes:
                lines.append(f"|  |  |  | _{note}_ |")

        errors = [i for i in self.artifact_issues if i.severity == "ERROR"]
        warnings = [i for i in self.artifact_issues if i.severity == "WARNING"]
        lines += ["", "## Artifact Validation", "",
                  f"{len(errors)} error(s), {len(warnings)} warning(s).", ""]
        for i in self.artifact_issues:
            lines.append(f"- **{i.severity}** `{i.code}`: {i.message}")

        lines += ["", "## Semantic Validation", ""]
        for i in self.semantic_issues:
            lines.append(f"- **{i.severity}** `{i.code}`: {i.message}")

        lines += ["", "## Visuals, Filters & Prompts", ""]
        for n in self.generation_notes:
            if not n.get("notes"):
                continue
            label = n.get("block_id") or n.get("block_type")
            lines.append(f"- **{n['block_type']}** `{label}` → `{n.get('visual_type', '')}` "
                          f"[{n['status']}, confidence {n['confidence']:.2f}]")
            for note in n["notes"]:
                lines.append(f"  - {note}")

        return "\n".join(lines) + "\n"

    def write(self, output_dir: Path, base_name: str) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / f"{base_name}.migration_audit.json"
        md_path = output_dir / f"{base_name}.migration_audit.md"
        json_path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        md_path.write_text(self._render_markdown(), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 16 — Entry Point: Server / CLI  (from webi_pbi_server.py)
# ═══════════════════════════════════════════════════════════════════════════
STAGES = [
    "Input Validation", "WebI Input Detection", "WebI Parsing",
    "Semantic Model Construction", "Universe Reconstruction",
    "Data Provider Reconstruction", "Query Reconstruction",
    "Data Model Generation", "Formula Conversion", "Filter Conversion",
    "Visual Classification", "Field Mapping", "Power BI Generation",
    "Layout Reconstruction", "Validation", "Migration Audit", "Finalization",
]

ERROR_CATEGORIES = (
    "INPUT_ERROR", "PARSE_ERROR", "SEMANTIC_ERROR", "DATASOURCE_ERROR",
    "FORMULA_ERROR", "DAX_ERROR", "VISUAL_ERROR", "LAYOUT_ERROR",
    "VALIDATION_ERROR", "ARTIFACT_ERROR", "UNKNOWN_ERROR",
)


class WebIMigrationError(Exception):
    """Raised by process_file() with a §35-taxonomy category and the
    pipeline stage it failed at, so one bad WebI feature never crashes the
    rest of a batch (see conversion_worker) and always shows up
    identifiably in the audit rather than as a bare traceback."""

    def __init__(self, category: str, stage: str, message: str):
        super().__init__(message)
        assert category in ERROR_CATEGORIES, f"Unknown error category: {category}"
        self.category = category
        self.stage = stage


# ─── Shared server state (mirrors tableau_pbi_server.py's shape so a
# future router can treat both engines uniformly) ──────────────────────────
conversion_state = {"running": False, "source": "", "target": "", "jobs": {}}
_clients: list = []
_clients_lock = threading.Lock()


def broadcast(event: str, data: dict) -> None:
    payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    with _clients_lock:
        dead = []
        for q in _clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _clients.remove(q)


def log(level: str, message: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{level.upper()}] {message}", flush=True)
    broadcast("log", {"ts": ts, "level": level, "file": "[WebI]", "message": message})


def _progress(job_key: str, stage_idx: int, message: str, status: str = "running") -> None:
    pct = int(round((stage_idx / len(STAGES)) * 100))
    job = conversion_state["jobs"].setdefault(job_key, {"file": job_key, "errors": [], "warnings": []})
    job.update(stage=STAGES[stage_idx - 1], stage_index=stage_idx, progress=pct, status=status, message=message)
    broadcast("state", conversion_state)
    log("info", f"[{job_key}] {STAGES[stage_idx - 1]}: {message}")


def process_file(path: Path, target_dir: Path) -> dict:
    """Run the full 17-stage pipeline for one WebI input file. Never
    raises — every failure is caught, classified, recorded in
    conversion_state['jobs'][job_key], and returned, so a batch run
    (conversion_worker) can continue past it (§35: 'Do not terminate the
    entire migration because one unsupported WebI feature is
    encountered')."""
    job_key = path.name
    conversion_state["jobs"][job_key] = {"file": job_key, "status": "running", "progress": 0,
                                          "stage": "", "errors": [], "warnings": []}
    broadcast("state", conversion_state)

    try:
        _progress(job_key, 1, f"Validating input file {path.name}")
        if not path.exists():
            raise WebIMigrationError("INPUT_ERROR", "Input Validation", f"File not found: {path}")

        _progress(job_key, 2, "Detecting WebI input format")
        try:
            detected = detect_file(path)
        except WebIInputError as e:
            raise WebIMigrationError("INPUT_ERROR", STAGES[1], str(e)) from e

        _progress(job_key, 3, f"Parsing as '{detected.format}'")
        try:
            document = parse_input(path)
        except Exception as e:
            raise WebIMigrationError("PARSE_ERROR", STAGES[2], f"{type(e).__name__}: {e}") from e

        _progress(job_key, 4, "Building Power BI semantic model")
        try:
            semantic_model = build_semantic_model(document)
        except Exception as e:
            raise WebIMigrationError("SEMANTIC_ERROR", STAGES[3], f"{type(e).__name__}: {e}") from e

        _progress(job_key, 5, f"Reconstructed {len(document.universes)} universe(s)")
        _progress(job_key, 6, f"Reconstructed {len(document.data_providers)} data provider(s)")
        _progress(job_key, 7, f"Resolved {sum(len(dp.objects) for dp in document.data_providers)} query object(s)")
        _progress(job_key, 8, f"Generated {len(semantic_model.tables)} Power BI table(s), "
                               f"{len(semantic_model.relationships)} relationship(s)")

        n_failed = sum(1 for v in document.variables if v.dax_status == "FAILED")
        n_ok = sum(1 for v in document.variables if v.dax_status in ("SUCCESS", "PARTIAL", "APPROXIMATED"))
        stage9_status = "warning" if n_failed else "running"
        _progress(job_key, 9, f"Converted {n_ok}/{len(document.variables)} formula(s) to DAX "
                               f"({n_failed} failed)", status=stage9_status)

        n_blocks = sum(1 for _ in document.all_blocks())
        output_path = target_dir / f"{path.stem.split('.')[0]}.pbit"
        try:
            generation_notes, schema, layout, diagram = write_pbit(
                document, semantic_model, output_path)
        except Exception as e:
            raise WebIMigrationError("VISUAL_ERROR", "Power BI Generation", f"{type(e).__name__}: {e}") from e

        _progress(job_key, 10, "Query filters and prompts converted")
        _progress(job_key, 11, f"Classified {n_blocks} block(s) into Power BI visuals")
        _progress(job_key, 12, "Resolved visual field wells")
        _progress(job_key, 13, f"Wrote {output_path.name}")
        _progress(job_key, 14, "Reconstructed report layout")

        _progress(job_key, 15, "Validating generated artifact")
        try:
            artifact_issues = validate_artifact(schema, layout)
            semantic_issues = validate_semantic(document, semantic_model)
        except Exception as e:
            raise WebIMigrationError("VALIDATION_ERROR", STAGES[14], f"{type(e).__name__}: {e}") from e

        _progress(job_key, 16, "Writing migration audit")
        audit = MigrationAudit(
            document, semantic_model, generation_notes, artifact_issues, semantic_issues).compute()
        audit.write(target_dir, output_path.stem)

        has_err = has_errors(artifact_issues) or has_errors(semantic_issues)
        final_status = "warning" if has_err else "success"
        _progress(job_key, 17, f"Done — quality score {audit.overall_score}/100", status=final_status)

        conversion_state["jobs"][job_key].update({
            "output": str(output_path),
            "audit": str(target_dir / f"{output_path.stem}.migration_audit.json"),
            "quality_score": audit.overall_score,
            "errors": [i.message for i in artifact_issues + semantic_issues if i.severity == "ERROR"],
            "warnings": [i.message for i in artifact_issues + semantic_issues if i.severity == "WARNING"],
        })
        broadcast("state", conversion_state)
        return conversion_state["jobs"][job_key]

    except WebIMigrationError as e:
        job = conversion_state["jobs"].setdefault(job_key, {"file": job_key})
        job.update(status="error", category=e.category, stage=e.stage, message=str(e))
        job.setdefault("errors", []).append(str(e))
        broadcast("state", conversion_state)
        log("error", f"[{job_key}] {e.category} at '{e.stage}': {e}")
        return job

    except Exception as e:
        job = conversion_state["jobs"].setdefault(job_key, {"file": job_key})
        job.update(status="error", category="UNKNOWN_ERROR", message=str(e))
        job.setdefault("errors", []).append(str(e))
        broadcast("state", conversion_state)
        log("error", f"[{job_key}] Unhandled exception: {e}\n{traceback.format_exc()}")
        return job


def conversion_worker(source_dir: str, target_dir: str) -> None:
    conversion_state.update(running=True, source=source_dir, target=target_dir, jobs={})
    broadcast("state", conversion_state)
    try:
        Path(target_dir).mkdir(parents=True, exist_ok=True)
        try:
            inputs = discover_inputs(Path(source_dir))
        except WebIInputError as e:
            log("error", str(e))
            inputs = []
        if not inputs:
            log("warn", f"No recognized WebI input (.wid.xml / .xml with a <WebIDocument> root) "
                         f"found in {source_dir}")
        for detected in inputs:
            if not conversion_state["running"]:
                log("info", "WebI migration stopped by user")
                break
            process_file(detected.path, Path(target_dir))
    finally:
        conversion_state["running"] = False
        broadcast("state", conversion_state)
        log("info", "WebI migration batch complete")


# ─── HTTP / SSE server — same route shapes as tableau_pbi_server.py so a
# future report_migration_tool.py update can proxy to this module exactly
# the way it already proxies to the Crystal Reports engine. ────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "/state":
            self._json(conversion_state)
        elif path == "/events":
            self._serve_sse()
        elif path == "/health":
            self._json({"ok": True, "engine": "webi"})
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw_body)
        except Exception:
            payload = {}

        if path == "/start":
            self._start(payload)
        elif path == "/stop":
            conversion_state["running"] = False
            self._json({"ok": True})
        elif path == "/convert_one":
            self._convert_one(payload)
        elif path == "/clear":
            conversion_state["jobs"].clear()
            broadcast("state", conversion_state)
            self._json({"ok": True})
        else:
            self.send_error(404)

    def _start(self, payload: dict):
        if conversion_state["running"]:
            self._json({"ok": False, "error": "Already running"})
            return
        src = payload.get("source", "").strip()
        tgt = payload.get("target", "").strip()
        if not src or not tgt:
            self._json({"ok": False, "error": "source and target are required"})
            return
        if not Path(src).is_dir():
            self._json({"ok": False, "error": f"Source folder not found: {src}"})
            return
        threading.Thread(target=conversion_worker, args=(src, tgt), daemon=True).start()
        self._json({"ok": True})

    def _convert_one(self, payload: dict):
        filepath = payload.get("file", "").strip()
        target = (payload.get("target", "") or conversion_state.get("target", "")).strip()
        if not filepath:
            self._json({"ok": False, "error": "No file path provided"})
            return
        if not target:
            self._json({"ok": False, "error": "No target folder set"})
            return
        p = Path(filepath)
        if not p.exists():
            self._json({"ok": False, "error": f"File not found: {filepath}"})
            return
        Path(target).mkdir(parents=True, exist_ok=True)
        threading.Thread(target=process_file, args=(p, Path(target)), daemon=True).start()
        self._json({"ok": True, "queued": p.name})

    def _serve_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._cors()
        self.end_headers()

        q: queue.Queue = queue.Queue(maxsize=200)
        with _clients_lock:
            _clients.append(q)
        try:
            init = f"event: state\ndata: {json.dumps(conversion_state)}\n\n"
            self.wfile.write(init.encode("utf-8"))
            self.wfile.flush()
        except Exception:
            pass
        try:
            while True:
                try:
                    payload = q.get(timeout=25)
                    self.wfile.write(payload.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            with _clients_lock:
                if q in _clients:
                    _clients.remove(q)


class _QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError)):
            return
        super().handle_error(request, client_address)


def _run_once(source: str, target: str, single_file: str) -> int:
    """Synchronous batch/single-file run for CLI/CI use, with no server.
    Returns a process exit code (0 = every file SUCCESS, 1 = any error)."""
    Path(target).mkdir(parents=True, exist_ok=True)
    results = []
    if single_file:
        results.append(process_file(Path(single_file), Path(target)))
    else:
        try:
            inputs = discover_inputs(Path(source))
        except WebIInputError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            return 1
        if not inputs:
            print(f"[WARN] No recognized WebI input found in {source}")
        for detected in inputs:
            results.append(process_file(detected.path, Path(target)))

    ok = 0
    for r in results:
        status = r.get("status")
        score = r.get("quality_score", "-")
        print(f"{r.get('file')}: {status} (quality score: {score})")
        if status == "error":
            for msg in r.get("errors", []):
                print(f"    error: {msg}")
        else:
            ok += 1
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="SAP BusinessObjects Web Intelligence (WebI) → Power BI Migration Engine")
    parser.add_argument("--port", type=int, default=8600)
    parser.add_argument("--source", default="./input")
    parser.add_argument("--target", default="./output")
    parser.add_argument("--file", default="", help="Convert a single WebI file and exit (implies --once)")
    parser.add_argument("--once", action="store_true",
                         help="Run one batch/single-file conversion synchronously and exit (no server)")
    args = parser.parse_args()

    if args.once or args.file:
        sys.exit(_run_once(args.source, args.target, args.file))

    for d in (args.source, args.target):
        Path(d).mkdir(parents=True, exist_ok=True)

    server = _QuietServer(("0.0.0.0", args.port), Handler)
    print(f"""
╔══════════════════════════════════════════════════════════╗
║   SAP WebI → Power BI Migration Engine                    ║
╠══════════════════════════════════════════════════════════╣
║  URL      : http://localhost:{args.port:<27}║
║  Source   : {args.source:<45}║
║  Target   : {args.target:<45}║
╚══════════════════════════════════════════════════════════╝
This engine is independent of tableau_pbi_server.py — it does not import
from or modify it. POST /start {{"source":..., "target":...}} to begin a
batch migration; GET /events for SSE progress.
Press Ctrl+C to stop.
""")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[webi] Server stopped.")
        server.server_close()
