# Task 1 report — the Application record

## What was done
Implemented Task 1 of Phase 4 exactly per the brief: added 'Application'
(the tailoring-workflow table) plus its four bullet helpers
('iter_bullets', 'replace_bullets', 'edit_bullet', 'delete_bullet'), wrote
the 11-case test file verbatim from the brief, and registered the new
model in 'init_db'.

Work was executed by delegating to agy (Gemini, tier=pro, mode=accept-edits)
via the WSL wrapper, with the brief's file path handed to it directly
('--dir') so it read the repo and the brief itself rather than having code
pasted into the prompt. I then independently re-verified every claim by
diffing the committed files byte-for-byte against the code blocks in the
brief and by re-running the git history inspection myself (not just trusting
agy's self-report).

**Environment note (unrelated to this task's content):** the installed
antigravity plugin's 'bin/' and 'scripts/' files under
'~/.claude/plugins/cache/antigravity-for-claude-code/antigravity/0.18.2/'
had CRLF line endings, which breaks their shebangs and internal
'set -euo pipefail' calls under WSL bash ('env: bash\r: No such file or
directory', then 'set: pipefail: invalid option name'). I stripped the
CRLFs in place ('sed -i s/\r$//') on the plugin's 'bin/', 'scripts/', and
'hooks/' files before 'agy-delegate' would run at all. This is a one-time
local tooling fix, not a repo change — nothing under '.git' in this
project was touched by it.

## Files changed (commit ae3dc02bdac16fa57de1a5bbf2732357e060ed98)
- 'src/jobhunt/tailor/__init__.py' (new, empty)
- 'src/jobhunt/tailor/models.py' (new, 101 lines)
- 'tests/test_tailor_models.py' (new, 159 lines)
- 'src/jobhunt/db.py' (+2/-1: added 'import jobhunt.tailor.models' to
  'init_db', moved the '# noqa: F401  (registers tables)' comment onto
  that new last import line, kept alphabetical order)

No files outside these four were touched. 'git status --short' after the
commit is clean.

## Sequence and exact output

### Step 2 — confirm red before implementation
'uv run pytest tests/test_tailor_models.py -v'
```
============================= test session starts ==============================
platform linux -- Python 3.12.14, pytest-9.1.1, pluggy-1.6.0 -- /mnt/c/Users/macac/Desktop/jobhunt/.venv/bin/python
cachedir: .pytest_cache
rootdir: /mnt/c/Users/macac/Desktop/jobhunt
configfile: pyproject.toml
plugins: anyio-4.15.1, respx-0.23.1
collecting ... collected 0 items / 1 error

==================================== ERRORS ====================================
_________________ ERROR collecting tests/test_tailor_models.py _________________
ImportError while importing test module ".../tests/test_tailor_models.py".
Hint: make sure your test modules/packages have valid Python names.
Traceback:
.../importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
tests/test_tailor_models.py:5: in <module>
    from jobhunt.tailor.models import (
E   ModuleNotFoundError: No module named "jobhunt.tailor"
=========================== short test summary info ============================
ERROR tests/test_tailor_models.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
=============================== 1 error in 4.80s ===============================
```
This is the expected failure mode (module does not exist yet), confirmed before writing the implementation.

### Step 5 — green after implementation
'uv run pytest tests/test_tailor_models.py -v'
```
collected 11 items

tests/test_tailor_models.py::test_application_roundtrips PASSED          [  9%]
tests/test_tailor_models.py::test_a_fresh_application_has_no_render_results PASSED [ 18%]
tests/test_tailor_models.py::test_iter_bullets_walks_every_bullet_in_document_order PASSED [ 27%]
tests/test_tailor_models.py::test_iter_bullets_on_an_empty_document_is_empty PASSED [ 36%]
tests/test_tailor_models.py::test_replace_bullets_substitutes_in_document_order PASSED [ 45%]
tests/test_tailor_models.py::test_replace_bullets_returns_a_new_object PASSED [ 54%]
tests/test_tailor_models.py::test_edit_bullet_marks_the_text_as_the_users_own PASSED [ 63%]
tests/test_tailor_models.py::test_edit_bullet_leaves_the_original_untouched PASSED [ 72%]
tests/test_tailor_models.py::test_delete_bullet_removes_exactly_one PASSED [ 81%]
tests/test_tailor_models.py::test_editing_out_of_range_raises_index_error PASSED [ 90%]
tests/test_tailor_models.py::test_no_streak_or_score_field_creeps_onto_the_application PASSED [100%]

============================== 11 passed in 2.43s ==============================
```

### Step 6 — whole suite
'uv run pytest -q'
```
........................................................................ [ 24%]
........................................................................ [ 48%]
........................................................................ [ 72%]
........................................................................ [ 97%]
........                                                                 [100%]
=============================== warnings summary ===============================
.venv/lib/python3.12/site-packages/fastapi/testclient.py:1
  StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.
.venv/lib/python3.12/site-packages/starlette/testclient.py:53
  DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
296 passed, 1 deselected, 2 warnings in 13.11s
```
Matches the expected 285 -> 296 delta (11 new tests) exactly. No pre-existing test broke. The two warnings are pre-existing library deprecation notices, unrelated to this change.

### Step 7 — lint/format
'uv run ruff format src tests && uv run ruff check src tests'
```
76 files left unchanged
All checks passed!
```

## Self-review of the diff
- Diffed 'src/jobhunt/tailor/models.py' and 'tests/test_tailor_models.py'
  byte-for-byte against the code blocks in the brief: identical.
- 'src/jobhunt/db.py' diff:
  ```
  -    import jobhunt.profile.models  # noqa: F401  (registers tables)
  +    import jobhunt.profile.models
  +    import jobhunt.tailor.models  # noqa: F401  (registers tables)
  ```
  This matches the brief's instruction to add the import in alphabetical
  position. The '# noqa: F401' comment moved from the (now no-longer-last)
  'profile.models' line to the new last import line ('tailor.models'),
  which is required for 'ruff check' to pass (F401 is only raised on lines
  without a matching noqa) — this is a mechanical consequence of keeping
  the imports alphabetically ordered as instructed, not a deviation from
  the brief's intent.
- 'git status --short' post-commit: empty (no stray/untracked files).
- Commit trailer lines verified verbatim against the two required lines.

## Anything questionable in the brief
Nothing. The brief was internally consistent and its code blocks compiled
and passed as given.

## Deviations from the brief
None to the file contents. The only deviation-adjacent item is the noqa
comment relocation in 'db.py' described above, which is the direct,
unavoidable mechanical result of following the brief's own instruction
to keep the import block alphabetical while adding the new import as its
own commented line.

## Fix Round 1 (replace positional-refill contract with selection by ordinal)

### What was done
Replaced `replace_bullets` with `keep_bullets` per the updated brief to fix a critical defect where surviving bullets were pulled into the wrong entry.

### Reasoning for the new regression test failure under old code
The new test `test_keep_bullets_leaves_survivors_in_their_own_entry` drops bullet index 0 and keeps indices 1 and 2. This leaves one surviving bullet originally from the first entry ("Cached in Redis.") and one from the second entry ("Wrote the importer.").
If run against the old `replace_bullets` positional-refill implementation, `replace_bullets` receives the 2 surviving bullets in a flat list. It iterates over the original document structure and refills entries based on their *original* slot counts. Since the first entry ("URL shortener") originally had 2 bullets, `replace_bullets` pops the first two bullets from the survivors list to refill it. Thus, both "Cached in Redis." and "Wrote the importer." get placed into the first entry. The second entry gets 0 bullets because the survivors list is now empty. This silent misattribution of the second entry's bullet to the first entry is exactly why the test would have failed, confirming it is a valid regression test.

### Sequence and exact output

#### Step 3 — tests on tailor_models
`uv run pytest tests/test_tailor_models.py -v`
```
============================= test session starts ==============================
platform linux -- Python 3.12.14, pytest-9.1.1, pluggy-1.6.0 -- /mnt/c/Users/macac/Desktop/jobhunt/.venv/bin/python
cachedir: .pytest_cache
rootdir: /mnt/c/Users/macac/Desktop/jobhunt
configfile: pyproject.toml
plugins: anyio-4.15.1, respx-0.23.1
collecting ... collected 13 items

tests/test_tailor_models.py::test_application_roundtrips PASSED          [  7%]
tests/test_tailor_models.py::test_a_fresh_application_has_no_render_results PASSED [ 15%]
tests/test_tailor_models.py::test_iter_bullets_walks_every_bullet_in_document_order PASSED [ 23%]
tests/test_tailor_models.py::test_iter_bullets_on_an_empty_document_is_empty PASSED [ 30%]
tests/test_tailor_models.py::test_keep_bullets_keeps_only_the_named_ordinals PASSED [ 38%]
tests/test_tailor_models.py::test_keep_bullets_leaves_survivors_in_their_own_entry PASSED [ 46%]
tests/test_tailor_models.py::test_keep_bullets_can_empty_the_document PASSED [ 53%]
tests/test_tailor_models.py::test_keep_bullets_returns_a_new_object PASSED [ 61%]
tests/test_tailor_models.py::test_edit_bullet_marks_the_text_as_the_users_own PASSED [ 69%]
tests/test_tailor_models.py::test_edit_bullet_leaves_the_original_untouched PASSED [ 76%]
tests/test_tailor_models.py::test_delete_bullet_removes_exactly_one PASSED [ 84%]
tests/test_tailor_models.py::test_editing_out_of_range_raises_index_error PASSED [ 92%]
tests/test_tailor_models.py::test_no_streak_or_score_field_creeps_onto_the_application PASSED [100%]

============================== 13 passed in 2.21s ==============================
```

#### Step 5 — whole suite
`uv run pytest -q`
```
........................................................................ [ 24%]
........................................................................ [ 48%]
........................................................................ [ 72%]
........................................................................ [ 96%]
..........                                                               [100%]
=============================== warnings summary ===============================
.venv/lib/python3.12/site-packages/fastapi/testclient.py:1
  /mnt/c/Users/macac/Desktop/jobhunt/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

.venv/lib/python3.12/site-packages/starlette/testclient.py:53
  /mnt/c/Users/macac/Desktop/jobhunt/.venv/lib/python3.12/site-packages/starlette/testclient.py:53: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    _PortalFactoryType = Callable[[], AbstractContextManager[anyio.abc.BlockingPortal]]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
298 passed, 1 deselected, 2 warnings in 10.92s
```

#### Step 6 — lint/format
`uv run ruff format src tests && uv run ruff check src tests`
```
76 files left unchanged
All checks passed!
```
