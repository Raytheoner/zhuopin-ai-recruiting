# 修 bug · `idempotent_effect` 撞 `effect_log` 唯一键改短路 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**通道**：`03-工具链协作规则.md` §5「轻量通道」——修 bug、不改任何外部契约（无新端点、无新字段、无 schema 变更、无幂等键格式变更），⛔ 不走 `openspec-propose`。

**Goal:** 重复 `confirm` 同一岗位时，`app/storage/idempotency.py` 的 `idempotent_effect` 在 `INSERT effect_log` 撞唯一索引后**当作"已被另一条路径应用"短路返回 `None`**，而不是把 `sqlite3.IntegrityError` 抛成用户可见的 500；且**本次的业务写必须整体回滚**，让「`effect_log` 条数 ≡ 业务表行数」这条铁律 1 的恒等式在竞态下继续成立。

**Architecture:** 装饰器第 33 行的 `SELECT` 预检**永远不可能充分**——它和 `INSERT` 之间隔着被装饰函数的整个函数体，另一条路径（双击、客户端超时重发、反向代理重试、并行 session）可以在这段窗口里完整应用同一个 `effect_key`。唯一索引才是唯一权威。因此修法不是"把预检做得更严"，而是**把 `INSERT` 的唯一键冲突翻译成幂等短路**：`INSERT` 撞约束 ⇒ `conn.rollback()` 撤掉本次的业务写 ⇒ 回滚后重新 `SELECT` 确认这把键确实已存在（存在才是幂等命中，不存在说明是别的完整性约束坏了，原样上抛）⇒ 记一条 WARNING ⇒ 返回 `None`。⛔ 不用 `INSERT OR IGNORE`——那会让本次的业务写照常 `commit`，业务表多一行而 `effect_log` 只有一行，恒等式当场破。⛔ 不靠字符串匹配错误文案判别冲突类型（SQLite 的消息文案不是契约），用「回滚后键是否存在」这条**语义判据**。

**Tech Stack:** Python 3.14（`venv/bin/python`，与 .51 部署环境严格对齐）· 标准库 `sqlite3`（legacy transaction control：`SELECT` 不开事务，DML 才隐式 `BEGIN`——这正是复现能被构造成确定性而非靠线程抢跑的原因）· SQLite WAL + `busy_timeout=5000`（`app/storage/db.py:get_connection`）· pytest。⛔ `requirements.txt` 一行不改。

---

## 背景与取证

现网实发一次，09-08 13:18，取证见 `docs/audit-and-outbound-ops.md` §五「顺带查实的遗留缺陷」：

```
File "app/web/server.py", line 400, in confirm
    effect_generate_and_persist_jd(
File "app/storage/idempotency.py", line 70, in wrapper
    conn.execute("INSERT INTO effect_log (effect_key, ...) VALUES (?, ?, ?, ?, ...)")
sqlite3.IntegrityError: UNIQUE constraint failed: effect_log.effect_key
```

**唯一索引本身是对的**（铁律 1 明文要求它存在），错的是装饰器把"命中已存在的键"当成异常路径。

⚠️ **危害比原始登记的更重一档，本计划据此定级。** 原登记写的是"现象是用户可见的 500，不是数据损坏——恒等式未破"。出计划时在临时库上实跑复现（记录见文末），实测：`IntegrityError` 从 `conn.execute` 抛出后，**本次已写入的业务行仍停留在 SQLite 隐式开启的、未回滚的事务里**（复现脚本此刻读到 `job` 表 2 行，而 `effect_log` 只有 1 行）。装饰器现有的 `try/except` 只包住**被装饰函数体**，`INSERT effect_log` 在它外面，所以这条路径**没有任何回滚**。这批脏行会被之后任何一次**不相关**的 effect 成功 `conn.commit()` 悄悄落盘（共享单连接，见 `get_connection` 注释）——那一刻恒等式就破了，且**没有任何症状**。

也就是说：这不只是一个 500，是一条"平时表现为 500、偶尔顺手把恒等式破掉"的路径。500 是显性的，恒等式破是静默的，后者才是必须修的理由。

---

## Global Constraints

以下每一条对**每个** Task 都成立，reviewer 按这一段逐条看。第 1–3 条从 `CLAUDE.md`「工程铁律 / 合规红线」逐字复制，第 4–10 条是本次修复的边界，逐条来自派发 opener。

1. **（工程铁律 1，逐字）LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。
   *为什么*：业务写失败而幂等记录成功 → 系统判定"已执行"→ 永不重试。**幂等本是防重复的保护，拆开事务后变成永久丢失的保证。**
2. **（工程铁律 2）L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。本次只改 L4 的**装饰器**，⛔ 不给任何节点加副作用。
3. **（合规红线）AI 只做排序推荐，不做自动淘汰。** 本次修复**不改变任何业务判断**：短路返回 `None` 的语义是"这件事已经被做过了"，⛔ 不是"这件事被否决了"。
4. **修法钉死**：`INSERT effect_log` 撞 `UNIQUE` ⇒ 说明另一条路径已应用同一 effect ⇒ **本次的业务写必须整体 `rollback`**（否则业务表多一行、恒等式破），然后按"已执行"返回 `None`；⛔ **不许 `INSERT OR IGNORE` 后照常 `commit`**（那会留下重复业务行）。
5. **回归测试要真复现**：不许用 mock 掉 `conn.execute` 之类的手法伪造冲突。断言四项齐全：① 返回 `None` ② 不抛 ③ 业务表行数不变（只剩抢先者那一行）④ `effect_log` 恰一行。
   ⚠️ **opener 给的第一种复现写法不成立，本计划改用第二种。** "同一连接内先手工插一条同键 `effect_log` 再调节点"**复现不出来**：同一连接看得见自己未提交的写，第 33 行预检会直接命中并短路，`INSERT` 根本走不到。必须用**两个连接**（详见 Task 1 的时序说明）。
6. **⛔ 不改 `effect_log` 表结构**（`app/storage/db.py:59-67` 一行不动）、**⛔ 不改幂等键格式** `{thread_id}:{node_name}:{business_key}`、**⛔ 不改任何 `effect_*` 节点函数体**。
7. **⛔ 不碰 `app/outbound/`、`app/audit/`。**
8. **`app/web/server.py` 的 confirm 路径**：装饰器返回 `None` 时应按幂等成功回 2xx。**出计划时已核对：现状已经是这样**（`confirm` 忽略两个 effect 的返回值，统一 `return _jd_payload(job_id, version)` 读回持久化结果，见 `app/web/server.py:455-459` 的注释）。⇒ **⛔ 本次一行不改 `app/web/server.py`**，只加一条端到端回归把这个现状钉住。
9. **并行同伴**：值守泳道在建 `tools/liaison/`（它 `import` 本装饰器但不改它），机制泳道改 `docs/openers/run-lanes.sh`。`git status` 里出现它们的改动是**正常的**——⛔ 不要停下、不要问、不要顺手提交。只 `git add` 本计划 File Structure 里列出的路径，⛔ 禁止 `git add -A` / `git add .` / `git commit -a`。
10. **⛔ 不改 `requirements.txt`、不改 CI、不改 `openspec/` 下任何文件。** 本次是轻量通道，没有变更包要回勾。

---

## File Structure

| 文件 | 新建/修改 | 职责 |
|---|---|---|
| `app/storage/idempotency.py` | 修改（**只改 `INSERT` 那一段**） | Task 2：唯一键冲突 ⇒ 回滚 + 语义确认 + 短路返回 `None` |
| `tests/test_idempotency.py` | 修改（**只追加**，⛔ 不改既有 4 条用例） | Task 1 / Task 3：竞态复现、非唯一约束仍上抛、回滚失败的兜底 |
| `tests/test_confirm_replay.py` | **新建** | Task 4：重复 `POST /confirm` 的端到端回归（2xx + 恒等式） |

依赖方向：**Task 1（先红）→ Task 2（转绿）→ Task 3（补边界）→ Task 4（端到端）→ Task 5（全量回归）**。⛔ 不要打乱顺序：Task 1 必须在 Task 2 之前跑一次并**看见它红**，否则无法证明测试真的复现了这个 bug。

分工边界（⛔ 不重复造，沿用 `tests/test_effect_idempotency_suite.py` 文件头写死的口径）：
- `tests/test_idempotency.py` —— 装饰器**本身**的语义（短路、回滚、日志）← **本次的主战场**
- `tests/test_transaction_ownership.py` —— 事务**归属**
- `tests/test_graph_idempotency.py` / `tests/test_effect_idempotency_suite.py` —— **节点**的崩溃-恢复覆盖 ← **本次一行不改**

---

### Task 1: 用两个连接确定性复现竞态（先红）

**为什么必须两个连接**：`sqlite3` 的 legacy transaction control 下，`SELECT` **不开事务**，只有 DML 才隐式 `BEGIN`。所以这个时序是确定性的、不需要线程抢跑：

```
conn1: 装饰器第 33 行 SELECT 预检 → 未命中（此刻 conn1 未持任何锁）
conn2: INSERT effect_log(同键) + INSERT 业务行 + commit     ← 抢先者完整应用
conn1: 被装饰函数体 INSERT 业务行（到这里才隐式 BEGIN、才拿写锁）
conn1: 装饰器 INSERT effect_log → 撞 UNIQUE
```

⚠️ 顺序不可换：若让 `conn2` 在 `conn1` 已经写过业务行之后再提交，`conn2` 会撞上 `conn1` 的写锁（SQLite 单写者），表现为 `busy_timeout` 5 秒后 `database is locked`，测的就不是这个 bug 了。所以**抢先者的整段动作放在被装饰函数体的最前面、业务写之前**。

- [ ] 在 `tests/test_idempotency.py` **末尾追加**（⛔ 不动既有 4 条用例）：

```python
def test_unique_key_race_short_circuits_and_rolls_back_this_write(tmp_path, caplog):
    """
    工程铁律1 的竞态落点。装饰器第 33 行的 SELECT 预检与随后的
    INSERT effect_log 之间隔着被装饰函数的整个函数体；另一条路径（双击、
    客户端超时重发、反向代理重试）完全可以在这段窗口里**完整**应用同一个
    effect_key。唯一索引才是唯一权威，预检只是省一次无谓执行的优化。

    命中这种情况时正确的语义是"已执行 ⇒ 短路"，⛔ 不是异常：
    - 抛出去 = 用户看到 500（现网 2026-09-08 13:18 实发一次）
    - 抛出去且不回滚 = 本次的业务写留在未提交事务里，被之后任何一次
      不相关的 effect 的 conn.commit() 悄悄带下去 ⇒ 业务表多一行、
      effect_log 仍一行 ⇒ 恒等式破，且没有任何症状。

    时序靠两个连接构造，是确定性的、不靠线程抢跑：sqlite3 的 legacy
    transaction control 下 SELECT 不开事务，所以预检之后 conn 尚未持写锁，
    conn2 此刻可以自由提交；等 conn 写了业务行拿到写锁，conn2 就会被挡住，
    所以抢先者必须抢在本次业务写之前。
    """
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    init_schema(conn)
    winner_conn = get_connection(db_path)

    calls = []

    @idempotent_effect("effect_race")
    def send(conn, thread_id, business_key):
        calls.append(business_key)
        # 预检已过、本连接尚未持写锁：另一条路径此刻完整应用同一个 effect
        winner_conn.execute(
            "INSERT INTO effect_log (effect_key, thread_id, node_name, business_key, applied_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (f"{thread_id}:effect_race:{business_key}", thread_id, "effect_race", business_key),
        )
        winner_conn.execute(
            "INSERT INTO job (id, title) VALUES (?, ?)", ("job-winner", "抢先者写的行")
        )
        winner_conn.commit()
        # 本次的业务写（这一行必须被回滚掉）
        conn.execute("INSERT INTO job (id, title) VALUES (?, ?)", ("job-loser", "本次写的行"))
        return "sent"

    with caplog.at_level(logging.WARNING, logger="app.storage.idempotency"):
        result = send(conn, thread_id="job1", business_key="v1")

    # ① 返回 None（按"已执行"短路）  ② 不抛（走到这里就说明没抛）
    assert result is None
    assert calls == ["v1"]  # 函数体确实跑过了——这不是预检短路，是冲突短路

    # ③ 业务表只剩抢先者那一行：本次的写被整体回滚
    #    ⛔ 这一条是 INSERT OR IGNORE 那种写法过不去的判据
    assert [r[0] for r in conn.execute("SELECT id FROM job ORDER BY id")] == ["job-winner"]

    # ④ effect_log 恰一行（恒等式：effect_log 条数 ≡ 业务表行数）
    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 1

    # 这条路径很罕见，必须在日志里留下痕迹，否则现网只能看见"什么都没发生"
    assert any("effect_race" in r.getMessage() for r in caplog.records)
```

- [ ] 文件顶部按需补 `import logging`（既有 import 只有 `pytest` 与两个 app 模块）。
- [ ] 跑 `venv/bin/python -m pytest tests/test_idempotency.py -x -q`，**必须看见新用例红**，且红的原因是 `sqlite3.IntegrityError: UNIQUE constraint failed: effect_log.effect_key`。⛔ 红成别的原因（比如 `database is locked`）说明时序写错了，回头改测试、**不要**去改被测代码。
- [ ] 把这一步实际看到的报错原文贴进实现报告。

---

### Task 2: 装饰器把唯一键冲突翻译成幂等短路（转绿）

- [ ] 修改 `app/storage/idempotency.py`，**只替换 `INSERT effect_log` + `conn.commit()` 那一段**（`except Exception:` 那一整块回滚逻辑与它的注释⛔ 一字不改）：

```python
            try:
                conn.execute(
                    "INSERT INTO effect_log (effect_key, thread_id, node_name, business_key, applied_at) "
                    "VALUES (?, ?, ?, ?, datetime('now'))",
                    (effect_key, thread_id, node_name, business_key),
                )
            except sqlite3.IntegrityError as exc:
                # 上面第一行的 SELECT 预检**永远不可能充分**：它和这条 INSERT 之间
                # 隔着被装饰函数的整个函数体，另一条路径（双击、客户端超时重发、
                # 反向代理重试）可以在这段窗口里完整应用同一个 effect_key。
                # 唯一索引才是唯一权威，预检只是省一次无谓执行的优化。
                #
                # 撞上唯一键 = 这件事已经被别人做完了 = 幂等命中，是**正常路径**，
                # ⛔ 不是异常。抛出去用户就看到 500（现网 2026-09-08 13:18 实发一次）。
                #
                # 但短路之前必须先回滚：fn 刚写的业务行还在这个未提交的事务里，
                # 而 conn 是全应用共享的单连接（见 db.get_connection）。不回滚，
                # 这些行会被之后任何一次*不相关*的 effect 的 conn.commit() 悄悄
                # 落盘 —— 业务表多一行、effect_log 仍只有一行，工程铁律1 的恒等式
                # 当场破掉，而且没有任何症状。
                # ⛔ 同理不能用 INSERT OR IGNORE 之后照常 commit：那等于主动把
                # 重复的业务行提交下去。
                try:
                    conn.rollback()
                except Exception as rollback_exc:
                    # 回滚失败 ⇒ 那批业务写仍留在未提交事务里，随时会被下一次
                    # 不相关的 commit 带下去。此时**不能**假装幂等成功返回 None
                    # （那会让调用方以为一切正常，而恒等式正悬在破掉的边缘），
                    # 只能记 ERROR 并把原始异常抛给调用方。
                    logger.error(
                        "rollback failed while short-circuiting duplicate effect_key=%s; "
                        "this call's business write was NOT undone and may be silently "
                        "committed by a later, unrelated effect",
                        effect_key,
                        exc_info=rollback_exc,
                    )
                    raise exc from rollback_exc

                # 语义判据，⛔ 不匹配错误文案（SQLite 的消息措辞不是契约）：
                # 回滚后这把键确实在库里 ⇒ 是幂等命中；不在 ⇒ 坏的是别的完整性
                # 约束（例如 thread_id 为 None 触发 NOT NULL），那是真 bug，原样上抛。
                already_applied = conn.execute(
                    "SELECT 1 FROM effect_log WHERE effect_key = ?", (effect_key,)
                ).fetchone()
                if already_applied is None:
                    raise
                logger.warning(
                    "effect_key=%s was applied by another path between this call's "
                    "pre-check and its effect_log insert; treating as already applied "
                    "and rolling back this call's business write",
                    effect_key,
                )
                return None

            conn.commit()
            return result
```

- [ ] ⛔ 不动函数签名、不动 `EffectAlreadyApplied`（它仍是预留、仍不主动抛）、不动第 33 行的预检 `SELECT`（它仍有价值：省掉重复执行函数体，绝大多数重放走的还是它）。
- [ ] 跑 `venv/bin/python -m pytest tests/test_idempotency.py -q`：Task 1 的用例转绿，**既有 4 条用例全绿**。

---

### Task 3: 补两条边界用例（非唯一约束照旧上抛 / 回滚失败不冒充成功）

- [ ] 在 `tests/test_idempotency.py` 继续追加：

```python
def test_non_unique_integrity_error_still_propagates(tmp_path):
    """
    "撞 IntegrityError 就当幂等命中"是**错的**修法：effect_log 上还有 NOT NULL
    约束，thread_id 传成 None 一样抛 IntegrityError，但那是调用方的真 bug，
    吞掉它等于把一个必现故障伪装成"这件事已经做过了"。

    判据用的是语义而不是错误文案：回滚之后这把 effect_key 不在库里 ⇒ 不是
    幂等命中 ⇒ 原样上抛。
    """
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    init_schema(conn)

    @idempotent_effect("effect_not_null")
    def send(conn, thread_id, business_key):
        return "sent"

    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        send(conn, thread_id=None, business_key="v1")

    assert conn.execute("SELECT COUNT(*) FROM effect_log").fetchone()[0] == 0


def test_rollback_failure_during_short_circuit_does_not_fake_success(tmp_path, caplog):
    """
    回滚失败是"回滚失败"这一族里最坏的一支：本次的业务写没被撤掉，仍躺在
    未提交事务里等着被下一次不相关的 commit 带走。此时返回 None 等于告诉
    调用方"一切正常、这件事早做过了"，而恒等式正悬在破掉的边缘。

    正确行为 = 记 ERROR + 把 IntegrityError 抛给调用方（宁可 500 一次，
    也不要静默地让恒等式破掉）。
    """
    db_path = str(tmp_path / "test.db")
    conn = get_connection(db_path)
    init_schema(conn)
    winner_conn = get_connection(db_path)

    @idempotent_effect("effect_race_rb")
    def send(conn, thread_id, business_key):
        winner_conn.execute(
            "INSERT INTO effect_log (effect_key, thread_id, node_name, business_key, applied_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (f"{thread_id}:effect_race_rb:{business_key}", thread_id, "effect_race_rb", business_key),
        )
        winner_conn.commit()
        conn.execute("INSERT INTO job (id, title) VALUES (?, ?)", ("job-loser", "本次写的行"))
        # 让紧随其后的 rollback 失败（模拟事务已被别的所有者结束等情况）
        def _boom():
            raise sqlite3.OperationalError("rollback exploded")
        conn.rollback = _boom
        return "sent"

    with caplog.at_level(logging.ERROR, logger="app.storage.idempotency"):
        with pytest.raises(sqlite3.IntegrityError):
            send(conn, thread_id="job1", business_key="v1")

    assert any(r.levelname == "ERROR" for r in caplog.records)
```

- [ ] 文件顶部按需补 `import sqlite3`。
- [ ] ⚠️ 第二条用例把 `conn.rollback` 替换成了会抛的函数，**该连接从此不可再用**——所以它是这条用例的最后一步，⛔ 不要在其后继续对 `conn` 断言库内状态（它的写还挂在未提交事务里，本来就是这条用例要描述的坏状态）。
- [ ] 跑 `venv/bin/python -m pytest tests/test_idempotency.py -q` 全绿。

---

### Task 4: 端到端把 confirm 路径的 2xx 钉住

**为什么单独一条**：Global Constraints 第 8 条说的"现状已经是 2xx"是**出计划时读代码得到的结论**，没有测试守着。这条 bug 的现网症状恰恰出在 `confirm`，修完必须有一条走真实 HTTP 路径的回归，否则将来有人给 `confirm` 加一句"effect 返回 None 就报错"，这个 500 会原地复活而没人发现。

- [ ] 先照 `tests/test_jd_endpoints.py` / `tests/test_approval_branches.py` 现成的 `TestClient` 装配方式建 `tests/test_confirm_replay.py`（⛔ 不自造第二套 app 装配，照抄既有 fixture 的用法）。
- [ ] 用例内容：把一个岗位推进到 `confirmation_prompt` 状态，然后**连续两次** `POST /api/jobs/{job_id}/confirm`，断言：
  - [ ] 两次都是 2xx，且两次响应体**逐字相同**（第二次读回的是同一份持久化结果）
  - [ ] `job_profile` 里该 `job_id` 的 `status='approved'` 行恰一行，`job.status == 'approved'`
  - [ ] `human_review` 该 `job_id` 恰一行（⛔ 重复确认不许留两条决策痕）
  - [ ] `effect_log` 里 `node_name='effect_confirm_profile'` 与 `'effect_generate_and_persist_jd'` 各恰一行
- [ ] ⛔ 本 Task 一行不改 `app/web/server.py`。若这条用例红了，先判断红的是**既有行为**还是**Task 2 引入的回归**：
  - 是 Task 2 的回归 → 回去改装饰器
  - 是既有行为（与本次修复无关的重复确认缺陷） → **⛔ 不在本轮修**，登记进实现报告的「红灯与观察项」，并把该条断言放宽到能通过的最小形态、在用例里写明为什么放宽

---

### Task 5: 全量回归

- [ ] `venv/bin/python -m pytest -q`（全量）——⛔ 不许有新增 failed。
- [ ] `venv/bin/python -m pytest -m compliance -q`
- [ ] `venv/bin/python -m pytest tests/test_effect_idempotency_suite.py tests/test_graph_idempotency.py tests/test_transaction_ownership.py tests/test_boundary_guard.py -q` —— 这四个是铁律 1 与层次边界的守护，**必须全绿**。
- [ ] 实现报告里给出全量 pytest 的 `passed/failed/skipped` 数字，⛔ 不许只写"通过了"。

---

## 红灯与观察项（登记，⛔ 本轮不修）

1. **`_jd_payload` 在抢先者尚未提交完时可能读到半截状态。** 竞态下的败方短路返回后立刻 `return _jd_payload(job_id, version)`，而抢先者那一侧可能还没走完自己的第二个 effect。现象是回执里 JD 字段暂缺，刷新即恢复。这是 SQLite 单连接 demo 形态的固有限制，M2 迁 Postgres + 每请求连接后自然消失。⛔ 不在本轮加轮询或重试掩盖它。
2. **被装饰函数体内部自己撞唯一约束的情况不在本次修法覆盖内**，且**本来就该抛**。例：`effect_confirm_profile` 里 `_record_hard_requirements` 写 `hard_requirement`（复合主键）时若已存在，`IntegrityError` 从 fn 内部抛出，走既有的 `except Exception` 分支回滚并上抛。这条路径的语义与本次修的不同——那是"业务写自己重复了"，不是"幂等键被抢先"，混在一起处理会把真 bug 吞掉。
3. **本修法只处理 SQLite 的 `sqlite3.IntegrityError`。** M2 迁 Postgres 时这段 `except` 必须同步换成对应的驱动异常（`psycopg.errors.UniqueViolation`），否则这个 500 会在 Postgres 上原样复活。已在此登记，迁移时按图索骥。

---

## 提取验证记录（2026-09-08，出计划时做的）

本计划的复现时序与修法**不是纸面推演**，出计划时在 `/tmp` 的临时脚本里用本仓库真实的 `get_connection` / `init_schema` 跑过（`venv/bin/python`，Python 3.14.6），⛔ 未改动仓库内任何文件：

| 场景 | 结果 |
|---|---|
| 现状 + 两连接时序 | 复现成功：`sqlite3.IntegrityError: UNIQUE constraint failed: effect_log.effect_key` |
| 现状复现时刻的库内状态 | `job` 表 **2 行**、`effect_log` **1 行** —— 本次的业务写未被回滚，**恒等式已悬空**（这条推翻了原登记"恒等式未破"的判断，见上方「背景与取证」） |
| 修法 + 同一时序 | 返回 `None`、不抛；`job` 表只剩 `job-winner` 一行；`effect_log` 1 行 —— 四项断言全中 |
| 修法 + `thread_id=None` | `NOT NULL constraint failed: effect_log.thread_id` 照旧上抛，未被吞掉 |
| 修法 + 正常首跑与普通重放 | 首跑返回 `"sent"`、重放返回 `None`，函数体只跑 1 次，`job` 1 行、`effect_log` 1 行 —— 既有语义无回归 |

出计划时纠正的一处问题：**opener 给的第一种复现写法（同一连接先手工插同键）复现不出来**——同一连接看得见自己未提交的写，预检直接命中短路。Task 1 用的是两连接写法，并写明了顺序不可换的理由。
