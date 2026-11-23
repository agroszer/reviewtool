"""Tests for unified diff parsing, word diff spans, expansion, and partial patches."""

from reviewtool.diff import (
    apply_reverse_hunk_to_lines,
    build_partial_patch,
    compute_expand_dividers,
    expand_divider_in_diff,
    extract_word_at_column,
    parse_unified_diff,
)

SAMPLE_DIFF = """\
diff --git a/calc.py b/calc.py
index 1111111..2222222 100644
--- a/calc.py
+++ b/calc.py
@@ -10,5 +10,6 @@ def compute():
     a = 1
-    b = a + 2
+    b = a + 20
+    c = b * 3
     return b
@@ -30,3 +31,3 @@ def footer():
     x = 10
-    return x
+    return x + 1
"""


def test_parse_unified_diff_and_word_spans() -> None:
    fdiff = parse_unified_diff(SAMPLE_DIFF, "calc.py")
    assert fdiff.path == "calc.py"
    assert len(fdiff.hunks) == 2

    h0 = fdiff.hunks[0]
    assert h0.old_start == 10
    assert h0.new_start == 10
    assert len(h0.lines) == 5
    assert h0.lines[1].kind == "del"
    assert h0.lines[2].kind == "add"

    h1 = fdiff.hunks[1]
    assert h1.lines[1].kind == "del"
    assert h1.lines[2].kind == "add"
    assert len(h1.lines[2].word_spans) > 0


def test_expand_dividers_and_gap_expansion() -> None:
    fdiff = parse_unified_diff(SAMPLE_DIFF, "calc.py")
    fdiff.total_new_lines = 40
    dividers = compute_expand_dividers(fdiff)
    assert len(dividers) == 3
    assert dividers[0].before_hunk_index == 0
    assert dividers[0].new_start == 1
    assert dividers[0].new_end == 9
    assert dividers[1].before_hunk_index == 1
    assert dividers[1].new_start == 16
    assert dividers[1].new_end == 30

    working_lines = [f"line_{i}" for i in range(1, 41)]
    expand_divider_in_diff(fdiff, dividers[1], working_lines)
    assert len(fdiff.hunks) == 1


def test_build_partial_patch_forward_and_reverse() -> None:
    fdiff = parse_unified_diff(SAMPLE_DIFF, "calc.py")
    patch_only_del = build_partial_patch(
        fdiff, hunk_index=0, selected_line_indices={1}, reverse=False
    )
    assert "-    b = a + 2\n" in patch_only_del
    assert "+    b = a + 20" not in patch_only_del
    assert "@@ -10,3 +10,2 @@" in patch_only_del

    patch_rev_add = build_partial_patch(
        fdiff, hunk_index=0, selected_line_indices={3}, reverse=True
    )
    assert "+    c = b * 3\n" in patch_rev_add
    assert "     b = a + 20\n" in patch_rev_add


def test_apply_reverse_hunk_to_lines() -> None:
    fdiff = parse_unified_diff(SAMPLE_DIFF, "calc.py")
    working = ["    x = 10", "    return x + 1"]
    hunk = fdiff.hunks[1]
    hunk.new_start = 1
    hunk.new_count = 2
    reverted = apply_reverse_hunk_to_lines(working, hunk)
    assert reverted == ["    x = 10", "    return x"]


def test_extract_word_at_column() -> None:
    line = "    result_val = compute_total(foo_bar, 42)"
    assert extract_word_at_column(line, 6) == "result_val"
    assert extract_word_at_column(line, 20) == "compute_total"
    assert extract_word_at_column(line, 34) == "foo_bar"
