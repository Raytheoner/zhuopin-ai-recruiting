# U2 deferred minors 与 final review 缺口（抢救副本）

## 关于这份文件

- **搬运日期**：2026-08-28（由 opener `[Mac]0828A-账目对齐` 执行）
- **来源路径**：`.claude/worktrees/audit-module-u2/.superpowers/sdd/2026-08-26-ai-audit-trail-unitU2-audit-module/progress.md`
- **为什么要搬**：原件在 worktree 内部、**git-ignored**，不进版本库。谁哪天清一次 worktree
  它就永久消失，而 U2 的**全分支 final review 至今没跑**（2026-08-27 那轮预算耗尽断在这里，
  见 `.claude/handoff/lanes-20260827-160205-看护报告.md` §二）——这份清单是那次 review
  唯一的输入材料。
- **正文以下为原件逐字复制**，未作任何改写。

## ⚠️ 一处计数不符：标题写 9，真值是 11

原件小节标题写作 `Deferred minors awaiting triage (9)`，**该数字是错的**。编号列表只列了 9 条，
但 Task 6 的两条 minor 在正文（原件 Task 6 段落）里已计数、只在括注里被追加进列表、没有编号：

10. T6 `backfill()` 不检查 `missing_id` 是否真的缺失就写入；调用两次会产生重复的 `backfill` 事件，无守卫。
11. T6 `test_reconcile_is_clean_when_both_sides_match` 没有单独断言 `missing_in_store == frozenset()`。

**分诊时以 11 条为准。** 原文照搬未改，此处只作标注。

---

（以下为原件逐字复制）

# SDD ledger — plan: docs/superpowers/plans/2026-08-26-ai-audit-trail-unitU2-audit-module.md

Worktree: /Users/paulshao/Projects/HumanResource/.claude/worktrees/audit-module-u2
Branch: worktree-audit-module-u2
Merge base: 42fd90fda7438d00f66a085fc0f95058698df73a
Baseline suite before Task 1: 356 passed
Expected after U2: baseline + 90 = 446

Headless run (run-lanes.sh, no human present). Ambiguity resolved conservatively
and registered here; no blocking questions dispatched.

## Pre-flight conflict scan (before Task 1)

Scanned plan for internal contradictions and rubric-vs-plan conflicts. Result: clean.
The plan's own "偏离登记" section registers three deviations from tasks.md /
delivery-units.md. All three are stricter-or-neutral and fall in the 可代 bucket
(plan technical review), not a Shao Peishen decision point. Ruling: proceed, and
require each task reviewer to explicitly confirm the deviation touching its task.

- D1 `AuditRecorder` two-phase `record()` / `mirror()`, no packed method.
  Mandated verbatim by the opener (OP-0826-E §三 first constraint). Governs over
  tasks 2.8's literal single-`record()` reading; 2.8 is a *sequence* requirement.
- D2 `rubric_version` folded into `rubric_snapshot` as `{"version":…,"snapshot":…}`.
  U1's table has no `rubric_version` column and U2 must not touch `db.py`.
- D3 `AuditSink.write` returns `bool`; `SqliteSink` returns `False` for non-`ai_analysis`
  events. Needed so the primary-key short-circuit (tasks 2.2) is observable.

## Task log

Task 1: complete (commits 42fd90f..c1ff33c, review clean) — spec ✅, quality Approved,
  no Critical/Important. Suite 356 → 378 (+22, exact match to plan).
Task 1: minor (deferred): `to_dict()` returns dict-valued fields (`rubric_snapshot`,
  `token_usage`, `evidence`) by reference, not deep-copied — a caller mutating the
  returned dict mutates the frozen event's stored dict. No consumers yet; carried as
  a pointer into the Task 2/3 sink dispatches.
Task 1: minor (deferred): `test_all_registered_event_types_construct` builds every
  event type through the AI-analysis-shaped fixture, so it proves construction only,
  not per-type field shape. Brief did not require per-type shape validation.

Task 2: implemented (commits c1ff33c..adacaf2). Suite 378 → 402 (+24, exact match).
  Review: spec ✅, quality Approved, 1 Important + 3 Minor.
Task 2: fix round 1/5 dispatched — Important: `test_empty_evidence_ref_is_not_swallowed`
  is tautological. The narrowing `try/except` wraps only the `analysis_run` INSERT; the
  `criterion_score` loop sits outside it, so a blank `evidence_ref` propagates regardless
  of whether the predicate is precise or widened to a bare
  `except sqlite3.IntegrityError: return False`. The guard cannot catch the exact
  mutation 铁律 4 names.
Task 2: ruling on the plan-mandated tension — the brief's Step 3 code block produced this
  shape, so restructuring deviates from plan-mandated code. Ruled: bring the
  `criterion_score` loop inside the same narrowing `try`. It makes the predicate genuinely
  load-bearing for both statements, is strictly stricter, and changes no observable
  behavior (a `criterion_score` PK conflict still re-raises — the predicate matches on
  `analysis_run.id`). The plan's stated intent ("CHECK constraint failed 必须原样抛出去"
  as a property of the narrowing logic) governs over the statement nesting. Fix must be
  proven by mutation test: widen the except, confirm the guard goes red, revert.
Task 2: minor (deferred): `_is_analysis_run_pk_conflict` substring-matches SQLite's
  exception message text — latent fragility if SQLite's wording changes across versions.
Task 2: minor (deferred): `score.id or f"{event.id}:{score.criterion_key}"` treats an
  explicitly-set empty-string `score.id` as unset, silently overriding caller intent.
Task 2: fix round 1/5 (2 addressed, 0 open — tautological CHECK guard; unused
  `CriterionScore` import; commits adacaf2..f6eb9b2). Mutation evidence in the report is
  real: widened except → 3 failed, `DID NOT RAISE sqlite3.IntegrityError`; reverted →
  24 passed / 402 passed. Re-reviewer separately confirmed a `criterion_score` PK conflict
  still re-raises (predicate matches `analysis_run.id`, not `criterion_score.id`) and that
  the `except` predicate text is byte-identical to base — statements relocated, not widened.
Task 2: complete (commits c1ff33c..f6eb9b2, review clean)

Task 3: complete (commits f6eb9b2..fc88f9c, review clean) — spec ✅, quality Approved,
  no Critical/Important. Suite 402 → 411 (+9, exact match).
  Reviewer independently reproduced the concurrency check with its own harness: real lock
  3/3 pass, `_lock_for` monkeypatched to no-op 5/5 fail ("chain broken at line 1:
  prev_hash mismatch"). The test asserts on actual file bytes, not return values.
  Cursor-miss test asserts `!= GENESIS_PREV_HASH`, so it would catch the
  accidental-genesis-in-mid-chain forgery case. Binary I/O confirmed at both call sites.
Task 3: minor (deferred): task-3-report.md's self-review claims the no-op-lock
  verification was run but pastes no failing output, unlike its RED/GREEN section which
  does. Claim independently confirmed true by the reviewer — report-rigor gap, not a code
  defect.

Task 4: implemented (commits fc88f9c..518a2cd). Suite 411 → 424 (+13, exact match).
  Review (opus): spec ✅, quality Needs fixes — 2 Important + 3 Minor.
  Watershed assertion confirmed genuine: implementer's mutation removing the line-1
  exemption gave `broken_at=1` vs expected `2`, with `ok is False` in BOTH cases — proving
  the position assertion, not the boolean, is what catches the spec-violating impl.
  Reviewer traced the byte span end-to-end: write hashes `json.dumps(...).encode()`,
  verify hashes the same span read via `"rb"`; no `json.dumps` anywhere in the verify path.
  Write side provably untouched (diff stat 256 insertions, 0 deletions, pure append).
Task 4: fix round 1/5 (2 addressed, 0 open; commits 518a2cd..8101b56). Suite 424 → 427.
  - Important 1: `verify_chain()` raised `TypeError` on a JSON-scalar line (`null`, `42`,
    `true`) because `"prev_hash" not in record` is not a membership test on a scalar. An
    attacker rewriting the mirror appends `null` and the verifier stops returning results
    at all. Plan-mandated defect (verbatim Step 3 code).
  - Ruling: fix it. The plan's stated contract is `ok`/`total`/`broken_at`/`error` — the
    `error` field exists so corrupt input is *reported*, not thrown. Returning a break
    result is strictly stricter than raising, and this project is fail-closed everywhere
    else. The contract governs over the statement list in the code block.
  - Important 2: line 1's `prev_hash` *value* tolerance (forward compat with pre-chain
    files) was implemented and documented but locked by no test. Added
    `test_line_one_prev_hash_value_is_not_validated`.
  - Both proven by mutation with pasted output. Mutation 2 (`elif record["prev_hash"] !=
    (expected or GENESIS_PREV_HASH)`) → `1 failed, 24 passed`: only the new test red,
    confirming it is load-bearing rather than redundant.
  - Re-reviewer additionally found the `isinstance` guard *improves* the JSON-array-on-
    line-1 case: the old code silently tolerated it as "prev_hash omitted" and returned
    `ok=True`; it now reports a break. Strict correctness gain, no assertion depended on
    the old accidental behavior.
Task 4: minor (deferred): `broken_at` and the `第 N 行` error text are record indices, not
  physical file lines — `_raw_lines()` drops whitespace-only lines, so an auditor told
  "第 3 行" may open the wrong line during a tamper investigation.
Task 4: minor (deferred): `verify_chain()` does not take `_lock_for(self._key)` while the
  writer holds it. Reviewer tried and could not demonstrate a torn read at realistic sizes
  (flush+fsync happen inside the lock; only a 2 MB unbuffered write went through). A
  consistency gap with no demonstrated failure — one line to close.
Task 4: minor (deferred): `tail_hash` is `None` on every early return, so a caller wanting
  to anchor the tail externally after detecting a break gets nothing to anchor.
Task 4: complete (commits fc88f9c..8101b56, review clean)

Task 5: implemented (commits 8101b56..25cda3f). Suite 427 → 441 (+14).
  Review: spec ✅, quality Needs fixes — 1 Important + 3 Minor. Two-phase API confirmed:
  `record()` touches only `self._store`, `mirror()` only `self._mirror`, no packed method.
  Reviewer confirmed `test_no_effect_function_appends_jsonl` is NOT a zero-file scan —
  `app/graph/nodes.py` already has 4 real `effect_*` functions, so the glob evaluates real
  content and correctly finds no violations.
Task 5: fix round 1/5 (2 addressed, 0 open; commits 25cda3f..463c231). Suite 441 → 445.
  - Important: the import-isolation guard shipped with no positive control — vacuously
    true today, so a regression (inspecting only `ast.Import` and forgetting
    `ast.ImportFrom`) would leave it permanently green. This is the exact failure class
    the project's own history warns about for this specific guard.
  - Fixed by extracting `_modules_importing_config_or_graph(source)` and having both the
    real assertion and the new `test_import_detector_actually_detects` call that same
    helper — so the control cannot drift from the check it protects.
  - Ruling: folded reviewer Minor 3 into this round (same defect class, same code being
    refactored) — `_effect_functions_touching_the_mirror` has three violation branches but
    its control exercised only `.mirror(`; added cases for `.backfill(` and the bare
    `JsonlChainSink` name.
  - All three mutations isolate to exactly the expected parametrize case, not a blanket
    failure: dropping `ast.ImportFrom` reddens only `from_import`; dropping `backfill`
    reddens only `backfill_call`; dropping the bare-name branch reddens only
    `jsonl_chain_sink_name`. Re-reviewer independently ran the focused file: 18 passed.
Task 5: minor (deferred): `app/audit/recorder.py:27` imports `AI_ANALYSIS` and `BACKFILL`
  but never uses them (inherited verbatim from the brief; Task 6 may consume them).
Task 5: minor (deferred): `tests/test_audit_recorder.py:84` `store.conn = conn` is a dead
  line — `mirror()` never touches `self._store`, so its comment claims a purpose the line
  does not serve.
Task 5: complete (commits 8101b56..463c231, review clean)

Task 6: implemented (commits 463c231..c4f66d0). Suite 445 → 453 (+8).
  Review: spec ✅, quality Approved, 1 Important + 2 Minor.
Task 6: fix round 1/5 (1 addressed, 0 open; commit c48b212). Suite 453 → 454.
  - Important: every one of the 8 new tests placed the backfilled id at the chronological
    end, so "true tail" and "logical original position" were byte-identical — no test could
    distinguish a correct tail-append from a position-preserving reinsert, which is exactly
    the property the plan singles out as load-bearing.
  - Added `test_backfill_of_a_mid_sequence_gap_still_lands_at_the_tail` (3 records, gap in
    the middle). Implementer proved it discriminates: RED on a simulated mid-position
    reinsert, green on real tail-append, while the old 2-record test stayed green in BOTH
    arrangements — confirming the gap and that the new test closes it.
Task 6: minor (deferred): `backfill()` does not check whether `missing_id` is actually
  absent before writing; calling it twice yields a duplicate `backfill` event, no guard.
Task 6: minor (deferred): `test_reconcile_is_clean_when_both_sides_match` does not
  separately assert `missing_in_store == frozenset()`.
Task 6: complete (commits 463c231..c48b212, review clean)

## ⏸ 留步 — final whole-branch review NOT run (budget exhausted)

The USD budget for this headless run was spent by the end of Task 6's fix round
($23.3 of $25). The final whole-branch review — dispatched on the most capable model, and
the step that triages the deferred-minor list below — was **not performed**. Task 6's
scoped re-review was also collapsed into this gap; its fix was verified by the controller
directly (full suite 454 passed, diff confined to the one new test) rather than by a
dispatched re-reviewer.

Merge proceeded anyway on this reasoning, recorded for audit: all 6 tasks passed their own
two-stage spec+quality review, 5 of them through a fix round with pasted mutation evidence;
and the branch diff is **8 new files, 2091 insertions, 0 deletions, 0 modifications to any
existing file** — so U2 cannot change observable behavior, which is the plan's own stated
definition of "independently mergeable" (§"明确的范围边界": U2 合并后系统的可观察行为
必须与合并前完全一致).

**Nothing here blocks U3, but the 9 deferred minors below were never triaged by a
merge-gate reviewer. Someone should run that pass before U3 wires this module up.**

### Deferred minors awaiting triage (9)

1. T1 `to_dict()` returns dict-valued fields by reference, not deep-copied.
2. T1 `test_all_registered_event_types_construct` proves construction only, not per-type shape.
3. T2 `_is_analysis_run_pk_conflict` substring-matches SQLite's exception message text.
4. T2 `score.id or …` treats an explicitly-set empty-string id as unset.
5. T3 task-3-report's no-op-lock claim pasted no failing output (independently confirmed true).
6. T4 `broken_at` / `第 N 行` are record indices, not physical file lines.
7. T4 `verify_chain()` does not take `_lock_for` while the writer holds it (no demonstrated failure).
8. T4 `tail_hash` is `None` on every early return.
9. T5 `tests/test_audit_recorder.py:84` dead `store.conn = conn` line.
   (T5's unused `AI_ANALYSIS`/`BACKFILL` imports were resolved — Task 6 consumes them.)
   (T6's two minors — `backfill()` idempotency, `missing_in_store` assertion — join this list.)

## Merge

Pushed 42fd90f..0fa4d54 to origin/main (fast-forward; origin/main was still at the merge
base, the parallel unit F had not pushed). Verified: `git rev-list --count origin/main..HEAD`
= 0. Checked against origin/main rather than local main because local main is checked out
in the primary worktree and cannot be updated from here.
