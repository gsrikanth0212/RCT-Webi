#!/usr/bin/env python3
"""
Quick test to verify the fuzzy worksheet name matching works correctly.
"""

def _find_ws_by_fuzzy_name(search_name, ws_dict):
    """Find a worksheet by fuzzy name matching."""
    if not search_name:
        return None
    # Exact match
    if search_name in ws_dict:
        return ws_dict[search_name]
    # Case-insensitive match
    search_lower = search_name.lower()
    for ws_name in ws_dict:
        if ws_name.lower() == search_lower:
            return ws_dict[ws_name]
    # Normalized match (strip whitespace, compare)
    search_norm = ' '.join(search_name.split()).lower()
    for ws_name in ws_dict:
        ws_norm = ' '.join(ws_name.split()).lower()
        if ws_norm == search_norm:
            return ws_dict[ws_name]
    # Partial match (search_name is substring of ws_name)
    for ws_name in ws_dict:
        if search_lower in ws_name.lower() or ws_name.lower() in search_lower:
            return ws_dict[ws_name]
    return None


# Test cases
test_worksheets = {
    'Dashboard': {'name': 'Dashboard', 'type': 'worksheet'},
    'Finance Dashboard': {'name': 'Finance Dashboard', 'type': 'worksheet'},
    'Revenue vs Profit': {'name': 'Revenue vs Profit', 'type': 'worksheet'},
    'Net working capital': {'name': 'Net working capital', 'type': 'worksheet'},
}

test_cases = [
    ('Dashboard', True, 'Exact match'),
    ('dashboard', True, 'Case-insensitive'),
    ('DASHBOARD', True, 'All caps'),
    ('Finance Dashboard', True, 'Exact multi-word'),
    ('finance dashboard', True, 'Case-insensitive multi-word'),
    ('Finance  Dashboard', True, 'Extra whitespace'),
    ('Revenue vs Profit', True, 'Exact with symbol'),
    ('revenue vs profit', True, 'Case-insensitive with symbol'),
    ('Net working capital', True, 'Exact long name'),
    ('net working capital', True, 'Case-insensitive long name'),
    ('NonExistent', False, 'Should not find'),
    ('Finance', True, 'Partial match (substring)'),
    ('Net', True, 'Partial match (prefix)'),
]

print("Testing Fuzzy Worksheet Name Matching\n" + "=" * 50)
passed = 0
failed = 0

for search_term, should_find, description in test_cases:
    result = _find_ws_by_fuzzy_name(search_term, test_worksheets)
    found = result is not None
    
    if found == should_find:
        status = "✓ PASS"
        passed += 1
    else:
        status = "✗ FAIL"
        failed += 1
    
    print(f"{status} | Search: '{search_term}' -> {description}")
    if result:
        print(f"     Found: '{result['name']}'")

print("\n" + "=" * 50)
print(f"Results: {passed} passed, {failed} failed")
print("\n✅ All tests passed!" if failed == 0 else f"\n❌ {failed} test(s) failed!")
