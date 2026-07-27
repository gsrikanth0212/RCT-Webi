CONTINUE EXISTING TABLEAU → POWER BI MIGRATION PROJECT

This is a continuation of the existing migration implementation.

DO NOT restart the project.

DO NOT rewrite the migration engine.

DO NOT undo or replace existing fixes.

DO NOT create workbook-specific fixes.

The objective is to fix two remaining generic classes of problems that are now appearing after the latest datasource/table/relationship improvements.

Before changing code, read and understand:

1. master_migration_prompt.md
2. MIGRATION_ENGINE_PROGRESS.md
3. tableau_pbi_server.py
4. report_migration_tool.py
5. The latest generated Power BI output
6. The latest migration logs/errors

Do not modify:

- Index.html
- Landing.html

Only modify the Python migration engine where necessary.

============================================================
CURRENT OBSERVATION
============================================================

The generated Power BI report now appears to identify the main tables correctly.

For example, the Data pane contains:

- Orders
- People
- Returns

This is an improvement and indicates that the datasource/table reconstruction is working better.

However, the generated visuals are still showing field errors.

Example screenshot:

X-axis:
    Region (People)   [warning icon]

Y-axis:
    Sum of Sales      [warning icon]

The visual displays:

"Something's wrong with one or more fields."

At the same time, the Power BI data model is also experiencing data-loading/refresh issues caused by datatype mismatches, incompatible relationship keys, or invalid source column typing.

Example:

Orders table
Postal Code column

Power BI may fail to load or refresh the query because the generated Power Query/M transformation or relationship model is assigning an incompatible datatype.

These two issues must be fixed separately but must work together.

============================================================
ISSUE 1 — GENERIC VISUAL FIELD / CATEGORY / AGGREGATION RESOLUTION
============================================================

The current migration engine is still generating visual field references that Power BI cannot correctly resolve.

Examples:

Region (People)

Sum of Sales

These are only examples.

The solution must work for ANY Tableau workbook and ANY field.

Do not hardcode:

- Region
- People
- Sales
- Orders
- Returns

============================================================
A. CATEGORY / DIMENSION FIELD RESOLUTION
============================================================

When Tableau uses a categorical/dimension field:

Example:

Region

from:

People

the migration engine must preserve:

Power BI table:
    People

Power BI column:
    Region

The visual must reference:

People[Region]

or the equivalent valid Power BI field reference.

If Tableau internally displays:

Region (People)

this is a semantic/disambiguation label.

It must NOT be treated as a physical Power BI column named:

Region (People)

unless that is genuinely the source column name.

The migration engine must resolve fields using the canonical mapping:

Tableau Datasource
    ↓
Tableau Logical Table
    ↓
Tableau Physical Table
    ↓
Tableau Physical Column
    ↓
Power BI Table
    ↓
Power BI Column

Use this mapping for:

- X-axis
- Y-axis
- Legend
- Category
- Details
- Tooltip
- Small multiples
- Filters
- Slicers
- Drillthrough
- Sorting
- Hierarchies

A field used as a category must resolve to the actual Power BI column.

============================================================
B. AGGREGATION RESOLUTION
============================================================

For a Tableau measure:

Sales

dragged to Rows:

Tableau semantics:

SUM(Sales)

The Power BI visual must reference:

Physical table:
    Orders

Physical column:
    Sales

Aggregation:
    SUM

It must NOT look for a physical column named:

Sum of Sales

Similarly:

AVG(Sales)

must resolve to:

Orders[Sales]
Aggregation = AVERAGE

COUNT(Sales)

must resolve to:

Orders[Sales]
Aggregation = COUNT

COUNTD(Customer ID)

must resolve to:

Orders[Customer ID]
Aggregation = DISTINCTCOUNT

The aggregation must remain separate from the physical field identity.

Do not create fake source columns such as:

Sum of Sales
Average of Sales
Count of Sales

unless these are actual source columns.

============================================================
C. VISUAL FIELD BINDING
============================================================

Audit the code that generates Power BI visual projections and field references.

For every Tableau visual field, determine:

- Physical table
- Physical column
- Tableau field name
- Tableau caption
- Dimension vs measure
- Aggregation
- Calculated field status
- Calculation expression
- Role
- Shelf
- Axis
- Legend/category role

The resulting Power BI visual field reference must point to the correct Power BI table and column.

For example:

Tableau:

Region (People)

must resolve to:

People[Region]

Tableau:

SUM(Sales)

must resolve to:

Orders[Sales] + SUM aggregation

The visual JSON must never reference a non-existent Power BI column.

============================================================
D. VALIDATE VISUAL REFERENCES BEFORE REPORT GENERATION
============================================================

Before generating the final Power BI report:

Validate every visual field.

For every visual field reference:

1. Does the Power BI table exist?

2. Does the Power BI column exist?

3. Is the field mapped to the correct physical table?

4. Is the field a dimension/category or measure?

5. Is the aggregation valid?

6. Does the aggregation match Tableau?

7. Does the visual role match Tableau?

8. Is the field reference using a semantic alias that does not exist physically?

If validation fails, resolve the field through the canonical mapping before generating the visual.

Do not generate a broken visual and expect Power BI Desktop to repair it.

============================================================
E. GENERIC DISAMBIGUATED FIELD NAMES
============================================================

Support Tableau field names such as:

Region (People)

Region (Customer)

Region (Orders)

Order ID (Returns)

Customer ID (Customers)

Date (Calendar)

etc.

Do not hardcode the parenthetical naming convention.

Instead, first use the Tableau metadata mapping.

If the Tableau field is associated with:

People[Region]

then the Power BI visual must reference:

People[Region]

If the same column name exists in multiple tables:

Orders[Region]

People[Region]

they must remain distinct.

============================================================
ISSUE 2 — GENERIC DATA LOADING / DATATYPE / KEY VALIDATION
============================================================

The generated tables and relationships may now be structurally correct, but Power BI can still fail during refresh.

Examples include:

- Numeric vs Text mismatch
- Date vs Text mismatch
- Integer vs Decimal mismatch
- Postal Code type mismatch
- Relationship key mismatch
- Null values
- Blank values
- Duplicate values on the "one" side
- Invalid relationship cardinality
- Invalid join keys
- Columns inferred incorrectly by Power Query
- Power Query conversion errors
- M query errors
- Data type conversion failures

The current example is:

Orders[Postal Code]

Do NOT implement a special fix only for Postal Code.

The migration engine must generically infer and preserve correct datatypes.

============================================================
A. SOURCE DATATYPE DETECTION
============================================================

For every physical column, determine:

- Tableau declared datatype
- Tableau role
- Tableau semantic type
- Actual source datatype
- Power Query datatype
- Power BI model datatype

Create a canonical datatype mapping.

Examples:

Tableau Integer
    → Power Query Int64.Type
    → Power BI Whole Number

Tableau Number/Decimal
    → Power Query type number
    → Power BI Decimal Number

Tableau String
    → Power Query type text
    → Power BI Text

Tableau Date
    → Power Query type date
    → Power BI Date

Tableau DateTime
    → Power Query type datetime
    → Power BI Date/Time

Boolean
    → Power Query logical
    → Power BI True/False

Do not infer datatype solely from the column name.

For example:

Postal Code

must NOT automatically be converted to a number merely because it contains digits.

Postal codes are often identifiers and may contain:

- Leading zeros
- Alphanumeric values
- Blank values
- Mixed values

If Tableau/source metadata identifies Postal Code as a string, preserve it as text.

The same generic rule applies to:

- ZIP codes
- Postal codes
- Account IDs
- Customer IDs
- Product IDs
- Order IDs
- Employee IDs
- Phone numbers
- SSNs or other identifiers
- Any key-like fields

Do not assume identifiers are numeric.

============================================================
B. POWER QUERY TYPE CONVERSION
============================================================

Audit all generated Power Query/M code.

Identify every location where the engine uses:

Table.TransformColumnTypes

Table.AddColumn

Table.Combine

Table.Join

Table.NestedJoin

Table.ExpandTableColumn

Table.ExpandRecordColumn

Table.SelectColumns

Table.RenameColumns

or equivalent transformations.

Ensure type conversions occur safely.

Do not blindly force all columns to numeric or text.

If conversion can fail because of dirty source values:

- Preserve source-compatible datatype where possible.
- Use safe conversion where appropriate.
- Handle nulls.
- Handle blanks.
- Handle malformed values.
- Do not silently corrupt valid values.

For example, do not convert:

"00123"

to:

123

if the field is an identifier.

============================================================
C. RELATIONSHIP KEY COMPATIBILITY
============================================================

Before generating a Power BI relationship:

TableA[KeyA]

↓

TableB[KeyB]

validate:

1. Both columns exist.

2. Both columns have compatible datatypes.

3. Both columns use compatible semantic meaning.

4. Null handling is compatible.

5. The "one" side is unique where required.

6. Cardinality is valid.

If Tableau metadata indicates:

Orders[Postal Code]

and:

People[Postal Code]

are related,

the migration engine must ensure both keys use compatible datatypes.

For example:

Text ↔ Text

not:

Whole Number ↔ Text

Do not automatically change one side merely to force the relationship.

Use Tableau metadata and source profiling to determine the correct common datatype.

============================================================
D. RELATIONSHIP CARDINALITY VALIDATION
============================================================

If source data is available:

Profile relationship keys.

For each relationship:

- Count distinct values on source side.
- Count distinct values on target side.
- Detect duplicates.
- Detect nulls.
- Detect orphan keys.
- Determine whether one-to-one, one-to-many, many-to-one, or many-to-many is valid.

Do not blindly force:

Many-to-One

if the "one" side contains duplicates.

Do not generate an invalid Power BI relationship.

If Tableau relationship semantics and actual data cardinality differ, preserve Tableau semantics where Power BI permits it, but report the conflict.

============================================================
E. JOIN KEY VALIDATION
============================================================

For physical joins:

Validate:

- Source table exists.
- Target table exists.
- Source column exists.
- Target column exists.
- Datatypes are compatible.
- Join keys are valid.

If incompatible:

Do not silently generate a broken Power Query join.

Generate a clear migration diagnostic.

============================================================
F. DATA LOAD VALIDATION BEFORE VISUAL GENERATION
============================================================

The migration process should follow:

1. Extract Tableau workbook.

2. Extract datasource metadata.

3. Extract physical tables.

4. Extract physical columns.

5. Determine datatypes.

6. Generate Power Query/M.

7. Validate Power Query/M.

8. Validate data loading.

9. Validate table schemas.

10. Validate relationship keys.

11. Generate Power BI semantic model.

12. Validate relationships.

13. Resolve visual fields.

14. Resolve aggregations.

15. Generate visuals.

16. Validate visual field references.

Do not generate visuals against a semantic model that has unresolved data-loading errors.

============================================================
G. GENERIC DATA QUALITY DIAGNOSTICS
============================================================

For each generated table, produce diagnostics for:

- Type conversion failures
- Null key counts
- Blank key counts
- Duplicate key counts
- Orphan relationship keys
- Incompatible relationship datatypes
- Missing columns
- Missing tables
- Failed joins
- Power Query errors

These diagnostics should be available in migration logs.

Do not hide data issues.

============================================================
IMPORTANT ARCHITECTURAL RULE
============================================================

Separate these concerns:

1. Physical source data

2. Power Query transformation

3. Power BI table schema

4. Relationship model

5. Semantic field mapping

6. Visual binding

7. Aggregation

A visual field error must not be fixed by changing the underlying physical datatype.

A relationship error must not be fixed by renaming visual fields.

A Power Query error must not be fixed by deleting columns required by the semantic model.

Each issue must be fixed at the correct architectural layer.

============================================================
REGRESSION TESTS
============================================================

Create or extend generic regression tests for:

1. Numeric measure with SUM aggregation.

2. Numeric measure with AVG aggregation.

3. Numeric measure with COUNT aggregation.

4. Dimension field from a related table.

5. Same field name in multiple tables.

6. Tableau-disambiguated field names.

7. Text identifier containing numeric-looking values.

8. Postal Code with leading zeros.

9. Postal Code containing text.

10. Numeric relationship key.

11. Text relationship key.

12. Same logical relationship where both sides have compatible text types.

13. Relationship where one side has duplicate keys.

14. Relationship with null keys.

15. Physical join with mismatched datatypes.

16. Multiple related tables.

17. Hyper datasource.

18. Excel datasource.

19. External datasource.

20. TWB without embedded data.

============================================================
CURRENT SCREENSHOT REGRESSION
============================================================

Use the current generated report as a regression test.

The current visual has:

X-axis:
    Region (People)

Y-axis:
    Sum of Sales

Both show warning icons.

The expected result is:

X-axis:
    People[Region]

Y-axis:
    Orders[Sales]
    Aggregation = SUM

The generated visual must load successfully.

Do not hardcode these names.

This is only a regression example.

============================================================
CURRENT DATA LOAD REGRESSION
============================================================

Use the current Orders table and Postal Code issue as a regression test.

However, the fix must work for all fields and all workbooks.

Validate:

- Correct datatype
- Safe Power Query conversion
- Correct relationship compatibility
- Correct Power BI model datatype
- Successful refresh

============================================================
SUCCESS CRITERIA
============================================================

This increment is complete only when:

✓ All visual category/dimension fields resolve to the correct Power BI table and column.

✓ All aggregations are preserved separately from physical column names.

✓ SUM(Sales) resolves to Sales + SUM aggregation.

✓ Tableau-disambiguated field names resolve to the correct physical table.

✓ Visuals no longer contain broken field references.

✓ Power BI visuals load without warning icons caused by invalid field references.

✓ All generated Power Query queries refresh successfully where source data is available.

✓ Datatypes are inferred from Tableau metadata and source data, not column names alone.

✓ Identifier fields such as Postal Code are not incorrectly converted to numeric types.

✓ Relationship keys have compatible datatypes.

✓ Relationship cardinality is validated.

✓ Duplicate keys are detected before generating invalid relationships.

✓ Join keys are validated before Power Query joins are generated.

✓ Null and blank key behavior is handled.

✓ Power Query type conversions do not corrupt valid source data.

✓ Data-loading errors are reported clearly.

✓ Existing tables and relationships continue to work.

✓ Existing visual migration improvements continue to work.

✓ No workbook-specific hardcoding is introduced.

✓ No field-specific hardcoding is introduced.

✓ The solution works for arbitrary Tableau workbooks and dashboards.

============================================================
FINAL EXECUTION
============================================================

Before coding:

1. Identify the exact root cause of the visual field warnings.

2. Identify the exact root cause of the data loading/type errors.

3. Determine whether either issue is caused by the recent relationship/table changes.

4. Reuse existing canonical field mappings.

5. Reuse existing datatype and relationship logic.

6. Fix the smallest correct architectural layer.

Then:

1. Run py_compile.

2. Run existing regression tests.

3. Regenerate the affected workbook.

4. Verify all Power Query queries load.

5. Refresh Power BI.

6. Verify Orders, People and Returns.

7. Verify relationships.

8. Verify visual category fields.

9. Verify aggregations.

10. Verify no warning icons remain.

11. Test at least one additional unrelated Tableau workbook.

12. Update MIGRATION_ENGINE_PROGRESS.md.

Do not move to additional visual enhancements until:

A. Data loading succeeds.

B. Semantic field resolution succeeds.

C. Aggregation resolution succeeds.

D. Relationship validation succeeds.

E. Generated visuals load successfully.

The final migration architecture must be:

Tableau Workbook
    ↓
Datasource
    ↓
Physical Tables
    ↓
Physical Columns + Datatypes
    ↓
Power Query / Data Load
    ↓
Power BI Tables
    ↓
Relationships / Joins
    ↓
Canonical Field Mapping
    ↓
Calculated Fields / Measures
    ↓
Aggregation Semantics
    ↓
Visual Field Binding
    ↓
Power BI Visuals

All layers must be validated before the final Power BI report is generated.