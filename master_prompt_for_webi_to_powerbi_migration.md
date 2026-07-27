You are working on an existing production-oriented BI Report Migration application.

IMPORTANT ARCHITECTURAL REQUIREMENT:

The existing application already contains a Tableau → Power BI migration engine implemented primarily in:

    tableau_pbi_server.py

This existing Tableau migration engine must remain functional.

We are now adding a SECOND, INDEPENDENT migration capability:

    SAP BusinessObjects Web Intelligence (WebI) → Microsoft Power BI

The goal is NOT to replace the Tableau migration engine.

The goal is NOT to modify tableau_pbi_server.py to support WebI.

The goal is to create a NEW, dedicated WebI → Power BI migration engine that exists alongside the Tableau → Power BI migration engine.

The final application must support both migration paths:

============================================================
EXISTING MIGRATION PATH
============================================================

Tableau Workbook
    ↓
tableau_pbi_server.py
    ↓
Power BI Report

============================================================
NEW MIGRATION PATH
============================================================

SAP BusinessObjects WebI Report / Document
    ↓
NEW WebI Migration Engine
    ↓
Power BI Report

============================================================
CRITICAL RULE
============================================================

DO NOT modify, rewrite, replace, or break:

    tableau_pbi_server.py

unless a change is absolutely required for shared infrastructure integration.

If shared code needs to be changed, first determine whether it is genuinely shared infrastructure.

If a change is required:

1. Preserve all existing Tableau functionality.
2. Do not alter Tableau migration behavior.
3. Run the complete Tableau regression suite before and after the change.
4. Document exactly what changed.
5. Confirm that all existing Tableau migrations still work.

Prefer creating NEW WebI-specific modules over modifying existing Tableau-specific code.

============================================================
1. FIRST: INSPECT THE COMPLETE EXISTING PROJECT
============================================================

Before writing or modifying code, inspect the complete repository.

Identify:

- tableau_pbi_server.py
- All Python files
- All migration-related files
- All Power BI generation utilities
- All Power BI serialization logic
- All report generation logic
- All frontend files
- All API routes
- All server routes
- All migration status/progress logic
- All SSE/event-streaming logic
- All logging
- All validation modules
- All test modules
- All test data
- All source directories
- All target directories
- All error directories
- All generated output directories
- All configuration files
- All dependency files
- All documentation

Understand exactly how the current Tableau → Power BI migration works.

DO NOT immediately change anything.

Create a complete architecture map.

Identify:

A. Tableau-specific components
B. Generic/shared components
C. Power BI generation components
D. UI/API components
E. File handling components
F. Validation components
G. Logging/progress components

Classify each component as:

- TABLEAU-SPECIFIC
- WEBI-SPECIFIC
- SHARED / GENERIC
- POWER-BI-GENERATION
- UI / API
- VALIDATION

Do not assume a component is reusable simply because it generates Power BI.

Verify whether it is actually source-system-independent.

============================================================
2. NEW FILE REQUIREMENT
============================================================

Create a new dedicated main server/migration engine file:

    webi_pbi_server.py

This file is the NEW primary entry point for SAP WebI → Power BI migration.

The existing file:

    tableau_pbi_server.py

continues to handle:

    Tableau → Power BI

The new file:

    webi_pbi_server.py

handles:

    SAP WebI → Power BI

Do not merge both migration engines into one large source-specific file.

The source-specific responsibilities must remain separated.

The architecture must support:

    tableau_pbi_server.py
    webi_pbi_server.py

as two independent migration engines.

============================================================
3. RECOMMENDED NEW WEBI MODULE STRUCTURE
============================================================

Create a clean modular architecture.

At minimum, design the WebI migration engine around:

    webi_pbi_server.py

and, where appropriate, additional modules such as:

    webi_parser.py
    webi_input_detector.py
    webi_ir.py
    webi_semantic_model.py
    webi_universe_parser.py
    webi_data_provider_parser.py
    webi_query_parser.py
    webi_formula_parser.py
    webi_dax_converter.py
    webi_visual_mapper.py
    webi_field_resolver.py
    webi_layout_mapper.py
    webi_powerbi_generator.py
    webi_validator.py
    webi_migration_audit.py

Do NOT blindly create all these files if the existing project architecture already has equivalent generic modules.

First inspect the repository.

If a generic module already exists and is truly source-independent, reuse it.

If a component is Tableau-specific, do not force WebI into it.

If a component contains mixed source-specific and generic logic, refactor carefully only if necessary.

The final structure should have clear separation:

SOURCE-SPECIFIC
    Tableau Parser
    WebI Parser

SHARED SEMANTIC / POWER BI
    Power BI Model Generator
    Power BI Artifact Validator
    Common Field Registry
    Common Layout Utilities
    Common Audit Utilities

============================================================
4. WEBI MIGRATION ENTRY POINT
============================================================

Implement the main WebI migration entry point in:

    webi_pbi_server.py

It must be capable of:

1. Accepting WebI input.
2. Detecting input type.
3. Parsing WebI metadata.
4. Building the WebI Intermediate Representation.
5. Reconstructing semantic/data model metadata.
6. Reconstructing Universe information where available.
7. Reconstructing Data Providers and Queries.
8. Extracting dimensions, measures, details, and variables.
9. Converting formulas to DAX.
10. Extracting filters and prompts.
11. Extracting WebI blocks.
12. Classifying visualizations.
13. Resolving visual field mappings.
14. Reconstructing report pages/sections.
15. Generating Power BI semantic model.
16. Generating Power BI report visuals.
17. Reconstructing layout.
18. Validating generated artifacts.
19. Generating migration audit.
20. Generating migration quality score.
21. Reporting progress.
22. Handling errors gracefully.

The flow must be:

WebI Input
    ↓
Input Detection
    ↓
WebI Parsing
    ↓
Normalized WebI Intermediate Representation
    ↓
Semantic Model Reconstruction
    ↓
Datasource / Universe Reconstruction
    ↓
Data Provider / Query Reconstruction
    ↓
Power BI Semantic Model
    ↓
Formula → DAX Conversion
    ↓
Filter / Prompt Conversion
    ↓
Block Classification
    ↓
Visual Classification
    ↓
Field-Well Resolution
    ↓
Visual Generation
    ↓
Layout Reconstruction
    ↓
Power BI Artifact Validation
    ↓
Semantic Validation
    ↓
Migration Audit
    ↓
Quality Score
    ↓
Power BI Output

============================================================
5. DO NOT COPY TABLEAU LOGIC BLINDLY
============================================================

The existing Tableau engine contains valuable architectural patterns.

Study and reuse proven generic concepts where appropriate:

- Metadata-driven migration
- Semantic model normalization
- Field resolution
- Aggregation preservation
- Visualization classification
- Automatic field mapping
- Layout reconstruction
- Validation
- Migration audit
- Regression testing
- Root-cause analysis

However:

DO NOT assume Tableau and WebI have the same source model.

Do not simply replace:

Tableau XML

with:

WebI XML

WebI has a fundamentally different reporting architecture.

The WebI migration engine must understand:

- WebI Documents
- Reports
- Sections
- Blocks
- Data Providers
- Queries
- Universes
- Universe Classes
- Universe Objects
- Dimensions
- Measures
- Details
- Variables
- Formulas
- Filters
- Prompts
- Input Controls
- Breaks
- Sorts
- Subtotals
- Crosstabs
- Tables
- Charts
- Report-level calculations
- Section-level calculations
- Block-level calculations
- Data provider relationships
- Merged dimensions
- Context-sensitive calculations

============================================================
6. WEBI INPUT FORMAT DISCOVERY
============================================================

First determine which WebI input formats can be provided to this application.

Do not assume a single WebI format.

Investigate the available input samples.

Potential formats may include:

- WebI document exports
- WebI XML
- WebI document metadata
- WebI SDK/API extracted metadata
- Universe metadata
- Report metadata
- Data Provider metadata

Implement an input detection layer.

The architecture should be:

WebI Input
    ↓
Input Format Detector
    ↓
Correct Parser Adapter
    ↓
Normalized WebI Intermediate Representation

The rest of the migration engine must operate on the normalized representation.

This prevents the downstream Power BI migration logic from being tightly coupled to one WebI input format.

============================================================
7. WEBI INTERMEDIATE REPRESENTATION
============================================================

Create a normalized WebI Intermediate Representation.

This is the central internal model for the new WebI migration engine.

Do not allow Power BI visual generation to repeatedly parse raw WebI source files.

The normalized representation must capture, wherever source metadata supports it:

DOCUMENT

- Document name
- Document ID
- Description
- Owner
- Created date
- Modified date
- Locale
- Currency
- Formatting defaults

REPORT

- Report name
- Report ID
- Report order
- Page size
- Orientation
- Filters
- Prompts
- Variables
- Layout

SECTION

- Section name
- Section order
- Hierarchy
- Filters
- Variables
- Layout

PAGE

- Page name
- Width
- Height
- Orientation
- Margins
- Headers
- Footers

BLOCK

- Block name
- Block type
- Position
- Size
- Dimensions
- Measures
- Details
- Variables
- Filters
- Sorts
- Breaks
- Subtotals
- Grand totals
- Formatting
- Conditional formatting

DATA PROVIDER

- Provider name
- Provider type
- Connection metadata
- Query metadata
- Queries
- Prompts
- Filters
- Objects
- Result columns

QUERY

- Query name
- Universe
- Objects
- Dimensions
- Measures
- Details
- Filters
- Prompts
- Derived objects
- Query calculations

UNIVERSE

- Universe name
- Universe ID
- Classes
- Objects
- Dimensions
- Measures
- Details
- Joins
- Tables
- Aliases
- Derived tables
- Contexts
- Predefined filters

CALCULATED OBJECT

- Name
- Caption
- Formula
- Dependencies
- Data type
- Aggregation
- Scope
- Qualification

FILTER

- Type
- Field
- Operator
- Value
- Values
- Prompt
- Scope

SORT

- Field
- Direction
- Custom ordering

BREAK

- Field
- Hierarchy
- Subtotal behavior

VISUAL

- Visual type
- Dimensions
- Measures
- Details
- Categories
- Legends
- X-axis
- Y-axis
- Secondary axis
- Color
- Size
- Labels
- Tooltips
- Sorting
- Filters
- Formatting
- Position
- Size

LAYOUT

- X
- Y
- Width
- Height
- Parent
- Container
- Z-order
- Alignment

INTERACTIONS

- Drill
- Drill-down
- Drill-through
- Hyperlink
- Navigation
- Input control
- Filter interaction
- Prompt interaction

All downstream Power BI generation must consume this normalized model.

============================================================
8. UNIVERSE AND SEMANTIC LAYER RECONSTRUCTION
============================================================

Treat the WebI Universe as a first-class semantic layer.

Do not treat WebI as merely a visual report.

Extract and preserve where available:

- Universe
- Classes
- Objects
- Dimensions
- Measures
- Details
- Object qualification
- Data types
- Aggregation
- Hierarchies
- Joins
- Cardinality
- Contexts
- Aliases
- Derived tables
- Custom SQL
- Predefined filters

Understand the relationship:

Universe
    ↓
Classes
    ↓
Universe Objects
    ↓
Physical Tables / Columns
    ↓
Joins
    ↓
WebI Query
    ↓
Data Provider
    ↓
WebI Report

Do not confuse:

Universe

with:

Physical database

Do not confuse:

Data Provider

with:

Physical table

Do not flatten the entire WebI semantic model into one generic table unless the source metadata genuinely represents one table.

============================================================
9. DATA MODEL RECONSTRUCTION
============================================================

Reconstruct the underlying Power BI data model.

Identify:

- Physical databases
- Schemas
- Tables
- Views
- Columns
- Derived tables
- Custom SQL
- Joins
- Relationship keys
- Join types
- Cardinality
- Aliases

Generate:

- Power BI tables
- Columns
- Measures
- Relationships
- Data types
- Formatting
- Hierarchies

For every relationship validate:

- Source table exists
- Target table exists
- Source column exists
- Target column exists
- Compatible data types
- Valid cardinality
- Valid cross-filter direction
- No duplicates
- No dangling references

Do not silently drop relationships.

Do not invent relationships without evidence.

If relationship information is unavailable:

- Preserve available metadata.
- Mark relationship as unknown.
- Add an audit warning.
- Provide manual remediation guidance.

============================================================
10. MULTIPLE DATA PROVIDERS
============================================================

Support WebI reports containing multiple Data Providers.

Do not assume:

One WebI document
=
One query
=
One table

Support:

- Multiple Data Providers
- Multiple Queries
- Different Universes
- Merged Dimensions
- Synchronized Data Providers
- Provider-specific filters
- Provider-specific calculations

Represent:

Document
    ↓
Report
    ↓
Data Providers
    ↓
Queries
    ↓
Universe Objects
    ↓
Physical Sources
    ↓
Filters / Prompts
    ↓
Result Sets

Preserve the semantic meaning of multiple providers.

Where exact WebI merged-dimension behavior cannot be replicated directly:

- Identify it.
- Implement the closest valid Power BI model.
- Record the limitation.
- Provide remediation guidance.

============================================================
11. DIMENSIONS, MEASURES, DETAILS
============================================================

Correctly classify:

- Dimensions
- Measures
- Details

Do not classify based only on physical data type.

A numeric field may be a:

- Dimension
- Measure
- Detail

A text field may be:

- Dimension
- Detail

Preserve:

- Qualification
- Default aggregation
- Data type
- Caption
- Physical lineage

Maintain separate concepts for:

Physical Field

Semantic Object

Aggregation

Visual Role

For example:

Physical:
    Orders[Sales]

Semantic:
    SUM(Orders[Sales])

Visual:
    Values

Do not create fake physical columns such as:

    "Sum of Sales"

when "Sales" is the actual physical source field.

============================================================
12. AGGREGATION PRESERVATION
============================================================

Preserve WebI aggregation semantics.

Support where applicable:

- SUM
- AVG
- MIN
- MAX
- COUNT
- DISTINCT COUNT
- MEDIAN
- STDEV
- VAR
- Other supported aggregations

Distinguish:

Physical Column

from:

Semantic Measure

from:

Visual Aggregation

from:

Calculated Measure

Do not accidentally lose aggregation metadata during normalization.

Do not duplicate measures unnecessarily.

Audit every aggregation conversion.

============================================================
13. WEBI VARIABLES AND FORMULAS → DAX
============================================================

Create a dedicated WebI formula conversion layer.

Do not rely only on string replacement.

Build structured formula parsing where possible.

Support patterns including:

- If
- Then
- Else
- ElseIf
- Case
- IsNull
- Not IsNull
- Previous
- RunningSum
- RunningCount
- RunningAverage
- Sum
- Average
- Min
- Max
- Count
- DistinctCount
- ForEach
- ForAll
- Where
- Date functions
- String functions
- Numeric functions
- Logical functions

Support:

- Nested expressions
- Variable dependencies
- Dependency graphs
- Circular dependency detection
- Formula scope
- Block scope
- Report scope
- Data provider scope
- Aggregation context

For each formula:

1. Extract.
2. Parse.
3. Build dependency graph.
4. Classify.
5. Convert to DAX.
6. Validate references.
7. Detect unresolved dependencies.
8. Assign confidence.
9. Record migration status.

Use:

SUCCESS
PARTIAL
APPROXIMATED
UNSUPPORTED
FAILED

Never silently generate incorrect DAX.

============================================================
14. CONTEXT-AWARE CALCULATIONS
============================================================

WebI calculations can depend on:

- Report
- Section
- Block
- Dimension
- Break
- Filter
- Data Provider

Do not convert every WebI formula into a global measure blindly.

Determine whether the correct Power BI representation is:

- Measure
- Calculated column
- Power Query transformation
- Supporting table
- Visual-level logic
- DAX with CALCULATE
- DAX with FILTER
- DAX with ALLEXCEPT
- DAX with REMOVEFILTERS
- DAX with VALUES
- DAX with SELECTEDVALUE

Use the representation that most closely preserves original semantics.

============================================================
15. FILTERS
============================================================

Detect and migrate:

- Query filters
- Report filters
- Section filters
- Block filters
- Universe filters
- Prompt filters
- Input controls
- Value filters
- Range filters
- Date filters
- Relative date filters
- In-list
- Not-in-list
- Null filters
- Complex logical filters

Preserve:

- Field
- Operator
- Value
- Scope
- Precedence
- Prompt dependency

Do not silently drop filters.

Do not move a query-level filter to a report-level filter if that changes data semantics.

============================================================
16. PROMPTS AND INPUT CONTROLS
============================================================

Detect:

- Mandatory prompts
- Optional prompts
- Multi-value prompts
- Default values
- Prompt dependencies
- Cascading prompts
- Input controls

Map to Power BI equivalents where appropriate:

- Slicers
- Field Parameters
- What-if Parameters
- Dynamic M Parameters
- Filter Controls

Do not blindly map all prompts to What-if Parameters.

Determine whether each prompt controls:

- Data retrieval
- Filtering
- Calculation
- Visualization
- Query behavior

============================================================
17. WEBI BLOCK MIGRATION
============================================================

Detect and migrate:

- Tables
- Crosstabs
- Forms
- Free-standing cells
- Charts
- Sections
- Headers
- Footers

For each block extract:

- Dimensions
- Measures
- Details
- Variables
- Filters
- Sorts
- Breaks
- Subtotals
- Grand totals
- Conditional formatting
- Position
- Size

The Power BI visual must represent the analytical intent of the original WebI block.

============================================================
18. VISUALIZATION CLASSIFICATION
============================================================

Build a generic WebI visual classification engine.

Do not classify only by chart name.

Use:

- Block type
- Chart metadata
- Dimensions
- Measures
- Aggregation
- Hierarchy
- Color
- Size
- Layout
- Axis metadata
- Legend metadata
- Analytical intent

Support where possible:

- Table
- Matrix
- Crosstab
- Card
- Multi-row Card
- KPI
- Bar
- Column
- Clustered Bar
- Clustered Column
- Stacked Bar
- Stacked Column
- 100% Stacked
- Line
- Multi-Line
- Area
- Stacked Area
- Combo
- Dual Axis
- Pie
- Doughnut
- Scatter
- Bubble
- Treemap
- Waterfall
- Ribbon
- Funnel
- Heat Map
- Highlight Table
- Gauge
- Map
- Filled Map
- Histogram
- Bullet
- Small Multiples

If Power BI does not have an exact equivalent:

1. Identify analytical intent.
2. Choose closest valid visual.
3. Preserve field roles.
4. Record approximation.
5. Provide audit warning.

Never silently replace a chart with an unrelated visual.

============================================================
19. FIELD-WELL RESOLUTION
============================================================

Automatically resolve Power BI visual fields.

Examples:

Line:
- Axis
- Legend
- Values

Bar:
- Category
- Values
- Legend

Scatter:
- X
- Y
- Size
- Details
- Legend

Treemap:
- Category
- Values
- Group

Pie/Doughnut:
- Legend
- Values

Matrix:
- Rows
- Columns
- Values

Card:
- Correct measure
- Correct aggregation

KPI:
- Indicator
- Trend Axis
- Target

Never generate empty visual templates that require manual field assignment.

Every generated visual should have its semantic fields resolved.

============================================================
20. REPORT LAYOUT
============================================================

Reconstruct WebI report layout.

Preserve where possible:

- Reports
- Pages
- Sections
- Blocks
- Position
- Width
- Height
- Alignment
- Spacing
- Headers
- Footers
- Backgrounds
- Text
- Images
- Containers
- Z-order

Map:

WebI Report
    →
Power BI Report Page

WebI Section
    →
Power BI Page / Section Equivalent

WebI Block
    →
Power BI Visual

Use original coordinates to calculate relative Power BI placement.

Do not simply place every visual into a generic grid.

============================================================
21. FORMATTING
============================================================

Preserve where metadata supports it:

- Titles
- Fonts
- Font sizes
- Colors
- Backgrounds
- Borders
- Axis titles
- Number formats
- Currency
- Percentage
- Date formats
- Legends
- Data labels
- Gridlines
- Conditional formatting
- Sorting
- Tooltips

Semantic correctness takes priority over cosmetic similarity.

============================================================
22. INTERACTIONS
============================================================

Detect and migrate where possible:

- Drill-down
- Drill-through
- Hyperlinks
- Navigation
- Input controls
- Filters
- Prompts
- Cross-filtering
- Cross-highlighting

If exact migration is not possible:

- Detect
- Record
- Explain
- Recommend closest Power BI equivalent

Do not invent invalid Power BI internal JSON.

============================================================
23. DATE AND TIME HANDLING
============================================================

Correctly preserve:

- Date
- DateTime
- Time
- Year
- Quarter
- Month
- Week
- Day

Prevent incorrect conversions to:

- 1900
- 1899
- Blank
- Text

Preserve:

- Date hierarchy
- Fiscal year
- Fiscal periods
- Month sorting
- Week numbers

============================================================
24. FIELD NAME AND LINEAGE MANAGEMENT
============================================================

Implement centralized field normalization.

Normalize:

- Object names
- Captions
- Aliases
- Physical names
- Special characters
- Duplicate names

Never lose lineage.

Track:

WebI Object
    ↓
Universe Object
    ↓
Query Object
    ↓
Data Provider Object
    ↓
Report Object
    ↓
Power BI Table
    ↓
Power BI Column / Measure

Prevent collisions.

============================================================
25. POWER BI GENERATION ORDER
============================================================

Generate the Power BI model BEFORE generating visuals.

Required order:

WebI Input
↓
WebI Parser
↓
WebI Intermediate Representation
↓
Datasource / Universe Reconstruction
↓
Power BI Tables
↓
Power BI Relationships
↓
Calculated Columns
↓
Measures
↓
Hierarchies
↓
Sort Columns
↓
Formatting
↓
Semantic Validation
↓
Visual Classification
↓
Field Resolution
↓
Visual Generation
↓
Layout
↓
Artifact Validation

No visual may reference:

- Missing table
- Missing column
- Missing measure

No measure may reference:

- Missing column
- Missing table

No relationship may reference:

- Missing table
- Missing column

============================================================
26. POWER BI ARTIFACT VALIDATION
============================================================

Before finalizing output validate:

- File structure
- JSON validity
- Encoding
- GUID references
- Table references
- Column references
- Measure references
- Relationship references
- Visual references
- Query references
- Projection references
- Filters
- Slicers
- Page references
- Layout coordinates

Detect:

- Dangling references
- Duplicate GUIDs
- Missing GUIDs
- Invalid query references
- Invalid selectNames
- Empty visual projections
- Missing fields
- Missing relationships

Do not rely on Power BI Desktop to discover errors.

Validate before output.

============================================================
27. SEMANTIC VALIDATION
============================================================

Do not validate only whether the Power BI file opens.

Validate semantic equivalence.

For example:

WebI:

Revenue by Month

Power BI must represent:

Revenue by Month

not:

Revenue by Region

Validate:

- Dimensions
- Measures
- Aggregations
- Filters
- Sorting
- Grouping
- Date granularity
- Visual type
- Field roles

Generate a semantic validation result for every migrated block.

============================================================
28. MIGRATION AUDIT
============================================================

Every WebI migration must generate a migration audit.

Track:

DOCUMENT

- Document
- Reports
- Sections
- Blocks

DATA

- Data Providers
- Queries
- Universes
- Tables
- Relationships

CALCULATIONS

- Variables
- Formulas
- Success
- Partial
- Approximation
- Unsupported
- Failed

VISUALS

- Original WebI visual
- Analytical intent
- Power BI visual
- Field assignments
- Confidence

LAYOUT

- Position match
- Size match
- Page structure

FILTERS

- Filter conversion
- Prompt conversion

INTERACTIONS

- Converted
- Approximated
- Unsupported

QUALITY SCORE

Generate:

- Per-document score
- Per-report score
- Per-section score
- Per-block score
- Overall score

Clearly distinguish:

MEASURED
HEURISTIC
MANUAL VERIFICATION REQUIRED

============================================================
29. QUALITY SCORING
============================================================

Calculate quality using measurable dimensions:

- Data Model Match
- Semantic Match
- Calculation Match
- Filter Match
- Prompt Match
- Visual Match
- Field Mapping Match
- Layout Match
- Formatting Match
- Interaction Match

Do not inflate scores.

Unsupported features must reduce the score.

Approximated features must be marked partial.

Unknown features must not be treated as successful.

============================================================
30. ROOT-CAUSE DEBUGGING
============================================================

When a WebI migration defect occurs:

Do NOT immediately patch the generated Power BI visual.

Trace the issue backward.

Possible root causes:

- Input extraction
- WebI parser
- Universe parser
- Data Provider parser
- Query parser
- Semantic model
- Object classification
- Aggregation handling
- Formula parser
- DAX conversion
- Visual classification
- Field resolver
- Power BI model generator
- Layout engine
- Artifact serialization

Fix the earliest incorrect stage.

Example:

Wrong chart measure:

WebI Object
    ↓
Universe Object
    ↓
Query Object
    ↓
Data Provider
    ↓
Semantic Field Registry
    ↓
Measure Registry
    ↓
Visual Field Resolver

Find where the incorrect information first appears.

Fix that shared layer.

Do not add report-specific hacks.

============================================================
31. GENERIC MIGRATION REQUIREMENT
============================================================

The new WebI engine must NOT contain:

if document_name == "X"

if report_name == "Y"

if block_name == "Z"

if field_name == "Revenue"

if chart_name == "Sales Chart"

unless these are genuinely required by a standard WebI semantic rule.

The engine must work across arbitrary future WebI reports.

Every bug fix must generalize.

If a report fails:

Identify the metadata pattern.

Fix the generic parser or semantic layer.

============================================================
32. EXISTING TABLEAU ENGINE PROTECTION
============================================================

The existing:

    tableau_pbi_server.py

must continue to work independently.

Before WebI implementation:

Run existing Tableau regression tests.

After WebI implementation:

Run existing Tableau regression tests again.

Confirm:

- Tableau migration still works.
- Existing output format remains compatible.
- Existing UI functionality remains compatible.
- Existing API routes remain functional.
- Existing progress reporting remains functional.

If shared code is changed:

Document:

1. Why the change was required.
2. Which Tableau behavior could be affected.
3. What regression tests were run.
4. Results before change.
5. Results after change.

============================================================
33. WEBI API / UI INTEGRATION
============================================================

Inspect the existing UI and API architecture.

Do not break existing Tableau migration workflows.

Add WebI migration support as a separate migration path.

The UI should allow users to select:

    Tableau → Power BI

or:

    SAP WebI → Power BI

The backend should route to:

Tableau:
    tableau_pbi_server.py

WebI:
    webi_pbi_server.py

Do not duplicate the entire frontend unnecessarily.

Reuse existing UI components where possible.

Add source-type-specific validation.

For example:

If source type = Tableau:

Accept Tableau-supported input.

If source type = WebI:

Accept WebI-supported input.

Do not send WebI input to tableau_pbi_server.py.

Do not send Tableau input to webi_pbi_server.py.

============================================================
34. PROGRESS REPORTING
============================================================

The new WebI migration engine must integrate with the existing application's progress reporting architecture.

Expose progress stages such as:

1. Input Validation
2. WebI Input Detection
3. WebI Parsing
4. Semantic Model Construction
5. Universe Reconstruction
6. Data Provider Reconstruction
7. Query Reconstruction
8. Data Model Generation
9. Formula Conversion
10. Filter Conversion
11. Visual Classification
12. Field Mapping
13. Power BI Generation
14. Layout Reconstruction
15. Validation
16. Migration Audit
17. Finalization

Progress must be meaningful.

Do not show 100% before validation is complete.

============================================================
35. ERROR HANDLING
============================================================

Implement robust error handling.

Every error must identify:

- Document
- Report
- Section
- Block
- Data Provider
- Query
- Object
- Visual
- Migration stage

where applicable.

Classify errors:

- INPUT_ERROR
- PARSE_ERROR
- SEMANTIC_ERROR
- DATASOURCE_ERROR
- FORMULA_ERROR
- DAX_ERROR
- VISUAL_ERROR
- LAYOUT_ERROR
- VALIDATION_ERROR
- ARTIFACT_ERROR

Do not terminate the entire migration because one unsupported WebI feature is encountered.

Continue migration where safe.

Record failures in the audit.

============================================================
36. UNSUPPORTED FEATURE POLICY
============================================================

Never silently ignore unsupported WebI functionality.

For every unsupported feature:

1. Detect.
2. Record.
3. Assign severity.
4. Explain impact.
5. Identify closest Power BI equivalent.
6. Provide remediation guidance.

Use:

SUPPORTED
PARTIAL
APPROXIMATED
UNSUPPORTED
FAILED

Do not fabricate unsupported Power BI internal structures.

If exact migration cannot be safely implemented:

Detect + Audit + Recommend

instead of:

Guess + Corrupt

============================================================
37. REGRESSION TESTING
============================================================

Create:

    WEBI_MIGRATION_TEST_SUITE.md

The suite must document:

- Test case
- Input
- Expected behavior
- Actual behavior
- Result
- Known limitations
- Fixed issues
- Regression status

After every meaningful WebI engine change:

1. Process every WebI test document.
2. Generate Power BI output.
3. Validate artifact.
4. Validate semantic model.
5. Validate relationships.
6. Validate formulas.
7. Validate filters.
8. Validate visual field mappings.
9. Validate layout.
10. Validate migration audit.
11. Compare previous results.
12. Detect regressions.

============================================================
38. TEST COVERAGE
============================================================

Test representative WebI categories.

At minimum:

A. Simple table

B. Complex table

C. Crosstab

D. Multi-section report

E. Multiple Data Providers

F. Multiple Queries

G. Universe-based report

H. Variables

I. Complex formulas

J. Prompts

K. Input Controls

L. Multiple Filters

M. Merged Dimensions

N. Breaks

O. Subtotals

P. Charts

Q. Mixed charts and tables

R. Complex layouts

S. Drill-down

T. Hyperlinks

U. Unsupported features

V. Date-heavy reports

W. KPI reports

X. Dashboard-style reports

Y. Multi-page reports

Use real WebI samples where available.

If real samples are unavailable, create synthetic normalized metadata fixtures.

============================================================
39. UNSEEN WEBI REPORT VALIDATION
============================================================

The engine must be tested against at least one WebI report that was NOT used during initial development.

The goal is to verify that the engine is genuinely generic.

The unseen report must be processed without:

- Report-specific code
- Field-specific code
- Manual mapping code
- Hardcoded chart logic

If the unseen report requires a fix:

Generalize the fix.

Do not hardcode the unseen report.

============================================================
40. DOCUMENTATION
============================================================

Create or update documentation for:

1. WEBI_MIGRATION_ARCHITECTURE.md

2. WEBI_MIGRATION_TEST_SUITE.md

3. WEBI_SUPPORTED_FEATURES.md

4. WEBI_UNSUPPORTED_FEATURES.md

5. WEBI_MIGRATION_TROUBLESHOOTING.md

6. WEBI_TO_POWERBI_MAPPING.md

Document:

- Supported input formats
- WebI semantic concepts
- Universe mapping
- Data Provider mapping
- Query mapping
- Formula mapping
- DAX mapping
- Visual mapping
- Layout mapping
- Filter mapping
- Prompt mapping
- Known limitations
- Manual remediation

============================================================
41. IMPLEMENTATION STRATEGY
============================================================

Do NOT rewrite the entire project.

Do NOT rewrite tableau_pbi_server.py.

Do NOT duplicate large amounts of existing generic Power BI code.

Work incrementally.

Recommended sequence:

PHASE 0
Repository architecture analysis

PHASE 1
Create webi_pbi_server.py

PHASE 2
Create WebI input detection

PHASE 3
Create WebI parser architecture

PHASE 4
Create WebI Intermediate Representation

PHASE 5
Universe and semantic layer extraction

PHASE 6
Data Provider and Query extraction

PHASE 7
Datasource reconstruction

PHASE 8
Dimensions / Measures / Details

PHASE 9
Aggregation preservation

PHASE 10
Variable and formula dependency graph

PHASE 11
WebI Formula → DAX

PHASE 12
Filters and Prompts

PHASE 13
Blocks and sections

PHASE 14
Visualization classification

PHASE 15
Automatic field mapping

PHASE 16
Power BI semantic model generation

PHASE 17
Power BI visual generation

PHASE 18
Layout reconstruction

PHASE 19
Formatting

PHASE 20
Interactions

PHASE 21
Artifact validation

PHASE 22
Semantic validation

PHASE 23
Migration audit

PHASE 24
Quality scoring

PHASE 25
UI/API integration

PHASE 26
Regression testing

PHASE 27
Unseen WebI report validation

============================================================
42. INITIAL TASK — ANALYZE ONLY
============================================================

Your FIRST task is NOT to implement the WebI migration engine.

First inspect the complete repository.

Then produce a detailed report containing:

1. Existing project architecture.

2. Current Tableau → Power BI migration flow.

3. Responsibilities of tableau_pbi_server.py.

4. Tableau-specific components.

5. Generic reusable components.

6. Power BI generation components.

7. Existing validation mechanisms.

8. Existing progress mechanisms.

9. Existing UI/API architecture.

10. Exact extension points for WebI.

11. Recommended new files.

12. Proposed webi_pbi_server.py architecture.

13. Proposed WebI Intermediate Representation.

14. Proposed WebI → Power BI pipeline.

15. Which existing components can safely be reused.

16. Which existing components must remain Tableau-specific.

17. Which shared modules, if any, need refactoring.

18. How Tableau regression safety will be maintained.

19. WebI-specific risks.

20. Missing information that requires actual WebI samples.

21. Implementation roadmap.

DO NOT modify existing code during this initial analysis phase.

============================================================
43. AFTER INITIAL ANALYSIS
============================================================

Once the analysis is complete:

Implement the WebI migration engine incrementally.

Start by creating:

    webi_pbi_server.py

Then implement the WebI-specific architecture.

At each stage:

- Implement.
- Test.
- Validate.
- Run WebI tests.
- Run Tableau regression tests.
- Inspect output.
- Update audit.
- Update documentation.

Never proceed without checking regression impact.

============================================================
44. FINAL ARCHITECTURE
============================================================

The final architecture should conceptually be:

                        REPORT MIGRATION APPLICATION
                                  │
                  ┌───────────────┴───────────────┐
                  │                               │
                  ▼                               ▼
          TABLEAU MIGRATION                 WEBI MIGRATION
                  │                               │
                  ▼                               ▼
     tableau_pbi_server.py               webi_pbi_server.py
                  │                               │
                  ▼                               ▼
       Tableau Parser Layer                 WebI Parser Layer
                  │                               │
                  ▼                               ▼
       Tableau Semantic Model             WebI Semantic Model
                  │                               │
                  └───────────────┬───────────────┘
                                  │
                                  ▼
                     SHARED POWER BI SERVICES
                                  │
                     ┌────────────┼────────────┐
                     │            │            │
                     ▼            ▼            ▼
                  Model        Visual       Layout
                Generator      Generator    Generator
                     │            │            │
                     └────────────┼────────────┘
                                  │
                                  ▼
                         Power BI Artifact
                                  │
                                  ▼
                         Validation Layer
                                  │
                                  ▼
                       Migration Audit / Score

The critical architectural rule is:

SOURCE-SPECIFIC LOGIC MUST STAY SOURCE-SPECIFIC.

TABLEAU-SPECIFIC LOGIC
    →
tableau_pbi_server.py

WEBI-SPECIFIC LOGIC
    →
webi_pbi_server.py

SHARED GENERIC POWER BI LOGIC
    →
Reusable shared modules only when proven safe.

============================================================
45. FINAL SUCCESS CRITERIA
============================================================

The project is successful when:

1. Existing Tableau → Power BI migration continues to work.

2. tableau_pbi_server.py remains functional.

3. A new webi_pbi_server.py exists as the dedicated WebI migration engine.

4. Tableau and WebI migrations can run independently.

5. WebI reports are parsed generically.

6. WebI semantic structures are preserved.

7. Universe semantics are reconstructed where metadata is available.

8. Data Providers and Queries are reconstructed.

9. Tables and relationships are generated correctly.

10. Dimensions, Measures and Details are preserved.

11. Aggregations are preserved.

12. WebI formulas are converted to DAX where possible.

13. Filters are preserved.

14. Prompts are migrated or approximated appropriately.

15. Blocks are converted to appropriate Power BI visuals.

16. Visual field wells are populated automatically.

17. Report structure is reconstructed.

18. Layout is preserved as closely as technically possible.

19. Formatting is preserved where metadata allows.

20. Interactions are migrated or documented.

21. Generated Power BI artifacts are structurally valid.

22. No dangling references exist.

23. Migration audit is generated.

24. Quality score is generated.

25. Unsupported features are explicitly reported.

26. Regression testing protects Tableau functionality.

27. Regression testing protects WebI functionality.

28. At least one unseen WebI report is migrated successfully without report-specific code.

29. No WebI report-specific hardcoding exists.

30. The system can support future WebI reports without modifying logic specifically for each report.

The ultimate goal is to have TWO robust migration products inside the same application:

    Tableau → Power BI

and:

    SAP WebI → Power BI

They should share only genuinely generic infrastructure while keeping source-system-specific parsing and semantic interpretation completely separated.

The target is not simply to generate a Power BI file that opens.

The target is to preserve, as closely as technically possible:

    DATA MODEL
    +
    SEMANTIC LAYER
    +
    BUSINESS LOGIC
    +
    CALCULATIONS
    +
    AGGREGATIONS
    +
    FILTERS
    +
    PROMPTS
    +
    VISUAL ANALYTICS
    +
    REPORT STRUCTURE
    +
    LAYOUT
    +
    FORMATTING
    +
    INTERACTIONS

while maintaining a generic, scalable architecture capable of migrating arbitrary future SAP WebI reports and dashboards.