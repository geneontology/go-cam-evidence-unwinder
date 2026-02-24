# [Feature Name] Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** [One sentence describing what this builds]

**Architecture:** [2-3 sentences about the approach and how it fits into the existing codebase]

**Tech Stack:** [Key technologies/libraries involved]

**Affected Areas:** [Which parts of the system this touches - e.g., "parsing layer", "report output", "CLI args"]

---

## Context

[2-5 sentences of background. Why does this change exist? Link to the issue or conversation that motivated it. What does the reader need to understand about the problem domain before they can implement this?]

## Constraints

- [Hard constraint, e.g., "Must not break existing GPAD export format"]
- [Performance constraint, e.g., "Must handle 10k+ models without OOM"]
- [Compatibility constraint, e.g., "Python 3.9+ only"]

## Out of Scope

- [Thing that sounds related but we're explicitly not doing]
- [Future work that this plan intentionally defers]

---

## Tasks

### Task 1: [Component or Behavior Name]

**Files:**
- Create: `exact/path/to/new_file.py`
- Modify: `exact/path/to/existing_file.py:45-67`
- Test: `tests/exact/path/to/test_file.py`

**Step 1: Write the failing test**

```python
def test_specific_behavior():
    # Arrange
    input_data = ...

    # Act
    result = function_under_test(input_data)

    # Assert
    assert result == expected_output
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/path/test_file.py::test_specific_behavior -v`
Expected: FAIL with `NameError: name 'function_under_test' is not defined`

**Step 3: Write minimal implementation**

```python
def function_under_test(input_data):
    return expected_output
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/path/test_file.py::test_specific_behavior -v`
Expected: PASS

---

### Task 2: [Next Component or Behavior Name]

**Files:**
- Modify: `exact/path/to/file.py:100-120`
- Test: `tests/exact/path/to/test_file.py`

**Step 1: Write the failing test**

```python
def test_next_behavior():
    ...
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/path/test_file.py::test_next_behavior -v`
Expected: FAIL with `[specific error message]`

**Step 3: Write minimal implementation**

```python
# Show the exact code changes, not "add validation here"
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/path/test_file.py::test_next_behavior -v`
Expected: PASS

**Step 5: Run full test suite for regressions**

Run: `pytest -v`
Expected: All tests PASS

---

### Task N: [Integration / Wiring / CLI]

**Files:**
- Modify: `exact/path/to/main_entry.py:200-215`
- Test: `tests/exact/path/to/test_integration.py`

**Step 1: Write the failing integration test**

```python
def test_end_to_end():
    ...
```

[Continue TDD cycle steps...]

---

## Verification

After all tasks are complete, run these commands to confirm everything works:

```bash
# Full test suite
pytest -v

# Lint / type checks (if applicable)
[linting command]

# Manual smoke test
[exact command to run the tool and verify output]
```

**Expected final state:**
- [ ] All tests pass
- [ ] No regressions in existing functionality
- [ ] [Specific acceptance criterion from the goal]
- [ ] [Another acceptance criterion]

---

## Notes

- [Anything the implementer should watch out for]
- [Edge cases to keep in mind]
- [Related code or docs worth reading: `path/to/relevant/file.py:30-50`]