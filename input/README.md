# SAP WebI Representative Source Artifacts

Four mock `.wid.xml` files that model the structural elements of a real SAP
BusinessObjects Web Intelligence document (data providers, universe objects,
queries, variables, blocks, sections, merges, charts). Actual `.wid` files
are a proprietary binary/XML hybrid opened only by WebI/BI Platform, so these
are hand-built representative sources — useful for parser/migration testing,
schema design, or documentation — not files exported from a live system.

| File | Type | Highlights |
|---|---|---|
| `01_simple_table_report.wid.xml` | Simple table report | 1 data provider, 1 flat table block, a query filter, one formula variable, column sort + footer aggregates |
| `02_multi_section_report.wid.xml` | Multi-section report | Nested breaks (Region → Country), section headers/footers, conditional formatting, per-section aggregates |
| `03_dashboard_with_charts.wid.xml` | Complex dashboard | KPI tiles, line/bar/pie charts, cross-tab with color scale, input controls (filters/date range) |
| `04_multi_data_provider_report.wid.xml` | Multi-DP / multi-universe | 3 data providers (2 universes + free-hand SQL), merged dimensions, cross-provider variables, combo chart |

## Schema notes
- `<DataProviders>` — one per query; `type` can be `Universe` or `FreeHandSQL`; each carries its own `<ResultObjects>` and `<QueryFilters>`.
- `<MergedDimensions>` — only present when >1 data provider; maps a common dimension name to its per-DP source object.
- `<Variables>` — report-level formulas, WebI syntax (e.g. `=[Revenue]-[COGS]`).
- `<Reports><Report><Sections><Section breakOn="...">` — sections nest via `<SubSections>` for multi-level breaks.
- `<Blocks>` — `Table`, `CrossTab`, `Chart` (with `chartType`), `KPI`, `FreeCell`; charts declare `Axis`/`Series`/`Slice` roles.
- Column/axis references use either `object="OBJ_ID"`, `object="DP_ID.OBJ_ID"` (multi-DP), or `mergedDimension="Name"`.

Feel free to ask for variants (e.g. an OLAP/BEx-based data provider, a report with input controls driving a merged dimension, or a version with hierarchies/drill).
