---
name: split-large-python-module
description: Refactor oversized Python modules into smaller packages while preserving public imports. Use when a file is too long, has deeply nested control flow, mixes unrelated responsibilities, or needs a file-to-package migration such as replacing pkg/module.py with pkg/module/__init__.py plus internal modules and compatibility re-exports.
---

# Split Large Python Module

## Goal

Split a large Python module by responsibility without changing runtime behavior, CLI behavior, docs semantics, or the existing public import path.

The preferred project pattern is the `a75d8ea` conductor runtime refactor: delete `src/perago/conductor_runtime.py`, create `src/perago/conductor_runtime/`, put compatibility re-exports in `__init__.py`, and move implementation into focused internal modules.

## Workflow

1. Establish the baseline before editing.
   - Use CodeGraph first for structure: explore the target file, public symbols, callers, callees, and likely impact.
   - Run targeted tests that cover the module before the split.
   - Record the public import surface that must remain stable.

2. Choose a package shape.
   - Prefer a same-name package when the old module path is already public: `pkg/foo.py` -> `pkg/foo/__init__.py`.
   - Do not keep both `pkg/foo.py` and `pkg/foo/`; Python import resolution becomes ambiguous and future edits become error-prone.
   - Avoid inventing a new top-level namespace when the same-name package preserves the mental model better or avoids dependency-name confusion.

3. Split by responsibility, not by line count alone.
   - Put data models, protocols, and field parsing in `models.py`.
   - Put external SDK or service adapters in `client.py`.
   - Put IPC/message dataclasses and queue helpers in `process_ipc.py` or equivalent.
   - Put task execution and result mapping in `execution.py`.
   - Put worker adapter classes in `workers.py`.
   - Put runner lifecycle, signal handling, and stop coordination in `runners.py`.
   - Put process-loop specific shutdown and assignment handling in `process_executor.py`.
   - Extract small helpers where nesting hides behavior, especially for validation, completion waits, signal restore, and control-message handling.

4. Preserve compatibility explicitly.
   - Make `__init__.py` import and re-export every old public symbol.
   - Define `__all__` with the compatibility surface.
   - Update internal callers to import from concrete new modules when that clarifies ownership.
   - Leave external-facing or compatibility tests importing from the old module path.
   - If tests monkeypatch old module attributes, either keep those attributes re-exported or move monkeypatches to the new concrete owner deliberately.

5. Split tests along the new module boundaries.
   - Move large monolithic tests into targeted files named after the new modules.
   - Add a small compatibility test that imports every old public symbol from the old path.
   - Add shared test helpers only when they remove repeated setup across split test files.
   - Keep behavioral assertions the same; a refactor should not weaken coverage.

6. Verify in layers.
   - Run the old targeted baseline before the split when possible.
   - Run the new targeted test set after each meaningful slice.
   - Run `rtk uv run pytest -q` before finishing in this repo.
   - Inspect `git diff --stat` and `git diff --check`; the expected diff should show code movement plus focused helper extraction, not semantic drift.

## Review Checklist

- The old public import path still works.
- `__init__.py` is a compatibility shim, not a dumping ground for business logic.
- Internal imports point to concrete owner modules where useful.
- No same-name `.py` file and package directory coexist.
- Signal handlers, process shutdown, queue waits, and other lifecycle code still restore or clean up on all exits.
- Error messages, result mapping, task registration behavior, and CLI contracts are unchanged.
- Tests include both module-level behavior and import compatibility.
