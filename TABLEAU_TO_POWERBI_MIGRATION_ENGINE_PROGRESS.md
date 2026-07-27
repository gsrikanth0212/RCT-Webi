# Tableau → Power BI Migration Engine — Progress Tracker

Working doc for the ongoing effort to improve `report_migration_tool.py` and
`tableau_pbi_server.py` so migrated reports match the original Tableau
workbooks as closely as possible (target: 80% min, 90%+ preferred).

**Hard constraints (do not violate in future work):**
- Never modify `Index.html` or `Landing.html` — UI is finalized.
- Only modify `report_migration_tool.py` and `tableau_pbi_server.py`.
- Never change the datasource/SQL/connection/tables — data model source stays identical.
- Preserve all existing public APIs/routes/frontend compatibility.
- Work incrementally — never do a wholesale rewrite; extend/reuse existing code.
- This repo is **not a git repository**, so there's no commit history/diff to
  lean on — verification has to be direct (compile + run against sample
  workbooks + inline unit tests), and nothing here has been committed anywhere.
  Asked the user explicitly about `git init` before the framework-engineering
  phase's structural refactors began; they declined — do not ask again
  unless something changes, just keep taking manual backups before risky edits.
- Real production flow is driven by `source`/`target` folders supplied via the
  UI (`/start` endpoint → `conversion_worker(source_dir, target_dir, ...)` in
  `tableau_pbi_server.py`). The `input/` folder in this repo is just 5 sample
  workbooks used for ad-hoc verification during development — it is NOT part
  of the app's real flow and was never modified.
- **Framework Engineering Phase standing directive (started after Increment
  8, still in force)**: build a generic, metadata-driven engine — no
  hardcoded workbook names, sheet names, field names, or workbook-specific
  conditions anywhere. Optimize for arbitrary future Tableau workbooks
  placed in `source/`, not for the current 8 test fixtures looking good.
  A fix that only works by special-casing a specific field/sheet/workbook
  name is the wrong fix — find the general pattern instead (every fix in
  this phase so far has been a general structural/heuristic correction, not
  a special case, and that discipline must continue). See "Framework
  Engineering Phase" section below for the full context and methodology.

**Test fixtures used for verification**: the original 5 in `input/`
(`House Sales Dashboard.twb`, `Netfix Workbook.twb`, `Amazon Sales
Insights.twbx`, `Finance Dashboard_v2026.1.twbx`, `LocBar.twbx`) **plus 3
more added in the framework-engineering phase**, in `source/`
(`AB_NYC Dashboard.twbx`, `HR Analytics.twbx`, `Sales Param.twbx`) — **8
total**, all 8 used for every regression run from that phase onward. Note:
`target/` also has PBIT output for "Global Payment 2016-2025 Dashboard" and
"Tweets Analysis" with **no corresponding source file** in `source/` — those
2 can't be used for regression until/unless their source workbooks are
supplied. Verification method each round: `python3 -m py_compile
tableau_pbi_server.py`, then a throwaway script that calls
`parse_tableau_workbook()` + `write_pbit()` on all 8 and checks for `OK` with
no exceptions, plus targeted unit tests of new code paths. From the
framework-engineering phase onward, a stronger method was added — see
"Verification methodology" below.

---

## Done

### Increment 1 — Filter gaps + DAX conversion gaps + audit truthfulness

**Filters** (schema-verified against the official Tableau 2026.2 TWB XSD from
`github.com/tableau/tableau-document-schemas` before implementing, not guessed):
- Context filters (`<filter context="true">`) — now detected, ordered first in
  the Filters pane (closest safe PBI approximation of "compute this filter first").
- Quantitative range filters (`<min>`/`<max>`) — previously bounds were silently
  dropped (visual rendered unfiltered); now a native Advanced Between filter,
  with correct `L`/`D` numeric literal suffixing.
- Top-N filters (`groupfilter end="top/bottom" count=...`) — previously
  undetected entirely; now mapped to Power BI's native `TopN` filter condition.
- Relative-date filters (`class="relative-date"`) — previously undetected; now
  resolved to a static snapshot date window via the workbook's anchor date +
  period math, emitted as an Advanced date-range filter, and logged clearly
  (`log('warn', ...)`) as needing manual reconversion to a live PBI relative
  date filter (no verified-safe internal format exists for a "live" one).
- Refactored: the three near-identical filter-building blocks inside
  `_make_chart_vc` / `_make_card_vc` / `_make_table_vc` were de-duplicated into
  one shared, tested helper: `_build_pbi_visual_filters()` (tableau_pbi_server.py:9762).
- New helpers: `_add_period()` (:164), `_relative_date_window()` (:193).

**DAX conversion** (`_tableau_formula_to_dax`, inside the same function body):
- Added: `DATENAME`, `DATEADD` (via safe `EDATE()`/`DATE()` arithmetic — NOT
  DAX's own `DATEADD()`, which is an incompatible date-table function),
  `MAKEDATE`, `IFNULL`, `PREVIOUS_VALUE` (honest fallback — row-order
  dependency can't be inferred from the formula text alone).
- Expanded `DATEPART` / `DATEDIFF` from year/month/day-only to all 8 Tableau
  period units (year/quarter/month/week/day/hour/minute/second).
- New generic helpers inside `_tableau_formula_to_dax`: `_split_top_args()`,
  `_rewrite_call()` — bracket/quote-aware multi-arg call rewriting, replacing
  the old single-purpose regexes.

**Audit-report honesty** (`_TAB_FN_SUPPORT` dict, tableau_pbi_server.py:7025):
- `INCLUDE`/`EXCLUDE` LOD were mismarked `unsupported` despite producing real
  `CALCULATE`/`VALUES`/`ALL` DAX — now `partial`, matching `FIXED`'s rating.
- Several date functions were marked `supported` with zero conversion code
  behind them (`DATEADD`, `MAKEDATE`, `IFNULL`) — now genuinely supported, and
  `DATENAME` (previously entirely absent from the catalogue) was added.
- Bigger fix: any function *not* in the catalogue silently defaulted to
  `'supported'` (i.e. an unreviewed function inflated the confidence score).
  Now defaults to `'partial'` with a "verify manually" note in both places
  this fallback occurs (`_detect_functions`, function-call and bare-keyword
  branches).

### Increment 2 — Per-Worksheet Migration Quality Score

Added the per-worksheet score table you asked for explicitly, in both
`migration_audit.md` and `migration_audit.json`, with columns: Worksheet,
Detected (Tableau) visual, Power BI Visual, Calc %, Visual %, Layout %,
Format %, Filter %, Overall %, plus a per-worksheet notes list surfaced under
"Unsupported Features / Warnings / Recommendations".

- `Calculation %` and `Filter %` are **measured** — calc % averages real
  per-calculation confidence scores for calcs that worksheet uses (via
  `MigrationAudit.calc_confidence_for()` / `.calcs_for_worksheet()`, new
  methods on `MigrationAudit`); filter % reflects actual filter-conversion
  results from Increment 1 (deducts only for the known Measure-Names-filter gap).
- `Visual %`, `Layout %`, `Format %` are **heuristics**, explicitly labeled as
  such in the rendered report so they aren't mistaken for pixel-level fidelity:
  - Visual % — reasoned mapping-quality table `_VISUAL_MATCH_SCORE` (:8007):
    native bar/line/card ≈ 95–100, combo/dual-axis/map ≈ 85, generic
    table/matrix fallback ≈ 75 (also flags "might have been a Heat Map" and
    "Gantt approximated as Area Chart" when the underlying signal is present).
  - Layout % — 100 if the worksheet kept its real Tableau dashboard position,
    65 if it fell through to the auto-generated orphan "Worksheets" grid page,
    90 if the workbook has no dashboards at all (nothing to have lost).
    Backed by new `workbook['_layout_fidelity']` dict populated inside
    `build_report_layout()` right after the existing `_orphan_ws`/`_referenced_ws`
    computation (no new logic there, just exposing what already existed).
  - Format % — from which formatting signals were actually captured
    (label format present, colors present, sort field present).
- New function: `_compute_worksheet_quality_scores(workbook, audit)` (:8028),
  called from `write_pbit()` right after `build_report_layout()` and before
  `audit.write()`.
- `MigrationAudit` extended: `worksheet_scores` attribute, `set_worksheet_scores()`,
  `calc_confidence_for()`, `calcs_for_worksheet()`, `summary()` now includes
  `worksheet_scores` + `avg_overall_match_pct`, `_render_md()` has a new
  "## Per-Worksheet Migration Quality Score" section.

**Verified**: all 5 sample workbooks converted end-to-end with no exceptions;
average Overall Match % across them ranged 91–96/100, and the automatic notes
(Gantt-as-area, orphaned worksheet, generic-table-might-be-heatmap,
Measure-Names-filter) only fired on worksheets where the underlying condition
actually applied — spot-checked in the rendered Markdown for `LocBar` and
`Finance Dashboard_v2026.1`.

### Increment 3 — Dashboard actions + reference/trend lines

**Dashboard actions**: `<actions>` is a direct child of `<workbook>` (sibling
to `<worksheets>`/`<dashboards>`, confirmed against a real workbook —
`Finance_Dashboard.twb` has 6 real filter actions, not just the abstract
XSD). Key discovery from checking the real file instead of only the schema:
a `<link>` element does NOT necessarily mean an external URL action — Tableau
reuses `<link>` for its internal "Sheet Link" mechanism (`expression`
starting with `tsl:`) when a filter action also navigates to another
sheet/dashboard. Only `http://`/`https://` expressions are genuine external
URL actions. Getting this right (vs. wrongly treating every `<link>` as a
browser URL) was the actual payoff of checking real data first.

- New function `_parse_dashboard_actions(root, dashboard_names, worksheet_names)`
  parses `<action>` (legacy filter/highlight/URL, classified into `filter`/
  `navigate_filter`/`url`/`other_link`/`unknown`), `<nav-action>`,
  `<edit-group-action>`, `<edit-parameter-action>` — stored on
  `workbook['dashboard_actions']`.
- **Deliberately NOT auto-wired** into Power BI's page-level `visualInteractions`
  or a button's URL-action JSON — those internal Report.json shapes couldn't
  be verified with the same confidence as this engine's per-visual filter
  format (which this codebase already proves out elsewhere). A wrong guess in
  page-level config risked producing something that looks wired but silently
  does nothing — exactly the "audit lies" failure mode this whole effort is
  fixing. Instead: every action is surfaced in a new "## Dashboard Actions"
  section of `migration_audit.md` with a specific, actionable recommendation
  (e.g. "Power BI's default cross-filtering already approximates this... to
  exclude {sheets}, configure Edit Interactions manually" or "add a Bookmark +
  button... configured manually").
- `MigrationAudit` extended: `dashboard_actions` attribute, `set_dashboard_actions()`.

**Reference lines / trend lines**: both live inside `<pane>` (schema-verified
via the 2026.2 TWB XSD — `<reference-line formula="constant|average|median|
sum|min|max|...">`, `<trendline enabled="true" fit="linear|polynomial|log|
exp|power">`). Parsed per-worksheet into `ws['reference_lines']`/`ws['trendline']`,
threaded through `_ws_to_page()` → `_make_chart_vc(reference_lines=, trendline=)`.

- Real PBI wiring only where the translation is unambiguous: `formula='constant'`
  (an actual fixed value) → `objects_cfg['constantLine']`; `fit='linear'`
  → `objects_cfg['trend']`. Both reuse this file's own already-proven
  `objects_cfg[...] = [{'properties': {...}}]` pattern (same shape as the
  existing `labels`/`legend`/`valueAxis2` objects) — a wrong property name
  here just means the line doesn't render, not file corruption, unlike the
  page-level actions risk above.
- Aggregate-formula reference lines (average/median/sum/min/max/percentile/
  stdev/confidence) and non-linear trend fits (polynomial/log/exp/power) have
  **no safe static translation** — Power BI's own dynamic Analytics-pane line
  features would be needed, and inventing a static number would be actively
  wrong, not just approximate. These are flagged in the per-worksheet quality
  score notes instead of guessed at.
- Gated to cartesian visual types only (line/area/column/bar/combo) — pie,
  treemap, scatter, table, card, map are excluded since PBI's Analytics pane
  constant-line/trend-line features don't apply there.

**Verified**: real actions parsed correctly against `Finance_Dashboard.twb`'s
actual 6-action block (all correctly showing target sheets and exclude-lists
in the recommendation text); reference-line/trendline `objects_cfg` wiring
verified via synthetic XML (constant line renders, average line correctly
skipped, linear trend renders, polynomial trend correctly skipped, non-cartesian
visual types correctly excluded); full 5-workbook regression unchanged at
91–96/100 average match (as expected — only Finance Dashboard has real
actions, and none of the 5 samples have reference lines/trend lines, so this
increment is additive with zero effect on existing scores). Caught and fixed
one real bug during implementation: an over-eager `replace_all` edit briefly
wired `reference_lines=`/`trendline=` kwargs into `_make_card_vc`/`_make_table_vc`
call sites, which don't accept them and have no `**kwargs` catch-all — would
have crashed at runtime; caught by checking call-site context before running
the regression suite, not by the regression suite itself (worth remembering:
grep the call-site function name before trusting a multi-occurrence replace).

### Increment 4 — True dual-axis + axis sync

**Same-mark dual axis**: Tableau's "Analysis > Dual Axis" combines two
measures on Rows onto separate axes. When they use *different* mark types
(e.g. Line+Bar), the existing combo-chart detection already caught it. When
they use the *same* mark type (e.g. two Line measures — the most common real
case), the cascade fell through to the single-measure `lineChart` branch and
silently dropped the second measure/axis entirely. Root cause: the existing
combo-detection branches all required `len(distinct_marks) >= 2`.

- Fixed by adding one new branch to the chart-type cascade in
  `parse_tableau_workbook`: `pane_count >= 2 and is_measure_rows and not
  is_dim_rows and len(distinct_marks) <= 1` → `lineClusteredColumnComboChart`
  (placed after the 'Area' branch, before the single-measure `lineChart`
  branch, so it only catches what would otherwise have been silently
  truncated). Reuses the exact same Y/Y2-axis wiring in `_make_chart_vc`
  already proven by the existing mixed-mark combo case — Power BI's
  line+column combo chart is the only built-in visual with genuine
  independent-secondary-axis semantics, so it's the correct target either
  way; the only cosmetic loss is the second measure rendering as a column
  instead of matching mark type, which is now flagged in the quality-score
  notes rather than silently presented as exact.
- Verified with a logic-level boundary check of the new guard condition (5
  cases: true same-mark dual-axis fires, single-pane doesn't, dimension-
  faceted panes don't, mixed-mark case correctly left to the existing
  branch, 3-pane same-mark case fires) plus the full 5-workbook regression
  (unchanged scores — none of the 5 samples happen to contain a genuine
  same-mark dual-axis worksheet, so this is a net-additive fix with zero
  effect on existing conversions, not something disprovable by the sample set).

**Axis sync**: Tableau's "Synchronize Dual Axis" lives at
`<style-rule element="axis"><encoding synchronized="true"/>` (confirmed via
the XSD only — no real sample has this, unlike the dashboard-actions case in
Increment 3 where a real example was available). Given no real file to
verify the exact XPath against, and no verified stable Power BI combo-chart
property for "lock secondary axis to primary," this is deliberately
detect-and-report only: parsed defensively (wrapped so it can never raise,
defaults to `False` on any structural mismatch) into
`ws['axis_synchronized']`, surfaced as a quality-score note when true rather
than guessed at with invented `objects_cfg` JSON — same discipline as the
dashboard-actions decision in Increment 3.

### Increment 5 — Weak/missing visual types (partial — 3 of 6 sub-items)

Before implementing anything, tried to verify PBI's built-in-visual internals
the same way Increment 3's reference lines were verified (extend this
codebase's own already-proven `objects_cfg[...] = [{'properties': {...}}]`
pattern). That verification genuinely failed for 3 of the 6 sub-items — worth
recording precisely, since "couldn't verify, didn't guess" is a real, deliberate
outcome here, not a skipped step:

- **Highlight Table / Heat Map color-scale**: Power BI's table/matrix
  conditional-formatting *does* exist (`fillRule`/`linearGradient2`/
  `linearGradient3` confirmed real property names via the official
  `microsoft/powerbi-visuals-api` `schema.capabilities.json`), but that file
  only documents the *capability declaration* schema, not how the actual
  value gets serialized inside a built-in visual's `objects_cfg` in the
  legacy Report.json this engine writes — unlike constant lines/trend lines,
  which extended a wrapper convention (`{'expr': {'Literal': {...}}}`) this
  codebase already uses successfully dozens of times. A first web search
  claimed to quote `microsoft/powerbi-models`' `models.ts` with specific
  interface names; fetching that exact file directly showed zero matches for
  any of them — a reminder that WebSearch's AI-generated summaries can
  state specifics confidently and be wrong, so treat them as a lead to verify
  by fetching the actual source, not as a citation.
- **Histogram gap-width**: no evidence Power BI's built-in Column Chart
  exposes a gap-width/inner-padding formatting control at all (unlike Excel
  or Tableau's histogram-specific "0% gap" setting) — nothing to safely wire
  even if the property name were known.
- **Waterfall chart**: Power BI does have a native built-in `waterfallChart`
  visual type, but its Category/Y projection role *names* (this codebase's
  `role_map` convention, e.g. `{'cat': 'Category', 'val': 'Y', 'leg': 'Series'}`)
  aren't documented anywhere public — every existing entry in that map was
  empirically reverse-engineered against a real reference PBIT (per this
  file's own "verified from reference PBIT" comments), which isn't available
  for waterfall. Guessing wrong role names risks a visual that renders
  completely empty, a worse failure than a missing formatting tweak.

Given that, shipped the 3 sub-items with a safe, real path and deferred the
3 that need a reference PBIT (see "Not yet done" below):

- **Heat Map / Highlight Table**: now distinguished from a generic table
  fallback — when a `tableEx`/`pivotTable` worksheet has a color encoding,
  the quality-score note names the exact Power BI steps to restore it
  (Format → Conditional formatting → Background color → Gradient/Rules) —
  same discipline as Increment 3's dashboard actions (detect + specific
  recommendation, not a guessed JSON write).
- **Bullet Graph**: no dedicated detection needed — a Tableau bullet graph is
  structurally a bar/column chart with a target reference line, and both of
  those are *already* real, wired features (bar-chart mapping since day one,
  reference-line `objects_cfg` since Increment 3). Added a note confirming
  this when the pattern is detected (bar/column chart type + a constant-value
  reference line present), and flagging that qualitative performance bands
  (if the original had poor/satisfactory/good zones) aren't replicated —
  only the single target value.
- **Histogram**: audited the existing bin-column DAX generation and found the
  sort-order concern from the original gap analysis was already resolved —
  the bin calculated column is written with `"dataType": "double"` (a real
  numeric type), so Power BI already sorts it ascending by value, not
  alphabetically. No code change needed there. Added a note about the
  gap-width cosmetic limitation instead of silently having no mention of it.

**Verified**: full 5-workbook regression unchanged (91–96/100); all three new
notes confirmed firing correctly on real data — House Sales Dashboard shows
both the histogram note (its bin-based worksheets) and the heat-map note (a
color-encoded table on 'Price'), LocBar shows the heat-map note (color-encoded
table on 'Sub-Category'). No bullet-graph note fired in the 5 samples since
none of them have a reference line at all (consistent with Increment 3's finding).

### Increment 6 — Parameters → live Power BI What-if parameters + slicers

Schema-verified against the 2026.2 TWB XSD (`<column param-domain-type=
"any|list|range">`, with a `<range min max granularity>` child for 'range'
and `<members><member value alias>` for 'list'), then confirmed against the
one real parameter in the 5 samples (Netflix workbook's "Year" date
parameter, `domain-type="any"` — declared but not referenced anywhere else in
that workbook). Real-world payoff of checking that example: it exposed a
genuine, previously-invisible correctness bug — any calc formula that *does*
reference a Tableau parameter produces DAX with a bare `[Parameter Name]`
reference (the old Pre-rule C just stripped the `[Parameters].` prefix and
left it at that); since no measure of that name has ever existed in the
model, that's a dangling reference that would fail to load in Power BI
Desktop. None of the 5 samples happen to reference their one declared
parameter anywhere, so this was latent, not observed breakage — same
"real bug, not just a missing feature" pattern as the Increment 1 filter fixes.

- Parsing extended (numeric-only `_param_values`, used for bin-size, kept
  unchanged for backward compatibility) with a new `workbook['tableau_parameters']`
  list capturing the full definition per parameter: domain type, datatype,
  current value of any type (not just numeric), and range/members as applicable.
- New `_build_whatif_parameter_table()`: for enumerable ('range' or 'list'
  domain) parameters, generates the exact structure Power BI Desktop's own
  "New Parameter" UI produces — `SELECTCOLUMNS(GENERATESERIES(min, max, step),
  "Caption", [Value])` (or a DAX table constructor for a fixed list) as a
  calculated table, plus a `SELECTEDVALUE(...)` measure. Standard, publicly
  documented DAX table/measure patterns — not an internal-format guess, same
  risk tier as the calculated-column work already proven throughout this file.
- Fixed Pre-rule C (`_fix_dax_aggregations`, the `[Parameters].[Field]` DAX
  rewrite) to resolve to the *right* thing per parameter: the new
  `[Measure Name]` for an enumerable parameter, or the literal current value
  inlined directly for an 'any'-domain one (extending `_param_literal()` to
  handle string/date/boolean values, not just numeric) — fixing the dangling-
  reference bug above for both cases.
- Parameter-control dashboard zones (`<zone type="parameter" param="[...]">` —
  previously silently dropped, in `_LAYOUT_SKIP_TYPES`/`_skip_types` since day
  one) are now collected and rendered as a real slicer bound to the matching
  What-if parameter table, reusing byte-for-byte the same proven slicer JSON
  shape already used for quick-filter zones (chart/card/table visuals'
  `visualType: 'slicer'` construction) — the only change is which table/
  column it points at, so this carries the same low structural risk as the
  reference-line `objects_cfg` reuse in Increment 3.
- New "## Parameters" audit report section: one row per parameter with status
  (✅ got a real What-if table / ⚠️ inlined as a literal, 'any' domain / ❌
  unresolved, no usable value) and a specific detail string.

**Verified**: full 5-workbook regression unchanged (91–96/100, as expected —
only Netflix has a real parameter and it's unenumerable/unreferenced, so
nothing observable changes for it beyond the new audit note); direct unit
tests of `_build_whatif_parameter_table()` for numeric range, string list,
and date range parameters, all producing the exact expected DAX; a full
synthetic end-to-end test (injecting a range parameter + a parameter-control
dashboard zone into a parsed real workbook) confirmed the whole chain — table
creation, `_param_tables` population, and slicer wiring — resolves correctly
(`Min_Price.Min Price` queryRef on the generated slicer visual).

### Increment 7 — Tableau Sets (IN/OUT)

Correction to the gap analysis this item was filed under: `tableau_pbi_server.py`
already had a baseline `<group>`-parsing path (pre-dating this multi-session
effort, not one of Increments 1–6) that handled simple single/multi-member
"Include" Sets as a DAX boolean. It was never exercised by real Sets in the 5
samples (only `Action (...)` sheet-link auto-columns and one internal
filter-widget artifact happened to hit it), and it silently mishandled or
dropped every other Set construction. Schema-verified against the 2026.2 TWB
XSD's `Function-ST` enum and `SetFunction-G`/`Group-G` grammar (a `<group>`
has exactly ONE top-level `<groupfilter>` child — confirmed this rules out a
"flat sibling list" reading of multi-value sets in favour of nested
per-value children under one wrapper, which is what the rewrite assumes).

- **Real bug fixed**: the existing Set/crossjoin code stored the calc's
  display name and `calc_caption_map` entry using the group's internal XML
  `name` instead of its `caption` — every other calc kind in this file
  already prefers caption. A Set renamed after creation keeps its original
  internal name (only `caption` updates), so this silently surfaced the
  stale pre-rename name in Power BI. Fixed for both the boolean-set and
  crossjoin (Combined Field) branches.
- **Real bug fixed**: an auto-generated internal `<group>` backing a
  continuous-field Filter-card widget (`user:ui-builder="filter-group"` —
  found via a real sample, House Sales Dashboard's "MY(Date) Set", confirmed
  never referenced anywhere else in that workbook) was previously falling
  through to the boolean-set path and emitting a dead `FALSE()` calculated
  column for a "Set" that was never a real user-visible field. Now skipped
  outright, the same way `Action (...)` sheet-link groups already were.
- New recursive resolver `_resolve_set_groupfilter()` (:~1918) + helper
  `_set_member_condition()` (:~1898), replacing the old flat single-field
  member-list logic. Both produce a Tableau-SYNTAX formula string (bracket
  field refs, AND/OR/NOT, RANK()) — never raw DAX — so it runs through the
  exact same `_tableau_formula_to_dax` pipeline as any user-authored calc; a
  wrong translation degrades to worse DAX, never a broken/unparseable file.
  Handles:
  - `member` — single or multi-value Include sets (existing behaviour,
    generalised to the correct nested-child XML shape).
  - `except` — Exclude-mode sets → `NOT (...)`. Previously silently treated
    as an ordinary member list with inverted semantics never accounted for
    (a real correctness bug in the pre-existing code, not just a gap).
  - `union` / `intersection` — combined sets, recursively resolving each
    child (which may itself be `member`, `except`, or a `named-set`
    reference to another Set by name — resolved via a name→element lookup
    built once per datasource, so reference order in the XML doesn't matter).
  - `filter` (condition-based "By condition" sets) — its `expression`
    attribute is already a normal Tableau calc formula; passed through
    as-is and translated by the existing pipeline exactly like a
    user-authored calculated field. No new logic needed.
  - `order` (Top/Bottom-N "Top" tab sets, `units="records"` only) — built
    on Tableau's own `RANK()`, already mapped to
    `RANKX(ALLSELECTED('T'), expr, , DESC, Dense)` elsewhere in the pipeline;
    Bottom-N obtained by negating the ranked expression (`RANK(-(expr))`)
    rather than adding a second unverified DAX shape. `percent`/`sample-*`
    units return `None` (honest FALSE() fallback) — no safe count-based cutoff
    without a different (COUNTROWS) formula shape not attempted here.
  - `range` — a continuous-field domain-restriction set with numeric
    `from`/`to` bounds → `[Field] >= from AND [Field] <= to`. Falls back to
    `None` for non-numeric bounds rather than guess (this is also how the
    filter-group date-slider artifact would have looked if it weren't
    already skipped by name above).
  - Anything unrecognised (e.g. `tuples`-based multi-field member sets,
    `relative-date-range` sets) still returns `None` → existing explicit
    `FALSE() /* implement manually */` fallback, unchanged from before.

**Verified**: full 5-workbook regression unchanged, zero exceptions (none of
the 5 samples contain a real user-created Set, so this is additive with no
observable score change — consistent with why this item existed in the gap
analysis at all). Every new groupfilter shape (`except`, `union`,
`intersection`, `named-set` reference, `filter`, `order` top/bottom-N,
`order` with unsupported units, `range` numeric, `range` non-numeric) unit-
tested directly against synthetic schema-conformant XML with the exact
expected Tableau-syntax output, then each surviving formula independently
round-tripped through `_tableau_formula_to_dax` to confirm valid DAX
(`RANKX`/`&&`/`||`/`NOT` all correct). Full end-to-end regression test: a
multi-value Include Set with an internal name deliberately diverging from
its caption (simulating a post-creation rename) injected into the real House
Sales Dashboard XML — confirmed the emitted calc is named after the caption
("VIP Condition Set", not the stale internal name), the formula is the
correct union-of-members condition, and `write_pbit()` completes with zero
exceptions.

**Known remaining gap, deliberately not attempted**: `tuples`-based
multi-field member sets and `relative-date-range` sets have no real sample
to verify against and are rarer in practice than what's now covered; they
still degrade to the existing honest `FALSE()` fallback rather than a guess.

### Roadmap item #8 (Groups/bins → native PBI groups) — evaluated, blocked

Checked before attempting, per the same "verify before implementing"
discipline as everything else in this file. Power BI Desktop's "native"
Group edit-ability (the pencil-icon "Edit groups..." dialog) requires
Desktop to recognise a calculated column as group-backed, which needs some
internal metadata block beyond the DAX formula itself. Fetched the official
public TMSL schema reference (`tables-object-tmsl.md` in
`MicrosoftDocs/bi-shared-docs`, which documents `Column` inline) directly —
zero mentions of grouping, binning, discretization, or any
`RelatedColumnDetails`-style property anywhere in it. That's the same
evidence tier that blocked item #5b (Waterfall/Box Plot/Gantt): an
undocumented Desktop-internal format, not a publicly verifiable one:
guessing at its shape risks corrupting the calculated column (worse than
today's working state) for a benefit that's purely authoring-convenience —
the roadmap's own original note already says the current SWITCH-based
calculated-column approach is visually/functionally equivalent. Not
attempted; treat as blocked the same way as #5b rather than a silent skip.

### Increment 8 — Stories (roadmap item #9)

Schema-verified against the 2026.2 TWB XSD: Tableau represents a Story
internally as `<dashboard type="storyboard">` (`Dashboard-DashboardType-ST`)
whose real content is a nested `<flipboard><story-points><story-point
id=... captured-sheet=... caption=.../></story-points></flipboard>` — each
story-point's `captured-sheet` points at a worksheet's `name`.

- **Real bug fixed**: confirmed via grep that nothing in the file had any
  `storyboard`/`flipboard`/`story-point` handling before this — a Story's
  `<dashboard>` element was falling straight into the generic
  zone-collection/layout-tree code (used for real dashboards), which has no
  carve-out for the 'flipboard' zone type. That zone's `name` doesn't
  reference a real worksheet, so a Story would previously have produced an
  empty or broken visual on its own page instead of being recognised as a
  Story at all — a real latent correctness gap, not just a missing feature.
- Storyboards are now detected and excluded from the regular
  `dashboards` list at parse time, parsed into a new `workbook['stories']`
  list instead (name + ordered story-points).
- `build_report_layout()` emits one page per story-point, in the story's own
  order (sorted by the XSD's `id` attribute), reusing byte-for-byte the same
  single-zone dashboard-style page shape already proven for the existing
  orphan-worksheet 'Worksheets' page — no new rendering logic. Page name is
  `"<Story name>: <caption>"` when the point has a caption, else `"<Story
  name> (<n>)"`, de-duplicated defensively if two points would collide.
  Each captured worksheet is marked "referenced" before the orphan-page
  computation runs, so it isn't also duplicated onto the generic
  'Worksheets' page.
- Power BI's page-level bookmark/button-navigation JSON is exactly the kind
  of unverified internal format this file has consistently declined to
  guess at (same call as Increment 3's dashboard actions) — so Tableau's
  story navigation strip/arrows are NOT wired up. Instead, a new "## Stories"
  audit report section (mirroring the existing "## Dashboard Actions"
  section's pattern) names the story, confirms the page count/order, and
  gives a specific manual recommendation: add a Bookmark per page + a
  Back/Next button pair.

**Verified**: full 5-workbook regression unchanged (none of the 5 samples
contain a real Story). Two synthetic-injection end-to-end tests since no
real sample has one: (1) a 2-point story injected into House Sales Dashboard
confirmed correct `id`-based ordering (points given out of document order)
and correct caption-vs-fallback page naming; (2) a 2-point story injected
into LocBar (which has 13 real orphan worksheets) referencing two of those
orphans confirmed the orphan count dropped from 13 → 11 with exactly those
two worksheet names removed from the orphan page's zone list, the two new
story pages were created with correct names, and the audit's "## Stories"
section rendered the expected recommendation text. `write_pbit()` completed
with zero exceptions in both cases.

---

## Framework Engineering Phase (started 2026-07-08/09)

**Context shift, explicit from the user**: after Increment 8, the user
redirected the whole engagement from "keep fixing bugs against the known
sample workbooks" to "build a generic, metadata-driven migration engine that
must work for arbitrary future Tableau workbooks, with no workbook-specific
logic anywhere." This is a standing instruction for all future work on this
file, not just the increments below — see [[feedback-migration-engine-workflow]]
for how it composes with the pre-existing incremental-verification rules
(still in force: no wholesale rewrites, one component at a time, verify
against real workbooks after every change — this phase just applies that
discipline to *architecture*, not just features).

**No git anywhere in this repo.** Asked the user explicitly whether to
`git init` before starting risky structural refactors; they said no. Given
that, treat any large structural edit as needing its own manual safety net —
copy the file to a scratch backup before a big change, verified-diff after.
**Important for a fresh session**: backups made during a session live under
that session's `/private/tmp/claude-501/.../scratchpad/backups/` path and do
**not** persist to a new session — if resuming this work from scratch, make
a fresh backup copy of `tableau_pbi_server.py` before touching anything
structural again, don't assume an old backup path still exists.

### Gap analysis (real-workbook, ground-truth-verified — not guessed)

Before any refactor, converted the 3 new real workbooks
(`source/AB_NYC Dashboard.twbx`, `HR Analytics.twbx`, `Sales Param.twbx`)
with the engine as it stood after Increment 8, and compared the *actual*
resolved chart type / category / value / legend for every worksheet against
the raw Tableau XML (rows/cols shelves, pane marks, encodings) — not against
assumptions. This found real, confirmed bugs, several of which affected
worksheets that were already "working" (not just fallback cases):

- Circle-mark + Size-encoding worksheets classified as `scatterChart` never
  wired the Size role — Power BI's scatter chart *is* the bubble chart once
  Size is populated, not a separate visual.
- `GanttBar` mark unconditionally forced to `areaChart` even when no date
  field exists anywhere on the worksheet (i.e. not a real Gantt at all, just
  Tableau's rendering technique for a plain bar) — now checks for a real
  date-typed axis field first, falling back to Bar/Column otherwise.
- Packed-Bubble and Treemap charts in the **dashboard-zone rendering path**
  (see next section for why this path matters most) pulled a **completely
  unrelated measure** instead of the actual size/color encoding, because the
  generic shelf-based value resolver has nothing to find when a worksheet's
  real value lives in an encoding, not a shelf.
- A worksheet literally named "KPI" (Tableau's `:Measure Names` + Measure
  Values-as-text pattern, filtered to N metrics) had no detection at all and
  fell back to a generic table — implemented real Power BI **Multi-row
  Card** support end to end (new `_make_multirow_card_vc`).
- `_nz()` (a name-normalization helper used throughout the zone-rendering
  path) stripped ALL non-alphanumeric characters, silently colliding a
  calculated field like `"Attrition #"` with an unrelated base column
  `"Attrition"` — this broke `Attrition #` on *every* chart that used it
  (a bar chart, a treemap size, an area chart), not just one. Fixed to only
  strip whitespace/hyphen/underscore.
- `_clean_field_name`'s aggregation-prefix regex was missing `med` (median)
  and `cnt` (count) — both real Tableau shelf-token prefixes used elsewhere
  in this same file.
- A `projectionActiveItems` queryRef/Select-Name mismatch for any
  `extra_fields` role (treemap/bubble Size, Values, Details) — a real
  "could prompt a repair dialog in Power BI Desktop" risk, fixed by always
  dispatching through the existing flag-based `_val_qref()` regardless of
  role name.

All fixes verified against all 8 workbooks (zero exceptions) at each step;
several were deliberately **not** attempted after real-world testing showed
they made a case worse (see "Sales Param 'Line graph'" below) — reverted
rather than shipped once evidence contradicted the change.

### Architectural review (before any refactor code was written)

- Searched for hardcoded workbook-name / sheet-name / field-name conditional
  logic across both files. **Found none** — every hit was either a comment
  documenting which real sample motivated a fix, or a genuine Tableau/schema
  reserved token (`'Parameters'` datasource, `'Double'`/`'Date'` datatypes,
  this codebase's own internal classification labels). The "no
  workbook-specific hacks" constraint was already satisfied; nothing to
  clean up there.
- **The real structural problem**: `build_report_layout()` is one ~3,000-line
  function containing **two independent, parallel implementations** of
  chart-type → Power BI visual JSON generation:
  - the **dashboard-zone path** (dispatches on `z_chart`, renders every
    dashboard, the auto-generated orphan-worksheets page, and Story pages —
    i.e. **every page a real workbook actually produces**)
  - the **standalone-page path** (dispatches on `chart_type`, ~1,000 lines
    of the most historically-hardened logic in the file — Quarter/Year
    auto-grouping, ratio-vs-value auto-combo-upgrade, ctd/cnt shelf-token
    handling, bin-calc matching, hierarchy levels)
  - **Confirmed via grepping every `pages.append(...)` call site**: in
    normal operation every page gets `is_dashboard=True` (dashboards, the
    orphan-worksheets page, Story pages), so **the standalone-page path is
    dead code** except for the pathological "workbook has zero dashboards
    and zero worksheets" case. This means the page path's sophistication
    was real but *never affected any actual output* — and the zone path's
    much thinner logic *is* what every real report has been built from.
    Bringing the page path's logic into the zone path (not just extracting
    duplication) is where the actual user-visible improvement comes from.

### Phase 1 — shared field-resolution helpers (small chart types)

New module-level functions (placed just before `_make_chart_vc`), called
identically by both rendering paths:
- `_field_norm_key()` / `_lookup_col()` / `_lookup_measure()` — the corrected,
  shared name-normalization + lookup helpers (see `_nz()` bug above).
- `_resolve_treemap_bubble_fields()` — Treemap + packed-Bubble size/color/
  detail resolution. Preserved a real feature the zone path never had
  (Treemap's "Group" second-hierarchy-level role, ported from the page path)
  and dropped one confirmed dead-code branch (`_extra_roles`, zero
  downstream consumers — grepped to confirm before deleting).
- `_resolve_scatter_size_extra()` — scatter chart optional Size role.
- `_resolve_multirow_card_fields()` — Measure-Names KPI-strip resolution;
  also added Multi-row Card support to the **page** path (previously had
  none at all, a genericity gap for whatever future workbook does route a
  KPI-strip worksheet through that path).

**Verified**: all 8 workbooks, zero exceptions; every previously-fixed
worksheet re-checked and produced byte-identical (or further improved)
results after the consolidation.

### Phase 2, slice 1 — shared measure/value resolution + scatter detection

The two biggest, highest-traffic pieces:

- **`_resolve_measure_values()`** — the full Y-axis/value priority chain for
  bar/column/line/combo charts: horizontal-bar axis swap, ctd:/cnt:
  shelf-token handling, side-by-side multi-measure detection, `:Measure
  Names` merging, ratio-vs-value auto-combo-upgrade. This is the ~250-line
  page-path chain, now the *only* implementation, called by both paths.
- **`_resolve_scatter_xy_fields()`** — scatter charts pulled into their own
  dedicated two-axis resolver (X and Y are genuinely independent columns;
  forcing them through the value-chain resolver structurally can't handle
  that — confirmed by real regressions below).

**Verification methodology** (stronger than "no exceptions" — this is the
method to reuse for the remaining slices): after each change, ran a
before/after diff of the resolved chart type + category + value + legend for
**every worksheet in all 8 workbooks** against the pre-change baseline, then
traced every difference back to the real Tableau XML to classify it as a fix
or a regression, rather than assuming plausibility. This caught and fixed,
in order (each confirmed against real ground truth, not assumption):

1. LocBar's "LocBar" worksheet (horizontal bar): resolved to the category
   dimension instead of the measure — bare `x_field`/`y_field` can be empty
   or wrong depending on chart orientation; fixed by trying
   `all_col_fields`/`all_row_measure_fields` as an ordered candidate list
   before the bare field name (this exact pattern recurred 3 more times
   below — it's the single most common root cause found this phase).
2. LocBar's "Sheet 3"/"Sheet 4" (multi-measure horizontal bar): dropped 2 of
   3 measures — the old hbar path only ever resolved one value and stopped;
   unified hbar and non-hbar onto the same side-by-side multi-measure scan.
3. LocBar's "Sheet 3"/"Sheet 4" again, after fix #2: **false positive** in
   the ratio-vs-value auto-combo heuristic — bare `"Margin"` (here a plain
   `Sale Amt − Cost Amt` subtraction, confirmed via its formula, not a
   percentage) triggered an unwanted auto-upgrade to combo chart just from
   the name. Tightened: ambiguous keywords (`margin`, `rate`, `return`, …)
   now require the formula itself to contain a division; unambiguous ones
   (`pct`, `percent`, `profitmargin`) still fire on name alone.
4. AB_NYC's "Reviews by Neighbourhood Group", HR Analytics' "Current role vs
   last prom" (scatter): lost one of two axes — same candidate-list gap as
   #1, applied to `_resolve_scatter_xy_fields`.
5. LocBar's "Sheet 14" (scatter with a `ctd:` shelf token on one axis):
   after the candidate-list fix, both axes resolved to the *same* field
   (duplicate) — added ctd/DISTINCTCOUNT handling to the scatter resolver
   too (previously scatter had none at all) plus an explicit "don't resolve
   Y to the same field X already took" exclusion.
6. LocBar's "Sheet 12": lost one of two measures — `shelf_row_fields`
   contained raw, unresolvable tokens (`cum:sum:Profit`) while the cleaned
   `all_row_measure_fields` list had it correctly; **first attempt** (merge
   both lists into one scan) caused a *new* bug — LocBar's "DSO vs DPO"
   started duplicating measures under two different names (`"DPO"` and
   `"Sum of DPO"`, resolved via two different paths for the same field, so
   name-based dedup couldn't catch it). Reverted the merge; fixed properly
   with a "try the given shelf; only if it yields *nothing at all*, retry
   wholesale with the cleaned fallback list" pattern — never mixes results
   from two sources in one output.
7. One diff investigated and confirmed as **already correct** on the new
   code, not a regression: Finance Dashboard's "DSO vs DPO" losing a
   `"Color Gap"` value — traced its formula and confirmed it's a
   color-conditional-formatting calc, not a real value measure (same
   pattern as the pre-existing "Sales Threshold" issue below); the old
   zone-path fallback was wrongly including it via an overly broad
   "grab any calc in scope" last resort.

**Tried and reverted** (kept as a documented decision, not silently
dropped): a fix for Sales Param's "Line graph" (a Tableau technique that
duplicates one measure across panes to give it a second mark-type layer —
confirmed via the `y-index="1"` pane attribute) was attempted by requiring
2+ *distinct* measures before routing to combo chart. Real-workbook testing
showed this exposed a **different, deeper** bug instead — a continuous
date-truncation shelf token (`tmn:Field:qk`) gets misclassified as a second
measure elsewhere in the chart-type cascade, so the "fixed" case fell into
an even worse branch (`scatterChart` with a non-numeric field as Y).
Reverted to the original always-combo behavior (cosmetically duplicates one
line, but still shows the real measure) rather than ship a worse trade.
**This date-truncation misclassification is a real, still-open bug** — see
"Not yet done" below.

All 8 workbooks re-verified with zero exceptions after every single fix
above, not just at the end of the slice.

**Stopped here deliberately** (user instruction: "Stop here update
migration_engine_process document to continue on next window or next session
or day") — not blocked, just a planned checkpoint. `tableau_pbi_server.py`
compiles clean and all 8 workbooks convert with zero exceptions as of this
checkpoint; it is safe to pick up from here.

---

### Phase 2, slice 2 — shared category resolution

New module-level `_resolve_category_fields()` (next to `_resolve_measure_values`),
consolidating what used to be a thin shelf-based scan in the zone path's
`_resolve_cat` (no bin-calc, no date-hierarchy, no multi-field-grouping
support at all) and a more sophisticated bare-field priority chain that only
existed in the dead standalone-page path. Both `_resolve_cat` (zone path,
now a thin wrapper that builds the candidate list and delegates) and the
standalone-page path's inline category block now call the same function.
Priority order preserved from the page path: bin-calc match (checked across
the whole candidate list, not just the bare field) → DATEPART date-hierarchy
match (emits one `HierarchyLevel` cat entry per grouping level) → plain
column resolution off an ORDERED CANDIDATE LIST (the same recurring fix
pattern as the value-resolution slice — a bare `x_field`/`y_field` can be
wrong for some chart orientations) → multi-field column-shelf grouping
(vertical charts only) → final fallback (first non-numeric, non-measure
column in scope).

**Verification**: same before/after per-worksheet diff methodology as slice
1, capturing `_make_chart_vc`'s actual `category_fields`/`value_fields`/
`legend_fields` arguments (via a monkeypatch wrapper, not guessing from
output JSON) for every worksheet across all 8 workbooks, then tracing every
difference to the real Tableau XML. 9 diffs found, all traced and classified:

1. Finance Dashboard's "Revenue vs Profit Margin": category
   `['Quarter']` → `['Quarter', 'Client', 'Year']` — **confirmed fix**. Cols
   shelf XML is `Quarter / (Client / Year)`; this is the exact multi-field
   grouping case the original page-path comment named as its motivating
   example, now finally live (it never executed before this slice — the
   zone path had no multi-field-grouping code at all).
2. House Sales Dashboard's "Distribution of House Prices",
   "Bedrooms/Bathrooms Distribution By Grade": category `['Date']` →
   `['Price (bin)']` / `['Grade (bin)']` — **confirmed fix, and a real
   pre-existing bug**. These are histogram-style bin charts; cols shelf is
   literally `none:Price (bin):qk` / a bin calculated column. The zone path
   had zero bin-calc detection (`_fc` only looks in `z_fcols`, and a bin
   calc lives in `ds['calculations']`), so it silently fell through to the
   wrong fallback column ("Date") for these three worksheets. Now correctly
   resolves to the bin calc.
3. LocBar's "Prod Bar" (`['Category']` → `+['Sub-Category']`), "CustBar"
   (`['Customer Name']` → `+['Segment']`), "Filters"
   (`['Category']` → `+['Order Date']`), Finance's combo chart above —
   all **confirmed fix** against real multi-field cols-shelf XML.
4. LocBar's "Sheet 4" (hbar): category showed as `['Order Date', 'Order
   Date']` in the raw diff — looked like a duplicate at first glance, but
   re-captured with `hierarchy_level` included and confirmed **correct**:
   two distinct `HierarchyLevel` entries (Year, Month), matching the rows
   shelf's `yr:Order Date:ok` + `mn:Order Date:ok` pair. Not a regression —
   the diagnostic script's first pass just didn't surface the
   distinguishing field.

**Found, NOT fixed in this slice (reported instead, per the "one at a time"
workflow)**: LocBar's "Custpie" (a `pieChart`) showed its category tracking
whatever "CustBar" (the immediately preceding zone) resolved, both before
and after this change — confirmed via real XML that Custpie's own cols
shelf is `Customer Name` alone, no `Segment`. Root cause: the zone-path's
already-written `_resolve_pie_cat`/`_resolve_pie_val` helper functions (see
`build_report_layout`, defined near `_resolve_leg`) are **never called
anywhere** — pie/donut zones reuse whatever `cat_f`/`val_f` variables were
left over from the previous zone in the same `for zone in
page.get('db_zones', [])` loop iteration, because those variables are never
reset between zones and pie/donut has no dedicated resolution call. This is
a **pre-existing latent bug**, not something this slice introduced — it
just happened to change the leaked value's shape (CustBar's newly-correct
`+Segment` leaked into Custpie too). Likely affects every workbook with a
pie/donut chart that isn't the first zone processed in its dashboard.
Belongs naturally with the already-planned "Pie/donut consolidation" item
(#4 below) — flagging here so that slice fixes this specific bug (wire up
the existing unused `_resolve_pie_cat`/`_resolve_pie_val` functions) rather
than just deduplicating cosmetically.

All 8 workbooks re-verified with zero exceptions after the change, plus a
full `write_pbit()` end-to-end pass (not just `build_report_layout`) for all
8 — confirms the fix holds through actual `.pbit` file generation, not just
the intermediate layout structure.

---

### Phase 2, slice 3 — pie/donut consolidation (item #4, promoted ahead of item #2)

Fixed the confirmed bug from slice 2 immediately rather than letting it sit:
new module-level `_resolve_pie_fields()` (color-encoding → Category, with
action-filter exclusion; wedge-size → Aggregation(Column) unless it's itself
a real calc, with a 3-tier priority chain and 'Doughnut (copy)'-artifact
exclusion — the richer logic that previously only existed, unexecuted, in
the standalone-page path). Both the zone path (new `_resolve_pie` wrapper,
replacing the never-called `_resolve_pie_cat`/`_resolve_pie_val`, which are
deleted) and the standalone-page path's inline pie/donut block now call it.
One deliberate behavior change vs. the old page-path version: wedge-size
resolution now tries a DAX-measure match **before** a same-named raw column
(previously column-first) — matching the measure-first convention every
other shared resolver in this phase uses (`_resolve_treemap_bubble_fields`,
`_resolve_scatter_size_extra`, `_scan_measure_shelf`), and consistent with
the earlier "Attrition #" naming-collision lesson (prefer the calc's own
identity when a name could plausibly match either).

**Verification**: same before/after `_make_chart_vc`-argument-capture diff as
slices 1-2, all 8 workbooks, zero exceptions, full `write_pbit()` pass. All 4
pie/donut charts across the 8 workbooks changed (every single one was wrong
before — this was not an edge case):

- Amazon Sales Insights "Customer Type By Revenue": category
  `Product Code (Products.Csv)` → `Customer Type` — confirmed against
  `<color column='...none:customer_type:nk'>`; value unchanged
  (`Normalized Amount`, already correct).
- Finance Dashboard "Revenue": category `Month` → `Client`, value
  `[Cash Ratio, Quick Ratio, Current Ratio]` → `Total` — confirmed against
  `<color column='...Client'>` / `<wedge-size column='...pcto:sum:Total...'>`.
  The before-state (3 unrelated ratio measures on a donut chart) was the
  clearest possible proof this was leaking a stale value from a prior zone.
- Finance Dashboard "GrossProfit": same category fix (→ `Client`), value →
  `Cal_Total` — confirmed via `<wedge-size>` and the calc's own
  `caption='Cal_Total'`.
- LocBar "Custpie": category `[Customer Name, Segment]` → `[Segment]` —
  initially looked like a possible over-correction (worksheet's `<cols>`
  shelf says `Customer Name`), but the pie chart's real category source is
  the `<color>` **encoding**, not the `<cols>` shelf (which for a pie mark
  is just Tableau's internal single-pane layout plumbing) — and
  `<color column='...none:Segment:nk'>` confirms `Segment` is correct.
  Value (`Sale Amt`) unchanged, already correct.

Item #4 is now done. Remaining from the original Phase 2 list: item #2
(shared legend resolution) and item #3 (shared combo bar/line role
assignment).

---

### Phase 2, slice 4 — shared legend resolution (item #2)

New module-level `_resolve_legend_fields()`, merging the best of both prior
copies: the page path's dedup-against-category (name-based, not object
identity — a color field that resolves to the SAME column already used for
Category should not also become a redundant Series role) and calc-based
legend support (a color encoding that's a Tableau calculated field, with
boolean/comparison calcs excluded since they produce True/False, not a
usable Series dimension); plus the zone path's numeric-dtype exclusion
(kept as a safety net the page path didn't have — a raw numeric column
resolving as color shouldn't become a per-value legend). Both the zone
path's `_resolve_leg` (now a thin wrapper) and the page path's inline block
call it.

**Verification**: same before/after `_make_chart_vc`-argument-capture diff,
all 8 workbooks, zero exceptions, full `write_pbit()` pass. 6 diffs, all the
*same* fix pattern and all confirmed correct: legend went from `[X]` (same
field name as the category) to `[]`. Traced Finance Dashboard's "Cash Flow"
to real XML: `<cols>Activity</cols>` and `<color column='...Activity'>` —
the same field, confirmed via direct XML read, not assumption. Previously
this fed the *same* column into both Category and Legend/Series roles,
which is exactly the "identity bug" a stale code comment already described
(`c not in cat_f` never worked because `_as_col_item(c)` builds a new dict
each time) — the dedup fix was written once before (page path) but never
executed until this slice. The other 5 diffs (LocBar's "Prod Bar",
"CustBar", "Sheet 3", "LocBar", "Sheet 12") are the same pattern, not
independently re-verified against XML one-by-one since the mechanism is
identical and already confirmed.

Item #2 is now done. Remaining from the original Phase 2 list: item #3
(shared combo bar/line role assignment) — the last one.

---

### Phase 2, slice 5 — shared combo bar/line role assignment (item #3, LAST Phase 2 item)

New module-level `_resolve_combo_roles()`. Ported **only** "Pattern A"
(Measure Names filter active → mnff measures go Y2/line, any other
resolved measure goes Y/bar) from the page path into the zone path (which
had no equivalent at all — a real Tableau-authored combo chart with 2+
measures relied purely on `_make_chart_vc`'s blind "no tags → first measure
bar, rest line" fallback). Both paths now call the shared function; the
zone path's call sits right after `_resolve_measure_values` and is a no-op
when that function's own ratio-vs-value auto-upgrade already tagged
`_combo_role` (kept untouched — already verified in slice 1).

**Rejected during verification, not shipped**: the page path's original
"Pattern B" (no Measure Names filter → map each pane's mark type to
`shelf_row_fields` by position: Bar/GanttBar → bar-axis, else → line-axis).
Ported it first, ran the standard before/after diff, and it broke a case
that was *already correct*: Finance Dashboard's "Revenue vs Profit Margin"
flipped from `[Sum of Revenue(bar), Profit Margin(line)]` — correct, and
already verified back in slice 1 — to `[Profit Margin(bar), Sum of
Revenue(line)]`, exactly backwards. Root cause, confirmed by reading the
real pane XML: this worksheet's `<panes>` list isn't one-pane-per-measure —
it has an extra `Automatic`-mark pane carrying a `:Measure Names` color
encoding, not a real second measure — so a positional zip between pane
marks and shelf fields is fundamentally unreliable here, not just an edge
case in this one worksheet. Rather than ship a plausible-looking but wrong
heuristic, Pattern B was dropped entirely (same discipline as the
already-blocked Waterfall/Box Plot/Gantt native-mapping items): charts
without an mnff pattern keep relying on `_make_chart_vc`'s existing
untagged default, which was already correct for every real sample. Removed
the now-unused `pane_marks` plumbing that was added to `_ws_to_page()` to
support the reverted attempt.

**Verification (combo roles)**: same before/after `_make_chart_vc`-argument-
capture diff (this time also capturing each value field's `_combo_role`,
not just its name), all 8 workbooks, zero exceptions, full `write_pbit()`
pass, **zero** diffs versus the pre-slice baseline — Pattern A only adds
explicit tags to a case (Finance Dashboard's "DSO vs DPO", confirmed via its
pane marks Line/Line/Bar and Measure-Names rows-shelf token) that was
already producing the right Y/Y2 split by coincidence of resolution order;
making it an explicit, robust rule rather than an accident is the actual
improvement, not a behavior change on the 8 known workbooks.

Item #3's original scope also bundled two more page-path-only pieces (both
also ported into shared, zone-path-callable functions in this same slice,
since the roadmap listed them together):

- **`_maybe_downgrade_stacked()`** — a stacked bar/column chart with no
  separate Series dimension has nothing to stack; downgrade to the
  clustered equivalent. Verified: fired for exactly the 4 zones that lost
  their (redundant, same-as-category) legend in slice 4 — Finance
  Dashboard's "Cash Flow", LocBar's "Prod Bar"/"CustBar"/"LocBar" — each
  went from a bare `columnChart`/`barChart` visual type to
  `clusteredColumnChart`/`clusteredBarChart`. Zero other diffs.
- **`_resolve_default_sort()`** — when Tableau specified no explicit sort,
  default column/bar charts to descending-by-primary-measure (matching how
  PBI authors typically build these charts), except when the category axis
  is a time-period field (Quarter/Month/Year/Week/Day), which sorts
  ascending by category instead so periods read in natural order. This is a
  broad, previously-completely-absent behavior in the zone path (it only
  ever passed through Tableau's own explicit sort spec, defaulting to no
  sort/natural order otherwise) — 24 zones across all 8 workbooks gained an
  explicit sort where none existed before. Verified a sample directly
  against XML: Amazon Sales Insights' "Revenue by Zone" has no `<sort>`
  element at all (confirmed via direct XML search), so this is genuinely
  filling an absence, not overriding a Tableau-authored order; Finance
  Dashboard's " Accounts Payable, Accounts Receivable, and Inventory" (a
  Quarter-categorized column chart) correctly got the ascending-by-Quarter
  exception instead of descending-by-measure. This directly addresses the
  master spec's explicitly named issue "Sorting not preserved."

**Phase 2 is now fully complete** (items #1 category, #2 legend, #3 combo
roles, #4 pie/donut — all four consolidated into shared, zone-path-and-page-
path functions, verified against real Tableau XML with zero regressions
across all 8 sample workbooks). See "How to resume" below for what's next.

### Increment 9 — New test fixtures + two real generic bugs found and fixed

**New test fixtures** (2 more added to `source/`, 10 total now across
`input/` + `source/`): `superstore_profits.twbx` (a **live Excel connection**
— `<Data/source/Sample - Superstore Sales (Excel).xls>` embedded in the
package, not an extract — the first live-non-extract fixture in the set) and
`superstore_profits_extract.twbx` (the same data as a Hyper extract). A loose
copy of `Sample - Superstore Sales (Excel).xls` also sits in `source/` — it's
just the raw data underlying the two `.twbx` files above, not a separate
migration target (this engine only migrates `.twb`/`.twbx`). Both new
fixtures convert cleanly with zero exceptions; the live-Excel one correctly
generates a real `Excel.Workbook(File.Contents(...))` M query (verified by
inspecting the generated `.pbit`'s `DataModelSchema` directly), not a
hardcoded/embedded-data path.

**Bug 1 — silent "dates become 1900" fallback (master spec §6/§15, a named
issue).** Root-caused by inspecting `target/Global Payment 2016-2025
Dashboard.pbit` (already-generated output from a prior session; its source
`.twbx` is no longer in `source/`) — its `Financial Years` column showed
`#date(1900,1,1)` for **every single row**, exactly matching the user's
complaint. Traced to `_get_embedded_rows`'s date-value branch (was inline,
now factored out to a new module-level `_date_value_to_m_literal()` at
~line 7532): any date value that didn't match a strict
`YYYY-MM-DD(THH:MM:SS)` regex silently fell back to a fixed placeholder date
with **no warning at all** — the exact "audit lies" failure mode this whole
engagement exists to fix, just in generated data instead of the report. Since
the original source workbook is gone, the precise raw value that failed to
parse (for "Financial Years", most likely extracted at year-only granularity,
e.g. a bare "2016") can't be recovered — so the fix is generic, not
guessed-to-fit one workbook:
1. Native `date`/`datetime`-like values (duck-typed via `.year`/`.month`/
   `.day` — covers `tableauhyperapi.Date`/`Timestamp` and `pandas.Timestamp`
   from a real Tier-1 SDK extraction on Windows) are read directly, no
   string round-trip at all.
2. The existing ISO regex still matches first for strings.
3. New: common alternate separators (`M/D/YYYY`, `D/M/YYYY`, `YYYY/M/D`,
   `M-D-YYYY`, `D-M-YYYY`) via `datetime.strptime`.
4. New: a bare 4-digit year (e.g. a Tableau "Financial Year" field extracted
   at year granularity) → `#date(YYYY,1,1)`.
5. Only if ALL of the above fail does it fall back to `#date(1900,1,1)` —
   and now `_get_embedded_rows` tracks failures per-column and emits **one
   summary WARN log per column** (count + a raw-value sample) instead of
   silently emitting the same wrong date for every row. Verified via 8 unit
   test cases (ISO string, ISO datetime, native `date`/`datetime` objects,
   US-style string, bare year, and a genuinely unparseable string) — all
   produce the expected literal — plus a full regression + before/after
   `DataModelSchema` diff across all 5 originally-affected-path workbooks
   (only lineage-tag GUIDs changed, zero data differences, since Tier-2
   synthesis already produced valid ISO strings for all of them — this fix's
   effect is only visible on a real Tier-1 SDK extraction, which isn't
   testable on this machine; `pip install tableauhyperapi`/`pantab` both
   fail here with `externally-managed-environment`).

**Bug 2 — `_lookup_measure`/`_lookup_col` prefix-match false positive (found
while investigating Bug 1's neighborhood, confirmed via a live regression
diff, not guessed).** Both shared lookup helpers had a "prefix match" fallback
intended to handle Tableau's rename-duplicate convention (`"Accounts
Payable"` should still find a calc literally named `"Accounts Payable
(copy)"`), implemented as a bare `_field_norm_key(candidate).startswith(nn)`.
This also matches any UNRELATED longer name that happens to start with the
same characters. Confirmed against Sales Param's real "Line graph" worksheet:
looking up base column `"Sales"` incorrectly resolved to the unrelated calc
`"Sales Threshold"` (`"salesthreshold".startswith("sales")`) purely by
coincidence of naming — sending a text-valued Good/Bad calc into a numeric
Y-value role, which would either error or blank out when Power BI tries to
aggregate it. Fixed with a new shared `_is_copy_suffix_of()` helper that
requires the matched remainder to actually look like a copy-marker
(`^\(copy\s*\d*\)$`), not just any suffix — used by `_lookup_measure`,
`_lookup_col`, and a third near-duplicate local closure (`_find_msr` in the
dead standalone-page path, fixed for consistency even though that path never
runs on a real dashboard). **Verified with the established before/after
`_make_chart_vc`-argument-diff methodology across all 10 real fixtures**
(all 8 previous + the 2 new Superstore ones): exactly one diff anywhere —
Sales Param's "Line graph" now correctly resolves `val=['Sum of Sales']`
instead of `val=['Sales Threshold']` — zero changes to any other zone in any
other workbook.

**Bug 3 — KPI-card resolver ignored calculated columns, fixed in the same
session's follow-up pass.** The "Sales Value" investigation above pointed at
the wrong subsystem (parameters), but the real root cause turned out to be
much simpler and more general. The card-resolution block (zone path, the one
every real dashboard uses) only searched `z_fagg` (aggregating DAX
calcs/measures) for the worksheet's real text-encoding field, then — if that
missed — fell back to the literally-arbitrary `z_fagg[0]` (whatever
calc happened to be first in registration order) instead of ALSO checking
`z_fcols`, where `_filter_ds()` already correctly merges non-aggregating
calcs (calculated columns). A calc whose formula has no aggregation function
— e.g. `Sales Param`'s `"Paramter1"` (`formula='[Parameters].[Parameter 1]'`,
a bare parameter passthrough) or `Amazon Sales Insights`' `"Normalized
Amount"` (`formula='If [currency]="USD" Then abs([sales_amount])*80 Else
...'`, a row-level currency conversion) — is correctly classified
`is_agg_calc=False` and therefore never appears in `z_fagg`, so the card
resolver always missed it and silently substituted an unrelated calc
instead. Fixed by trying `_fc(z_tf, z_fcols)` between the exact-match
`_fm()` lookup and the `z_fagg[0]` last-resort fallback. **Verified via
before/after `_make_card_vc`-argument diff across all 10 fixtures: exactly
2 cards changed, both confirmed correct against real Tableau XML** — Sales
Param's "Sales Value" now shows `'Paramter1'` (was the unrelated `'Sales
Threshold'`), and Amazon Sales Insights' "Revenue" card now shows the
worksheet's real text-encoding field `'Normalized Amount'` (was the
unrelated `'Sales Amount'` — this one had been silently wrong since before
this session even started, just unnoticed because "Sales Amount" sounds
plausible for a card named "Revenue"). Zero other cards/charts changed.

**Also fixed for consistency (no behavior change on current fixtures,
confirmed via the same diff — zero effect, pure hardening):** the
zone-rendering scope's own local `_fc`/`_fm` closures had the identical
blind-`startswith()` prefix-match bug as Bug 2's `_lookup_col`/
`_lookup_measure` (not yet caught because Bug 2's fix only touched the
shared module-level functions, not this separate local pair). Now both use
the same `_is_copy_suffix_of()` helper.

**Still open, not yet investigated further:** none from this session — the
"Sales Value" lead from earlier is now fully explained and fixed (it was
never actually about the `Parameters` pseudo-datasource; that was a false
lead from investigating the symptom, not the cause).

### Increment 10 — `show-title` zone attribute (master spec §13, "titles")

Picked from a fresh architectural pass over §13 Dashboard Layout, since no
specific slice was queued after Increment 9. Grepped for `show-title`
handling — zero matches anywhere in `tableau_pbi_server.py`. Confirmed via
the real XSD/zone shape that Tableau's `<zone show-title="false">` is a
genuine, common, schema-verified per-instance attribute (a dashboard author
hiding one worksheet's title bar on one specific dashboard placement, e.g.
when a KPI card's number is self-explanatory or a text zone already labels
the area) — not workbook-specific, present in **3 of the 10 fixtures, 41
zone occurrences total** (`Amazon Sales Insights.twbx`: 8,
`Finance Dashboard_v2026.1.twbx`: 29, `HR Analytics.twbx`: 4). Every
generated visual previously forced `'show': true` unconditionally,
regardless of this attribute — a real, silent visual-fidelity mismatch
against master spec §13/§14's "titles" requirement.

Fixed generically, not per-workbook:
1. `_collect_zone()` (the function that walks `<dashboard><zones>` into the
   per-instance `db_zones`/`chart_zones`/etc. lists) now captures
   `show_title = z.get('show-title', 'true').strip().lower() != 'false'`
   into each zone's entry dict. This is deliberately captured **per zone
   instance**, not baked into the worksheet's own metadata (`zone_pg`) —
   the same worksheet could appear on two different dashboards with
   different show-title settings in each placement, and a worksheet-level
   flag would conflate them.
2. Added a `show_title: bool = True` parameter (default preserves prior
   behavior everywhere) to all 5 visual-constructor functions used by the
   zone-rendering path: `_make_chart_vc`, `_make_map_vc` (2 separate title
   blocks inside it — filledMap and ordinary-map branches), `_make_card_vc`,
   `_make_multirow_card_vc`, `_make_table_vc` — each now emits
   `'show': true if show_title else false` in its `vcObjects.title`
   instead of a hardcoded `'true'`.
3. Wired `z_show_title = zone.get('show_title', True)` once at the top of
   the `for zone in page.get('db_zones', [])` loop (the one live rendering
   path — see the framework-engineering phase notes on the dead
   standalone-page path, which is untouched here since it has no per-zone
   concept at all) and threaded it through **all 9** `_make_*_vc` call
   sites inside that loop, including the exception-fallback table call.
4. Found and fixed a second, easy-to-miss spot: the worksheet-based
   **slicer** branch (`z_chart == 'slicer'`, e.g. Tableau's Month/Year
   date-picker sheets) builds its visual config as a raw inline dict
   rather than through any `_make_*_vc` function, with its own hardcoded
   title block — missed on the first pass through this increment, caught
   by the same before/after diff methodology (Amazon Sales Insights'
   'Month'/'Year' slicers still showed `show=true` after the first round of
   fixes, which shouldn't have been possible given their real
   `show-title='false'` zones — traced to this separate inline title
   block and fixed the same way).

**Verified via a full title-`show`-flag diff across all 10 fixtures**
(dump every visual's `(visualType, titleText, show)` before/after, compare):
exactly 15 visuals changed, **all in the 3 fixtures confirmed to actually
have `show-title='false'` zones**, zero changes anywhere else — Amazon
Sales Insights' Revenue/Quantity cards + Month/Year slicers, Finance
Dashboard's Cash Flow/Revenue-GP-Actual/Balance Trend/DSO vs DPO charts +
6 KPI cards, HR Analytics' KPI multi-row card. Full regression (all 10,
`python3 -m py_compile` + `convert_file()`) still zero exceptions.

### Increment 11 — Dashboard filter zones — DONE

**Original finding (confirmed real, this is why the increment started):**
Tableau dashboard filter zones (`<zone type="filter">`, the quick-filter
cards dragged onto a dashboard) were rendered as PBI slicers using a blind
guess for which field to filter on (`worksheet.x_field`, or if empty, the
datasource's first non-measure column) — completely ignoring the filter
zone's OWN `param` attribute, which names the exact field. Confirmed wrong
on a real sample: HR Analytics' "KPI" dashboard has a Department filter
card (`param='[...].[none:Department:nk]'`), but the "KPI" worksheet (a
multiRowCard with no rows/cols shelf) has an empty `x_field`, so the old
code fell back to the first non-measure column, `'Attrition'` — a
completely unrelated field. Also confirmed: the filter card's `mode`
attribute (`dropdown` = single-select, `checkdropdown` = multi-select
checklist) was ignored, always rendered as multi-select.

**What was coded (first pass):**
1. `_collect_zone()` now captures `entry['filter_param']` and
   `entry['filter_mode']` from `z.get('param', '')` / `z.get('mode', '')`
   for `zt == 'filter'` zones.
2. A new post-processing step, added right after `_clean_field_name()` is
   defined in `parse_tableau_workbook` (deferred there deliberately —
   `_clean_field_name` and `global_calc_caption_map` don't exist yet at the
   point `_collect_zone` runs, since that's nested inside the earlier
   per-dashboard loop), walks every dashboard's `filter_zones` and resolves
   `filter_param` → a clean field name via `_clean_field_name(param, {})`,
   storing it as `entry['filter_field']`.
3. The filter-zone→slicer render loop (`for fzone in
   page.get('db_filter_zones', [])` in `build_report_layout`) now tries
   `fzone.get('filter_field')` (matched against the datasource's columns via
   `_fc`, then calcs via `_fm`) BEFORE falling back to the old
   x_field/first-non-measure guess. Also sets `singleSelect` from
   `fzone.get('filter_mode') == 'dropdown'` instead of a hardcoded `false`.

**A deeper root cause was found while verifying the above against the real
HR Analytics case** (this pause point was recorded and resumed correctly —
see below for the actual fix): the "KPI" filter zone wasn't even being
classified as `type='filter'` in `_collect_zone`, so none of the item 1-3
code ran for it at all. Confirmed via a raw ElementTree parse test: this
zone's real XML is
```
<zone _.fcp.SetMembershipControl.false...type='filter'
      _.fcp.SetMembershipControl.true...type-v2='filter'
      h='10622' id='18' mode='dropdown' name='KPI'
      param='[federated...].[none:Department:nk]' ... >
```
Tableau's "feature capability" (`_.fcp.<FeatureName>.<bool>...`) attribute-
name-gating convention stores the attribute under a literal prefixed key
name instead of plain `type`/`type-v2` — `z.get('type')` returns `None`,
falling through to the untyped/chart-zone default branch.

**Scope confirmed before writing the fix** (grepped every `<zone>` tag
across all 10 real fixtures, not guessed): only `type`/`type-v2` are ever
fcp-gated on `<zone>` elements in practice — seen in AB_NYC Dashboard, HR
Analytics, and Sales Param (all saved by a newer Tableau version than the
other 7 fixtures). `param`, `mode`, `show-title`, `layout` were never seen
gated this way in any real sample. (A broader whole-document grep did turn
up many other fcp-gated attribute names — `column`, `format`, `relation`,
`style`, etc. — but those belong to entirely different XML elements, not
`<zone>`, and are out of scope here.)

**Fix**: new module-level `_zone_attr(z, name)` helper (reusable — not
nested inside `parse_tableau_workbook`, so the same helper covers the
analysis-report code path too, see below) that tries `z.get(name)` first,
then falls back to scanning `z.attrib` for a key ending in `...{name}`,
preferring the `.true...` variant over `.false...` when both are present.
Wired into the three places that read a zone/element's `type`:
`_collect_zone` (`zt = _zone_attr(z, 'type') or ''`), `_build_layout_tree`
(same pattern), and a third, previously-unnoticed instance in `analyze_file`
(`dashboards_info`'s `sheets_in_dash` list — was silently excluding any
fcp-gated zone from a dashboard's analysis-report sheet list, since
`z.get('type')` returning `None` matched the exclusion tuple
`('layout-flow','',None)`).

**Verified, real-world, end-to-end (not just "no exceptions"):**
- HR Analytics' "KPI" filter zone: now classified `type='filter'`,
  `filter_field` resolves to `'Department'` (was invisible before — zero
  filter zones detected at all), generated slicer's `projections` binds to
  `HR_Analytics_Data.Department` (was previously falling back to the wrong
  field, `Attrition`, before this zone was even found), `singleSelect:
  true` correctly reflecting the real `mode='dropdown'`.
- **A second, independently-confirmed instance of the exact same bug**,
  found by the before/after diff across all 10 fixtures (not the case being
  debugged): AB_NYC Dashboard's "Neighbour with High avg price" filter zone
  was ALSO invisible before this fix (zero filter zones detected), now
  resolves to field `'neighbourhood_group'` (`mode` absent in this zone's
  XML, so multi-select — the correct default when unspecified); the
  generated slicer's `projections` binds to `AB_NYC_2019.neighbourhood_group`.
- Before/after diff covered every chart/card visual across all 10 fixtures
  (not just filter zones) in case the `type`/`type-v2` fix affected
  container/layout-tree classification too: **zero diffs anywhere except
  the two filter zones above** — purely additive, no regressions.
- Full regression (`python3 -m py_compile` + `convert_file()` on all 10
  fixtures): zero exceptions.

### Increment 12 — Real per-column/measure number formatting (currency/%/scale) — DONE

**Motivation**: after Increment 11, re-checked the per-worksheet Migration
Quality Score across all 10 fixtures — `format_pct` sat flat at ~84% across
every complexity tier (basic/medium/complex alike), the weakest sub-score
everywhere. Traced why: currency detection in the `measure_cols` loop
(`tableau_pbi_server.py`, model-schema generation) **guessed currency purely
from the column's NAME** (`if any(k in _fmt_col for k in ('price','cost',
'revenue','sales','profit','amount','amt','value','salary','wage'))`) —
never reading Tableau's own real per-column formatting metadata. Same gap
existed for every calculated-field DAX measure (`formatString` hardcoded to
`"#,0"` unconditionally).

**Schema-verified before writing any fix** (grepped every
`default-format='...'` across all 10 real fixtures' raw XML, not guessed):
Tableau's `<column default-format='...'>` attribute carries an exact
VBA/Excel-style format code. Exactly **9 distinct values** appear across all
10 fixtures, using 4 prefix letters:
- `c` currency (e.g. `c"$"#,##0;("$"#,##0)`, plus two comma-scaled variants:
  `,,.0M` = ÷1,000,000 + literal "M" suffix, `,K` = ÷1,000 + literal "K")
- `n` plain number (e.g. `n#,##0.00;-#,##0.00`, including one with a
  literal, non-multiplying `"%"` suffix baked into the pattern)
- `p` true percentage (`p0.0%`)
- `*` a literal custom pattern (a zero-padded ID `*00000`, a date format
  `*mmmm yyyy`)

**Fix**: new module-level `_tableau_default_format_to_pbi(default_format)`
(right after `_pbi_m_type`) strips the prefix letter and reuses the
remaining VBA/Excel-style body almost verbatim as the PBI FormatString — all
9 real cases translate near-identically once the prefix is stripped, since
Power BI's FormatString property accepts the same format-code vocabulary.
The one deliberate deviation: the two comma-scaled currency cases have a
BARE, unquoted trailing suffix letter (`M`/`K`) in Tableau's own syntax,
which is ambiguous in Excel/PBI format-code parsing — quoted here (`"M"`/
`"K"`) for safety rather than copied verbatim, since a wrong guess would
risk a broken/misleading number display, worse than the keyword-guess
fallback it replaces. Returns `None` for any prefix outside this confirmed
set, so callers fall back to the existing heuristic rather than guess at an
unknown format-code prefix.

Wired in three places, all now preferring the real format over any
previous fallback:
1. `_add_column()` (datasource column parsing) now captures
   `col['tableau_format'] = col.get('default-format', '')` for every base
   column.
2. The formula-calc parsing loop (`entry['tableau_format'] = col.get(
   'default-format', '')`) captures the same for every calculated field.
3. `measure_cols`' `formatString` computation now tries
   `_tableau_default_format_to_pbi(col['tableau_format'])` FIRST, falling
   back to the old name-keyword guess only when no real format exists.
   Calculated-field DAX measures (`pbi_measures.append(...)`, previously
   hardcoded `"#,0"`) and calculated columns (`pbi_calc_columns.append(...)`,
   previously no formatString at all) now do the same, including the two
   "promoted to measure" edge-case branches (cyclic/cross-referencing calc
   columns) which now inherit the calc column's own real format instead of
   a bare `"#,##0"`.

**Verified end-to-end against the real case** (Finance Dashboard, the one
fixture with real currency/percentage measure-level `default-format`):
inspected the generated PBIT's `DataModelSchema` directly — `Profit Margin
% -> '0.0%'`, `Cash Ratio -> '#,##0.00;-#,##0.00'`, `Total Revenue ->
'"$"#,##0,,.0"M";("$"#,##0,,.0"M")'`, etc. — all correct, matching the
source XML exactly. **Before/after diff across all 10 fixtures: 17 format
strings corrected in Finance Dashboard (only), zero changes anywhere else,
zero visual/chart diffs anywhere** (confirmed via the same
`_make_chart_vc`/`_make_card_vc` monkeypatch-diff methodology used every
increment this phase) — purely a formatting-fidelity improvement, no risk
to existing correctness.

**Also closed the matching audit blind spot** (found while re-checking
whether `format_pct` moved after the fix — it hadn't, because the score's
own heuristic checked unrelated signals: data-label format, chart-pane
colors, sort order): added a 4th signal to `_compute_worksheet_quality_scores`'
`fmt_signals` — "does this worksheet use a field with a real, successfully-
converted Tableau format". **Deliberately restricted to measure columns and
calculations only** (NOT dimension/date columns) after catching myself: an
earlier version of this signal also credited LocBar's Order Date column
(`default-format='*mmmm yyyy'`) even though date-column formatString is
still hardcoded to `"General Date"` elsewhere and not yet wired to the real
value — that would have been the audit claiming more than the generated
PBIT actually delivers, the exact "audit lies" failure mode this whole
engagement exists to fix. Kept the original per-signal weight (7, not
rescaled for a 4th signal) specifically so a worksheet's score can only go
UP from gaining the new signal, never down from the rebalancing alone —
confirmed via diff: only 5 worksheets in Finance Dashboard changed, all
increases (87→94, 80→87, 87→97 etc.), zero elsewhere.

Real percentage-matching numbers **do not use this project's own private
`/tmp` scratch scripts to reproduce** — they come directly from
`workbook['datasources'][*]['columns'/'calculations']` and
`migration_audit.json`'s `worksheet_scores`, both already part of
`tableau_pbi_server.py`'s normal output for every conversion.

### Increment 13 — Real formatting for dimension columns (dates, zip/postal codes) — DONE

**Direct follow-on from Increment 12**, queued at the end of that increment:
`_add_column()` already captures every column's real `tableau_format`
(Increment 12), but only the `measure_cols` loop consumed it — the
`dimension_cols` loop (where date columns are built) still hardcoded
`formatString = "General Date"` unconditionally, ignoring any real format
the column actually had.

**Fixed**: the `dimension_cols` loop now tries
`_tableau_default_format_to_pbi(col['tableau_format'])` first, falling back
to `"General Date"` only for date/dateTime columns with no real format (and
no formatString at all for other dimension columns without one, same as
before).

**This isn't only a date-formatting fix — verified a second, independently
real bug the same code path catches**: Tableau's `*00000` format code
(schema-verified in Increment 12's format-vocabulary grep) isn't a date
format at all — it appears on `datatype='integer'` Zipcode/Postal Code
columns (`House Sales Dashboard.twb`'s `[zipcode]`, `Sales Param.twbx`'s
`[Postal Code]`), where it means "zero-pad to 5 digits". Without this fix,
Power BI would display a zip code like `02134` as `2134` — a real,
user-visible data-fidelity bug, not just a cosmetic date-format gap.

**Verified end-to-end against both real cases**:
- House Sales Dashboard: `Data_HouseData.Zipcode` (`int64`) → formatString
  `'00000'` (was unset entirely).
- LocBar: `Orders_Global_Superstore.Order Date` → formatString
  `'mmmm yyyy'` (was `'General Date'`); the same table's `Ship Date` (no
  `default-format` in the source XML) correctly still falls back to
  `'General Date'` — confirms the fix is precise, not over-applied.

**Before/after diff across all 10 fixtures**: exactly 3 column format
strings changed (House Sales Dashboard's Zipcode, Sales Param's Postal
Code, LocBar's Order Date), zero elsewhere, **zero visual diffs anywhere**,
zero exceptions on full regression.

### Increment 14 — Worksheet plot-area background color — DONE

**Master spec §14 continuation.** Surveyed real `<style-rule>`/`<format>`
usage across all 10 fixtures before picking a target (not guessed): found
`background-color` set to a real, non-default value on 3 of the 10
fixtures — most strikingly `Netfix Workbook.twb`, a Netflix-themed
dashboard where every worksheet sets `background-color='#000000'` (real
black). Rendering that in Power BI with the default white visual
background would look completely wrong. Confirmed completely unhandled
(zero references to `background-color`/`gridline`/`border` anywhere in the
codebase before this).

**Parsed** from `<worksheet>/.//style-rule[@element='table']/format[@attr=
'background-color']` (schema-verified: direct child of `<worksheet>`,
simple structure) into `ws['background_color']`, with defensive filtering
of meaningless "no override" values: empty, pure white (PBI's own
default), and fully-transparent 8-digit hex (`#RRGGBBAA` with alpha byte
`00`, seen on Finance Dashboard's `table` style-rule — a real case that
would have been a false positive without this check).

**Wired into `_make_chart_vc`** (new `background_color=''` parameter,
consumed as a `background` vcObject alongside the existing `title`
vcObject) and its 3 zone-path call sites (pie/donut, treemap/bubble
extra-fields branch, and the main bar/column/line/scatter/combo branch).
**Deliberately scoped to `_make_chart_vc` only this increment** — card/
table/map/multi-row-card visual constructors don't yet receive it; a
known, undocumented-until-now remaining gap, not silently dropped.

**Verified against all 3 real cases**: Netfix Workbook's 4 charts (area,
bubble, column, bar) all correctly get `'#000000'`; LocBar's 3 charts each
get their own distinct pastel color (`'Prod Bar'` → `'#ddebf0'`,
`'CustBar'` → `'#f3ebf3'`, `'Custpie'` → `'#eff3e3'`) — confirming
per-worksheet precision, not a workbook-wide default; House Sales
Dashboard's 4 charts all get `'#e6e6e6'`.

**Before/after diff across all 10 fixtures**: 11 charts across exactly
these 3 workbooks gained a background color, **every category/value/legend
field identical before and after** — purely additive, zero regressions,
zero exceptions.

### Increment 15 — Extend background color to table/map visuals — DONE

**Direct follow-on from Increment 14**, queued at the end of that increment.
Verified real cases exist before starting (not assumed): House Sales
Dashboard has a `mapChart` and two `tableEx` worksheets with the real
`#e6e6e6` background; Netfix Workbook has a `filledMap` and four `tableEx`
worksheets with the real `#000000` background — all currently unhandled.

**Added `background_color=''` to `_make_map_vc` and `_make_table_vc`**,
wired into their zone-path call sites the same way as Increment 14's
`_make_chart_vc` work.

**Real, non-obvious bug found while verifying** (background still showed as
`None` for tableEx after the first pass — traced with a live debug print
rather than guessing): `_make_table_vc` is 434 lines long and has **two
separate `singleVisual`/`vcObjects`-building sections**, gated by
`if visual_type == 'pivotTable' and pcto_field and row_fields:` — a special
pivotTable-with-%-of-total case — versus the plain fallthrough case used by
every ordinary `tableEx` call (i.e. every real worksheet in all 10
fixtures). My first edit landed in the special pivotTable branch, which
never executes for any real case. Found and fixed the actual
default/fallthrough branch instead (~line 17165).

**This also surfaced a real, independent latent bug from Increment 10**:
that same default/fallthrough branch had `show`: hardcoded to `"true"`
unconditionally — Increment 10's `show-title` fix only reached the special
pivotTable branch, not this one, so **every real tableEx table's title-show
flag was never actually controllable by `show-title="false"`, for the
entire time since Increment 10.** Fixed alongside the background fix (both
live in the same `vcObjects` dict). No real fixture currently has
`show-title="false"` on a tableEx zone, so this didn't show up in the
diff — it's a correctness fix for future workbooks, not a behavior change
on the current 10.

**Verified against all real cases**: House Sales Dashboard's `map` +
2 `tableEx` visuals now show `'#e6e6e6'`; Netfix's `map` (filledMap→map
remap) + 4 `tableEx` visuals now show `'#000000'`. Before/after diff across
all 10 fixtures (title text, visual type, title-show, background): exactly
these 8 visuals gained a background, nothing else changed anywhere, zero
exceptions.

**`_make_card_vc`/`_make_multirow_card_vc` still don't receive
`background_color`** — no real card/multi-row-card worksheet in any of the
10 fixtures has a non-default background, so there's nothing to verify
against yet; left as an honest, documented gap rather than guessed at.

---

## Not yet done

### Framework-engineering phase — Phase 2 continuation (priority order)

Same consolidation pattern as Phase 2 slice 1 above: extract to a shared
module-level function, wire BOTH the dashboard-zone path and the
standalone-page path to call it, verify with the before/after
per-worksheet diff-against-ground-truth methodology (not just "no
exceptions") across all 8 workbooks after each one. Do these **one at a
time**, in this order — each is its own slice, not a single big rewrite:

1. ~~**Shared category resolution**~~ — **DONE, see "Phase 2, slice 2" above.**
   New module-level `_resolve_category_fields()`, wired into both paths.
2. ~~**Shared legend resolution**~~ — **DONE, see "Phase 2, slice 4" above.**
   New module-level `_resolve_legend_fields()`, wired into both paths.
3. ~~**Shared combo bar/line role assignment**~~ — **DONE, see "Phase 2,
   slice 5" above.** New module-level `_resolve_combo_roles()` (Pattern A
   only — Pattern B was ported, found to break a real verified case, and
   deliberately dropped, see slice 5), `_maybe_downgrade_stacked()`, and
   `_resolve_default_sort()`, all wired into both paths.
4. ~~**Pie/donut consolidation**~~ — **DONE, see "Phase 2, slice 3" above.**
   New module-level `_resolve_pie_fields()`, wired into both paths; also
   fixed the confirmed live bug (pie/donut zones silently inheriting
   `cat_f`/`val_f` from the previous zone) found while verifying slice 2.

### ~~Known open bug~~ — RESOLVED (found already fixed, re-verified in Increment 9)

**Continuous date-truncation shelf tokens misclassified as measures** — this
section previously claimed `tmn:`/`tyr:` continuous date-truncation tokens
were misclassified as measures by `_shelf_type`. Re-checked directly against
the current code and against Sales Param's real `[tmn:Order Date:qk]` shelf
token in Increment 9: `_shelf_type()` already has a `_DATE_TRUNC_PFXS` branch
returning `'DIM_ORDINAL'` (excluded from `is_measure_cols`/`is_measure_rows`)
that handles this correctly. Whatever fixed it wasn't recorded in this file
at the time. Sales Param's "Line graph" does still render as a combo chart
rather than a plain line (it has a genuinely unusual 3-pane structure — the
same measure on Rows twice for a dual-axis, plus a third Shape-mark pane
color-coded by a Good/Bad calc, evaluated and left alone this session as a
reasonable approximation, not a bug) — but that's unrelated to date-token
misclassification. No further action needed here.

### From the original gap analysis (still open)

5b. **Waterfall chart, Box Plot, true Gantt (native mapping)** — Medium,
    **blocked on verification**, not just unscheduled. All three need either a
    real reference PBIT containing that visual type (to reverse-engineer exact
    projection role names/objects_cfg the way every other visual mapping in
    this file was originally verified) or a lower-risk fallback approach
    decided explicitly with the user (e.g. approximate Box Plot as a
    scatter/column chart of min/median/max, approximate Gantt as a stacked
    horizontal bar with an invisible offset segment — both doable with
    already-proven role maps, just lower-fidelity than a true native mapping).
    Do not guess at unverified built-in-visual role names — a wrong guess
    risks an empty/broken visual, worse than the current honest approximation.

    (Roadmap item #8, Groups/bins → native PBI groups, and item #9, Stories,
    are resolved — see "Roadmap item #8 ... evaluated, blocked" and
    "Increment 8 — Stories" above.)

---

## How to resume

Just continue the conversation, or open a new one and say something like
"continue the Tableau→PBI migration engine work — see
MIGRATION_ENGINE_PROGRESS.md" — the persistent memory (project-type entry)
also has this context and will surface it automatically.

**Most recent work: Increment 29 (worksheet title font/color formatting —
master spec §14, found via a full master-spec-vs-implementation audit
requested by the user), after Increment 28 (locale-aware ambiguous date
parsing — a real bug, found by auditing existing code for the same
"arbitrary workbooks worldwide" reason, not from a new fixture), Increment
27 (generic robustness for unrecognized live-DB connectors — user
explicitly redirected away from "wait for more fixtures" framing, see that
section's opening paragraph and [[feedback-migration-engine-workflow]] for
the standing rule this established), Increment 26 (`_make_map_vc`
dead-code removal + re-investigation of remaining open items — found a new
gridline-color candidate, not yet actioned), Increment 25 (zone container
border formatting), Increment 24 (`MIGRATION_TEST_SUITE.md` regenerated),
Increment 23 (calc-audit coverage gap root-caused, found a real
dropped-calc-column bug along the way), and Increment 22 (Relationship,
Join & Data Model completion) — see all eight sections at the very end
of this file first.**
Everything below this point (fixture count "10", Increment 9-era notes,
etc.) predates it and has drifted — **test fixture count is now 11**
(`input/`'s 5 + `source/`'s 6: `AB_NYC Dashboard.twbx`, `HR
Analytics.twbx`, `Sales Param.twbx`, `superstore_profits.twbx`,
`superstore_profits_extract.twbx`, and `sales_dashboard.twb` — the last
one found already present in `source/` but not previously logged in this
file; it has real Tableau relationships and is one of only 5 fixtures
that do). Re-verify anything below against the current code before
trusting it, same standing rule as always.

**Current mode is the Framework Engineering Phase**, not bug-fixing against
the known samples — the user's standing instruction is to build a generic,
metadata-driven engine with no workbook-specific logic, verified against
arbitrary future workbooks, not just the fixtures currently in this repo. Do
not revert to "just make the known workbooks look right"; if a fix only
works by special-casing a specific field/sheet/workbook name, it's the wrong
fix.

**Test fixture count is now 10**, not 8: the original 5 in `input/` + 3 from
earlier in the framework-engineering phase in `source/` (`AB_NYC
Dashboard.twbx`, `HR Analytics.twbx`, `Sales Param.twbx`) + **2 new ones
added by the user in Increment 9** (`superstore_profits.twbx` — a live Excel
connection, the first non-extract fixture — and `superstore_profits_extract.twbx`,
its Hyper-extract counterpart). Use all 10 for every future regression pass.
Note `source/` also has a loose `Sample - Superstore Sales (Excel).xls` —
that's just the raw data behind the two `.twbx` files above, not a separate
migration target.

**Phase 2 is fully complete** — all four items (category, legend, combo
roles + stacked-downgrade + default-sort, pie/donut) are done, see "Phase 2,
slice 2" through "slice 5" above. No open Phase 2 item remains.

**The "continuous date-truncation shelf tokens misclassified as measures"
bug this file previously listed as open is NOT actually open** — checked the
current code in Increment 9 and found `_shelf_type()` already has a
dedicated `_DATE_TRUNC_PFXS` branch (`tmn:`, `tyr:`, `tqr:`, etc. → returns
`'DIM_ORDINAL'`, excluded from `is_measure_cols`/`is_measure_rows`) that
correctly handles it — verified directly against Sales Param's real
`[tmn:Order Date:qk]` shelf token, which now correctly resolves as a
category, not a measure. This must have been fixed in a session this file's
"Known open bug" section below wasn't updated for. **Delete/ignore that
section below** — it no longer reflects the code. (General lesson, already
in [[feedback-migration-engine-workflow]]: this file can drift from the
actual code between sessions — verify a "known bug" is still reproducible
in the current code before spending a slice fixing it.)

**Three real bugs found and fixed this session (Increment 9)** — see above
for full detail:
1. Silent "dates become 1900" fallback in `_get_embedded_rows` /
   `_date_value_to_m_literal()` — master spec's own named issue (§6/§15).
2. `_lookup_measure`/`_lookup_col` prefix-match false positive (new
   `_is_copy_suffix_of()` helper) — found investigating bug 1's
   neighborhood, confirmed via a live before/after diff, not guessed.
3. KPI-card resolver ignored calculated columns (fixed by trying `_fc(z_tf,
   z_fcols)` before the `z_fagg[0]` last resort) — this is what the "Sales
   Value" parameter lead from earlier actually was; the parameter-datasource
   angle was a red herring from investigating the symptom. Also fixed the
   same-shaped local `_fc`/`_fm` closures for consistency (zero behavior
   change on current fixtures — pure hardening).
4. (Increment 10) `show-title` zone attribute was completely unhandled —
   every visual forced its title on regardless of Tableau's per-instance
   `show-title="false"` setting. Fixed across all 5 zone-path visual
   constructors + the inline worksheet-slicer branch. See Increment 10
   above for full detail, including a second missed spot (the slicer
   branch) caught by the same diff methodology.

5. (Increment 11) Dashboard filter zones ignored their own `param`/`mode`
   attributes (guessed the filtered field from the worksheet's x_field
   instead — confirmed wrong on HR Analytics' Department filter, which
   resolved to the unrelated column `Attrition`). Fixing it surfaced a
   deeper bug: Tableau's `_.fcp.<Feature>.<bool>...` attribute-gating
   convention hid `type`/`type-v2` behind literal prefixed key names on some
   zones, so they weren't even classified as filter zones at all. New
   `_zone_attr()` helper added (scope-confirmed via grep across all 10
   fixtures' raw XML: only `type`/`type-v2` are ever gated this way on
   `<zone>` elements), wired into `_collect_zone`, `_build_layout_tree`, and
   a third previously-unnoticed spot in `analyze_file`. Verified end-to-end
   against two real, independently-confirmed cases (HR Analytics'
   Department filter, AB_NYC's neighbourhood_group filter — the second one
   found by the diff, not the one being debugged) — both now produce a
   correctly-bound slicer. Zero regressions elsewhere. See Increment 11
   above for full detail.

6. (Increment 12) Currency/percentage/number formatting was guessed from
   column NAMES (`if 'revenue' in name.lower()...`) instead of Tableau's own
   real per-column `default-format` metadata — confirmed via a schema-first
   grep across all 10 fixtures (9 distinct real format codes, 4 prefix
   letters: currency/number/percentage/literal-pattern). New
   `_tableau_default_format_to_pbi()` converts them to real PBI
   FormatStrings; wired into base measure columns, calculated-field DAX
   measures, and calculated columns (previously hardcoded `"#,0"`/`"#,##0"`/
   nothing). Verified against Finance Dashboard: 17 format strings corrected,
   zero elsewhere, zero visual diffs anywhere. Also added an honest 4th
   Format Match audit signal — deliberately restricted to measure
   columns/calcs only (caught and excluded a would-be overclaim on date
   columns, whose real format isn't wired into the model yet). See
   Increment 12 above for full detail.

7. (Increment 13) Direct follow-on from Increment 12: the `dimension_cols`
   loop still hardcoded `formatString = "General Date"` for every date
   column regardless of its real `tableau_format` (already captured by
   Increment 12, just not consumed here). Fixed the same way — real format
   first, `"General Date"` fallback only when none exists. Turned out to
   catch a second, independently real bug via the same code path: Tableau's
   `*00000` format code isn't date-related at all — it appears on
   `datatype='integer'` Zipcode/Postal Code columns, meaning "zero-pad to 5
   digits"; without this fix Power BI would show `02134` as `2134`, a real
   data-fidelity bug, not just cosmetic. Verified against both real cases
   (House Sales Dashboard's Zipcode, LocBar's Order Date, with Ship Date's
   correct fallback confirming precision). Diff: exactly 3 column format
   strings changed across all 10 fixtures, zero visual diffs, zero
   exceptions. See Increment 13 above for full detail.

8. (Increment 14) Worksheet plot-area `background-color` was completely
   unhandled — found via a real-usage survey (not guessed) showing 3 of 10
   fixtures set a real non-default background, most strikingly Netfix
   Workbook's Netflix-themed black (`#000000`) background on every
   worksheet. Parsed from the schema-verified `<style-rule element='table'>`
   location, with defensive filtering of "no override" values (empty, pure
   white, fully-transparent 8-digit hex — the last one a real case on
   Finance Dashboard that would've been a false positive). Wired into
   `_make_chart_vc` only this increment (card/table/map/multi-row-card
   still don't receive it — a known remaining gap, not silently dropped).
   Verified against all 3 real cases, including LocBar's 3 DIFFERENT pastel
   colors on 3 different worksheets (confirms per-worksheet precision, not
   a workbook-wide default). Diff: 11 charts across exactly those 3
   workbooks gained a background, every category/value/legend field
   unchanged — purely additive. See Increment 14 above for full detail.

9. (Increment 15) Direct follow-on from Increment 14: extended
   `background_color` to `_make_map_vc`/`_make_table_vc` (verified real
   cases existed first — House Sales Dashboard's map+2 tableEx, Netfix's
   map+4 tableEx). **Found a real bug while verifying, not guessed**:
   `_make_table_vc` has two separate config-building branches gated by
   `visual_type=='pivotTable' and pcto_field and row_fields`; the first fix
   attempt landed in that special branch, which never fires for the plain
   `tableEx` used by every real worksheet. Fixed the actual
   default/fallthrough branch instead. **Also caught and fixed a real
   latent bug from Increment 10 in the same spot**: that fallthrough
   branch's title-show flag was hardcoded `"true"` — Increment 10's
   show-title fix never reached it, so no tableEx table's title has ever
   actually respected `show-title="false"` since Increment 10 shipped (no
   current fixture happens to set it on a tableEx zone, so this wasn't a
   visible regression, just a latent gap now closed). Verified against all
   real cases; diff shows exactly the 8 expected visuals gained a
   background, nothing else changed. `_make_card_vc`/
   `_make_multirow_card_vc` still don't receive it — no real card/
   multi-row-card case exists in the 10 fixtures to verify against, left as
   an honest documented gap. See Increment 15 above for full detail.

**No open item remains from this session.** Next step: pick up either:
- item 5b from the original gap analysis (Waterfall/Box Plot/true Gantt
  native mapping) — still blocked on a reference PBIT or an explicit
  lower-fidelity-fallback decision from the user, or
- continue the fresh architectural pass over master spec §13/§14 — number/
  currency/percentage/date/zip-code formatting is real (Increments 12-13),
  chart/map/table background color is real (Increments 14-15; card/
  multi-row-card still pending a real verification case). Still unaudited:
  images, conditional formatting, axis labels, gridlines, borders. Note:
  §13's core layout mechanics (floating/horizontal/vertical/tiled
  containers, proportional distribution, container-edge padding) are
  ALREADY well-implemented (see `_distribute()`/`_build_layout_tree()`) —
  don't assume §13 is a blank slate, audit what's actually missing first,
  or
- §19 `MIGRATION_TEST_SUITE.md` doesn't exist yet as a dedicated file (the
  ad-hoc regression script used every session covers the spirit of §18's
  automated regression framework informally, but nothing persists a
  structured per-workbook test-suite doc as the master spec asks), or
- ask the user directly what they want next.

Whatever's next, the exact methodology that worked for Phase 2 and
Increment 9 (before/after per-worksheet diff against all 10 workbooks —
capture `_make_chart_vc`'s actual arguments via a monkeypatch wrapper, don't
infer from output JSON — then trace every difference to the real Tableau
XML, don't assume) is the one to reuse. It has caught real regressions
(Phase 2) and real, previously-invisible bugs (Increment 9) that "zero
exceptions" alone would have missed both times.

Before touching `tableau_pbi_server.py` again: make a fresh backup copy
first (no git in this repo, and a previous session's scratchpad backups
don't carry over) — see "No git anywhere in this repo" above.

---

### Increment 16 — zone-type resolution (`type` vs `type-v2`) + a real
name-collision bug it exposed in filter/parameter positioning

Picked up the §13/§14 audit trail (unaudited items: images, conditional
formatting, axis labels, gridlines, borders). Surveying real usage across
all 10 fixtures for these led somewhere much more foundational: while
confirming real `<zone type='text'>` usage (6 real text zones in Finance
Dashboard — genuinely unhandled, `_skip_types`/`_LAYOUT_SKIP_TYPES` both
drop them entirely, no PBI textbox is ever generated for them — **left
undone this increment, see "Not yet done" below**), a grep for the SAME
zone-type family across the other 9 fixtures surfaced something bigger:
6 of the 10 real fixtures (House Sales Dashboard, Netfix Workbook, Amazon
Sales Insights, LocBar, superstore_profits, superstore_profits_extract)
**never emit a plain `type` attribute on any `<zone>` at all — only
`type-v2`**, while the other 4 (AB_NYC, HR Analytics, Sales Param, Finance
Dashboard) emit either both (fcp-gated, same value) or plain `type` only.
`_zone_attr(z, 'type')` (added in Increment 11 for the fcp-gating case)
only ever looks for a key ending in `...type` — never `...type-v2`, a
genuinely different attribute name — so for those 6 fixtures **every**
typed zone (filter, text, layout container) silently resolved to `''`
(untyped), unnoticed until now because nothing had grepped `type-v2` in
isolation before.

**Real, confirmed bug found via this (not the type-v2 gap itself, a
consequence of correctly fixing it)**: LocBar's dashboard has 2 real
quick-filter-card zones (`CustBar`, `Custpie`) whose own `name` attribute
is — per a real, confirmed Tableau convention — **the same string as the
worksheet each filters** (two independent `<zone>` elements, unrelated
x/y/w/h, same name; also confirmed on HR Analytics' 'KPI' filter and
AB_NYC's neighbourhood_group filter). Before this increment, LocBar's
filter zones silently resolved to untyped (`''`) and got folded into
`chart_zones` under the `elif zt=='':` branch — `seen_chart`'s name-based
dedup meant only the FIRST-encountered same-named zone element survived,
which happened (by document order, not by any correctness guarantee) to
be the real worksheet, so LocBar's CustBar/Custpie charts rendered
correctly **by luck** while their filter cards were silently dropped
entirely (no slicer, at all, ever, for either).

Fixing zone-type resolution alone (new `_zone_type(z)` = `_zone_attr(z,
'type') or _zone_attr(z, 'type-v2') or ''`, wired into `_collect_zone`,
`_build_layout_tree`, and the minor `analyze_workbook` dashboard-sheet
listing) correctly routes LocBar's CustBar/Custpie into `filter_zones` —
but immediately exposed a SEPARATE, already-existing, already-shipped bug:
`_pbi_zone_coords` (the page-level cache `_sz()` uses for
layout-tree-derived positions) is keyed **only by zone name**, shared
between chart zones and filter/parameter zones. Since a filter card's name
routinely equals its worksheet's name, seeding it from
`db_zones + db_filter_zones` (filter zones processed second) let the
filter card's own tiny rect silently overwrite the worksheet's real chart
rect under the same dict key — confirmed this was **already live and
already broken**, not something this increment's fix introduced: dumping
`Report/Layout` from a PBIT built with the pre-session, completely
unmodified code showed HR Analytics' real 'KPI' `multiRowCard` already
squashed to its own filter slicer's rect (`9,132,188,131`, identical for
both), and Finance Dashboard's 'Revenue/GP - Actual', 'Balance Trend', and
'Revenue vs Profit Margin' charts already squashed to their respective
quick-filter cards' tiny `420×65` boxes — three real, already-shipped
chart-corrupting bugs on a fixture that's been used for regression checks
since Increment 1, undetected until this increment's before/after
*rendered-position* diff (previous increments only diffed resolved
chart-type/category/value/legend arguments, never the final on-canvas
rect — a real gap in the verification method, not just the code, now
closed for this session).

**Fix**: added `_sz_own(zone)` — the same proportional-scale-plus-
collision-nudge logic `_sz()` already had as its "Priority 2" fallback,
but with NO `_pbi_zone_coords` lookup at all — and switched the
filter-zone and parameter-zone rendering loops to call it directly instead
of `_sz()`. Also removed `db_filter_zones` from the `_pbi_zone_coords`
seed loop (it has no legitimate reason to seed a "worksheet chart
position" cache; only `db_zones`, real worksheet/chart zones, should).
`_sz()` itself is unchanged for its real remaining caller (chart/table
zone rendering) — still gets the same Priority-1 layout-tree lookup,
Priority-2 fallback otherwise.

**Investigated and deliberately NOT shipped this increment**: also tried
unlocking `_LAYOUT_SKIP_TYPES`'s `'layout-basic'/'layout-flow'` entries
(both are schema-verified as the ONLY real container zone types across
all 10 fixtures — the older `<layout type='.../>` child-element form
`_build_layout_tree` was written against appears in **zero** of the 10
real fixtures) and routing `'layout-flow'` through a real horizontal/
vertical split via its own `param='horz'/'vert'` attribute (also
universal — no fixture ever sets the `layout` attribute or a `<layout>`
child element either). This is a real, mixed result, not a clean win:
before/after-rendered-rect diffing showed it correctly fixed House Sales
Dashboard's left-side "Filters" panel (was wrongly squat at `120×274`;
became a correctly tall `212×636`) but **actively corrupted** Finance
Dashboard's KPI card row (6 correct short/wide `204×96` cards became 6
wrong tall/narrow `120×1160` slivers). Root-caused to `_distribute()`'s
`'tiled'` fallback branch: it forces a naive `cols=min(n,2)` 2-column
grid, which happens to degenerate correctly when a container has 1-2
children (House Sales' case) but is actively wrong for a container with
many (Finance Dashboard's dashboard root has ~19) flat, non-flow-wrapped
children meant to stack full-width down a long, scrolling dashboard — a
real, common authoring pattern, not an edge case. **This is real,
scoped, next-increment work**, not something to ship half-verified
alongside the zone-type fix: most likely fix is treating large flat
`'tiled'` sibling groups the same way the (currently unused-by-any-real-
fixture, unverified) `'floating'` branch already does — direct
proportional scaling of each child's own real recorded position rather
than a forced grid — but that needs its own before/after verification
pass across every container shape in all 10 fixtures before shipping, not
a guess layered onto an already-large increment. Reverted the
`_LAYOUT_SKIP_TYPES` removal; kept only the `_zone_type()` resolution fix
(which is what `_build_layout_tree` needs regardless, so that its
existing skip-check actually works consistently across all 10 fixtures
rather than accidentally skipping 4 and accidentally not-skipping 6).

**Correction to this progress doc's own prior note**: the "Not yet done"
list above claimed "§13's core layout mechanics... are ALREADY
well-implemented... don't assume §13 is a blank slate." That was wrong,
or at least importantly incomplete — confirmed this increment via direct
XML inspection across all 10 fixtures that `_build_layout_tree`'s
container-orientation detection (the `<layout type='.../>` child-element
check) has **never fired for any real fixture**, and the layout tree
itself was **empty** (0 roots) for 4 of the 10 (Finance Dashboard, AB_NYC,
HR Analytics were confirmed via a direct dump) even before this session's
changes. `_distribute()`'s container math is real code that runs, but was
running on a materially different, mostly-misclassified/absent tree than
assumed. Don't re-trust that earlier note; trust this one until it's
re-verified in a future session against the code at that time.

**Verified**: `python3 -m py_compile` clean; all 10 fixtures convert with
zero exceptions. Before/after diff of every generated PBIT's actual
`Report/Layout` visual rectangles (not just resolved chart-type/category/
value/legend arguments — a stronger check than prior increments used, now
the preferred method for anything touching layout/positioning) across all
10 fixtures: real, traced-to-XML fixes for House Sales Dashboard's
"Filters" quick-filter (previously silently dropped, now a real bound
slicer), Netfix's "Description" quick-filter (same), LocBar's
`CustBar`/`Custpie` filter cards (previously silently dropped, chart
position untouched only by luck of document order — now both a real
slicer AND a correct, no-longer-collision-vulnerable chart position),
Finance Dashboard's 3 previously-corrupted charts ('Revenue/GP - Actual',
'Balance Trend', 'Revenue vs Profit Margin' — real regression fixed, not
introduced), HR Analytics' 'KPI' multiRowCard (real regression fixed),
and AB_NYC's 'Neighbour with High avg price' table (real regression
fixed). Two cosmetic-only rect shifts on `superstore_profits`/
`superstore_profits_extract`'s single worksheet (small padding-constant
difference, no filter zones involved, harmless) were the only other
changes; zero regressions found anywhere across all 10 fixtures.

**Not yet done, real and scoped for a future increment**:
1. `_distribute()`'s `'tiled'` fallback's `cols=min(n,2)` grid heuristic
   breaks on containers with many (>2) flat children — see above.
2. Real `<zone type='text'>` rendering (Tableau dashboard Text Objects —
   currently in both `_skip_types` and `_LAYOUT_SKIP_TYPES`, silently
   dropped everywhere). Real, schema-verified structure captured this
   increment for when this is picked up: `<formatted-text><run
   bold=... fontalignment=... fontcolor=... fontname=... fontsize=...>
   text</run></formatted-text>` plus a `<zone-style>` with
   `border-color`/`border-style`/`border-width`/`margin`/
   `background-color` — confirmed real on Finance Dashboard's 6 text
   zones (dashboard section headers). Natural implementation shape:
   collect as a new `text_zones` list parallel to `filter_zones`/
   `parameter_zones` in `_collect_zone`, thread through as
   `db_text_zones` the same way, render via a new function sharing
   `_make_textbox_vc`'s proven `textbox`/`general`/`paragraphs`/
   `textRuns` PBI JSON shape (do NOT reuse `_make_textbox_vc` itself
   directly — it's called by 2 existing sites for a different purpose,
   auto-generated page titles — add a new function instead, consistent
   with this file's established "don't `replace_all` across differently-
   shaped call sites" caution).
3. Rest of the original §13/§14 unaudited list: images (zero real
   `type='bitmap'`/`type-v2='bitmap'` zones have a `name` attribute in any
   of the 10 fixtures, so they're already harmlessly excluded from
   `chart_zones`/layout everywhere they appear — but the image itself is
   never embedded/rendered; not verified as a real user-visible gap vs.
   already a non-issue), conditional formatting, axis labels, gridlines,
   borders (real `border-color`/`border-style`/`border-width` values exist
   on `<style-rule element='header'|'pane'>` in Finance Dashboard, all
   currently `'none'`/`0` in the 10 fixtures though — no real non-default
   border sample yet to verify a fix against).

---

### Increment 17 — real `<zone type='text'>` rendering (Dashboard Text
Objects)

Direct follow-on from Increment 16's "not yet done" item #2, picked up the
same session. Schema-verified against the 2026.2 TWB XSD's
`FormattedText-G` group before implementing: a `<zone type='text'>`
contains `<formatted-text><run bold=.../italic=.../underline=.../
fontname=.../fontsize=.../fontalignment=.../fontcolor=...>text</run>...
</formatted-text>`, plus a separate `<zone-style>` with
`background-color`/`border-*`/`margin` — `fontalignment` is typed as a
bare `xs:int` in the XSD with no documented enum (confirmed by reading the
actual XSD, not assumed); every real occurrence across all 10 fixtures is
`'1'`. Mapped 0/1/2 → Left/Center/Right using the ordinary UI ordinal
convention since there's no schema documentation either way — flagged as
not schema-proven in the code comment, but low-risk (a wrong guess just
leaves PBI's default left alignment, not a broken visual, unlike a wrong
chart-visual role mapping).

- These zones **never carry a `name` attribute** (confirmed across all 10
  fixtures) — unlike every other zone kind this file handles, so they
  can't go through the existing `zn`-gated block in `_collect_zone` at
  all. Added a separate, earlier check in the same function:
  `if zt == 'text' and rw > 0 and rh > 0:` that parses the zone's own
  `<formatted-text>` runs and `<zone-style>` background (same
  "no-override" filtering already proven in Increment 14 — empty/white/
  fully-transparent 8-digit hex all skipped) into a new `text_zones` list,
  keyed by traversal order rather than name (no name exists to key by,
  and none is needed — nothing looks these up later the way `_ws_by_name`
  does for filter/parameter zones).
- Threaded through exactly like `filter_zones`/`parameter_zones`:
  `dashboards.append(...)` gets a `text_zones` key,
  `pages.append(...)` gets a `db_text_zones` key.
- New rendering loop (right after the parameter-zone slicer loop, before
  chart/table zones) uses `_sz_own()` for position — the same helper
  Increment 16 added specifically to avoid the `_pbi_zone_coords`
  name-collision bug. Text zones can't hit that exact collision (no name
  at all), but `_sz_own()` is the right helper regardless: `_sz()`'s
  Priority-1 lookup requires a non-empty `zname`, which text zones never
  have, so it would always fall through to the same fallback anyway —
  using `_sz_own()` directly is simpler and consistent with the
  established pattern rather than relying on that fallthrough being
  obviously true from context.
- New `_make_dashboard_text_vc()` function (deliberately NOT a
  modification of `_make_textbox_vc`, which has 2 existing call sites for
  a different purpose — auto-generated page titles — consistent with this
  file's standing caution about touching multi-call-site functions).
  Reuses `_make_textbox_vc`'s exact proven `textbox`/`objects.general.
  paragraphs.textRuns` shape, extended to real multi-run text (the
  existing function only ever had exactly one hardcoded run) with real
  per-run `fontWeight`/`fontStyle`/`textDecoration`/`fontSize`/
  `fontFamily`/`color` and a real per-paragraph `horizontalTextAlignment`
  instead of one hardcoded style for everything. Background color reuses,
  byte-for-byte, the exact `vcObjects.background` wrapper Increments
  14/15 already proved for chart/table/map visuals — same low risk
  (wrong property name would just mean no background renders, not
  corruption), just applied to a visual type (`textbox`) that hadn't used
  it before this increment. Border was investigated and **deliberately
  NOT attempted** — grepped this whole codebase for any existing
  `'border'` `vcObjects`/`objects` usage and found zero; unlike
  background, there's no proven-in-this-codebase pattern to extend, and
  guessing an unverified property name here has no prior art backing it
  the way background does. Left as an honest, explicit gap rather than a
  guess.

**Verified**: `python3 -m py_compile` clean; all 10 fixtures convert with
zero exceptions. Real text zones turned out to exist in 3 of the 10
fixtures, not just Finance Dashboard as assumed when this was scoped in
Increment 16 — House Sales Dashboard has 2 (`'Filters'`, `'House Sales
Dashboard'`) and Amazon Sales Insights has 1 (`'Amazon Sales Insights'`,
orange `#f28e2b`), both confirmed via direct real-XML lookup after the
fact, not assumed from the Increment-16-era survey (which only checked
Finance Dashboard's `type='text'` form, not the same zones' `type-v2`
form on the other two — a reminder that Increment 16's own lesson
applies here too: don't trust an earlier session's survey of one
attribute name as complete once `_zone_type()` makes the other one live
too). Dumped every generated textbox's actual `Report/Layout` content
directly (not inferred) and confirmed exact text/bold/size/font/color/
alignment/background match against each real `<run>`/`<zone-style>` in
all 3 fixtures — e.g. Finance Dashboard's "Revenue vs Gross Profit
(2016)" renders as bold 13pt Calibri `#333333` centered text on an
`#e8edda` background, matching the source XML exactly; "Finance
dashboard" (the one real zone with no `<zone-style>` background) renders
with no background, confirming the no-override skip logic didn't
over-fire. Visual-count diff against the pre-increment baseline across
all 10 fixtures: Finance Dashboard +6, House Sales Dashboard +2, Amazon
Sales Insights +1, all exactly matching real zone counts; zero other
visuals (chart/table/map/card/slicer) changed position or size anywhere
— purely additive.

**Not yet done**: `<zone-style>` border formatting (no proven property
name in this codebase to extend, see above); the rest of the §13/§14
audit (conditional formatting, axis labels, gridlines, non-default
borders — still no real non-default border sample across any of the 10
fixtures to verify a fix against); item 5b (Waterfall/Box Plot/Gantt,
blocked on a reference PBIT); `_distribute()`'s `'tiled'` fallback
`cols=min(n,2)` grid bug found in Increment 16 — **DONE, see Increment
18 below**; §19 `MIGRATION_TEST_SUITE.md` (still doesn't exist as a
dedicated file).

---

### Increment 18 — real container distribution (fixes the Increment-16
`_distribute()` regression, unlocks `layout-basic`/`layout-flow`)

Direct follow-on from Increment 16's deliberately-deferred item: unlocking
`'layout-basic'/'layout-flow'` from `_LAYOUT_SKIP_TYPES` fixed one real
case (House Sales Dashboard's left "Filters" panel) but corrupted another
(Finance Dashboard's KPI card row), root-caused there to `_distribute()`'s
`'tiled'` fallback forcing a naive `cols=min(n,2)` 2-column grid onto a
container with ~19 flat children meant to stack full-width down a long
dashboard. This increment fixes that root cause, then re-verifies the
unlock is safe.

**Fix 1 — `_distribute()`'s `'tiled'` branch**: replaced the
`cols=min(n,2)` grid with direct proportional scaling of each child's own
real recorded position, relative to the CONTAINER's own real recorded
bounding box (the same idea the pre-existing `'floating'` branch already
used, just scoped to the immediate container's own rect instead of the
whole dashboard's). Rationale, stated plainly: Tableau bakes a real,
final absolute position for every zone at save time — confirmed across
all 10 fixtures, every zone at every nesting depth has real x/y/w/h in
the same coordinate space — so a `'tiled'`/`'layout-basic'` container's
children are not relatively-sized flex items needing redistribution; they
already tile the container's own bounding box exactly as Tableau laid
them out. A grid heuristic invented that layout instead of reading it.

**Then re-unlocked `'layout-basic'/'layout-flow'`** in
`_LAYOUT_SKIP_TYPES` and restored the `param='horz'/'vert'`
orientation-detection code for `'layout-flow'` (both were built, verified
useful, then reverted in Increment 16 pending this fix).

**Found a SECOND real bug while re-verifying, before shipping**: Fix 1
alone reintroduced a different, real overlap — House Sales Dashboard's
left sidebar is a `'vertical'` container whose real XML has TWO children
(a quick-filter-card zone, positioned first/on top, plus the `'Filters'`
worksheet below it) — but only `'Filters'` is tree-visible (the filter
card zone is deliberately skip-typed, rendered separately as a slicer via
`_sz_own()`, per Increment 16). The OLD (and, it turned out, still
partly-present) horizontal/vertical branches normalized a weighted split
across only the TREE-VISIBLE children, so the lone visible `'Filters'`
child was stretched to consume 100% of the container — including the
space Tableau's real layout reserved for the invisible quick-filter card
— producing a real, visible overlap between the resulting `'Filters'`
table and its own filter slicer. **Fix 2**: unified the `'horizontal'`
and `'vertical'` branches onto the exact same real-relative-position
scaling as the new `'tiled'` fix, instead of the old
weight-normalized-to-100% `_safe_split` approach (which is now unused
inside `_distribute()` itself, though `_child_weight_w`/`_child_weight_h`/
`_safe_split` are kept — still used for the separate top-level
multi-container-root split, an unrelated, unchanged case). Since real
recorded positions are now trusted uniformly across all three container
kinds, a lone visible child whose own container reserves extra space for
an invisible sibling now correctly gets ONLY its own real proportional
share, leaving the rest as an honest gap — matching where the invisible
filter card actually sits — instead of stretching into it.

**Verified**: `python3 -m py_compile` clean; all 10 fixtures convert with
zero exceptions. Before/after diff of every generated PBIT's actual
`Report/Layout` visual rectangles across all 10 fixtures (same stronger
method Increment 16 established): after Fix 1 alone, 4 visuals changed
(House Sales' Filters panel correctly grew taller — but also
regressed into overlapping its own filter slicer, the Fix-2 trigger;
Finance Dashboard's KPI cards **confirmed unaffected**, unlike the
Increment-16 attempt; 2 small Netfix table repositions; 1 AB_NYC table
height increase). After Fix 2, re-diffed against the same baseline: only
the 2 minor Netfix repositions remain (`~10px` shifts, no overlap, no
size distortion) — House Sales' Filters panel and AB_NYC's "Top-20
Nieghborhood areas" both settled back to their pre-existing (correct,
non-overlapping) positions once the invisible-sibling space was
correctly reserved rather than consumed, and Finance Dashboard's cards
remained correct throughout. Also ran a full pairwise visual-overlap
scan (>30% area overlap) across all 10 fixtures' pages, before and
after: identical overlap set both times (Amazon Sales Insights'
Month/Year slicer overlap, Sales Param's 'Line graph'/'Sales' card
100% overlap) — confirmed these are pre-existing, unrelated to this
increment, not introduced by it.

**Correction (checked in a later session, same day)**: the Sales Param
overlap flagged above is **NOT a bug** — re-verified against the real XML
before spending a slice on it (the standing discipline this file itself
insists on). `<zones>` for Sales Param's "Dashboard 1" has exactly TWO
direct children: the single tiled wrapper (containing `'Line graph'`,
which fills ~97% of the canvas) and, as a SEPARATE SIBLING at the same
nesting level — not nested inside that wrapper — `<zone name='Sales
Value' x='38000' y='11467' w='36462' h='12985' />`, with no type
attribute of its own. This is Tableau's real, documented zone-model
signature for a **floating** object: floating zones are direct children
of `<zones>`, positioned by absolute coordinate independent of the tiled
hierarchy, while the tiled structure itself is always exactly one
wrapper zone. A worksheet floating on top of a full-bleed background
chart (a KPI number overlaid on a chart) is a common, intentional
Tableau dashboard technique, not a mistake in the source file. Confirmed
the generated PBIT's card position is a precise, correct scale of this
zone's own real coordinates (computed by hand: raw x=38000/y=11467/
w=36462/h=12985 in the 0-100000 space → 247/98.6/237/111.7 in the
workbook's own 650×860 canvas → 486/~/466/~ in the final 1280-wide PBI
canvas, matching the generated `x=486, w=466` exactly) — already handled
correctly by the existing `_named_leaf_roots`/`_best_leaf` seeding path,
which was never touched by Increment 18. The `'Sales'` vs `'Sales Value'`
title (the KPI-card display-name cleanup regex strips a trailing
"Value"/"Count"/"KPI"/etc. suffix, added in an earlier increment for
cards like "Revenue_Count" → "Revenue") is a separate, debatable naming
heuristic, not a data or layout defect — left as-is, not chased further.
No fix needed; this item is closed.

`_LAYOUT_SKIP_TYPES` now only contains zone kinds that are genuinely
rendered via their own separate path, independent of this tree (`text`
via Increment 17's `db_text_zones`, `filter`/`parameter` via
`db_filter_zones`/`db_parameter_zones`, both using `_sz_own()`) or that
genuinely have no visual at all (`color`, `bitmap`, `empty`, `blank`,
`legend`, `highlighter`, `tab-navigation`). `'layout-basic'`/
`'layout-flow'` are no longer skip-typed; every real dashboard's
container hierarchy is now genuinely built and distributed, not silently
dropped or forced through an invented grid.

---

### Increment 19 — real custom axis titles

Same session, after closing out the Sales Param non-bug above. Picked up
the remaining §13/§14 audit item (axis labels) with a real-usage survey
first: schema-verified `<style-rule element='axis'><format attr='title'
scope='rows'|'cols' value='...'/></style-rule>` (confirmed valid,
undocumented-beyond-name, in the 2026.2 TWB XSD's `StyleAttribute-ST`
enum alongside `stroke-color`/`tick-color`/`stroke-size` — real gridline/
tick formatting attributes also confirmed present on Finance Dashboard,
not attempted this increment, see "Not yet done"). Real, non-empty axis
title overrides found on 4 Finance Dashboard worksheets (e.g. `'DSO vs
DPO'`'s value axis reading `'Amount($)'`/`'DSO vs DPO'`, not the raw
field name FIX 9's existing generator would otherwise show) and 1 AB_NYC
worksheet (`'availability'`) — real user customizations the existing
field-name-derived axis title generation (`FIX 9` in `_make_chart_vc`)
silently overwrote.

- New per-worksheet parsing in `parse_tableau_workbook` (same spot as
  `label_format`/`background_color`): collects non-empty `title`
  overrides per shelf scope (`rows`/`cols`), then maps them to
  value/value2(secondary-axis)/category using the worksheet's own
  `is_measure_rows`/`is_measure_cols`/`is_dim_rows`/`is_dim_cols` flags —
  `scope` names the raw SHELF, not the visual axis, so this mapping is
  needed to know which one a given override belongs to. A dual-axis
  worksheet can carry two `rows`-scope overrides (confirmed real on `'DSO
  vs DPO'`: `'DSO vs DPO'` for the primary axis, `'Gap'` for the
  secondary/Y2) — first found is primary, second is Y2, matching the one
  real multi-override sample.
- **Real gap found while verifying against all 4 Finance Dashboard
  cases, not just the first one that worked**: 2 of the 4 (`'Quick
  Ratio'`, `' Accounts Payable, Accounts Receivable, and Inventory'`)
  sit on worksheets whose rows shelf is Tableau's `[Multiple Values]`
  placeholder (a composite of several distinct field references, e.g. a
  cross-sheet ratio calc) — the existing `row_type` classifier can't
  recognize that as a clean measure token and falls back to `'UNKNOWN'`,
  so gating purely on `is_measure_rows` silently missed both. Both real
  cases have a confidently-classified `DIMENSION` on the OTHER shelf
  (`col_type == 'DIMENSION'`), so added a narrow fallback: an `'UNKNOWN'`
  shelf is treated as the value shelf when its counterpart is confidently
  a dimension (a worksheet needs a measure somewhere; the classifier's
  inability to name a composite token doesn't mean it isn't one).
  Deliberately scoped to just this feature's own mapping logic, not a
  fix to `_shelf_type()`/`row_type` itself, which has unknown blast
  radius elsewhere and wasn't investigated this increment.
- Threaded through as 3 new `_make_chart_vc` parameters
  (`axis_title_value`/`axis_title_value2`/`axis_title_category`,
  defaulted so the 2 sibling call sites — pie/donut, treemap/bubble —
  that don't pass them are unaffected via the function's existing
  `**kwargs`), used in FIX 9 to override the field-name-derived
  `_x_label`/`_y_label`/`_y2_label` whenever a real override exists,
  falling back to the existing field-name generation otherwise.

**Verified**: `python3 -m py_compile` clean; all 10 fixtures convert with
zero exceptions. Dumped every generated chart's actual `categoryAxis`/
`valueAxis`/`valueAxis2` `titleText` and confirmed all 4 Finance
Dashboard overrides (`'Amount($)'`, `'DSO vs DPO'`+`'Gap'`, `'Days'`+
`'Cash Coversion Cycle'`, `'Ratio'`) and AB_NYC's (`'availability'`+
`'Sum of availability_365'`) now match the real XML exactly — including
the 2 that were missing before the `row_type=='UNKNOWN'` fallback was
added. Every worksheet WITHOUT a real override still falls back
correctly to its resolved field name (e.g. Finance Dashboard's `'Cash
Flow'` → `'Sum of Cash Flow'`), confirming the fallback path is
untouched. Before/after diff of every generated PBIT's actual
`Report/Layout` visual rectangles across all 10 fixtures: zero changes
anywhere (this feature only touches axis title text, never position/
size) — purely additive.

**Not yet done**: gridline/tick formatting (`stroke-color`, `stroke-size`,
`tick-color`, `tick-length`, `tick-spacing` — all schema-confirmed real
on Finance Dashboard: a `#c0c0c0` gridline stroke-color override and a
fully-transparent (hidden) `tick-color`; not attempted this increment,
no PBI `vcObjects` property name precedent yet for axis gridlines in
this codebase specifically, unlike `background`/`title` which already
had proven wrappers to extend); `<zone-style>` border formatting; item
5b (Waterfall/Box Plot/Gantt, blocked on a reference PBIT); conditional
formatting (no real sample yet); §19 `MIGRATION_TEST_SUITE.md` (still
doesn't exist as a dedicated file).

---

### Increment 20 — gridlines explicitly hidden

Same session, direct follow-on picking up the gridline/tick item flagged
as "not yet done" above. Verified the PBI property name properly this
time before implementing anything (the exact gap noted in Increment 16):
fetched a real Power BI custom-theme JSON from GitHub directly (not just
a WebSearch summary — same "verify the actual source" discipline as
Increment 5) and parsed it with Python, confirming `valueAxis.
gridlineShow` / `gridlineColor` / `gridlineThickness` / `gridlineStyle`
are real properties living inside the SAME `valueAxis` object this
codebase's `FIX 9` already writes (`show`/`titleText`/`axisStyle`/
`labelDisplayUnits`) — extending an already-proven wrapper with new
sibling properties, the same low-risk pattern as `background`'s reuse in
Increments 14/15/17, not inventing an unverified new object.

Then surveyed real `stroke-color`/`stroke-size` usage across all 10
fixtures before deciding what to actually implement, and the survey
changed the plan: `stroke-color='#c0c0c0'` turned out to appear on 10+
unrelated Finance Dashboard worksheets — clearly Tableau's own uniform
cached default, not a deliberate per-worksheet customization. Replicating
it would have been the exact mistake this engagement's background-color
work (Increment 14) already learned to avoid: treating an ambient
default as if it were meaningful signal. `stroke-size='0'`, by contrast,
never appears as an ambient default anywhere in the 10 fixtures — it
shows up exactly once, on Sales Param's `'Line graph'` worksheet, a
real, deliberate, unambiguous "gridlines off" customization. Scoped the
increment down to just that, rather than shipping a broader
color-replication feature on thin/misleading evidence.

- New per-worksheet detection in `parse_tableau_workbook` (same spot as
  the Increment 19 axis-title parsing): `gridlines_hidden = True` when
  any `<style-rule element='axis'><format attr='stroke-size' value='0'/>`
  exists for that worksheet.
- Threaded through as a new `_make_chart_vc` parameter, applied to both
  `valueAxis` and `valueAxis2` (combo secondary axis) when present —
  `gridlineShow: false`, using the now real Power-BI-source-verified
  property name.
- Also restructured the `valueAxis`/`valueAxis2` object-building slightly:
  previously `valueAxis` was only ever created when a title label
  existed, which would have silently dropped `gridlineShow` on any
  worksheet with hidden gridlines but no real axis title. Now built
  whenever either a label OR `gridlines_hidden` applies.

**Verified**: `python3 -m py_compile` clean; all 10 fixtures convert with
zero exceptions. Dumped every generated chart's actual `valueAxis`/
`valueAxis2` `gridlineShow` property across all 10 fixtures: exactly one
hit — Sales Param's `'Line graph'` → `valueAxis.gridlineShow=false` —
confirming zero false positives (no worksheet with the ubiquitous
`#c0c0c0` default, and none of the other 9 fixtures, picked up a spurious
override). Before/after diff of every generated PBIT's actual
`Report/Layout` visual rectangles across all 10 fixtures: zero changes
anywhere — purely additive, formatting-only.

**Not yet done**: gridline/tick COLOR (`stroke-color`, `tick-color`) —
deliberately not implemented this increment, see above (thin/misleading
evidence, mostly Tableau's own default); `<zone-style>` border
formatting; item 5b (Waterfall/Box Plot/Gantt, blocked on a reference
PBIT); conditional formatting (no real sample yet); §19
`MIGRATION_TEST_SUITE.md` (still doesn't exist as a dedicated file) — **DONE, see Increment 21 below**.

---

### Increment 21 — `MIGRATION_TEST_SUITE.md` (master spec §19)

Same session. Not a code change to `tableau_pbi_server.py` — a real
documentation/tooling deliverable the master spec has named since day
one and this file's own "not yet done" list has carried since Increment
16 without ever being picked up.

Built it from actual pipeline output, not hand-typed estimates: ran
`parse_tableau_workbook()` + `write_pbit()` (the exact same functions
production uses) against all 10 real fixtures, then read each run's real
generated `migration_audit.json` (`summary.worksheet_scores`,
`summary.avg_overall_match_pct`, `summary.overall_confidence_score`,
per-worksheet `notes`) to populate every field master spec §19 asks
for: Workbook Name, Worksheets, Dashboards, Datasources, Relationships,
Calculated Fields, Chart Inventory (Tableau mark types), Generated Chart
Types (PBI visuals), Data Model Status, Layout/Formatting/Visual-
Similarity Match (heuristic, same disclosure as `migration_audit.md`),
Filter/Calculation Match (measured), Unsupported Features (per-worksheet
audit notes), plus a fleet-wide summary table. "Resolved Issues" and
"Pending Improvements" point at `MIGRATION_ENGINE_PROGRESS.md` rather
than duplicating it inline — that file already tracks this, and a second
copy would just go stale.

**Found a real, previously-invisible gap while building this, not
guessed**: for 3 of the 10 fixtures (House Sales Dashboard, Finance
Dashboard, LocBar), the number of calculated fields the audit tracks
(`summary.total_calculations`) is LESS than the workbook's real
calculated-field count (e.g. House Sales Dashboard: 0 of 3 audited;
Finance Dashboard: 40 of 56). Calculation Match % is computed only over
whatever the audit tracked — so House Sales Dashboard's "100% calc
confidence" is 100% of *zero* audited calcs, not proof all 3 real ones
converted cleanly. Root cause not confirmed this session (leading
hypothesis: bin/group calculated columns use a separate hardcoded SWITCH-
based generation path that never calls the audit-recording function the
general formula-translation pipeline calls) — flagged explicitly with a
⚠️ note on every affected workbook's entry in the new file rather than
silently presented as a clean, fully-representative number. This is
exactly the "audit lies" failure mode this whole engagement exists to
fix, just newly found in a different corner (audit *coverage*, not audit
*accuracy* — Increment 1 fixed the latter, this is the former).

**Verified**: all 10 fixtures processed through the real pipeline with
zero exceptions during generation; every number in the file traces
directly to a real `migration_audit.json` field or a direct count off
the parsed workbook dict (worksheets/dashboards/datasources/
relationships/calculated-fields/parameters) — nothing fabricated or
estimated by hand.

**Not yet done / left for a future increment**: root-causing the
calc-audit coverage gap found above (needs tracing why bin/group calc
generation doesn't call into `MigrationAudit`'s recording path — a real,
scoped, well-defined next item); everything else already listed as "not
yet done" throughout this file (gridline/tick color, zone-style borders,
Waterfall/Box Plot/Gantt, conditional formatting) is unchanged by this
increment. `MIGRATION_TEST_SUITE.md` itself should be regenerated after
any future increment that could plausibly move a score — see the file's
own "How to regenerate" section for the exact method.

---

### Increment 22 — Relationship, Join & Data Model completion (user-directed)

New session. The user redirected priority explicitly: pause chart/
visualization work and first confirm whether Tableau relationship
migration was fully implemented, and if only partial, complete/harden it
— the master spec's "RELATIONSHIP, JOIN & DATA MODEL COMPLETION" section
names this the new highest priority over new chart types.

**Audit finding (critical, not previously known)**: relationship
*parsing* already existed and was schema-correct (reads Tableau's
`<object-graph><relationships>` — or a bare top-level `<relationships>`
in older-style workbooks — logical-layer join XML correctly). But
`build_data_model_schema()` has always flattened every Tableau
datasource — including federated ones with real declared relationships —
into exactly ONE merged PBI table, while still emitting relationship
objects that referenced the ORIGINAL Tableau sub-table names (e.g.
`transactions`, `customers`) as `fromTable`/`toTable`. Since only ONE
table (`Sales`) actually got generated, **100% of emitted relationships
in every real fixture that has them were dangling** — confirmed by
directly building the model and cross-checking every relationship's
`fromTable`/`toTable`/`fromColumn`/`toColumn` against the real generated
table/column lists, across all 11 fixtures now in the repo (`input/`'s
original 5 + `source/`'s 6 — `sales_dashboard.twb` is a 6th `source/`
fixture not previously logged in this file, found via `ls source/`; real
in this session, has genuine Tableau relationships). 5 of 11 fixtures
have real relationships and were 100% affected: Amazon Sales Insights (4
relationships), Sales Param, sales_dashboard, superstore_profits,
superstore_profits_extract (2 each). This is exactly the master spec's
named failure mode — Power BI Desktop would show "Relationship object
doesn't exist" on open.

**Architecture decision (asked the user before implementing, given the
size/risk)**: a fully "correct" fix would split the merged fact table
into real separate per-Tableau-table PBI tables. Investigated this first
and found the true blast radius: every chart/DAX-reference resolver in
this ~18,000-line file (`_lookup_col`/`_lookup_measure`, all ~30
`_make_*_vc` functions, the whole Report/Layout pipeline) is hard-wired
to "one worksheet's datasource = one fixed table-qualified DAX
reference" — genuinely splitting the table would require threading a
per-column table name through most of the visual-generation pipeline, a
much larger and riskier undertaking than any prior increment, in tension
with this project's "extend, don't rewrite" rule. Chose instead an
**additive** design, presented to and confirmed by the user: keep the
existing flat/merged "fact" table completely unchanged (every already-
verified chart/DAX binding keeps working, zero regression risk to 20+
increments of prior work) and ADD real, standalone dimension tables +
valid relationship objects wired to the fact table's own existing FK
columns. This delivers a genuine, non-dangling Logical layer
(relationships) + Physical layer (real imported dimension tables) with
real cardinality — satisfying the master spec's checklist — without
touching the visual pipeline at all.

**Implementation** (`tableau_pbi_server.py`):
- Parse-time (`parse_tableau_workbook`): rebuilt `_obj_to_table` (object-
  id → canonical table name) to read primarily from schema-verified
  `<metadata-records><metadata-record class="column">`'s own object-id
  (found in TWO different real forms — a plain `object-id` XML
  *attribute* on Sales Param's German-style export, and a nested
  `<object-id>` *child element* on sales_dashboard/superstore_profits'
  older export style; both handled) paired with `<parent-name>`, falling
  back to the old `<relation type="table">` name-attribute approach only
  when no metadata-records exist. The old approach alone left
  `sales_dashboard`/`superstore_profits`'s relationships unresolved
  (their `<relation>` `name` attribute never carries the hash-suffixed
  form the relationship endpoints reference — only metadata-records do).
- New `_ds_dim_table_info` (parse-time): for each datasource with ≥2 real
  physical tables tied together by real relationships, resolves (a) which
  table is the fact/detail side (the one Tableau lists as
  `first-end-point` most often — confirmed consistent across every real
  relationship in every fixture), (b) a column→real-table membership map
  from metadata-records (`local-name`→`parent-name`, plus `remote-name`/
  `local-type` for synthesis, see below), (c) each dimension table's own
  connection info (filename/sheet) via `named-connection`. Attached onto
  the matching `workbook['datasources']` entry as `ds['_dim_split']`.
  Replaces a previous `_federated_sub_tables` scaffold that was dead code
  (its column-collecting loop body was empty — a prior half-finished
  attempt at this exact problem, confirmed via direct read before
  deciding to replace rather than "harden" it, since it had never
  actually run).
- New `_build_dimension_table()` + `_strip_dim_disambig_suffix()`: builds
  one real, standalone PBI table per dimension, sourced from the SAME
  already-correct per-real-table extracted row data
  `parse_tableau_workbook` already separates out
  (`extracted_data['datasources']['customers']` etc. — confirmed these
  were already being correctly extracted per real sub-table all along,
  just discarded/unused by the old single-merged-table code). Strips
  Tableau's own cross-table name-disambiguation suffix (e.g. `"Customer
  Code (Customers.Csv)"` → `"Customer Code"`) since it's only needed
  while the column shares a table with its same-named fact-side twin.
  Deliberately NOT a refactor of the existing ~1400-line per-datasource
  loop (dimension tables don't need geo map-columns, drill-path
  hierarchies, or Tableau parameter measures) — a smaller, self-contained
  rebuild carries far less regression risk than extracting shared logic
  out from under code every existing visual depends on.
- **Real bug found and fixed while wiring the first fixture**: Tableau
  hides the raw join-key columns by UI convention once a datasource uses
  its Relationships (logical layer) data model — confirmed real
  (`hidden="true"` on all 4 FK columns in Amazon Sales Insights,
  including a real measure `sales_amount` that happened to share the
  pattern) — so the existing `if c.get('hidden'): continue` filter (pre-
  existing, unrelated to this increment) silently dropped every FK column
  a relationship needs to bind to. Fixed narrowly: a column hidden in
  Tableau is now still emitted (marked `isHidden: true` in the PBI TOM
  model too, respecting Tableau's intent that it's plumbing, not a user-
  facing field) ONLY when it's a real relationship FK — never a general
  change to hidden-column handling.
- **Second real bug found, a different fixture**: on Sales Param /
  sales_dashboard / superstore_profits(_extract), the dimension-side join
  column (e.g. `"Region (People)"`) has **no `<column>` element anywhere
  in the datasource at all** — not even hidden. Tableau only emits
  `<column>` elements for fields actually dragged into a worksheet at
  some point; `<metadata-records>` records every physical column
  regardless. Fixed by synthesizing a minimal column (name/type from the
  metadata-record's `remote-name`/`local-type`) on whichever side
  (fact FK or dimension PK) is missing one, rather than silently dropping
  the relationship — confirmed necessary via a direct before/after run
  (without this, Sales Param's/sales_dashboard's/superstore_profits'
  relationships were still being dropped with "FK or PK column did not
  resolve to a real generated column" even after the hidden-column fix).
- New data-model validation/repair pass (master spec's explicit "Power BI
  Data Model Validation" requirement): after every table + relationship
  is built, cross-checks every relationship's `fromTable`/`toTable`/
  `fromColumn`/`toColumn` against the real generated table/column sets;
  anything that fails is dropped (never silently written) and logged,
  plus de-duplicates relationship names. Real TMSL relationship schema
  (`fromCardinality`/`toCardinality`/`crossFilteringBehavior` enum
  values) fetched directly from
  `learn.microsoft.com/analysis-services/tmsl/relationships-object-tmsl`
  before use, not guessed — cardinality defaults to `many:one` (Tableau's
  relationships are always fact→dimension by convention, confirmed
  across every real relationship in every fixture) with a best-effort
  sample-data uniqueness check on the "one" side, honestly labelled as a
  heuristic (not "verified") in the audit output when no real sample data
  exists to check against.
- New `## Data Model` section in `migration_audit.md`/`.json` (tables,
  relationships with cardinality/cross-filter direction, and any skipped/
  repaired items with the specific reason) — `MigrationAudit.set_data_model()`.

**Verified**: `python3 -m py_compile` clean. Ran `parse_tableau_workbook`
+ `build_data_model_schema` + full `write_pbit()` (writing a real
`.pbit`, then re-opening the zip and parsing the real UTF-16LE
`DataModelSchema` JSON — not just checking the in-memory dict) against
all 11 fixtures: zero exceptions, zero dangling relationships anywhere
(every `fromTable`/`toTable`/`fromColumn`/`toColumn` cross-checked
against the real written table/column lists). 12 relationships total
across the fleet (4 + 2 + 2 + 2 + 2), all valid. Spot-checked that the
5 relationship-bearing fixtures' EXISTING chart resolution is unaffected
(dumped every worksheet's resolved chart-type/category/value during a
real `write_pbit()` run — every field still resolves to its original,
pre-increment name; the one case that resolves to a bare hidden-FK-
looking name, sales_dashboard's `'Sheet 1'` charting `'Region'`, was
confirmed to be pre-existing behaviour via Tableau's worksheet
`<datasource-dependencies>` registry — Pass 3 of the existing column-
collection code, unrelated to and unaffected by this increment's FK
synthesis, which only ever adds a column when nothing else already did).
The 6 non-relationship fixtures are provably untouched by construction:
every new code path in this increment is gated behind
`ds.get('_dim_split')`, which parse-time code only ever sets when a
datasource has ≥2 real physical tables tied together by a real
Tableau relationship — never set for any of those 6, so every new branch
is a no-op for them. (No git in this repo; a true byte-for-byte
before/after diff of those 6 wasn't possible this session — this
reasoning-from-code-path-gating is the substitute, and matches how
Increment 8's "double-append" and Increment 9's "date 1900" fixes were
also verified when a full diff wasn't available.)

**Not yet done / deliberately out of scope this increment**: the master
spec also asks for Inner/Left/Right/Full-Outer *join* reconstruction
(the older physical `<relation type="join" join="...">` XML style) —
grepped for it across all 11 real fixtures and found zero real usage;
every real fixture's relationships use the newer logical-layer
`<relationships>` element instead, which has no join-type attribute at
all (Tableau resolves it implicitly, always left-join-equivalent
semantics). Not implemented since there's no real sample to verify
against — flagged here rather than guessed, per this project's standing
rule. Multi-hop relationships (A→B→C) and cross-datasource relationships
(separate Tableau `<datasource>` blends) are also unimplemented — no real
fixture has either. PK/FK *detection* beyond "the relationship's own
declared columns" (e.g. inferring an implicit PK from data uniqueness
when Tableau doesn't declare one) is not attempted — only the
cardinality heuristic uses sample-data uniqueness, and only as a
non-blocking confidence note, never to add or drop a relationship.
`MIGRATION_TEST_SUITE.md` was NOT regenerated this increment (its
`Relationships: N` field already counted Tableau-*detected* relationships,
which this increment didn't change — only whether they made it into the
written PBI model, which the doc doesn't track) — but it's still missing
`sales_dashboard.twb` entirely (11th fixture, found this session, not
previously logged) and has no Data Model quality signal; regenerate per
its own "How to regenerate" section next time scores are touched.

---

### Increment 23 — Root-caused the calc-audit coverage gap (Increment 21's flagged item)

Same session, continued after Increment 22. Increment 21 had flagged (but
not root-caused) that 3 fixtures' `migration_audit.json` tracks fewer
calculated fields than the workbook actually has (House Sales Dashboard:
0 of 3). Investigated properly this time instead of leaving it as a
leading hypothesis.

**Real bug #1 — bin/categorical-bin calcs never call `_audit.record()`**.
Both the numeric-bin (`is_bin_calc`) and categorical-bin (`is_categorical_bin`)
branches in `build_data_model_schema`'s calculated-column loop build a
real `pbi_calc_columns` entry (the PBI output is correct) and then
`continue` straight past the audit-recording call every other calc-column
path reaches — confirmed by direct read, not guessed. Fixed by adding an
`_audit.record()` call (mirroring the exact shape the regular calc-column
path already uses — same worksheet-reference lookup, same fields) to both
branches before their `continue`. Verified: House Sales Dashboard now
tracks 3 of 3 real calcs (was 0 of 3).

**Real bug #2 — found while fixing bug #1, a genuine data-correctness bug,
not just an audit gap**. LocBar and Finance Dashboard still under-counted
after fixing bug #1 (LocBar 3/5, Finance Dashboard 40/56). Traced to the
"skip alias columns that would cause cyclic references" block's Pattern-2
regex (`^\s*['"]?[^'"]+['"]?\[([^\]]+)\]\s*$`, meant to catch ONLY
Tableau's real alias shape `'Table'[Col]`) — the quotes are *optional* in
that pattern, so `[^'"]+` (unconstrained, matches almost anything
including operators and other bracket refs) can match ANY formula that
merely *ends* in a bracketed reference to an existing column, then
backtrack to satisfy the trailing `\[...\]$`. Confirmed real and
significant: LocBar's `'neg profit'` (`-[Profit]`) and `'Cost Amt'`
(`[Quantity]*[Profit]`) — both genuine, distinct formulas — matched this
pattern (capturing "Profit", a real base column) and were **silently
dropped from the generated Power BI model entirely**, not merely missing
from the audit. Same for Finance Dashboard's `'Cal_Gap'` (`[DPO]-[DSO]`)
and `'Cal_Total'` (`[2016 JAN]+[2015 NOV]+[2015 DEC]`). This is a real
missing-field bug an end user would hit as "why is this calculated column
just not in my Power BI model at all" — found by chasing an audit
*coverage* gap, not reported as one initially. Fixed by requiring the
quote pair to actually match (`^\s*(['"])[^'"]+\1\[([^\]]+)\]\s*$`,
backreference `\1`), anchoring the pattern to ONLY the genuine
`'Table'[Col]` shape. **Verified via a dedicated before/after diff**
(replicated both the old and new regex plus the real downstream
"is the captured ref an existing column" check, run against every real
calc formula in all 11 fixtures — not just eyeballing the two known
cases): exactly 4 calcs across 2 fixtures change from wrongly-dropped to
correctly-kept (the 4 named above), zero cases newly and incorrectly
skipped anywhere.

**Verified end-to-end**: `python3 -m py_compile` clean; real `write_pbit()`
run against all 11 fixtures, zero exceptions, zero dangling relationships
(Increment 22's guarantee still holds — this increment didn't touch
relationship code). Calc-audit coverage now exactly matches the real
calc count on 10 of 11 fixtures (previously only correct "by accident" on
fixtures with zero bin/alias calcs). Finance Dashboard's remaining 42/56
is now a **fully explained, legitimate** gap, not a mystery: the missing
13 are genuine Tableau-generated `"(copy)"`/trailing-space duplicate
aliases (e.g. `'Revenue (copy)'` = `[Revenue]`) that correctly get
skipped — confirmed by listing every missing name and checking each one
really is a bare reference to a real existing column. **Deliberately did
NOT** add `_audit.record_skipped()` calls for these: tried it first, then
reverted after noticing `record_skipped()` hard-codes `status='FAILED'`/
`confidence=0`, which would have misrepresented a correct, intentional
skip as a translation failure and dragged down the workbook's overall
confidence score — the opposite of this increment's goal. A duplicate
alias isn't a distinct calculation Power BI needs to represent (the base
column already covers it), so omitting it from `total_calculations` is
correct, unlike bin calcs, which produce a real, distinct output column
that WAS being silently dropped from the audit while still (correctly)
being generated.

**Not yet done**: `MIGRATION_TEST_SUITE.md` still not regenerated (now two
increments behind — see Increment 22's note); everything else already
flagged as not-yet-done throughout this file is unchanged.

---

### Increment 24 — `MIGRATION_TEST_SUITE.md` regeneration (master spec §19 / REGRESSION TESTING)

Same session, continued after Increment 23. The master prompt's own
"REGRESSION TESTING" section explicitly requires this file be updated
after every architectural change — it was two increments (22, 23) behind.
Regenerated entirely from real pipeline output (`analyze_workbook()` for
worksheet/dashboard/datasource counts and Tableau mark-type inventory,
`parse_tableau_workbook()` + `write_pbit()` + the real generated
`migration_audit.json` for everything else) across all 11 fixtures —
nothing hand-typed.

New this regeneration:
- Added `sales_dashboard.twb` (11th fixture, found in Increment 22, never
  previously logged in this file).
- New **Data Model Tables** / **Relationships** fields per workbook,
  reflecting Increment 22's real wiring (previously this file's
  `Relationships: N` field counted Tableau-*detected* relationships, which
  existed before Increment 22 even though 100% were dangling in the
  written model — now shows the real, validated, wired count, which is
  what actually matters for whether the `.pbit` opens cleanly).
- Removed every stale "⚠️ Audit gap" callout (House Sales Dashboard,
  Finance Dashboard, LocBar) — Increment 23 closed those. House Sales
  Dashboard and LocBar now show 0 gap; Finance Dashboard's remaining
  56-vs-42 difference is now stated accurately as 14 legitimate
  duplicate-alias exclusions (not a mystery/coverage bug — see
  Increment 23), not silently dropped from the doc.

**Verified**: every number traces to a real `migration_audit.json` field,
`analyze_workbook()` output, or a direct count off the parsed workbook
dict — same discipline as the file's original Increment 21 build. Fleet
average: 94% overall match, 93% calc confidence across 11 fixtures (was
10 before `sales_dashboard.twb` was added).

---

### Increment 25 — Zone container border formatting (closes a previously-blocked item)

Same session, continued after Increment 24. Picked up `<zone-style>`
border formatting — flagged as blocked since Increment 17 ("no proven
PBI property precedent yet"). Two things changed that made it doable now:

1. **Re-surveyed real usage across all 11 fixtures** (not just the
   original set this was flagged against): `border-color`/`border-style`
   appear in every fixture's `<zone-style>` blocks, but almost all of it
   is Tableau's own inert default (`border-color=#000000` +
   `border-style=none`/`border-width=0`, appearing ~167-189 times each —
   the exact "ambient default, not a real signal" trap Increment 14
   (background-color) and Increment 20 (gridline-color) already learned
   to check for). Filtered to `border-style=solid` (an explicit,
   non-default visible border) and found it real and diverse: 5 zones
   across 5 different visual types (multiRowCard, clusteredBarChart,
   scatterChart, treemap, areaChart) in HR Analytics' one dashboard, all
   sharing the same 1px black border. (2 more matches in AB_NYC turned
   out to be on layout-container zones, not real chart zones — this
   codebase never renders containers as their own visual, so correctly
   produces no output for those; confirmed via the same `_zone_type`
   check other increments already use.)
2. **Fetched the real Power BI Report Theme JSON Schema directly**
   (`github.com/microsoft/powerbi-desktop-samples`, `reportThemeSchema-
   2.155.json`) and confirmed the exact property shape:
   `definitions.commonCards.border` = `[{show: bool, color: <fill>,
   width: number, radius: number}]` — not guessed, not taken from a
   WebSearch summary (this project's own standing rule after Increment 5's
   near-miss). The `<fill>` color wrapper matches the same
   `{'solid':{'color':{'expr':{'Literal':{'Value':...}}}}}` shape this
   codebase already uses and has proven for `background` (Increment 14).

Implementation: new shared `_border_vc_objects(border_color, border_width)`
helper (one definition, used by all 5 constructors — unlike `background_color`,
which was added function-by-function across 3 separate increments with
some duplication). Parse-time capture added to `_collect_zone()`'s general
zone entry (mirroring `show_title`'s Increment-10 pattern exactly) — only
populated when Tableau explicitly declares `border-style != 'none'`, never
guessed from color/width alone since those are populated even on invisible
borders. Threaded through as new `border_color`/`border_width` params on
`_make_chart_vc`, `_make_map_vc`, `_make_table_vc` (both of its two
reachable config-building branches — see Increment 15's note on this
function having two branches), `_make_card_vc`, and `_make_multirow_card_vc`
— the last two never received `background_color` either (no real sample
existed until now); closing that same gap for both background_color and
border_color was in scope this time since HR Analytics' real 'KPI' zone is
exactly a multiRowCard. All 8 zone-rendering call sites updated.

**Found, not fixed (out of scope, noted for a future session)**: `_make_map_vc`
contains a large second, fully unreachable copy of its own implementation
after an unconditional early `return` (dead code, pre-existing — not
introduced or touched this increment). Only the reachable first copy
was wired for border. Worth a cleanup pass sometime, but touching dead
code the interpreter never executes carries no correctness benefit for
this increment's goal and wasn't investigated further.

**Verified end-to-end**: `python3 -m py_compile` clean; real `write_pbit()`
run across all 11 fixtures, zero exceptions, zero dangling relationships.
Dumped every generated visual's real `vcObjects.border` across all 11
fixtures' actual `Report/Layout` JSON: exactly 5 visuals gain a border
(all in HR Analytics, matching the 5 real XML zones exactly — multiRowCard
'KPI', clusteredBarChart 'Attrition by job role', scatterChart 'Current
role vs last prom', treemap 'Attrition for current mangr', areaChart
'atrrition with no of comp worked'), zero elsewhere — purely additive,
zero false positives, zero position/layout changes anywhere (formatting-
only property, doesn't touch sizing/positioning code at all).

**Not yet done**: gridline/tick COLOR (deliberately not replicated, see
Increment 20), Waterfall/Box Plot/true-Gantt native mapping (blocked on a
reference PBIT), conditional formatting (still no real non-default sample
across the 11 fixtures), the `_make_map_vc` dead-code cleanup noted above,
`MIGRATION_TEST_SUITE.md` regeneration (one increment behind again — this
increment changed a real signal on a real fixture).

---

### Increment 26 — `_make_map_vc` dead-code removal; re-investigated remaining "not yet done" items

Same session, continued after Increment 25. User asked to continue but
explicitly not to re-do anything already covered — went through the
"not yet done" list item by item with fresh eyes against the current
11-fixture set before touching anything, per this project's standing
"re-verify a flagged item against current code/fixtures before spending
a slice on it" rule (confirmed useful again in Increments 9, 17, 22).

- **Conditional formatting (table FillRule)**: re-checked whether the
  fetched Power BI Report Theme JSON Schema (already pulled for
  Increment 25's border work) covers this. It doesn't — `fillRule` only
  appears under chart-type `dataPoint` definitions (bar/column/scatter/
  treemap/map dataPoint color-by-value), never under any table/matrix/
  pivotTable definition, because per-column table conditional formatting
  is bound to specific column references and isn't part of a generic
  theme schema. Real heat-map/highlight-table WORKSHEETS do exist across
  the 11 fixtures (confirmed: House Sales Dashboard's 'Condition and View
  By Price'/'Filters', already surfaced via Increment 5's detection +
  recommendation), but the actual PBI-native FillRule serialization
  remains genuinely blocked on a reference PBIT — unchanged from prior
  increments' conclusion, not re-attempted on thinner evidence.
- **Gridline/tick color**: re-surveyed `stroke-color`/`tick-color` across
  all 11 fixtures (was only checked against a smaller set in Increment
  20). Found something new: `stroke-color='#333333'` with an explicit
  `scope='rows'`/`scope='cols'` attribute on exactly the 3 Finance
  Dashboard worksheets that Increment 19 already confirmed have real
  custom axis-title overrides ('Net working capital...', 'Operating Cycle
  vs Cash Conversion Cycle', 'Quick Ratio') — a real, non-ambient-looking
  correlation, unlike the unscoped `#c0c0c0` already ruled out as cached
  default in Increment 20. **Deliberately not implemented this session**:
  couldn't rule out an alternative explanation (Tableau bundling an
  axis-line-vs-gridline color property as a side effect of any axis-
  dialog edit, unrelated to deliberate color choice) without a reference
  PBIT or deeper verification than time allowed — flagging this as a
  concrete, well-defined candidate for a future session rather than
  guessing on real but ambiguous evidence, which is a different situation
  from Increment 20's clearly-ambient `#c0c0c0` finding.
- **`_make_map_vc` dead-code removal — DONE.** Flagged in Increment 25:
  the function had a large second, fully unreachable copy of its own
  implementation (169 lines) after an unconditional early `return` —
  confirmed harmless pre-existing dead code (Python doesn't error on
  unreachable code, it just never executes), not something introduced by
  this engagement. Removed it entirely. **Verified**: `python3 -m
  py_compile` clean; real `write_pbit()` run across all 11 fixtures,
  zero exceptions, zero dangling relationships; re-ran Increment 25's
  exact border survey (5 visuals in HR Analytics, zero elsewhere) —
  byte-identical result before and after, confirming the removed code
  was truly dead and this was a pure no-op cleanup.

**Not yet done**: everything listed at the end of Increment 25 remains
open except the dead-code item just closed; the gridline scoped-color
candidate above is new and not yet actioned.

**Also this session**: audited master spec §15's named legacy issue list
("Line Charts incorrectly becoming Bar Charts", "Bar Charts incorrectly
becoming Line Charts", "Multi-Line charts losing multiple series",
"Doughnut charts missing legends and values", etc.) against the current
code and all 11 fixtures, since it hadn't been explicitly re-checked
since those items were first named. Wrote a real, generic check (not
special-cased to any one fixture): for every worksheet, flag any case
where Tableau's `<pane><mark class>` set is bar-only or line-only but
the resolved PBI `chart_type` is from the opposite family. **Found
exactly one hit, confirmed a false positive, not a real bug**: Finance
Dashboard's 'Revenue vs Profit Margin' has 3 panes with 3 different real
mark classes (Automatic/Bar/GanttBar — a genuine Measure-Names-driven
multi-measure worksheet), correctly resolving to
`lineClusteredColumnComboChart` — this is the exact same worksheet
Phase 2 slice 5 (combo role assignment) already verified as a correct
Pattern-A combo case; the crude single-set mark check doesn't distinguish
multi-pane combo worksheets from single-mark ones, but the underlying
resolution is confirmed correct by tracing the real XML. Doughnut
legend/value handling confirmed already correct via the existing pie/
donut consolidation (`_resolve_pie_fields`, Phase 2 slice 3) — donut
charts deliberately don't get a separate PBI "legend" role assignment
(line ~13820) because Category IS the color/legend driver for pie/donut
in Power BI; this is correct behavior, not the historical bug. **No code
changes from this check** — a negative result across all real fixtures,
recorded so a future session doesn't re-spend time re-verifying the same
already-clean ground.

---

### Increment 27 — Generic robustness for unrecognized live-DB connectors (not fixture-driven)

Same session. The user explicitly corrected the direction after Increment
26: waiting for more sample workbooks or a reference PBIT to "unblock"
further work is the wrong framing — the master spec's own §3 already says
the 11 fixtures are regression tests, not the product scope, and "hundreds
of completely different Tableau workbooks" must work with zero
workbook-specific code. The productive move when no real sample exists for
a specific unseen construct is to harden the engine's GENERIC fallback
behavior for whatever it doesn't recognize — verified with constructed
synthetic edge cases, not real fixtures, since by definition there's no
real sample of "a workbook using a connector this engine has never heard
of" sitting in `source/`.

**Real gap found**: `_LIVE_DB_BUILDERS` already covers 30+ Tableau
connector classes with real per-connector M-query builders (Databricks,
Snowflake, SQL Server, Postgres, BigQuery, Oracle, Salesforce, SharePoint,
OData, Google Sheets, Hive/Impala/Presto/Trino via ODBC, etc. — more
extensive than expected going in). But the ultimate fallback for a
connector class NOT in that list, when `embed_sample_data` also has no
extracted data available for it (a real, plausible combination — a novel
live-DB connector Tableau adds in the future, hit on a workbook with no
cached extract), is `_build_generic_m_query()`, which produces a
correctly-typed but **completely empty** M query (`#table(type table
[...], {})`, zero rows) — silently. The table and every column still get
created with the right name/type (every visual/DAX binding referencing it
keeps working structurally), but the report would open in Power BI Desktop
showing a real-looking table with literally nothing in it, and nothing
anywhere would tell the user why. This is exactly the "audit lies"/silent
gap failure mode this whole engagement exists to eliminate — just found
in a spot no real fixture happens to trigger, rather than a spot one does.

**Fix**: new `MigrationAudit.record_unsupported_datasource()` + a new
`## ⚠️ Unsupported Data Source Connections` section at the top of
`migration_audit.md` (placed before the Data Model section — this is a
more urgent, actionable warning) listing the affected table(s), the
Tableau connection class, and the original datasource name, plus a
`log('warn', ...)` line so it's visible in the server console too. No
data is fabricated — this is purely a "tell the user clearly" fix, not an
attempt to guess at a query syntax for a connector this engine has never
seen.

**Verified**: constructed a synthetic test (real House Sales Dashboard
workbook, connection class force-set to `'clickhouse'` — not in
`_LIVE_DB_BUILDERS` — and extracted data cleared) confirming: no crash,
all 20 real columns preserved with correct types, the M query is a valid
empty typed table, the warning is logged, and the new audit section
renders correctly with the right table/connector/datasource values. Also
re-ran the full `write_pbit()` regression across all 11 real fixtures:
zero exceptions, zero dangling relationships, and — as expected, since
all 11 use either a known connector or have real cached extract data —
the new warning section never appears for any of them, confirming this
is purely additive with no effect on existing behavior.

Also stress-tested two further synthetic edge cases while in this
mindset (no code changes needed — both already handled correctly):
a workbook with zero worksheets/dashboards/stories still produces a
valid, non-crashing `.pbit`; a workbook with zero datasources on top of
that also still succeeds. Baseline defensive coding for missing/empty
structures was already solid going in.

**Not yet done**: the same "silently empty, no warning" pattern could in
principle exist elsewhere in the pipeline for other kinds of unrecognized
input (not just connector class) — this increment fixed the one concretely
identified and verified; a broader sweep for other silent-fallback spots
would be a reasonable next increment in this same direction.

---

### Increment 28 — Locale-aware ambiguous date parsing (real bug, found via the same "harden for arbitrary workbooks" direction)

Same session, continued after Increment 27. Same mindset: don't wait for a
new fixture, audit the existing generic fallback logic for correctness on
inputs the current 11 fixtures don't happen to exercise badly.

**Real bug found, not hypothetical**: `_date_value_to_m_literal()`'s string-
date fallback (used when a raw extracted date value isn't already a native
date object or ISO-formatted string) always resolved an ambiguous
`D/M/Y`-vs-`M/D/Y` string date (e.g. `"03/04/2020"`) as US month-first,
unconditionally, with a comment claiming this matched "Tableau's own
CSV/text exports of US-locale extracts" — but Tableau's real text-connection
XML carries an actual, schema-verified locale signal
(`<columns character-set='UTF-8' header='yes' locale='en_IN' separator=...>`
on the connection's column-parsing metadata) that was never being read at
all. Confirmed this locale attribute is real and present on 3 of 11
current fixtures (AB_NYC Dashboard, HR Analytics, Netfix Workbook — all
`locale='en_IN'`), previously never parsed anywhere in the codebase. India's
own civil short-date convention is DD/MM/YYYY despite `en_IN` starting with
"en" — so any workbook built from an India-locale text connection with a
genuinely ambiguous string date (day ≤ 12) was being silently misdated by
this engine (e.g. "03/04/2020" meaning 3 April read as March 4) whenever
the extraction pipeline fell back to string-date parsing rather than native
date objects. Real risk for any international workbook, exactly the class
of "arbitrary future Tableau workbook" issue master spec §3 cares about —
Tableau is used worldwide, this locale attribute exists specifically to
disambiguate this.

**Fix**: parse-time capture of `conn_info['locale']` from `<columns
locale='...'>` (schema-verified real element, first occurrence in the
datasource's connection tree — locale is a per-connection setting, not
per-column). New `_locale_prefers_day_first(locale)` helper — day-first
for any locale except a small explicit month-first set (`en_us`, `en_ph`;
day-first is the majority world convention, not US-like just because a
code starts with "en"). Threaded a new `day_first` parameter through
`_date_value_to_m_literal()` → `_get_embedded_rows()` →
`_build_sample_m_query()` to both its real call sites (the main fact-table
embed path and `_build_dimension_table()`'s Increment-22 path, so
dimension tables inherit their parent datasource's same locale). **Default
(`day_first=False`) is unchanged from today's existing behavior** when no
locale signal is present (8 of 11 fixtures) — purely additive, not a
default change, so zero regression risk to any already-verified fixture.

**Verified**: unit-level checks confirm the fix in isolation — ambiguous
date `"03/04/2020"` resolves to `#date(2020,3,4)` (March 4) with
`day_first=False` and `#date(2020,4,3)` (April 3) with `day_first=True`;
an unambiguous date (`"25/12/2020"`, day>12) resolves identically either
way (confirms the fix only changes genuinely ambiguous cases); calling
with no `day_first` argument at all reproduces the exact pre-fix output
byte-for-byte. Confirmed all 3 real `en_IN` fixtures now correctly compute
`day_first=True` end-to-end through real parsing. Full `write_pbit()` run
across all 11 fixtures: zero exceptions, zero dangling relationships, zero
`#date(1900,1,1)` fallback occurrences anywhere (same clean result as
Increment 9's original fix — confirms no regression to the working case).

**Not yet done**: this fix only affects the embed_sample_data / extracted-
row date-parsing path (`_get_embedded_rows`); the file-based (non-embedded)
M-query path lets Power Query's own `Date.From`/locale-aware type
coercion handle raw CSV text at load time in Power BI Desktop itself, which
is Power BI's own runtime behavior, not something this engine's M-query
generation controls — out of scope, not a gap in this codebase. Numeric
locale handling (decimal-comma vs decimal-point, e.g. `de_DE`/`fr_FR` using
"1.234,56") was not investigated this increment — a reasonable next
candidate in this same direction if a real numeric-locale sample or a
similar synthetic-edge-case audit surfaces a concrete gap.

---

### Increment 29 — Worksheet title font/color formatting (master spec §14: "fonts, font sizes")

Same session. User asked for a full audit of the master spec against
current implementation before any more changes (not a code request) —
that audit found title/axis fonts as a confirmed, concrete gap (font
formatting was only handled for dashboard text-zone runs and one flat
global theme default, never per-worksheet titles) and the user then asked
to complete it.

**Real signal, schema-verified before writing code, same discipline as
every prior formatting increment**: surveyed `<style-rule element="title">`
(direct child of `<worksheet>`) across all 11 fixtures — found real,
non-uniform values (not an ambient default, the trap Increments 14/20/25/26
all had to check for): Finance Dashboard has 11 worksheets at 10pt bold
Calibri on a `#e8edda` title background, and 6 different "_Count" KPI
worksheets at 14pt normal white text instead — a real, deliberate
per-worksheet distinction. LocBar has 2 worksheets ('CustBar', 'Custpie')
with distinct title background colors only, no font override. Confirmed
via the already-downloaded real Power BI Report Theme JSON Schema
(Increment 25's source) that `commonCards.title` has exactly `fontFamily`,
`fontSize` (number, 6-45 range), `bold`, `italic`, `fontColor`, `background`
— all schema-verified, not guessed.

**Implementation**: parse-time capture of `title_font_family`/
`title_font_size`/`title_bold`/`title_italic`/`title_color`/`title_bg_color`
per worksheet (mirrors Increment 14's `background_color` capture pattern
exactly — same "no meaningful override" filtering for white/transparent
backgrounds, plus a new fontSize clamp to PBI's real 6-45 schema range).
New shared `_title_font_vc_properties()` helper (mirrors `_border_vc_objects`
from Increment 25) using this codebase's own already-proven fontSize
convention (`'28D'` decimal-literal suffix, found already in use in
`_make_card_vc`'s label fontSize — not invented this increment). Threaded
through all 5 visual constructors' title blocks (`_make_chart_vc`,
`_make_map_vc`, `_make_table_vc` — both branches, `_make_card_vc`,
`_make_multirow_card_vc`) and all 8 zone-rendering call sites — same
threading pattern Increment 25 already established for border, so no new
plumbing design needed, just applying the proven pattern to a new property
set.

**Verified end-to-end** against the real generated `.pbit` (not just the
in-memory dict): Finance Dashboard — exactly 17 visuals get real title
formatting, across every affected chart type (clusteredColumnChart,
lineChart, lineClusteredColumnComboChart, card, donutChart), each with the
exact right font/size/color/background matching the source XML (the 6
KPI cards correctly got 14pt white text + no background, the 11 others got
10pt bold Calibri + `#e8edda` background, the 2 donut charts correctly got
*only* the background — no font override existed for them in the XML, and
none was fabricated). LocBar — exactly 2 visuals, exact colors matching
XML (`#00ffff`, `#00aa7f`). Full 11-fixture survey: zero title-formatting
leakage on any of the other 9 fixtures (purely additive). Full
`write_pbit()` regression: zero exceptions, zero dangling relationships.

**Not yet done**: axis-label fonts, legend fonts, and mark/data-label fonts
(`element='worksheet'`, `'legend'`, `'datalabel'` style-rules — all real
signals found in the same survey, smaller counts, not investigated this
increment) are a natural, already-scoped-out next slice in this same
direction if requested. `underline` (schema-supported) has no real sample
across the 11 fixtures to verify against, left unimplemented rather than
guessed, consistent with this project's standing rule.

---

### Increment 30 — Axis-label / legend / data-label font formatting (Increment 29's own scoped-out next slice)

New session. Resumed via `MIGRATION_ENGINE_PROGRESS.md` per the standing
instruction. Picked up exactly the item Increment 29 had already scoped out
as the natural next slice, rather than re-deriving priorities from scratch.

**Real signal, re-surveyed fresh with correct XML quoting** (the raw `.twb`
XML uses `element='worksheet'`, single-quoted — an initial double-quote grep
returned zero everywhere before this was caught): `<style-rule
element='worksheet'>` (Tableau's general per-worksheet default font, no
rows/cols scope on the font attrs — cascades to axis tick labels absent a
more specific override, same judgment call already made for
`background_color`), `element='legend'`, and `element='datalabel'` all carry
real font-family/font-size/font-weight/color attrs. Confirmed non-ambient
(not every worksheet, real variation) on 3 of 11 fixtures for the
`'worksheet'` signal (Finance Dashboard: 10pt Calibri black on its normal
worksheets vs 14pt white on the same 6 "_Count" KPI worksheets Increment 29
already found for title font; Netfix Workbook: white; Amazon Sales Insights:
orange `#f28e2b`), and on Finance Dashboard only for `legend`/`datalabel`
(its 'GrossProfit'/'Revenue' worksheets).

**Property names schema-verified against two independent real theme JSON
files fetched directly** (not a WebSearch summary — Apress's
`pro-power-bi-theme-creation` companion repo and deldersveld's
`PowerBI-ThemeTemplates`, both fetched raw and parsed with Python): the
`categoryAxis`/`valueAxis`/`legend` cards use **`labelColor`** for text
color (not `fontColor`, which is only the `title` card's property — a real
and easy-to-guess-wrong distinction); the `labels` (data-label) card uses
bare **`color`**. Where the two sources disagreed (Apress used
`legendColor`/capital-L `gridLineShow`; deldersveld used `labelColor`/
lowercase `gridlineShow`), deldersveld's convention was trusted because its
lowercase `gridlineShow` matches this codebase's own already-verified-real
Increment 20 property name — an internal consistency check, not a coin
flip.

**New parsing helper** `_collect_worksheet_style_font(ws, element_name)`
(module-level, right before `parse_tableau_workbook`) scans every
style-rule block for a given element and returns a value per attribute
**only when every block that sets it agrees**. This mattered for a real
case found while implementing, not hypothesized: Finance Dashboard's
'Revenue vs Profit Margin' worksheet has two `element='datalabel'` blocks
with genuinely different colors (white and black, for two
differently-colored series in one combo chart) — Power BI's single
per-visual `labels.color` can't represent a per-series split, so the fix
skips the color property entirely for that worksheet rather than fabricate
one that's only half-right (same "audit lies" discipline as every prior
formatting increment, just caught here via a real multi-block conflict
instead of an ambient-default trap).

**Wiring, deliberately scoped to `_make_chart_vc` only** (not
map/table/card/multiRowCard, unlike Increments 25/29's border/title font):
axis-label font → `categoryAxis`/`valueAxis`/`valueAxis2` (all three get
the same font — Tableau's signal doesn't distinguish primary vs. secondary
axis); legend font → the existing `legend` object (donut/pie charts still
correctly excluded per Phase 2 slice 3 — Category already drives their
legend, no separate object); data-label font → merged into the existing
`labels` object alongside the already-shipped format-string logic. New
shared `_axis_legend_font_vc_properties()` (mirrors `_title_font_vc_properties`
but emits `labelColor` instead of `fontColor`/`background`); data-label
font properties added inline into the existing `_label_props` dict. Threaded
through the same 3 real (non-dead) zone-path `_make_chart_vc` call sites
Increment 29 already identified via the same grep-upward-from-call-site
method the standing feedback memory requires (the other 6 `_make_chart_vc`
call sites are the historically-dead standalone-page path — left alone,
same precedent as Increment 29).

**Verified, honestly scoped per property** — this is the one part of this
increment worth being precise about, since not all three sub-features ended
up equally exercised by the real fixtures:
- **Axis-label font**: verified end-to-end against a real generated `.pbit`
  on Finance Dashboard — exactly 9 non-KPI-card chart visuals get
  `categoryAxis`/`valueAxis`(/`valueAxis2` on the 1 combo chart) with
  `fontFamily='Calibri'`, `fontSize='10D'`, `labelColor='#000000'`, matching
  the source XML exactly. Netfix Workbook's and Amazon Sales Insights' real
  `element='worksheet'` font signal turned out to sit on `tableEx`
  (Netfix) and `slicer` (Amazon) worksheets respectively — neither goes
  through `_make_chart_vc`, so this specific increment doesn't reach them.
  Not a bug in what shipped; a real, honestly-scoped gap (table
  column-header font and slicer font are a different PBI object shape
  entirely, not a same-increment extension).
- **Data-label font**: verified end-to-end on Finance Dashboard — 'Revenue'
  donut gets `fontFamily`/`fontSize`/`color` all three; 'GrossProfit' donut
  correctly gets only `fontFamily`/`color` (no font-size was set in its real
  XML — nothing fabricated); 'Revenue vs Profit Margin' correctly gets
  none of the three (the real conflicting-color case above, confirmed
  skipped not guessed).
- **Legend font**: implemented and schema-verified the same as the other
  two, but has **zero live coverage in the current 11 fixtures** — the only
  real `element='legend'` font signal sits on Finance Dashboard's
  'GrossProfit'/'Revenue' worksheets, which are donut charts and therefore
  never receive a `legend` object at all (per Phase 2 slice 3, correctly).
  Verified instead via a **synthetic unit test** (direct `_make_chart_vc()`
  call with `visual_type='clusteredColumnChart'` and real `legend_fields`
  present): confirms `fontFamily`/`fontSize`/`bold`/`labelColor` all wire
  correctly into the `legend` object when one is actually emitted. Flagging
  this explicitly rather than implying the same real-fixture-verified
  confidence as the other two — this is the "don't claim more precision
  than verified" rule applied to test coverage, not just to audit output.

Full `write_pbit()` regression re-run across all 11 fixtures after each
edit: zero exceptions throughout, 11/11 OK on the final pass.

**Not yet done**: table column-header font (Netfix/House Sales Dashboard —
a different PBI object shape, `columnHeaders`, not `categoryAxis`/`legend`/
`labels`); slicer font (Amazon Sales Insights' Month/Year date pickers);
`underline` (still no real sample anywhere, including for this increment's
three properties); mark/data-point-specific font overrides beyond the
worksheet-wide default. Rest of the master-spec audit backlog from
Increment 29 (`element='legend'`/`'datalabel'` are now done; conditional
formatting and border color/gridline-color scoped candidates from
Increments 26/27 remain open) is the natural next place to look, or ask the
user for direction.

---

### Increment 31 — Physical join-type (inner/left/right/full) reconstruction, zero real fixtures

User-directed. Follow-on from a master-spec-coverage audit that named this
as one of two remaining unstarted appended-relationship-block items
(alongside multi-hop, still open — see below). Confirmed via grep first
(not assumed): zero real fixtures use Tableau's classic `<relation
type='join'>` physical join tree anywhere — all 12 current fixtures use
either a single physical table or the newer Relationships model (Increment
22). This is genuinely synthetic-only work, done per the Increment 26/27
direction (harden generic capability via constructed edge cases, don't wait
for a fixture) rather than left "blocked."

**Schema, fetched fresh and verified directly** (not trusted from memory):
Tableau's official 2026.2 TWB XSD (`tableau/tableau-document-schemas`)
confirms `<relation type='join' join='inner|left|right|full'>` (JoinType-ST
enum — matches the user's own naming exactly) wraps a `<clause type='join'>`
holding the join condition (SQLExpression-G — the SAME fully-recursive
`<expression op='...'>` shape already proven for `<relationship>` parsing)
plus 2+ nested `<relation>` children (`Relation-G`, recursive — so N-way
joins are schema-encoded as nested binary joins, not flat N-child lists).
One assumption could NOT be verified against a real sample and is flagged
honestly in code (`_extract_join_keys`'s docstring): whether join-clause
operands are table-qualified (`[Table].[Col]`) — inferred from Tableau's
general `[Table].[Field]` convention used everywhere else in this XML, and
made self-correcting rather than blindly trusted (see below).

**Parsing** (new, all in `parse_tableau_workbook`, gated to fire only when
a datasource has neither a plain single table nor a real `<relationships>`
block — mutually exclusive with Increment 22's model):
`_extract_join_keys` (recursive `=`/`AND` walk → key-column pairs, handles
composite/multi-column keys), `_parse_relation_leaf`/
`_resolve_join_leaf_connection` (per-leaf table name + full connection-attr
dict, reusing the generic-attribute-copy pattern already proven for
Increment 22's `_nc_map_rel`), `_parse_relation_join_tree` (recursive
tree builder, returns `None` — safe fallback to today's flatten behavior —
on any unresolvable shape rather than guessing). Per-leaf column
membership reuses the exact metadata-records `<parent-name>` technique
Increment 22 already proved, attributing `ds['columns']` (already
correctly-typed/named) to real physical tables — not a second, possibly-
inconsistent column derivation. Result stored as `ds['_join_tree']` +
`ds['_join_leaf_columns']`.

**M-query generation** (new, in `build_data_model_schema`'s main
per-datasource loop, checked BEFORE `is_live_connection`/CSV/Excel since
those would only see one arbitrary side of the join): `_build_joined_m_query`
recursively emits real Power Query M — `Table.NestedJoin(Left, {keys},
Right, {keys}, "__RightData", JoinKind.X)` + `Table.ExpandTableColumn(...)`
— composing naturally for N-way trees via the same recursion.
`Table.NestedJoin`/`JoinKind` are official, documented Power Query M
functions (learn.microsoft.com/powerquery-m), not an undocumented internal
PBI JSON shape, so this doesn't carry the "wrong guess corrupts the file"
risk this project avoids elsewhere for genuinely undocumented formats —
worst case here is a line of M that doesn't evaluate, not a broken .pbit.
Leaf tables reuse the EXISTING single-table builders with zero new
per-connector code: CSV/Excel via `_build_csv_m_query`/`_build_excel_m_query`
using a literal quoted path (new `_resolve_literal_file_path`, extracted
from `_build_filepath_expression` via a pure, verified-identical-output
refactor) instead of a parameter reference — `File.Contents()` accepts
either; any `_LIVE_DB_BUILDERS` connector called directly (already
parameter-free). Real-column-name collisions between the two joined
tables get this codebase's existing `(TableName)` disambiguation suffix.
Join keys are cross-validated against each side's REAL resolved columns
(not trusted from clause order) — self-correcting for the one unverified
parsing assumption above; a key resolving to neither/both sides is
dropped, and a join with zero resolvable keys degrades to the left side
alone rather than emit a fabricated key that would silently produce wrong
(usually empty) results in Power BI.

**Verified two ways, no real fixture available**:
1. Direct unit tests of the parser + M-query builder: all 4 join kinds
   (correct `JoinKind.Inner/LeftOuter/RightOuter/FullOuter`), a composite
   2-column `AND`-joined key, a genuine non-key column-name collision
   (`Status` → `Status (Returns)`, `Table.ExpandTableColumn`'s rename arg
   confirmed correct), and a 3-way nested join (2 real
   `Table.NestedJoin` calls, both join kinds present, correct final column
   set) — all passed.
2. **End-to-end integration test using REAL fixture XML**, not fabricated
   from scratch: took `source/sales_dashboard.twb`'s already-proven-working
   datasource (real Excel connection, real metadata-records, real columns)
   and surgically converted its `<relation type='collection'>` wrapper
   into `type='join' join='X'` (removing the `<relationships>` block,
   since a datasource has one or the other) for all 4 join kinds, then ran
   the FULL `parse_tableau_workbook()` + `write_pbit()` pipeline. Inspected
   the actual generated `.pbit`'s `DataModelSchema`: both leaf tables
   correctly generated as real `Excel.Workbook(...)` M (this fixture's real
   connector), correct real literal file path, correct join keys using
   each side's real (differently-named) column — `Order ID` on the Orders
   side, `Order ID (Returns)` on the Returns side — and the correct
   `JoinKind` for all 4 tested kinds. This is the strongest verification
   available without an actual `<relation type='join'>` sample: real
   connection/metadata/column structure throughout, only the join
   construct itself is synthetic.

Full `write_pbit()` regression re-run after every edit: zero exceptions
across all real fixtures throughout, and the new code path's log line
(`"uses a physical join-tree datasource"`) never fires on any of them —
confirmed dormant/additive, exactly as designed.

**Not done, explicitly deferred**: cross-datasource relationships (the
OTHER named item in this same master-spec block) — confirmed via grep this
session that ZERO code exists for Tableau's actual mechanism (data
blending), and unlike join-type this isn't a case of "existing plumbing
just needs a new branch" — blending is a completely different, worksheet-
level XML construct this codebase has never parsed at all. Also
identified but not fixed this session: a **real latent bug** in the
existing (Increment 22) Relationships-model code — the relationship-
emission loop only writes a PBI relationship when `fromTable == fact_table`
(single-hub assumption), so a genuine multi-hop chain (A→B→C where C only
relates to A via B) would silently drop the B→C edge, leaving C
disconnected in the model. Not touched this session (different code path,
different fix shape — needs to walk the relationship graph instead of
assuming one hub) — a clear, well-scoped next candidate.

**A file worth noting, not touched**: `input/sales_dashboard.twb` now
exists alongside the original `source/sales_dashboard.twb` (discovered
mid-session via an unexpected regression-fixture count of 12, not 11) —
same file, differs only in Tableau-internal zone IDs (9/8 vs 7/6) and a
same-day-later mtime, consistent with the user re-saving it from Tableau
Desktop into `input/` during this session. Not created or modified by any
tool call this session — left as-is; the fixture-count references above
(12, not 11) reflect this new state, re-verify with `ls input/ source/`
rather than trusting "11" in earlier increments' text.

---

### Increment 32 — Datasource resolution, canonical column mapping & relationship-cardinality hardening (user-directed)

New session. The user's `continue_enhancement_prompt.md` named three
specific fixture failures and asked for root-cause fixes, not workbook-
specific patches: `sales_dashboard.twbx` (embedded Excel, placeholder
`C:\path\to\...` path in the generated M query), `sales_dashboard_new.twb`
(standalone, "The column 'Regional Manager' of the table wasn't found"),
and `sales_dashboard_no_conn.twbx` (embedded Hyper extract, DAX Number-vs-
Text errors and duplicate-value errors on the "one" side of a many-to-one
relationship). `Finance Dashboard_v2026.1.twbx` was named as the known-good
regression fixture to diff against. All three failures were reproduced and
root-caused against the real fixtures (not guessed) before any fix — each
turned out to be a genuine, previously-undiscovered bug in shared, generic
code paths, not three unrelated issues:

**Root cause 1 (`sales_dashboard.twbx`, placeholder path)**: `extract_twbx_data`
only ever extracted embedded `.csv` and `.hyper` files from a `.twbx`
package — there was no code path for an embedded `.xlsx`/`.xls`/`.xlsm`
file at all. `sales_dashboard.twbx` embeds
`Data/Data Sources/sample_super_store.xlsx` (confirmed via direct zip
inspection: 3 sheets, Orders/People/Returns, matching the datasource's own
`<relation type="table" table="[Orders$]">` etc.), so `has_data` stayed
`False` for it, `embed_sample_data` never activated, and
`_resolve_literal_file_path` fell through to its documented placeholder-path
fallback (`Data/Data Sources/sample_super_store.xlsx` is a package-relative
path — not absolute by `_is_abs`'s own Windows/Unix check — and no
`directory` was available to make it absolute), reproducing the exact
reported `C:\path\to\sample_super_store.xlsx` error.

**Root cause 2 (`sales_dashboard_no_conn.twbx`, DAX/cardinality errors)**:
two independent bugs compounded. First, `_extract_hyper_schema` located the
embedded `.hyper` file's JSON catalog and `return`ed on the FIRST
`type='block'` relation found — silently discarding every other table in a
multi-table extract. Direct inspection of this fixture's
`superstore_data.hyper` (crude JSON-catalog scan, no SDK) showed the SAME
single `.hyper` file actually contains THREE real tables —
`Orders_<hash>`/`People_<hash>`/`Returns_<hash>`, with hash suffixes that
are the exact same object-graph relationship endpoint IDs
(`<first-end-point object-id="Orders_0284213D...">` etc.) — so `People`'s
and `Returns`' row data was never even attempted, regardless of whether an
extraction SDK was available. Second, `tableauhyperapi` (already
auto-installed by the pre-existing `_ensure_hyper_libs()`, confirmed by
running it fresh: `pip install tableauhyperapi` pulls a real 86 MB
manylinux wheel cleanly) was never actually being asked for `People`/
`Returns` data because of bug one — meaning even when Tier-1 SDK extraction
was available, the single-table extraction bug prevented it from ever being
invoked for the dimension tables. Querying the real Hyper catalog directly
via `tableauhyperapi` (`SELECT * FROM "Extract"."People_..."` etc.) showed
the REAL data has **zero** duplicate values (People: 4 unique regions;
Returns: 296 unique Order IDs) — confirming the duplicate-value errors the
user saw came from the codebase's Tier-2/3 *synthetic* fallback data
(cycling a small seed pool across an estimated row count) being silently
used as if it were real, not from the real Tableau extract. Separately, the
existing relationship-cardinality code (Increment 22) always hardcoded
`fromCardinality: "many"`/`toCardinality: "one"` and only used the sample-
uniqueness check as a non-blocking audit *note* — it never prevented an
invalid cardinality from being written even when real data (once actually
extracted) proved the "one" side non-unique.

**Root cause 3 (`sales_dashboard_new.twb`, "Regional Manager" not found)**:
this fixture has NO `<object-graph><relationships>` block at all — it uses
the older `<relation type='collection'>` physical style (Orders/People/
Returns as flat `type='table'` sub-relations) plus a `<cols>` map assigning
every field to its owning physical table. Increment 22's dimension-table
splitting only ever fires for datasources with real `<object-graph>`
relationships, so this datasource fell straight through to the single-
flat-table path. Meanwhile, `parse_tableau_workbook`'s Pass 2 column
collection (`ds.findall('.//column')`, recursive) picks up nested
`<relation><columns><column>` elements — which describe each joined
sub-table's own raw physical sheet layout, NOT Tableau logical field
definitions — indiscriminately, regardless of which table the datasource's
actual M query reads from. Since the flat table's M query only ever reads
ONE physical sheet (`Orders`, chosen as the first sub-relation), every
People-only or Returns-only physical column (`Regional Manager`, `Returned`)
leaked into the flat table's declared TOM columns despite being
unproducible by its own M query — exactly Power BI's "The column '...' of
the table wasn't found" error. (The SAME leak exists on every other
multi-sub-table fixture too, e.g. Amazon Sales Insights — confirmed via
direct XML inspection — but happens to fail silently there instead of
erroring, because that fixture's `embed_sample_data` path emits `null` for
any declared column absent from a row dict rather than raising.)

**Fixes implemented** (`tableau_pbi_server.py`), all generic and
metadata-driven — no workbook/table/field names hardcoded:

1. **`extract_twbx_data`, new Step 1b**: extracts embedded `.xlsx`/`.xls`/
   `.xlsm` files via `openpyxl` (already an existing dependency), keyed per
   real sheet name (matching each datasource's own
   `<relation type="table" name="...">`), with a size-based alias under the
   outer datasource caption so the flat/fact table's own embed lookup (keyed
   by Tableau datasource caption, not sheet name) still resolves. Once any
   embedded data is found, the existing `embed_sample_data` auto-activation
   (already in `write_pbit`) takes over and the file-path-parameter/
   placeholder-path branch is never reached.
2. **New `_extract_hyper_schemas`** (plural): returns EVERY real `type='block'`
   table relation in a `.hyper` file's JSON catalog, not just the first;
   `_extract_hyper_schema` (singular) is now a thin backward-compatible
   wrapper. `extract_twbx_data`'s Step 2 rewritten to extract every table
   found this way — keyed by its hash-suffix-stripped clean name (e.g.
   `People_F408B2A4...` → `People`) for dimension-table/uniqueness lookups,
   plus the same largest-table caption alias as Step 1b. `_extract_hyper_rows`
   itself (the existing 3-tier SDK/binary/synthetic extractor) was not
   touched — it already accepted an explicit per-table schema/name; it just
   was never being asked for anything beyond the first table before now.
3. **New `_infer_relationships_from_cols_map`**: for a datasource with no
   `<object-graph>` relationships but a `<relation type='collection'>` +
   `<cols>` map, infers the same relationship shape
   (`fromTable`/`fromColumn`/`toTable`/`toColumn`) from Tableau's own
   disambiguation-suffix convention already trusted elsewhere in this
   codebase (`_strip_dim_disambig_suffix`) — a field `'X (Table)'` mapped to
   `[Table].[Col]` is the join partner of the bare `'X'` mapped to the
   majority/fact table. Wired as a fallback in the existing `_ds_relationships`
   loop (only fires when the `<object-graph>` scan found nothing), so every
   downstream consumer (`_ds_dim_table_info`, `_build_dimension_table`, the
   relationship-emission loop) works unchanged regardless of which Tableau
   XML shape the relationship was discovered in — zero duplicated logic,
   per the master spec's "one shared implementation" rule. This gives
   `sales_dashboard_new.twb` real People/Returns dimension tables +
   relationships for the first time.
4. **Column provenance tag + fact-table leak filter**: `_add_column` now
   tags every column with `_worksheet_used` (is this field's bracketed name
   present in the pre-scanned worksheet `datasource-dependencies` registry,
   i.e. genuinely dragged onto some shelf — as opposed to only being a raw
   nested-schema descriptor Pass 2 happened to pick up). In the main per-
   datasource table-building loop, a column whose `col_table`-declared owner
   is a DIFFERENT real table than the datasource's own fact table is now
   excluded from the flat/fact table's TOM columns — UNLESS it's a
   relationship FK/PK column (needed for the join) or `_worksheet_used`
   (left exactly as before, to avoid creating a NEW dangling *visual*
   reference — see the important scoping note below). This is gated behind
   `ds.get('_dim_split')`, so it is a no-op for any datasource without one
   — zero behavioural change for the 6+ fixtures that never had this
   problem. Verified end-to-end: `sales_dashboard_new.twb`'s flat table no
   longer declares `Regional Manager` (was genuinely unused, real leak,
   safe to drop); `Region (People)` (genuinely used by the `sales_by_region`
   worksheet) is correctly left in place.
5. **Relationship cardinality now checked on BOTH sides against real
   extracted data**, not just the dimension side, and the result now
   actually gates `fromCardinality`/`toCardinality`/`crossFilteringBehavior`
   instead of only annotating an audit note that never changed behaviour:
   dimension key confirmed unique → `many:one` (Tableau's own declared
   convention, now *verified* rather than assumed); fact key confirmed
   unique instead → `one:many` (swapped — real data contradicting Tableau's
   convention is trusted over the declaration); dimension key confirmed
   NON-unique (with or without fact-side confirmation) → `many:many` /
   `bothDirections` (many:one would violate Power BI's one-side uniqueness
   constraint and fail to load); both sides unknown (no extracted data at
   all, e.g. a plain `.twb`) → the previous conservative `many:one` default,
   still honestly labelled unverified in the audit note. `_sample_key_unique`
   generalised to work for either side (was dimension-only) and now ignores
   null/blank values when checking uniqueness (matching Power BI's own
   constraint, which also ignores BLANK on the one side).
6. **Datatype-compatibility gate**: before emitting a relationship, the
   final PBI TOM `dataType` of both the FK and PK column is compared; a
   mismatch (e.g. one side synthesized as `int64`, the other as `string`)
   drops the relationship with a clear audit reason instead of emitting one
   that would produce Power BI's "DAX comparison operations do not support
   comparing values of type Number with values of type Text" — no
   VALUE()/FORMAT() coercion attempted, per the master spec's explicit
   instruction not to paper over a real schema mismatch. Not exercised by
   any real or synthetic fixture this session (every real relationship's two
   sides already agree once correctly typed) — flagged here rather than
   claimed as fixture-verified.
7. **Placeholder-path scan added to the existing data-model validation
   pass**: after the existing dangling-relationship repair (Increment 22),
   every table's M-query text is scanned for the placeholder marker; any
   survivor is recorded as an audit warning (never silently shipped)
   rather than failing the whole migration — matches the master spec's
   Error-1 guidance to warn, not silently fabricate a path, when the real
   source truly cannot be resolved.
8. **Unrelated pre-existing bug fixed in passing**: `_HEATMAP_MARKS` was
   referenced in a chart-type `elif` branch before its own definition
   later in the same function — a real `NameError`
   ("cannot access free variable '_HEATMAP_MARKS'... in enclosing scope")
   that was crashing `input/sales_dashboard.twbx` (the tiny 12th/13th
   fixture) end-to-end, unrelated to any datasource/relationship work this
   session. Fixed by inlining the literal set at its point of use rather
   than relying on a later definition in the same scope.

**Verified**: `python3 -m py_compile` clean throughout. Ran the full
`parse_tableau_workbook` → `build_data_model_schema` → `write_pbit()`
pipeline (writing a real `.pbit`, then re-opening the zip and parsing the
real UTF-16LE `DataModelSchema` JSON — not just the in-memory dict) against
all 9 real fixtures now present across `source/` and `input/` (Amazon Sales
Insights, Finance Dashboard, House Sales Dashboard, LocBar, Netfix
Workbook, `source/`'s Finance Dashboard/sales_dashboard/
sales_dashboard_new/sales_dashboard_no_conn, plus `input/sales_dashboard.twbx`
once the unrelated `_HEATMAP_MARKS` bug above was fixed): **9/9 wrote a
valid `.pbit` with zero exceptions**, zero placeholder paths anywhere in
any generated M query, and — an independent check walking every visual's
`Report/Layout` JSON and cross-referencing every `Column`/`Measure`
`Entity`+`Property` reference against the real written table/column
lists — **zero dangling visual field references in any of the 9**,
including Amazon Sales Insights' 4 pre-existing relationships (unaffected:
its `_dim_split` came from the `<object-graph>` path exactly as before, and
none of its leaked columns turned out to be worksheet-used). Every
relationship in every fixture (Amazon Sales Insights' 4, `sales_dashboard`/
`sales_dashboard_new`/`sales_dashboard_no_conn`'s 2 each) independently
re-verified to reference real tables/columns with no duplicate names.
Cardinality on `sales_dashboard`/`sales_dashboard_no_conn`/
`sales_dashboard_new`'s People/Returns relationships is `many:one` with
uniqueness *confirmed* (not just assumed) wherever real extracted data was
available. The `many:many` downgrade path was exercised with a synthetic
duplicate-value People table (monkey-patched extracted rows) and correctly
produced `many:many`/`bothDirections` while leaving the unaffected Returns
relationship at `many:one` — direct proof the two relationships on one
datasource are decided independently.

**Not yet done / deliberately out of scope this session**: a worksheet
field that is both (a) genuinely used and (b) owned by a dimension table
(the `Region (People)` case above) is still declared on the flat/fact
table even though that table's own M query can never produce it — this is
a PRE-EXISTING gap (not introduced this session; confirmed the flat table
already declared it with no possible M-query fulfilment before any of
today's changes) that today's fix deliberately did not touch, to avoid
creating a NEW dangling *visual* reference by removing it without also
rewiring the visual pipeline to point at the real dimension table. Fully
fixing this requires threading a per-column table name through the
Report/Layout visual-generation pipeline (`_lookup_col` and every
`_make_*_vc` caller currently assume one worksheet's datasource = one
fixed table-qualified DAX reference) — Increment 22 explicitly scoped this
same work out as "a much larger and riskier undertaking... in tension with
this project's extend, don't rewrite rule," and that assessment still
holds. This is the clear next priority for the semantic-model/relationship
work: make dimension-table fields (not just fact-side fields) resolvable
by the visual pipeline, starting from this exact reproducible case
(`sales_dashboard`/`sales_dashboard_new`'s `sales_by_region` worksheet).
The datatype-compatibility gate (fix 6 above) is implemented and code-
reviewed but has no real or synthetic fixture that actually exercises the
mismatch branch — worth a targeted synthetic test next time relationship
work is touched. The audit report's per-relationship cardinality *reason*
text (`_dim_notes`, e.g. "dimension key confirmed unique") does not
currently render into `migration_audit.md`'s Data Model section — a
pre-existing gap in `MigrationAudit`'s Markdown renderer (not something
this session introduced or fixed); the underlying data is present in
`migration_audit.json` via `set_data_model`'s `notes` parameter for now.

---

### Increment 32b — Same-session regression: cross-datasource 'Extract' table collision in the new multi-table hyper extraction

Caught immediately after Increment 32 by the user, who attached a `.pbit`
generated by the PRE-Increment-32 engine (`Finance Dashboard_v2026.1_old.pbit`)
and reported it was "much better" than a freshly re-migrated one. Root-caused
by direct structural comparison rather than guessing: the old file has 9
tables (`Income_by_Country`, `Dso_vs_Dpo`, `CashFlow`, `Balance_Sheet`,
`Revenue`, `Gross_Profit`, `Sheet35`, `Revenue_vs_Profit_Margin`, `Main_2`)
with real column/measure counts; re-running the CURRENT engine against the
same source workbook logged "Embedded data found in 2 datasource(s)" —
only 2, not 9.

**Root cause**: `Finance Dashboard_v2026.1.twbx` has 9 SEPARATE Tableau
datasources, each with its own SEPARATE embedded `.hyper` extract file
(`Data/Extracts/excel_direct_*.hyper` × 9) — confirmed by reading every one
of the 9 files' JSON catalogs directly: **every single one names its own
internal table `'Extract'`** (Tableau's generic default name for a simple,
non-relationship extract — confirmed real, not assumed). Increment 32's new
`_extract_hyper_schemas`-based Step 2 keyed every extracted table by its
own hash-stripped clean name (`'Extract'` in this case, no hash to strip)
so that a genuinely multi-table `.hyper` — the Orders/People/Returns case
Increment 32 was built for — could store each of its real sub-tables
separately. But keying by that shared generic name ACROSS datasources
meant the second, third, ... ninth datasource's `_dest_key='Extract'` was
already present in `result['datasources']` (from the first datasource), so
`if key_name in result['datasources']: continue` silently dropped 8 of the
9 datasources' real row data — and, since each was also meant to get an
alias under its own real caption, that alias never happened either.

**Fix** (`tableau_pbi_server.py`, `extract_twbx_data`): a hyper file that
yields exactly ONE table (`len(table_schemas) == 1` — the common/simple-
extract case, true for all 9 of this fixture's files) is now keyed by
`ds_cap` directly — the exact same keying the pre-Increment-22-era code
used, which is safe by construction (a Tableau datasource caption is
unique within one workbook, so it can never collide across datasources).
Only a hyper file that genuinely contains MULTIPLE real tables (an actual
relationship-bearing extract, e.g. `sales_dashboard_no_conn.twbx`'s
Orders/People/Returns) uses the new per-table clean-name keying, since
those names are what the dimension-table builder actually looks up by.
Applied the identical guard to Step 1b's embedded-Excel extraction (a
datasource with only one relevant sheet is keyed by `ds_cap` too) as a
preventative measure — no real fixture has hit that exact collision yet
(two different datasources' embedded Excel files sharing a sheet name),
but it is the same class of bug and the fix is the same shape, so there
was no reason to leave it latent.

**Verified**: re-ran `extract_twbx_data` directly against
`Finance Dashboard_v2026.1.twbx` — all 9 datasources now extract with
their real row counts (30/48/24/364/50/50/528/360/107 rows respectively,
matching the old file's implied structure). Full `write_pbit()` re-run
produced a `DataModelSchema` with the exact same 9 table names AND exact
same column/measure counts as the user's attached pre-Increment-32
`.pbit` (`Income_by_Country`: 7 cols/0 measures, `Dso_vs_Dpo`: 9/1,
`CashFlow`: 5/1, `Balance_Sheet`: 10/1, `Revenue`: 8/0, `Gross_Profit`:
9/0, `Sheet35`: 15/0, `Revenue_vs_Profit_Margin`: 9/2, `Main_2`: 38/12 —
a field-by-field match, not just a table count match). Full regression
re-run across all 10 fixtures in `source/`+`input/` after the fix: 10/10
`write_pbit()` succeed with zero exceptions; independent dangling-visual-
field-reference scan (same method as Increment 32) still clean on every
fixture; `sales_dashboard`/`sales_dashboard_no_conn`'s genuinely
multi-table Orders/People/Returns extraction (the case Increment 32 was
built for) still correctly extracts all 3 real sub-tables per fixture —
confirming the single-vs-multi-table branch picks the right keying
strategy in both directions, not just the one that regressed.

**Lesson for future increments touching `extract_twbx_data`**: Tableau's
generic default table/extract names (`'Extract'`, and likely also default
sheet names like `'Sheet1'`) are NOT unique across a workbook's
datasources — only the datasource's own caption is guaranteed unique.
Any future per-table keying scheme must keep this in mind rather than
assuming a table's own internal name is a safe dict key on its own.

---

### Increment 32c — Backfill worksheet-used relationship join-key columns (real blank-chart bug from user-supplied .pbit files)

The user uploaded actual generated `.pbit` outputs (`sales_dashboard.pbit`,
`sales_dashboard_new.pbit`, `sales_dashboard_no_conn.pbit`,
`Finance Dashboard_v2026.1.pbit`) into `target/` from a run of the
Increment 32/32b-fixed engine and reported the sales_dashboard-family files
"still have issues." Inspected each `.pbit`'s real `DataModelSchema`
directly: all four now have the CORRECT table/relationship structure
(confirmed field-for-field matching the expected Increment 32/32b output —
no placeholder paths, no dangling relationships, correct many:one
cardinality). So the remaining issue had to be something the structural
audit doesn't catch — traced it to the exact residual gap flagged at the
end of Increment 32: `superstore_data`'s `'Region (People)'` column (used
by the `sales_by_region` worksheet) is declared in the TOM but every row
evaluates to `NULL`, confirmed directly in the uploaded file's own M query
(`#"Region (People)" = text` in the schema, `null` in every embedded data
row). That worksheet's chart would render as a completely empty/blank bar
chart in Power BI — a real, user-visible bug, not just a theoretical gap.

**Fix, without the large visual-pipeline rewiring Increment 22/32 both
deferred**: realised the specific field involved — `'Region (People)'` —
isn't just SOME dimension-table field; it is literally the relationship's
own dimension-side join key (`toColumn`). Tableau's relationship join
condition (`Orders.Region = People.Region`) is an equality, so wherever the
relationship holds, `People.Region` is by definition identical to
`Orders.Region` — meaning the flat table doesn't need a real cross-table
join to populate this specific column correctly; a same-row copy from the
already-present fact-side column suffices. New `_append_m_copy_columns()`
(module-level M-query helper) appends a `Table.AddColumn` step to any
already-built M query (works uniformly across the sample/CSV/Excel/
generic/join-tree/live-DB paths — it operates on the finished M text, not
on any one builder). Wired into the main per-datasource loop, right after
`m_query` is finalized: for every dimension column kept on the flat table
because it's worksheet-used (see Increment 32's `_worksheet_used` guard),
if its raw key exactly matches some relationship's `toColumn`, queue a
copy from that relationship's `fromColumn` (already resolved to its final
PBI name via `col_name_map`) and append the steps. Scoped deliberately
narrow: only fires for a column that IS a relationship's own join key —
does NOT attempt to backfill an arbitrary dimension attribute (e.g.
`Regional Manager`) via a real lookup/join, which remains the correctly-
scoped-out larger undertaking noted in Increment 32.

**Verified**: re-ran `write_pbit()` on `sales_dashboard.twbx`,
`sales_dashboard_no_conn.twbx`, and `sales_dashboard_new.twb` — all three
now log `"backfilled 1 worksheet-used relationship join-key column(s)...
'Region (People)'<-'Region'"`, and the generated M query's final step is
`Table.AddColumn(..., "Region (People)", each [Region])` in every case
(embed_sample_data mode for the two `.twbx` fixtures AND file-path-
parameter mode for the plain `.twb` — confirmed both paths since the
helper operates on already-finished M text regardless of which builder
produced it). Full 10-fixture regression re-run: 10/10 `write_pbit()`
succeed with zero exceptions, and the same independent dangling-visual-
field-reference scan used in Increment 32/32b is still clean everywhere
(zero dangling references, including on the 3 fixtures this fix touched).

**Not yet done**: any dimension attribute that is NOT itself a
relationship's join key (e.g. `Regional Manager`) is still unproducible on
the flat table if some future workbook's worksheet references it directly
— that requires the real lookup/join Increment 22/32 both explicitly
scoped out. No current fixture exercises that case (the only real
worksheet-used dimension field across all 12 fixtures is the join-key case
this increment fixes), so it remains a flagged gap, not a regression.

---

### Increment 33 — Aggregation semantics + physical table/relationship reconstruction (user-directed, `measures_and_relationships_enhancement_prompt.md`)

New session. The user's prompt named two remaining semantic gaps: (1)
Tableau aggregation semantics (SUM/AVG/MIN/MAX/COUNT/COUNTD/MEDIAN) not
preserved correctly, and (2) the Tableau datasource caption
(`superstore_data`) standing in for the real physical tables
(`Orders`/`People`/`Returns`) instead of being reconstructed as them. Both
root-caused against the real codebase and real fixtures before any fix —
Issue 1 turned out to be real and confirmed on an existing regression
fixture (`House Sales Dashboard.twb`) that had been silently wrong the
whole time; Issue 2 was a real, generic naming gap.

**Root cause of Issue 1 (aggregation semantics)**: the CORE architecture
was already correct — `_clean_field_name` already strips Tableau's
aggregation-prefix shelf tokens (`sum:`/`avg:`/`min:`/etc.) down to the
bare physical field name without ever fabricating a physical column named
e.g. `"Sum of Sales"` (confirmed by scanning every generated fixture's TOM
`columns` array directly: zero matches for any aggregation-prefixed name —
`Sum of X` etc. only ever appear as real DAX **measures**, which is
correct and matches Power BI Desktop's own default-aggregation display-
name convention). The real bug was narrower but still generic: of
Tableau's aggregation prefixes, only `sum:` (implicit, via a blanket
"any numeric field on a row/column shelf gets a `Sum of X` measure" pass)
and `ctd:` (COUNT DISTINCT, via a dedicated `_ctd_measure_map` mechanism)
were ever actually detected and given their own measure. **`avg:`/`min:`/
`max:`/`med:` (MEDIAN) were parsed into shelf tokens
(`_parse_shelf_tokens` returns `{'agg': prefix, ...}`) but the `'agg'` key
was read in exactly ONE place in this ~21,000-line file — a narrow `'bin'`
check for histogram detection — and discarded everywhere else.** Confirmed
this silently coerced every AVG/MIN/MAX/MEDIAN field to SUM by:
grepping every native-aggregation `'Function':` JSON code in the file
(only `0`=Sum and `5`=CountNonNull ever appear — never 1/2/3/4) and by
finding zero `"AVERAGE("`/`"MIN("`/`"MAX("`/`"MEDIAN("` DAX generation
anywhere. Reproduced on a REAL existing fixture, not synthesized:
`input/House Sales Dashboard.twb`'s own `<column-instance
derivation='Avg'>` on `price`, actively used by 4 worksheets — one of them
literally named **"Average House Sales Price"** — which were all silently
rendering `SUM(Price)` before this fix.

**Fix (fix 1 of 2, `tableau_pbi_server.py`)**: generalised the existing,
already-proven `ctd:` detection/measure-creation pattern (previously
duplicated nowhere — it was the only one of its kind) into one shared
implementation used by all five explicit aggregations:
- New `_scan_agg_fields(deriv_name, prefix)` (parse-time, nested in
  `parse_tableau_workbook`, replacing the old ctd-only inline block):
  scans the same 3 real sources per worksheet (`<column-instance
  derivation=...>`, raw shelf-token `prefix:Field:qk`, pane-encoding
  tokens) generically for any aggregation kind. Called once per kind
  (`ctd`/`avg`/`min`/`max`/`med`/`cnt`) and stored on the worksheet dict as
  `ctd_fields`/`avg_fields`/`min_fields`/`max_fields`/`med_fields`/
  `cnt_fields`.
- New `_build_explicit_agg_measures(ws_field_key, dax_fn, display_prefix)`
  (in `build_data_model_schema`, replacing the old ctd-only inline block):
  generates one real DAX measure per referenced column
  (`AVERAGE`/`MIN`/`MAX`/`MEDIAN`/`COUNT`/`DISTINCTCOUNT`), stored in
  `ds['_avg_measure_map']` etc., mirroring the existing `_ctd_measure_map`
  exactly.
- `MeasureRegistry` (used by `build_report_layout`'s visual-field
  resolution) extended with a data-driven `_EXPLICIT_AGG_KINDS` list so
  `resolve()`'s priority order is now: named calc → count-distinct →
  median → max → min → average → count → **sum (fallback)** — a field
  the user explicitly aggregated as non-SUM in Tableau now wins over the
  generic SUM measure that gets created for it anyway (every numeric
  shelf field still gets a `Sum of X` measure as a fallback/default; this
  ordering is what makes the *right* one win when more than one exists).
- **Second, deeper bug found while verifying against the real House Sales
  Dashboard fixture**: even after the registry fix above, `'Average House
  Sales Price'` STILL resolved to `Sum of Price` in the actual generated
  visual. Traced to `ds_sum_maps` (a "thin compatibility shim" dict built
  once in `build_report_layout` and passed into ~9 separate call sites
  across the file as `sum_map`) — every one of those call sites checks
  `sum_map.get(col_name)` **before** ever calling
  `_measure_registry.resolve()`, so the correctly-reordered registry was
  never actually reached because the blanket sum measure always exists and
  always won first. Rather than patch 9 scattered call sites (each a
  different risk surface), fixed the shim's own construction to already
  resolve to the correct measure per column — merging `_avg_measure_map`/
  `_min_measure_map`/`_max_measure_map`/`_median_measure_map`/
  `_count_measure_map` on top of `_sum_measure_map` with the same
  most-specific-wins precedence, so every existing `sum_map.get(col)` call
  site gets the right answer automatically, with zero call-site changes
  and therefore minimal blast radius.

**Root cause of Issue 2 (datasource caption replacing physical tables)**:
Increment 22/32's dimension-table-splitting design (additive: keep a flat
"fact" table + add real dimension tables + relationships) already
correctly identifies the real physical fact table per datasource
(`ds['_dim_split']['fact_table']`, e.g. `'Orders'`, resolved from real
Tableau relation/metadata-records XML — see Increment 22) — but the flat
table itself was still being **named** after the Tableau datasource
caption (`raw_name`, e.g. `'superstore_data'`) rather than that already-
correctly-resolved physical fact table name. The datasource caption is
metadata describing the *container*, not a physical table, and the prompt
explicitly named this exact confusion. (Increment 32's earlier column-leak
filter and `_worksheet_used`/relationship-backfill work already satisfied
the prompt's "do not recursively collect every nested physical column
into one flat logical table" / "only expose columns actually produced by
that table's M query" requirements — verified, not re-implemented.)

**Fix (fix 2 of 2)**: `build_data_model_schema`'s per-datasource loop now
computes `_fact_source_name = ds['_dim_split']['fact_table'] if
ds.get('_dim_split') else raw_name` and uses it (instead of bare
`raw_name`) for both the fact table's own PBI name and its FilePath
parameter table's name. Every other reference that's supposed to stay
datasource-caption-based (the `embed_sample_data` extracted-data lookup
key, measure-name disambiguation suffixes) was deliberately left
unchanged — confirmed by tracing each `raw_name` usage individually rather
than a blanket rename, since the extracted-data alias
(`extract_twbx_data`'s Step 1b/2 `ds_cap` alias, Increment 32b) is keyed
by the datasource caption specifically and must stay that way for the
lookup to keep working.

**Verified**: `python3 -m py_compile` clean throughout. Direct fixture
checks: `sales_dashboard`/`sales_dashboard_no_conn`/`sales_dashboard_new`
now produce exactly `Orders`/`People`/`Returns` (previously
`superstore_data`/`People`/`Returns`) with the same 2 correct, cardinality-
verified relationships as Increment 32 (`Orders.Region -> People.Region`,
`Orders.Order ID -> Returns.Order ID`) — the exact "SUPERSTORE EXPECTED
MODEL" the prompt specified, achieved generically (no workbook-specific
code — confirmed on a structurally different fixture too: Amazon Sales
Insights' fact table is now `transactions`, its own real physical sub-
table name, instead of whatever its datasource caption was). Full
`write_pbit()` regression re-run across all 10 real fixtures in
`source/`+`input/`: 10/10 succeed with zero exceptions, zero placeholder
paths, zero fake-aggregation-named physical columns (scanned every
generated fixture's TOM columns array directly), and the same independent
dangling-visual-field-reference scan used since Increment 32 is still
clean on every one. `House Sales Dashboard.twb`'s `'Average House Sales
Price'`/`'Average Price Distribution By Country'`/`'Distribution of House
Prices'` worksheets now correctly resolve to `Average of Price` in the
final generated visual JSON (confirmed by inspecting the real written
`Report/Layout`, not just the intermediate DAX measure list) while
unrelated `'Grade'`-based worksheets on the same fixture correctly remain
`Sum of Grade` — proof the fix is per-column/per-aggregation, not a
blanket behavior change.

**Not yet done / deliberately out of scope**: `STDEV`/`STDEVP`/`VAR`/
`VARP`/`ATTR` aggregations were not implemented — no real or synthetic
fixture exercises them, and the prompt itself warns "do not blindly map
functions if Tableau and Power BI semantics differ," so guessing their
DAX shape without a real sample to verify against was avoided (same
standing project discipline as every prior increment). The
`MeasureRegistry.resolve()` priority order (median → max → min → avg →
count → sum) is a deterministic but not fully context-aware
disambiguation: if the exact same physical column were used with two
DIFFERENT explicit aggregations in two DIFFERENT worksheets of the same
workbook (e.g. `AVG(Price)` in one chart, `MAX(Price)` in another), both
would currently resolve to whichever kind wins the fixed priority order,
not the aggregation that specific worksheet actually asked for — no
current fixture exercises this exact collision (confirmed: every field
found via `_scan_agg_fields` across all 10 fixtures uses exactly one
explicit aggregation kind workbook-wide), so it's a flagged, unverified
edge case rather than a fix. Threading a per-worksheet aggregation hint
through `MeasureRegistry.resolve()`'s ~9 call sites (mirroring the
already-present-but-unused `prefer_ctd` parameter) is the natural next
step if a real fixture ever surfaces that collision.

---

### Increment 34 — Root-cause fix: Power Query duplicate-field error ("The field '...' already exists in the record")

User-reported, urgent: a real Power BI refresh failure — `Orders`: "The
field 'Region (People)' already exists in the record." — explicitly
requested as a generic fix, not a `Region (People)`-specific patch.
Root-caused before touching any code, and the root cause was self-
inflicted: Increment 32c's own `_append_m_copy_columns` backfill (added
last session to stop `Region (People)`-style relationship join-key columns
from rendering blank charts) used `Table.AddColumn` unconditionally,
without checking whether the target column already existed in the table
it was appending to.

**Why it already existed**: the column being backfilled (e.g.
`Region (People)`) is kept in a table's TOM schema (`pbi_columns`) because
some worksheet genuinely uses it, but it is NOT physically produced by
that table's own source. The bug: `all_cols` (the column list handed to
EVERY M-query builder — sample/CSV/Excel/generic/live — for
`Table.TransformColumnTypes`/`RenameColumns`/the `#table(...)` schema
declaration) was built from the SAME `dimension_cols`/`measure_cols` list
as `pbi_columns`, so it included this column too, unconditionally. Two
compounding symptoms, same root cause:
1. `Table.TransformColumnTypes(Promoted, {..., {"Region (People)", type
   text}})` — declared a source-column transform for a column the raw
   Excel sheet doesn't have at all (file-path-parameter mode), or the
   `#table(...)` schema declared it with a `null` placeholder value for
   every row (embed_sample_data mode, via `_get_embedded_rows`'s existing
   "column not found in row → emit null" fallback).
2. Increment 32c's own `Table.AddColumn(Typed, "Region (People)", each
   [Region])` step then tried to ADD a column of the same name — which
   Power Query rejects outright: **"The field '...' already exists in the
   record."** — reproduced and confirmed exactly on
   `sales_dashboard_no_conn.twbx`'s `Orders` table before any fix.

This is a real instance of the generic class of bug the user's prompt
described (a physical column not genuinely owned by a table's source being
treated as if it were), not a one-off `Region (People)` bug — confirmed
generic because the fix required zero references to any specific field,
table, or workbook name anywhere in the code path; it operates purely on
`ds['_dim_split']['relationships']` metadata (see Increment 22/32) for
whichever fields a given workbook happens to have.

**Fix (`tableau_pbi_server.py`, `build_data_model_schema`)**:
1. The `_fk_copy_pairs` computation (which columns need backfilling, and
   from which fact-side column) was moved to run **before** `all_cols` is
   built, instead of after the M-query builders had already run.
2. `all_cols` now explicitly **excludes** every backfill-target column:
   `all_cols = [c for c in (dimension_cols + measure_cols) if c['name']
   not in _fk_copy_target_names]`. `pbi_columns` (the TOM declaration) is
   unaffected — built earlier, from the unfiltered `dimension_cols`, so
   the column is still fully declared and visual-bindable; only the
   M-query builders' "what does this table's physical source produce"
   list changes.
3. `_append_m_copy_columns` now takes a defensive `existing_col_names` set
   (the same filtered `all_cols`, passed at the call site) and **skips**
   (never silently overwrites, never blindly renames) any copy pair whose
   target is already present, logging a clear warning — belt-and-suspenders
   against the same collision class from any future code path, per the
   user's explicit "do not blindly rename/delete/ignore a duplicate"
   instruction.
4. Audited every other `Table.AddColumn` call site in the file (2 found:
   this one, and the pre-existing "Month Number" real-M-column injection
   in `_build_sample_m_query`) — the Month Number one was already safe,
   since its target name is generated via `_unique_name(...,
   seen_field_names, ...)` against the same collision-tracking set used
   for every other column in the table, confirmed by direct code reading,
   not assumed.

**Verified**: `python3 -m py_compile` clean. Re-inspected
`sales_dashboard_no_conn.twbx`'s generated `Orders` M query directly (both
file-path-parameter mode and, via a full `write_pbit()` run, embed_sample_data
mode): `Region (People)` no longer appears in
`Table.TransformColumnTypes`/the `#table(...)` schema at all, and is added
exactly once via `Table.AddColumn` at the very end — no collision possible.
Full `write_pbit()` regression across all 10 real fixtures: 10/10 succeed
with zero exceptions. New comprehensive scan added and run across every
fixture's ENTIRE generated model — every table's TOM `columns` array
checked for duplicate names, every table's M-query `#table(type table
[...])` schema header parsed and checked for duplicate field declarations,
every `Table.AddColumn` target checked for duplicate calls within the same
query: **zero duplicates found anywhere**, on top of the existing
dangling-visual-field-reference and placeholder-path scans (also still
clean on all 10).

**Relationship to the user's broader architectural requests**: several of
the prompt's specific requirements were already satisfied by prior
increments, confirmed rather than re-implemented this session — physical
table ownership is already preserved (Increment 22's dimension-table
split + Increment 33's fact-table-physical-naming: a datasource's
physical tables are real, separate Power BI tables, not flattened into
one); same-name-different-table fields already stay separate (each
physical table's own column list is independently built from
`col_table`-verified ownership, Increment 32); Tableau-disambiguated names
(`Field (Table)`) already resolve to their real owning table via
`col_table`/`_dim_split`, not a hardcoded parenthetical-parsing rule. The
prompt's full ask — one single canonical field-resolution object consumed
identically by Power Query generation, DAX, visual binding, filters,
sorting, tooltips, hierarchies, and parameters — is not literally one
object in this codebase; it is several purpose-built maps (`col_table`,
`col_name_map`, `MeasureRegistry`, `_dim_split`) each covering one layer,
consistent with "reuse existing structures" but short of a single unified
API. Unifying those into one unbrella resolver was not attempted this
session — the concrete, reported, reproducible bug is fixed at its true
root cause and verified with a real duplicate-field scan across the whole
model, not merely suppressed; a full unification is a much larger
refactor with its own regression risk and was not requested as urgently
as the refresh-blocking error this increment fixes.

---

### Increment 35 — Permanent visual-field validation gate + 3 newly-discovered root causes (user-directed, `new_enhancement_prompt.md`)

New session. The user's prompt described two remaining generic problem
classes, using the SAME symptom already fixed for specific cases in prior
increments (`Region (People)` / `Sum of Sales` warning icons; Postal Code
datatype mismatches on refresh) but asked for the *general* fix, not another
one-off patch, plus an explicit pre-generation validation gate (its Section
D: "validate every visual field... before generating the final report...
do not generate a broken visual and expect Power BI Desktop to repair it").

**Audit before coding, per standing discipline**: read `master_migration_prompt.md`,
this file (all 32-34 increments), `tableau_pbi_server.py`, and
`report_migration_tool.py` first. Found that Section A (datatype inferred
from Tableau's own declared `datatype=` attribute, never sniffed from
column names — confirmed no name-based Postal-Code-style coercion exists
anywhere), Section B (Power Query column types are always keyed from that
same declared datatype, never guessed), and Section C/parts of D
(relationship datatype-compatibility gate, cardinality inference from real
extracted data, dangling-relationship validation-and-drop) were **already
implemented** in Increments 22/32/32b/32c/33/34 — verified by direct code
reading, not re-implemented. The one clearly missing piece, confirmed by
reading `write_pbit()` directly: `build_data_model_schema()` (schema) and
`build_report_layout()` (visual JSON) are built independently and **never
cross-validated against each other** before being written to the `.pbit`.
Every prior increment's "dangling-reference scan" was a throwaway script
re-written each session to verify a fix — never a permanent part of the
generated output itself.

**Fix 1 — permanent validation + repair gate (Section D)**: new
`_validate_and_repair_visual_field_refs(layout, schema)`
(tableau_pbi_server.py, just above `write_pbit`), wired into `write_pbit()`
right after both `schema` and `layout` are built. Walks every visual
container's already-serialized `config`/`query`/`filters` JSON strings (the
literal bytes Power BI Desktop itself parses, not the intermediate Python
objects ~15 different `_make_*_vc` functions built them from — so this
works uniformly across every visual type without touching each builder) and
recursively resolves every `Column`/`Measure` reference's table (via
`Entity` or a scope-tracked `Source` alias resolved against the nearest
enclosing `From` list) + `Property` against the real generated TOM
table/column/measure lists. Three-tier repair policy, safe-by-construction
because every JSON shape it writes is one already proven correct elsewhere
in this file (never an invented/guessed shape):
1. Resolves correctly as-is → no-op.
2. Wrong table, but the field exists as the SAME kind on exactly one other
   real table → repointed (`Entity` string swap, or a new aliased `From`
   entry for a `Source`-alias reference — reusing the exact "second aliased
   `From` entry in one query" shape this file's dimension-lookup filter
   visuals already use, so only the one dangling reference is touched, not
   every reference sharing that alias).
3. Not that kind anywhere, but a real field of the same name exists as the
   OPPOSITE kind (`Column` reference where only a `Measure` exists, or vice
   versa) on the same or one other unambiguous table → the JSON key itself
   is flipped (`Column`↔`Measure`), optionally combined with tier 2's
   repointing.
4. Missing everywhere, or ambiguous across multiple tables with no single
   owner → left untouched (never guessed) and reported.
Every table/field lookup is schema-driven (built fresh from the actual
generated TOM each run) — nothing here inspects or special-cases any field,
table, or workbook name. Results feed a new "## Visual Field Validation"
section in `migration_audit.md` (`MigrationAudit.set_visual_field_diagnostics`)
listing every auto-repair and every genuine unresolved gap.

**Fix 2 — root cause of measures silently never being created (Issue 1 /
Section B, generic aggregation resolution)**: running the new validator
against the `sales_dashboard` family (the exact fixture used to build
Increments 32/32b/32c/33) immediately surfaced that `Orders` had **zero
measures at all** — `Sum of Sales` (used by two real visuals) didn't exist
anywhere in the model, a previously-invisible bug since nothing before this
session cross-checked visual references against the actual measure list.
Root-caused to `parse_tableau_workbook`'s `_add_column`: a datasource's raw
physical-schema `<column>` elements (e.g. a `<relation>`'s own column list —
`<column datatype='real' name='Sales' ordinal='17'/>`, confirmed via direct
XML read) carry no `role`/`type` attributes at all, so `role = col.get('role',
'dimension')` silently defaults every such column to `role='dimension',
measure=False` — even though the SAME field's real semantic classification
(`role='measure' type='quantitative'`) exists in a worksheet's
`<datasource-dependencies>` block (Pass 3), it was discarded because Pass
1/2's lower-confidence entry (physical schema only, added first) already
"won" the name and Pass 3 only fills genuine gaps, never upgrades. With
`measure=False`, the field never lands in `measure_cols`, so the fallback
"promote every numeric column to a Sum-of-X measure" pass in
`build_data_model_schema` never sees it either — zero measures, and every
chart referencing `SUM(Sales)` binds to a Power BI measure that was never
generated. Fixed generically: `_add_column` now tracks, per column name,
whether the CURRENT entry came from an explicit `role=` attribute; a later
element that DOES carry an explicit `role=` and finds no explicit-role entry
on file upgrades the existing entry's `role`/`measure` classification in
place instead of being discarded as a duplicate. Never inspects a field
name — purely "does this XML element carry real semantic metadata or not."

**Fix 3 — root cause of `Region (People)`/`Sum of Sales`-class dangling
references for ANY dimension-split datasource (Issue 1 / Section D, the
actual root of the prompt's headline screenshot)**: `build_report_layout`
computes its own table-name map, `ds_pbi_names = {ds['name']: _safe(ds['name'])
for ds in datasources}`, entirely from the raw Tableau datasource name/caption
(e.g. `'superstore_data'`). Increment 33 renamed the FACT table itself (in
`build_data_model_schema`, via `ds['_pbi_table_name']`) to the real physical
table name (e.g. `'Orders'`) whenever a datasource was dimension-split — but
never touched `build_report_layout`'s independent name derivation, so every
one of the ~30 call sites across this file that build a visual's table
reference from `pbi_table_name` kept pointing at the OLD, now-nonexistent
table name for every field except the ones Increment 32c's join-key backfill
happened to cover. Confirmed as the single largest source of dangling
references this session: fixing it alone resolved 100% of Amazon Sales
Insights' 23 unresolved references (its fact table is `transactions`, not
its datasource's raw name) and eliminated the need for the new validator's
repair logic on the `sales_dashboard` family entirely (0 repairs needed once
the table name is right from the start, vs. 10 needed before this fix).
Fixed by making `ds_pbi_names` prefer `ds.get('_pbi_table_name')` — the
exact name `build_data_model_schema` already computed and left on the same
`ds` dict object, available because `write_pbit` always builds the schema
before the layout — falling back to the old `_safe(ds['name'])` only when
that attribute is absent (e.g. a caller invoking `build_report_layout`
without `build_data_model_schema` first).

**Fix 4 — missing `MY` (Month/Year) Tableau date-derivation prefix (found
via the new validator on House Sales Dashboard)**: `<column-instance
derivation='MY' name='[my:date:ok]'/>` (real, schema-confirmed XML) was not
in any of this file's 4 separate hard-coded shelf-token-prefix
lists/regexes (`_clean_field_name`'s qualified-prefix regex, `_DATE_DIM_PFXS`,
`_DG_LEVEL_MAP` + its regex, the table-visual `shelf_row_fields` regex, and
the date-part filter-skip regex) — every one already lists `yr/qr/mn/wk/dt/
dy/hr/mi/ss` but not `my`. Without it, the literal token string `'my:date:ok'`
survived uncleaned into a `tableEx` visual's shelf-field list, producing
exactly the class of "field doesn't exist" warning icon the prompt
describes. Added `my` to all 5 locations (mapped to PBI's `'Month'` Date
Hierarchy level in `_DG_LEVEL_MAP` — the closest single level to Tableau's
combined Month/Year truncation, the same approximation already used for
`dt`/`dy` both mapping to `'Day'`).

**Not attempted this session**: Section B's "safe Power Query type
conversion for dirty source values" (wrapping `Table.TransformColumnTypes`
so one malformed row doesn't fail the whole query) — real and
well-documented Power Query behavior, but no real or synthetic fixture in
this repo currently exercises a genuine conversion failure (unlike the four
fixes above, all reproduced against real fixtures), and retrofitting
per-value-safe conversion across every M-query builder (sample/CSV/Excel/
generic/join-tree/live-DB) is a materially larger, higher-risk change than
anything else this session touched. Flagged as the next concrete target if
a fixture ever surfaces an actual conversion failure — consistent with this
project's "verify before implementing, don't guess" discipline.

**Verified**: `python3 -m py_compile tableau_pbi_server.py` clean throughout.
Full regression across all 10 real fixtures (`input/` × 5 + `source/` × 5,
via a driver script that calls `parse_tableau_workbook` →
`write_pbit(..., extracted_data=workbook['extracted_data'])` exactly like
production `convert_file()` does): **10/10 succeed with zero exceptions**,
and — independently, by opening the actual written `.pbit` zip and decoding
every `UTF-16LE` JSON part — **zero dangling visual field references and
zero duplicate column/measure names in any of the 10**, all `DataModelSchema`/
`Report/Layout`/`DiagramLayout`/`Settings`/`Metadata` parts round-trip as
valid JSON. Before these fixes the same 10-fixture run had 4 fixtures with
genuine unresolved dangling references (Amazon Sales Insights: 23; LocBar:
8; House Sales Dashboard: 4; Netflix: 3) and the `sales_dashboard` family
needed 10 auto-repairs per fixture just to reach zero — after fix 2/3/4,
every one of those is now correct from generation, and the validator (fix 1)
remains as a permanent regression gate for whatever the next workbook
surfaces, not a one-time patch.
