# U7 · 边界守护与 CI（7.1 / 7.2）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「不新增 `zhuopin_platform` 依赖、不跨仓库 import、不拷贝参考文件」这三条本包硬边界，从 2026-08-28 那一次人工 `grep`（`06-企业AI转型资产借鉴清单.md` §10.3）变成 CI 每次都跑、破了就红、且**被证明过确实会红**的机器检查。

**Architecture:** 新增单文件脚本 `scripts/check_boundary.py`，只用标准库（`ast` / `tomllib` / `subprocess` / `pathlib`），对外是四个纯查询函数 + 一个 `main(argv) -> int` CLI，吐一组 `Violation`，**不写任何文件、不改任何既有模块**。CI 侧往既有 `hooks` job **追加一个步骤**调这个 CLI——挂 `hooks` 而不是 `test`，是因为 7.2 的依赖 diff 判据要按变更包立项 commit 比，需要 `fetch-depth: 0`，而 `hooks` job 的 checkout 已经是 0；顺带 ⛔ 不碰 `test` job 里 U6 刚定型的 pytest / 合规断言两步。反证落在 `tests/test_boundary_guard.py`，由现有 pytest 全量带跑。

**Tech Stack:** Python 3.14（`requires-python = ">=3.14,<3.15"`）· 标准库 `ast` / `tomllib` / `subprocess` / `argparse` / `dataclasses` / `warnings` · pytest 8.3.4 · GitHub Actions（既有 `.github/workflows/ci.yml`，`hooks` job 跑在 ubuntu-latest）

**范围：** `openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md` 第 7 章 **7.1 / 7.2 两项**。
7.3 / 7.4 已于 2026-08-28 提前落地并回勾（`db6596e`），7.5 / 7.6 已登记为 `docs/tech-debt.md` TD-6 / TD-7。**本计划 ⛔ 不碰这四条。**

**输入（真源）：** `delivery-units.md` §2.U7 与 §4 约定 7；`06-企业AI转型资产借鉴清单.md` §10.3 的两条已实跑判据；`CLAUDE.md`「🔒『企业AI转型』：已迁 GitHub，只读参考」。

**依赖：** U1–U6 均已合入 main（`202cb5d` 回勾第 6 章 7/7）。本单元只读它们，不改它们。

---

## ⚠️ 与 spec 的关系（先读，别按常规找 Requirement）

**7.1 / 7.2 在两份 spec 里没有对应的 `### Requirement:`，这不是漏找。**

`specs/ai-decision-audit/spec.md` 的 6 条与 `specs/outbound-approval-gate/spec.md` 的 7 条 Requirement 全部是**产品行为契约**（留痕字段、证据回指、fail-closed 判定、总开关……）。7.1/7.2 约束的是**这份代码从哪儿来**，是工程边界不是行为契约——真源在 `delivery-units.md` §4 约定 7：

> **本包三条硬边界**（全部单元）：不新增 `zhuopin_platform` 依赖、不跨仓库 import、不拷贝参考文件。U7 的 7.1/7.2 把它变成 CI 可查。

以及 `CLAUDE.md`：

> 引入方式是**读取参考 + 在本仓库自建实现**，⛔ 不要 clone 进本仓库、不要跨仓库引用或拷贝文件形成耦合——两个仓库独立演进，共享的是做法不是代码。

**给 reviewer 的判据**：不要因为"这个 Task 指不到任何 Requirement"就判 reject。反过来的检查照做——本计划**不得**新增或修改任何 spec 行为。

---

## Global Constraints

**每一条都是 reviewer 的注意力透镜。违反其中任何一条即判 reject，不进入下一个 Task。**

### 一、本单元专属（违反即重写）

1. **只 `append` CI step，⛔ 不重写现有 job、不改 pytest / compliance step。** 具体到行：`.github/workflows/ci.yml` 的 `test` job 全部 6 个步骤（`checkout` / `setup-python` / `装依赖` / `版本留痕` / `pytest` / `合规断言（红线守护）`）**一个字都不许动**；`hooks` job 现有 4 步同样不动，只在末尾追加 1 步。reviewer 判据：`git diff .github/workflows/ci.yml` 只能是纯新增行，**零删除行、零修改行**。

2. **检查用仓库内脚本（`scripts/` 下新建）或纯 shell，⛔ 不引第三方 action、不加依赖。** 即：⛔ 不许出现 `uses:` 指向任何非 `actions/*` 的第三方 action；⛔ `requirements.txt` / `pyproject.toml` 一行不许改（本单元自己就是这条的守护者，自己违反是最讽刺的失败形状）；脚本 ⛔ 只用 Python 标准库。

3. **「本变更的依赖文件 diff 必须为空」的判据写死为**
   `git diff --stat e65f6857fe255634d49a3e8696b1dba0f5facbec..HEAD -- requirements.txt`
   起点 commit ＝ 本变更包立项 commit（`e65f685 docs: AI 评分留痕与外发人工确认门禁——立项`，2026-08-14），**写死进脚本常量，⛔ 不许写成 `origin/main` 或 `HEAD~N`**——那两个都会随 main 前进而漂移，"本变更没加依赖"这句话就失去了固定的参照物。

4. **7.1 / 7.2 的检查必须有反证。** 判据：故意塞一行 `import zhuopin_platform` 进临时文件、故意喂一个非空 diff，**检查必须失败**。不失败 ＝ 检查恒真 ＝ 重写。这条的形状与 U6 的 6.7 完全相同——「零命中」同时兼容"边界守住了"和"检查根本没生效"两种解释，只有反证能把它们分开。

5. **⛔ 本单元不碰 7.3 / 7.4 / 7.5 / 7.6**（已分别落地于 `docs/audit-and-outbound-ops.md`、`06` §10、`docs/tech-debt.md` TD-6/TD-7）。⛔ 不改 `docs/audit-and-outbound-ops.md` 第五节那三项 `.51` 留步——那属生产环境事项，**不可代**。

6. **⛔ 本单元不新增、不修改 `app/` 下任何文件。** 它是被检查方，不是实现方。reviewer 判据：`git diff --stat` 里出现任何 `app/` 路径即 reject。

### 二、跨单元接口约定（`delivery-units.md` §4，逐字抄录）

7. **本包三条硬边界**（全部单元）：不新增 `zhuopin_platform` 依赖、不跨仓库 import、不拷贝参考文件。U7 的 7.1/7.2 把它变成 CI 可查。

8. **每个单元开工前必须 rebase 到最新 main**——本包与 `m1-intake-quality-fixes` 同期在跑，`app/graph/nodes.py` 是两批共同的最热文件。

### 三、工程铁律（`CLAUDE.md`，逐字复制）

9. **LangGraph 恢复时节点从头整个重跑。** 每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。
   **幂等记录与业务写必须在同一个事务里提交**——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。

10. **L3 Agent 全部是无副作用纯函数**，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。

11. **所有 AI 评分必须持久化**：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。

12. **每条 `criterion_score` 必须有 `evidence_ref`**（回指简历原文或面试 turn 的 offset）。`evidence_ref` 为空不允许写入。

13. **`temperature=0`；模型版本优先显式锁定**，禁止 `latest` 类别名。

> **本单元对铁律 1/2 的落点**：`scripts/check_boundary.py` 全部是**只读扫描**，⛔ 不得出现任何文件写入、`subprocess` 的写命令（只允许 `git diff`）、数据库连接。因此本单元**不新增任何 `effect_*` 节点**，铁律 1 的幂等键要求在本单元没有落点——这不是豁免，是"没有副作用所以没有幂等问题"。reviewer 判据可直接 grep：脚本里出现 `open(..., "w")` / `write_text` / `commit()` / `git add` / `git commit` 任一即 reject。

### 四、合规红线（`CLAUDE.md`，逐字复制）

14. **AI 只做排序推荐，不做自动淘汰。** 淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。

15. **模型全部走境内**，简历数据不出境。

16. **绝不用历史录用结果做监督信号**（Amazon 2018 教训），只用显式岗位能力 rubric。

> **本单元的落点**：这三条本单元既不实现也不放宽，但**边界本身就是它们的载体之一**——姊妹仓库 `zhuopin-ai-transformation` 是公开仓库（Public），一旦本仓库跨仓库耦合，简历数据路径与那侧的边界就纠缠在一起。7.1/7.2 守的正是这层隔离。

### 五、单一真源（违反即分叉，分叉的那一侧就是红线的缺口）

17. **禁止的模块名只有一份**：`scripts/check_boundary.py` 的 `FORBIDDEN_MODULE` 常量。⛔ 测试与 CI 里不得再写一遍字面量 `"zhuopin_platform"`——**除了反证 fixture 里那些故意造的违例内容**（那是被检查的输入，不是判据）。

18. **基线 commit 只有一份**：`BASELINE_COMMIT` 常量。⛔ CI 的 `run:` 里不得再写一遍 SHA。

19. **本计划 ⛔ 不改 `06-企业AI转型资产借鉴清单.md` §10.3。** 那一节明确写着「本节记录的是 2026-08-28 这一时刻的人工核验结果，**不替代 CI**」——两者是互补的两条记录，不是同一件事的两个副本。

### 六、层次与文件边界

20. **`scripts/check_boundary.py` ⛔ 不 import `app.*` 任何模块。** 它是仓库级的工具，不是应用代码；反过来 `app/` 也 ⛔ 不 import 它。

21. **本单元只许碰这三个路径**：`scripts/check_boundary.py`（新建）、`tests/test_boundary_guard.py`（新建）、`.github/workflows/ci.yml`（纯追加）。加上收尾的 `openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md`（回勾 7.1/7.2）共四个。**出现第五个路径即 reject。**

---

## File Structure

```
scripts/
  check_boundary.py          ← 新建，约 330 行，纯标准库，无副作用
tests/
  test_boundary_guard.py     ← 新建，约 230 行，28 条用例（其中 20 条是反证）
.github/workflows/
  ci.yml                     ← 纯追加 1 个 step 到 hooks job（零删除行）
openspec/changes/ai-audit-trail-and-outbound-gate/
  tasks.md                   ← 收尾回勾 7.1 / 7.2
```

---

## 已知的落地口径（实现前先读，避免走回头路）

这五条是本计划成文前**实跑量出来的**，不是推测。照着走能省掉三次返工。

**① `pyproject.toml` 不能进 diff 判据——今天它就是非空的。**

```
$ git diff --stat e65f685..HEAD -- requirements.txt pyproject.toml
 pyproject.toml | 6 ++++++
 1 file changed, 6 insertions(+)
```

那 6 行是 U6 往 `[tool.pytest.ini_options]` 加的 `markers = ["compliance: ..."]`——**测试配置，不是依赖**。把 `pyproject.toml` 一起纳入 diff 判据，这条检查从上线第一天就是红的，⇒ 必然在一周内被加白名单或整条注释掉。**恒假的检查和恒真的检查一样没用。** 所以 diff 判据只锁 `requirements.txt`（该文件自 `e65f685` 起 diff 确为零行，已实跑），`pyproject.toml` 的依赖侧改用**结构性检查**（不得声明任何依赖表），那条不会被无关的配置编辑打扰。

**② `app/` 今天 `sys.path` 零命中、`zhuopin_platform` 零命中、`OneDrive` 零命中**（三条 grep 均已实跑）。所以判据可以从严而不产生任何存量误报。

**③ 判据刻意比 `tasks.md` 字面更严的三处**，理由都写进了脚本的 docstring，⛔ 放宽前先读那段：

| # | 字面 | 本计划落地 | 为什么 |
|---|---|---|---|
| a | 禁 `from/import zhuopin_platform` | 禁 `app/` 下**任何位置**出现该 token（含非 `.py` 文件） | 与 §10.3 判据二逐字同源（`grep -rn` 退出码必须是 1）；且 `importlib.import_module("zhuopin_platform")` 不是 import 语句，AST 抓不到 |
| b | 禁 `sys.path` **指向 OneDrive 路径**的注入 | 禁 `app/` 下**任何** `sys.path` 访问 | 字面是合取，而合取可被一个变量绕开：`p = os.environ["SISTER_REPO"]; sys.path.insert(0, p)` 没有任何 OneDrive 字样，照样把姊妹仓库挂进来 |
| c | —— | 另加 `pyproject.toml` 不得声明依赖表 | 补 ① 收缩 diff 判据留下的绕过口 |

**④ `sys.path` 必须用 AST 抓，⛔ 不许用文本 grep。** `app/` 里写一句「⛔ 禁止 sys.path 注入」的注释是完全合理的，文本扫描会把它判成违例——**误报是检查被拆掉的最常见死因**。同理，`ast.parse` 必须包在 `warnings.catch_warnings()` 里：`app/outbound/delivery.py:12` 的 docstring 含 Windows 路径 `C:\apps\...\data\...`，重新 parse 会喷 `SyntaxWarning: "\z" is an invalid escape sequence`（已实测）。那是被扫文件自己的事，检查的输出里混进无关噪音，下一个人就学会了忽略它的输出。

**⑤ CI 步骤挂 `hooks` job，不挂 `test` job。** 三个理由：`hooks` 的 checkout 已经是 `fetch-depth: 0`（`test` 是默认的 1，取不到 `e65f685` 这个 object，会以 `fatal: bad object` 失败）；`hooks` 跑 ubuntu-latest 比 windows 便宜；且 ⛔ 不碰 `test` job 里 U6 刚定型的两步。`hooks` job 已有 `actions/setup-python@v5`（3.14），`python` 可直接用，**无需新增任何安装步骤**。

---

## Tasks

**3 个 Task，严格顺序**：7.1 判据 → 7.2 判据 → 接进 CI 并回勾。每个 Task 先写测试再写实现，⛔ 不许倒过来。

---

### Task 1: `app/` 边界扫描器与其反证（7.1）

**做什么**：新建 `scripts/check_boundary.py`，落地 7.1 的三条判据（`zhuopin_platform` token、姊妹仓库路径特征、`sys.path` 注入），并用**故意造违例**的测试证明它们真的会红。

**先写测试，再写实现。** 这一步的顺序不是形式：先写实现的话，你会照着实现去写测试，测出来的必然是恒真的那一半。

- [ ] **1.1** 新建 `tests/test_boundary_guard.py`，内容如下（**⛔ 此时 `scripts/check_boundary.py` 还不存在，跑一次确认是 `ModuleNotFoundError` 而不是 pass**）

```python
"""边界守护的反证（`tasks.md` 7.1 / 7.2）。

⚠️ **这个文件才是 7.1/7.2 的价值所在。** 真实仓库里这两条检查永远是绿的
（`app/` 下 `zhuopin_platform` 零命中、依赖 diff 零行），而"零命中"同时兼容
两种解释：**边界守住了**，和**检查根本没生效**。只有"故意造违例 → 必须失败"
能把这两种解释分开。这条判据 U6 的 6.7 已经确立过一次，同一形状。

⛔ 不要把这里的任何一条反证删掉换成"跑一遍真实仓库就够了"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_boundary import Violation, scan_app_tree


CLEAN_PYPROJECT = """\
[project]
name = "fixture"
version = "0.0.1"
requires-python = ">=3.14,<3.15"

[tool.pytest.ini_options]
pythonpath = ["."]
"""

CLEAN_REQUIREMENTS = "fastapi==0.115.6\npytest==8.3.4\n"


def make_repo(root: Path, app_files: dict[str, str] | None = None, **overrides: str) -> Path:
    """造一个最小的假仓库：`app/` + 两个依赖声明文件。"""
    (root / "app").mkdir(parents=True, exist_ok=True)
    for name, body in (app_files or {"__init__.py": "", "main.py": "X = 1\n"}).items():
        target = root / "app" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    (root / "requirements.txt").write_text(
        overrides.get("requirements", CLEAN_REQUIREMENTS), encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        overrides.get("pyproject", CLEAN_PYPROJECT), encoding="utf-8"
    )
    return root


def rules(violations: list[Violation]) -> set[str]:
    return {v.rule for v in violations}



# ── 基线：干净的 app/ 必须全过 ────────────────────────────────────────────


def test_clean_app_tree_has_no_violations(tmp_path: Path) -> None:
    assert scan_app_tree(make_repo(tmp_path)) == []


# ── 反证一（7.1）：import zhuopin_platform 必须被抓 ────────────────────────


@pytest.mark.parametrize(
    "snippet",
    [
        "import zhuopin_platform\n",
        "from zhuopin_platform import audit\n",
        "from zhuopin_platform.audit.sinks import JsonlSink\n",
        "import zhuopin_platform.audit as platform_audit\n",
        'mod = importlib.import_module("zhuopin_platform.audit")\n',
    ],
    ids=["import", "from-import", "deep-from", "import-as", "importlib-string"],
)
def test_zhuopin_platform_import_is_detected(tmp_path: Path, snippet: str) -> None:
    """opener 指定的反证：故意塞一行 import 进临时文件，检查必须失败。

    `importlib-string` 那条是裸 token 扫描换来的——AST 只看 import 语句的话，
    动态 import 会整条溜过去。
    """
    root = make_repo(tmp_path, app_files={"__init__.py": "", "leak.py": snippet})
    violations = scan_app_tree(root)
    assert violations, f"未抓到违例：{snippet!r}（检查恒真＝检查没生效）"
    assert "7.1-module" in rules(violations)
    assert violations[0].path == "app/leak.py"
    assert violations[0].line == 1


def test_zhuopin_platform_in_non_python_file_is_detected(tmp_path: Path) -> None:
    """`index.html` 里的一行 fetch 也能跨仓库——扫描不能只看 .py。"""
    root = make_repo(
        tmp_path,
        app_files={
            "__init__.py": "",
            "web/index.html": '<script>fetch("/zhuopin_platform/api")</script>\n',
        },
    )
    assert "7.1-module" in rules(scan_app_tree(root))


# ── 反证二（7.1）：sys.path 注入必须被抓 ──────────────────────────────────


@pytest.mark.parametrize(
    "snippet",
    [
        'import sys\nsys.path.insert(0, "/Users/x/OneDrive/Projects/企业AI转型")\n',
        'import sys\nsys.path.append(SISTER_REPO)\n',
        "import sys\nsys.path += [SISTER_REPO]\n",
        "from sys import path\npath.insert(0, SISTER_REPO)\n",
    ],
    ids=["onedrive-literal", "append-variable", "augmented-assign", "from-sys-import"],
)
def test_sys_path_injection_is_detected(tmp_path: Path, snippet: str) -> None:
    """后三条不含任何 OneDrive 字样。

    合取式判据（"sys.path **且** 指向 OneDrive"）在这三条上全部放行，而它们
    照样把姊妹仓库挂进 `app/`。这就是为什么判据收紧成"app/ 下任何 sys.path
    都算违例"。
    """
    root = make_repo(tmp_path, app_files={"__init__.py": "", "inject.py": snippet})
    assert "7.1-syspath" in rules(scan_app_tree(root))


def test_sys_path_in_comment_is_not_flagged(tmp_path: Path) -> None:
    """误报是检查被拆掉的最常见死因：注释里提一句 sys.path 必须放行。"""
    root = make_repo(
        tmp_path,
        app_files={
            "__init__.py": "",
            "ok.py": "# ⛔ 禁止 sys.path 注入，见 tasks.md 7.1\nVALUE = 1\n",
        },
    )
    assert "7.1-syspath" not in rules(scan_app_tree(root))


def test_onedrive_path_marker_is_detected(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        app_files={
            "__init__.py": "",
            "cfg.py": 'REF = "~/Library/CloudStorage/OneDrive-Personal/Projects"\n',
        },
    )
    assert "7.1-path" in rules(scan_app_tree(root))


def test_syntax_error_file_does_not_crash_the_check(tmp_path: Path) -> None:
    """AST 解析失败不能让整条检查抛异常——异常穿透到 CI 就是一个绿色的谎。"""
    root = make_repo(tmp_path, app_files={"__init__.py": "", "broken.py": "def (\n"})
    assert scan_app_tree(root) == []
```

- [ ] **1.2** 跑一次，确认红在"模块不存在"上：

```bash
python -m pytest tests/test_boundary_guard.py -q
# 预期：collection error —— ModuleNotFoundError: No module named 'scripts.check_boundary'
```

- [ ] **1.3** 新建 `scripts/check_boundary.py`，内容如下。

> ⚠️ **import 块与常量块一次写全**（`subprocess` / `tomllib` / `Callable` / `Sequence` / `DEPENDENCY_FILES` / `DIFF_GUARDED_FILES` / `BASELINE_COMMIT` 在本 Task 里还没有使用方，Task 2 会立刻用上）。
> ⛔ **不要为了"消除未使用 import"把它们删掉**——Task 2 第一件事就是加回来，那是纯粹的来回churn。reviewer 看到这几行不算违例。

```python
#!/usr/bin/env python3
"""边界守护 —— `tasks.md` 7.1 / 7.2 的机器化。

本包（`ai-audit-trail-and-outbound-gate`）的三条硬边界写在
`delivery-units.md` §4 约定 7：**不新增 `zhuopin_platform` 依赖、不跨仓库
import、不拷贝参考文件**。`06-企业AI转型资产借鉴清单.md` §10.3 在 2026-08-28
用两条命令人工核验过一次，但那只是**那一刻**的结论——本脚本把同样的两条判据
变成 CI 每次都跑的机器检查。

⚠️ 判据刻意比 tasks.md 的字面更严，三处，都在下面各自的函数 docstring 里
说明了理由。放宽任何一处之前先读那段。

用法（退出码 0 = 全过，1 = 有违例，2 = 用法错误）：

    python scripts/check_boundary.py
    python scripts/check_boundary.py --root /path/to/repo --skip-diff
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
import tomllib
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

# ── 判据常量（改这里，⛔ 不要把字面量散到各个函数里）────────────────────────

FORBIDDEN_MODULE = "zhuopin_platform"

# 姊妹项目 `企业AI转型` 的路径特征。2026-08-26 它已迁 GitHub，旧的 OneDrive
# 本地路径作废（CLAUDE.md「已迁 GitHub，只读参考」），任何指回去的路径都是
# 死链 + 跨仓库耦合，两条都不许有。
FORBIDDEN_PATH_MARKERS: tuple[str, ...] = (
    "OneDrive",
    "企业AI转型",
    "zhuopin-ai-transformation",
)

# 依赖声明文件。两个都查内容，只有 requirements.txt 查 diff——理由见
# `check_dependency_diff()` 的 docstring。
DEPENDENCY_FILES: tuple[str, ...] = ("requirements.txt", "pyproject.toml")
DIFF_GUARDED_FILES: tuple[str, ...] = ("requirements.txt",)

# 本变更包的起点＝立项 commit `e65f685 docs: AI 评分留痕与外发人工确认门禁——立项`
# （2026-08-14）。写死是刻意的：写成 `origin/main` 会随 main 前进而漂移，
# 「本变更没加依赖」这句话就失去了固定的参照物。
BASELINE_COMMIT = "e65f6857fe255634d49a3e8696b1dba0f5facbec"

SKIP_DIR_NAMES = frozenset({"__pycache__", ".git", ".pytest_cache", ".mypy_cache"})


@dataclass(frozen=True)
class Violation:
    """一条违例。`line` 为 0 表示该规则不针对具体行。"""

    rule: str
    path: str
    line: int
    message: str

    def render(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"[{self.rule}] {where}: {self.message}"


def _iter_files(base: Path) -> Iterable[Path]:
    if not base.exists():
        return
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if SKIP_DIR_NAMES & set(path.parts):
            continue
        yield path


def _read_text(path: Path) -> str | None:
    """读不成 UTF-8 文本的（图片等二进制）返回 None，由调用方跳过。"""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _sys_path_mutation_lines(source: str, filename: str = "<app>") -> list[int]:
    """AST 找 `sys.path` 的写入点。

    ⚠️ 用 AST 不用 grep：`app/` 里出现一句解释这条规则的注释或 docstring
    是完全合理的，文本扫描会把它判成违例，于是这条检查第一次误报之后就会被
    人放宽——**误报是检查被拆掉的最常见死因**。AST 只看真实的属性访问。

    ⚠️ `catch_warnings` 不是可有可无的：`app/outbound/delivery.py:12` 的
    docstring 里有一条 Windows 路径 `C:\\apps\\...\\data\\...`，重新 parse
    会抛 `SyntaxWarning: "\\z" is an invalid escape sequence`。那是被扫文件
    自己的事，⛔ 不该由这条边界检查在 CI 日志里再喊一遍——检查的输出里混进
    与检查无关的噪音，下一个人就学会了忽略它的输出。
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []

    lines: list[int] = []
    for node in ast.walk(tree):
        # import sys; sys.path.insert(...) / sys.path += [...] / sys.path = [...]
        if isinstance(node, ast.Attribute) and node.attr == "path":
            value = node.value
            if isinstance(value, ast.Name) and value.id == "sys":
                lines.append(node.lineno)
        # from sys import path
        elif isinstance(node, ast.ImportFrom) and node.module == "sys":
            if any(alias.name == "path" for alias in node.names):
                lines.append(node.lineno)
    return sorted(set(lines))


def scan_app_tree(root: Path) -> list[Violation]:
    """7.1：`app/` 下禁止 `zhuopin_platform`，禁止 `sys.path` 注入。

    三处刻意从严，都不是笔误：

    1. **`zhuopin_platform` 按裸 token 扫全文，不只扫 import 语句。** 判据与
       `06-企业AI转型资产借鉴清单.md` §10.3 判据二逐字同源
       （`grep -rn "zhuopin_platform" app/` 退出码必须是 1）。副作用是
       `app/` 里连提一句这个名字的注释都不许写——接受，注释该写在
       `docs/` 或本脚本里。

    2. **`app/` 下任何 `sys.path` 写入都算违例，不只是"指向 OneDrive 的"。**
       tasks.md 7.1 的字面是二者的合取，但合取可以被一个变量绕开
       （`p = os.environ["SISTER_REPO"]; sys.path.insert(0, p)` —— 没有任何
       OneDrive 字样，照样把姊妹仓库挂进来）。`app/` 今天 `sys.path` 零命中
       （已实测），从严不会产生任何存量误报，收紧的成本是零。

    3. **扫全部文件不只 `.py`。** `index.html` 里的一行 fetch 也能跨仓库。
    """
    violations: list[Violation] = []
    app_dir = root / "app"

    for path in _iter_files(app_dir):
        text = _read_text(path)
        if text is None:
            continue
        rel = path.relative_to(root).as_posix()

        for lineno, line in enumerate(text.splitlines(), start=1):
            if FORBIDDEN_MODULE in line:
                violations.append(
                    Violation(
                        rule="7.1-module",
                        path=rel,
                        line=lineno,
                        message=(
                            f"出现 {FORBIDDEN_MODULE!r}。本包硬边界：不新增该依赖、"
                            "不跨仓库 import（delivery-units.md §4 约定 7）"
                        ),
                    )
                )
            for marker in FORBIDDEN_PATH_MARKERS:
                if marker in line:
                    violations.append(
                        Violation(
                            rule="7.1-path",
                            path=rel,
                            line=lineno,
                            message=(
                                f"出现姊妹仓库路径特征 {marker!r}。"
                                "企业AI转型已迁 GitHub 只读参考，⛔ 不跨仓库引用"
                            ),
                        )
                    )

        if path.suffix == ".py":
            for lineno in _sys_path_mutation_lines(text, filename=rel):
                violations.append(
                    Violation(
                        rule="7.1-syspath",
                        path=rel,
                        line=lineno,
                        message=(
                            "app/ 下出现 sys.path 访问。⛔ 禁止任何 sys.path 注入"
                            "——合取式判据（仅禁 OneDrive 字样）可被一个变量绕开"
                        ),
                    )
                )

    return violations
```

- [ ] **1.4** 跑测试，14 条必须全绿：

```bash
python -m pytest tests/test_boundary_guard.py -q
# 预期：14 passed
```

- [ ] **1.5** **反证的反证**——手工确认这些用例真的绑在实现上，而不是碰巧绿。逐条改坏实现，对应用例必须红：

```bash
# ① 把 token 扫描改成只扫 import 语句开头（模拟"看起来更精确"的重构）
#    scan_app_tree 里 `if FORBIDDEN_MODULE in line:` → `if line.startswith(f"import {FORBIDDEN_MODULE}"):`
python -m pytest tests/test_boundary_guard.py -q
# 期望：FAILED test_zhuopin_platform_import_is_detected[from-import]
#           / [deep-from] / [importlib-string]
#       FAILED test_zhuopin_platform_in_non_python_file_is_detected

# ② 把 sys.path 判据改回"合取式"（只在同一行出现 OneDrive 才算）
#    _sys_path_mutation_lines 的结果再按 `"OneDrive" in source` 过滤一次
python -m pytest tests/test_boundary_guard.py -q
# 期望：FAILED test_sys_path_injection_is_detected[append-variable]
#           / [augmented-assign] / [from-sys-import]

# ③ 把 SyntaxError 的兜底去掉（`except SyntaxError: return []` 删掉）
python -m pytest tests/test_boundary_guard.py -q
# 期望：FAILED test_syntax_error_file_does_not_crash_the_check

# ④ 把 sys.path 判据改成文本 grep（`"sys.path" in line`）
python -m pytest tests/test_boundary_guard.py -q
# 期望：FAILED test_sys_path_in_comment_is_not_flagged
#       ——这条守的是"误报"，而误报是检查被拆掉的最常见死因

# 四条都验完，git checkout scripts/check_boundary.py 还原
```

- [ ] **1.6** 全量套件回归，确认新文件没有污染既有用例：

```bash
python -m pytest -q
# 预期：既有 814 passed, 1 skipped 之上多出本 Task 的 14 条 → 828 passed, 1 skipped
```

**验收**：`python -m pytest tests/test_boundary_guard.py -q` 14 passed；1.5 的四条改坏各自红在预期用例上；`git status` 只多出 `scripts/check_boundary.py` 与 `tests/test_boundary_guard.py` 两个新文件。

---

### Task 2: 依赖边界与 diff 判据及其反证（7.2）

**做什么**：在同一个脚本里补齐 7.2 的三条判据——依赖声明文件不含 `zhuopin_platform`、`pyproject.toml` 不声明任何依赖表、`requirements.txt` 自变更包立项 commit 起 diff 为零行——外加 `run_all()` 与 CLI 入口。

**这个 Task 最容易写错的一处**：真实仓库里 diff 判据**永远是绿的**，所以只靠真实仓库测不出它会不会红。`check_dependency_diff()` 的 `runner` 参数就是为反证留的注入口，⛔ 不许把它去掉改成直接调 `subprocess.run`。

- [ ] **2.1** 先补测试。把 `tests/test_boundary_guard.py` 里 `from __future__ import annotations` 起、到 `# ── 基线：干净的 app/` 之前的整段**替换**成下面这段（新增 `subprocess` / `sys` / `REPO_ROOT` / `fake_git`，并把 import 补全）：

```python
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Sequence

import pytest

from scripts.check_boundary import (
    BASELINE_COMMIT,
    Violation,
    check_dependency_diff,
    run_all,
    scan_app_tree,
    scan_dependency_files,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

CLEAN_PYPROJECT = """\
[project]
name = "fixture"
version = "0.0.1"
requires-python = ">=3.14,<3.15"

[tool.pytest.ini_options]
pythonpath = ["."]
"""

CLEAN_REQUIREMENTS = "fastapi==0.115.6\npytest==8.3.4\n"


def make_repo(root: Path, app_files: dict[str, str] | None = None, **overrides: str) -> Path:
    """造一个最小的假仓库：`app/` + 两个依赖声明文件。"""
    (root / "app").mkdir(parents=True, exist_ok=True)
    for name, body in (app_files or {"__init__.py": "", "main.py": "X = 1\n"}).items():
        target = root / "app" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    (root / "requirements.txt").write_text(
        overrides.get("requirements", CLEAN_REQUIREMENTS), encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        overrides.get("pyproject", CLEAN_PYPROJECT), encoding="utf-8"
    )
    return root


def fake_git(stdout: str = "", returncode: int = 0, stderr: str = ""):
    def runner(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(args), returncode, stdout, stderr)

    return runner


def rules(violations: list[Violation]) -> set[str]:
    return {v.rule for v in violations}
```

- [ ] **2.2** 在 `tests/test_boundary_guard.py` **文件末尾追加**下面这段：

```python
# ── 基线：干净的树必须全过 ────────────────────────────────────────────────


def test_clean_tree_has_no_violations(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    assert run_all(root, skip_diff=True, runner=fake_git()) == []


# ── 反证三（7.2）：依赖声明里的 zhuopin_platform 必须被抓 ─────────────────


def test_zhuopin_platform_in_requirements_is_detected(tmp_path: Path) -> None:
    root = make_repo(tmp_path, requirements=CLEAN_REQUIREMENTS + "zhuopin_platform==1.0.0\n")
    violations = scan_dependency_files(root)
    assert "7.2-module" in rules(violations)


def test_zhuopin_platform_in_pyproject_is_detected(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        pyproject=CLEAN_PYPROJECT + '\n[tool.zhuopin_platform]\nenabled = true\n',
    )
    assert "7.2-module" in rules(scan_dependency_files(root))


def test_missing_dependency_file_is_a_violation(tmp_path: Path) -> None:
    """删掉 requirements.txt 不能等于"没有违例"。"""
    root = make_repo(tmp_path)
    (root / "requirements.txt").unlink()
    assert "7.2-missing" in rules(scan_dependency_files(root))


# ── 反证四（7.2）：pyproject 声明依赖必须被抓 ─────────────────────────────


@pytest.mark.parametrize(
    "table",
    [
        '[project]\nname = "f"\nversion = "0"\ndependencies = ["zhuopin-sdk"]\n',
        '[project]\nname = "f"\nversion = "0"\n[project.optional-dependencies]\ndev = ["x"]\n',
        '[project]\nname = "f"\nversion = "0"\n[dependency-groups]\ndev = ["x"]\n',
        '[project]\nname = "f"\nversion = "0"\n[tool.poetry.dependencies]\nx = "^1"\n',
    ],
    ids=["project", "optional", "groups", "poetry"],
)
def test_pyproject_dependency_table_is_detected(tmp_path: Path, table: str) -> None:
    """diff 判据只锁 requirements.txt，这条堵住由此产生的绕过口。"""
    root = make_repo(tmp_path, pyproject=table)
    assert "7.2-pyproject" in rules(scan_dependency_files(root))


def test_broken_pyproject_is_a_violation(tmp_path: Path) -> None:
    root = make_repo(tmp_path, pyproject="[project\nname =\n")
    assert "7.2-pyproject" in rules(scan_dependency_files(root))


# ── 反证五（7.2）：非空 diff 必须被抓 ─────────────────────────────────────


def test_non_empty_dependency_diff_is_detected(tmp_path: Path) -> None:
    """真实仓库里这条永远绿，只靠真实仓库测不出它会不会红。"""
    root = make_repo(tmp_path)
    violations = check_dependency_diff(
        root, runner=fake_git(stdout=" requirements.txt | 1 +\n 1 file changed, 1 insertion(+)\n")
    )
    assert "7.2-diff" in rules(violations)
    assert "requirements.txt" in violations[0].message


def test_empty_dependency_diff_passes(tmp_path: Path) -> None:
    assert check_dependency_diff(make_repo(tmp_path), runner=fake_git(stdout="\n")) == []


def test_git_failure_is_a_violation_not_a_pass(tmp_path: Path) -> None:
    """取不到基线 commit（CI 浅克隆）必须红。

    ⛔ 不许把 git 失败当成"没有 diff"——那正是 CI 上最容易出现的、
    看起来是绿色的静默失效。
    """
    violations = check_dependency_diff(
        make_repo(tmp_path),
        runner=fake_git(returncode=128, stderr="fatal: bad object"),
    )
    assert "7.2-diff" in rules(violations)
    assert "fetch-depth" in violations[0].message


# ── 真实仓库：今天必须是绿的 ──────────────────────────────────────────────


def test_real_repository_passes_boundary_guard() -> None:
    """反证证明"会红"，这条证明"今天不该红"。两条都要有。"""
    violations = run_all(REPO_ROOT, baseline=BASELINE_COMMIT)
    assert violations == [], "\n".join(v.render() for v in violations)


def test_cli_exits_1_on_violation(tmp_path: Path) -> None:
    """CI 靠退出码判成败，退出码本身要有测试。"""
    root = make_repo(tmp_path, app_files={"__init__.py": "", "leak.py": "import zhuopin_platform\n"})
    result = subprocess.run(
        [sys.executable, "scripts/check_boundary.py", "--root", str(root), "--skip-diff"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "7.1-module" in result.stderr


def test_cli_exits_0_on_clean_tree(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    result = subprocess.run(
        [sys.executable, "scripts/check_boundary.py", "--root", str(root), "--skip-diff"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
```

- [ ] **2.3** 跑一次，确认红在"函数不存在"上：

```bash
python -m pytest tests/test_boundary_guard.py -q
# 预期：collection error —— ImportError: cannot import name 'check_dependency_diff'
#       from 'scripts.check_boundary'
```

- [ ] **2.4** 在 `scripts/check_boundary.py` 的 `scan_app_tree()` 之后**追加**下面这段（文件其余部分一个字不动）：

```python
def scan_dependency_files(root: Path) -> list[Violation]:
    """7.2 前半：`requirements.txt` 与 `pyproject.toml` 不含 `zhuopin_platform`。

    另加一条 `pyproject.toml` 的**结构性**检查：不得声明任何运行时依赖表。
    理由见 `check_dependency_diff()` —— diff 判据只锁 `requirements.txt`，
    若不补这条，往 `[project] dependencies` 里加一行依赖就能整条溜过去。
    """
    violations: list[Violation] = []

    for name in DEPENDENCY_FILES:
        path = root / name
        if not path.exists():
            violations.append(
                Violation(
                    rule="7.2-missing",
                    path=name,
                    line=0,
                    message="依赖声明文件不存在，无法核验边界（被删除或改名了？）",
                )
            )
            continue
        text = _read_text(path) or ""
        for lineno, line in enumerate(text.splitlines(), start=1):
            if FORBIDDEN_MODULE in line:
                violations.append(
                    Violation(
                        rule="7.2-module",
                        path=name,
                        line=lineno,
                        message=f"依赖声明里出现 {FORBIDDEN_MODULE!r}",
                    )
                )

    violations.extend(_scan_pyproject_dependency_tables(root))
    return violations


def _scan_pyproject_dependency_tables(root: Path) -> list[Violation]:
    path = root / "pyproject.toml"
    if not path.exists():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        return [
            Violation(
                rule="7.2-pyproject",
                path="pyproject.toml",
                line=0,
                message=f"解析失败，无法核验依赖表：{exc}",
            )
        ]

    project = data.get("project", {})
    tables: list[tuple[str, object]] = [
        ("project.dependencies", project.get("dependencies")),
        ("project.optional-dependencies", project.get("optional-dependencies")),
        ("dependency-groups", data.get("dependency-groups")),
        (
            "tool.poetry.dependencies",
            data.get("tool", {}).get("poetry", {}).get("dependencies"),
        ),
    ]

    violations: list[Violation] = []
    for label, value in tables:
        if value:
            violations.append(
                Violation(
                    rule="7.2-pyproject",
                    path="pyproject.toml",
                    line=0,
                    message=(
                        f"{label} 非空。本仓库的依赖真源是 requirements.txt，"
                        "pyproject.toml ⛔ 不声明依赖——否则 diff 判据只锁 "
                        "requirements.txt 就留下一个绕过口"
                    ),
                )
            )
    return violations


def _default_runner(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def check_dependency_diff(
    root: Path,
    baseline: str = BASELINE_COMMIT,
    runner: Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]] = _default_runner,
) -> list[Violation]:
    """7.2 后半：本变更的依赖文件 diff 必须为空。

    **判据只锁 `requirements.txt`，这是实测后的刻意收缩，不是漏写。**
    `pyproject.toml` 自立项以来确有 6 行 diff——U6 往 `[tool.pytest.ini_options]`
    加了 `markers` 段（合规断言的 pytest 标记）。那是测试配置，不是依赖。把
    `pyproject.toml` 一起纳入 diff 判据，这条检查从上线第一天就是红的，
    ⇒ 必然被加白名单或整条注释掉。**恒假的检查和恒真的检查一样没用。**
    `pyproject.toml` 的依赖侧改由 `_scan_pyproject_dependency_tables()`
    做结构性检查，那条不会被无关的配置编辑打扰。

    `runner` 参数是为了测试能注入非空 diff 做反证——真实仓库里这条永远是绿的，
    只靠真实仓库测不出它会不会红。
    """
    args = ["git", "diff", "--stat", f"{baseline}..HEAD", "--", *DIFF_GUARDED_FILES]
    result = runner(args, root)

    if result.returncode != 0:
        return [
            Violation(
                rule="7.2-diff",
                path=" ".join(DIFF_GUARDED_FILES),
                line=0,
                message=(
                    f"`{' '.join(args)}` 退出码 {result.returncode}："
                    f"{result.stderr.strip() or '无 stderr'}。"
                    "CI 上最常见的原因是 checkout 深度不足取不到基线 commit，"
                    "该 job 需要 fetch-depth: 0"
                ),
            )
        ]

    if result.stdout.strip():
        return [
            Violation(
                rule="7.2-diff",
                path=" ".join(DIFF_GUARDED_FILES),
                line=0,
                message=(
                    f"自基线 {baseline[:7]} 起依赖文件有改动，本变更不得新增依赖：\n"
                    + result.stdout.rstrip()
                ),
            )
        ]

    return []


def run_all(
    root: Path,
    baseline: str = BASELINE_COMMIT,
    skip_diff: bool = False,
    runner: Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]] = _default_runner,
) -> list[Violation]:
    violations = scan_app_tree(root) + scan_dependency_files(root)
    if not skip_diff:
        violations += check_dependency_diff(root, baseline=baseline, runner=runner)
    return violations


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="边界守护：禁止 zhuopin_platform 依赖与跨仓库注入（tasks.md 7.1/7.2）"
    )
    parser.add_argument("--root", default=".", help="仓库根目录，默认当前目录")
    parser.add_argument(
        "--baseline", default=BASELINE_COMMIT, help="依赖 diff 的基线 commit"
    )
    parser.add_argument(
        "--skip-diff",
        action="store_true",
        help="跳过 git diff 判据（无 git 历史的临时目录里用）",
    )
    ns = parser.parse_args(argv)

    root = Path(ns.root).resolve()
    violations = run_all(root, baseline=ns.baseline, skip_diff=ns.skip_diff)

    if violations:
        print(f"边界守护：{len(violations)} 条违例", file=sys.stderr)
        for v in violations:
            print("  " + v.render(), file=sys.stderr)
        print(
            "\n本包硬边界见 delivery-units.md §4 约定 7 与 CLAUDE.md"
            "「已迁 GitHub，只读参考」：读取参考 + 在本仓库自建实现，"
            "⛔ 不 clone、不跨仓库引用、不拷贝文件。",
            file=sys.stderr,
        )
        return 1

    print(f"边界守护：通过（root={root}，基线={ns.baseline[:7]}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **2.5** 跑测试与 CLI：

```bash
python -m pytest tests/test_boundary_guard.py -q
# 预期：29 passed

python scripts/check_boundary.py
# 预期（stdout，退出码 0）：
#   边界守护：通过（root=/Users/paulshao/Projects/HumanResource，基线=e65f685）
```

- [ ] **2.6** **对真实仓库做一次真反证**——不是在 tmp_path 里，而是真的往 `app/` 塞一行再删掉。tmp_path 的用例证明"函数会红"，这条证明"接到真实仓库上也会红"：

```bash
echo "import zhuopin_platform" > app/_boundary_probe.py
python scripts/check_boundary.py; echo "exit=$?"
# 预期（stderr，退出码 1）：
#   边界守护：1 条违例
#     [7.1-module] app/_boundary_probe.py:1: 出现 'zhuopin_platform'。本包硬边界：不新增该依赖、不跨仓库 import（delivery-units.md §4 约定 7）
#   exit=1
rm -f app/_boundary_probe.py
python scripts/check_boundary.py; echo "exit=$?"    # 预期：通过，exit=0
```

⛔ **`rm` 那一步不许忘。** 忘了它就会被下一个 `git add` 带进提交，而这个文件的存在本身就是它要拦的东西。

- [ ] **2.7** **改坏实现的反证**，逐条确认判据绑在实现上：

```bash
# ① 把 diff 判据的 returncode 检查删掉（git 失败当成"没有 diff"）
python -m pytest tests/test_boundary_guard.py -q
# 期望：FAILED test_git_failure_is_a_violation_not_a_pass
#       ——这正是 CI 上最容易出现的、看起来是绿色的静默失效

# ② 把 pyproject 的结构性检查整个删掉（只留 token 扫描）
python -m pytest tests/test_boundary_guard.py -q
# 期望：FAILED test_pyproject_dependency_table_is_detected[project] / [optional]
#           / [groups] / [poetry]
#       FAILED test_broken_pyproject_is_a_violation

# ③ 把 DIFF_GUARDED_FILES 改成含 pyproject.toml
python scripts/check_boundary.py; echo "exit=$?"
# 期望：exit=1，报 pyproject.toml | 6 ++++++
#       ——这就是「已知的落地口径 ①」说的那条恒假检查，看一眼再改回来

# 三条都验完，git checkout scripts/check_boundary.py 还原
```

- [ ] **2.8** 全量套件回归：

```bash
python -m pytest -q
# 预期：843 passed, 1 skipped（既有 814 + 本文件 29）
```

**验收**：29 passed；`python scripts/check_boundary.py` 退出码 0；2.6 的真反证退出码 1 且 `app/_boundary_probe.py` 已删除；`git status` 里 `app/` 零改动。

---

### Task 3: CI 追加步骤、端到端验证与回勾

**做什么**：把脚本挂进 CI，让"人为破坏这条边界"被机器挡下；然后回勾 `tasks.md` 的 7.1 / 7.2。

**⛔ 这个 Task 的唯一硬约束**：`.github/workflows/ci.yml` 的 diff 必须是**纯新增**。`test` job 的 6 个步骤、`hooks` job 现有 4 个步骤、`openspec` job 全部——一个字不许动。

- [ ] **3.1** 在 `.github/workflows/ci.yml` 的 `hooks` job 里，**紧接在**

```yaml
      - name: 全仓库跑一遍钩子
        run: pre-commit run --all-files --show-diff-on-failure
```

之后追加下面这一步（注意缩进是 6 个空格，与同级步骤对齐）：

```yaml

      # 本包三条硬边界（delivery-units.md §4 约定 7）的机器化：不新增
      # zhuopin_platform 依赖、不跨仓库 import、不拷贝参考文件。
      # 挂在 hooks job 而不是 test job，两个理由：
      #   ① 这个 job 的 checkout 已经是 fetch-depth: 0 —— 7.2 的依赖 diff
      #      要按变更包立项 commit e65f685 比，浅克隆取不到那个 object，
      #      会以 `fatal: bad object` 失败；
      #   ② ⛔ 不动 test job 的 pytest / 合规断言两个步骤（U6 刚定型）。
      #
      # ⚠️ 这一步在健康的仓库里恒绿。它的效力不来自绿色，来自
      # tests/test_boundary_guard.py 里那些"造违例 → 必须失败"的反证。
      # ⛔ 不要把反证删掉只留这一步。
      - name: 边界守护（禁止 zhuopin_platform 与跨仓库注入）
        run: python scripts/check_boundary.py
```

- [ ] **3.2** 确认 YAML 仍可解析，且**两个既有 job 的步骤列表一字未变**：

```bash
python - <<'PY'
import yaml, pathlib
data = yaml.safe_load(pathlib.Path(".github/workflows/ci.yml").read_text(encoding="utf-8"))
name = lambda steps: [s.get("name") or s.get("uses") for s in steps]
print("test :", name(data["jobs"]["test"]["steps"]))
print("hooks:", name(data["jobs"]["hooks"]["steps"]))
print("fetch-depth:", data["jobs"]["hooks"]["steps"][0].get("with"))
PY
# 预期：
#   test : ['actions/checkout@v4', 'actions/setup-python@v5', '装依赖',
#           '版本留痕（排查版本偏斜时要看这一段）', 'pytest', '合规断言（红线守护）']
#   hooks: ['actions/checkout@v4', 'actions/setup-python@v5', '装 pre-commit',
#           '全仓库跑一遍钩子', '边界守护（禁止 zhuopin_platform 与跨仓库注入）']
#   fetch-depth: {'fetch-depth': 0}
```

- [ ] **3.3** 确认 diff 是纯新增（零删除行）：

```bash
git diff --numstat .github/workflows/ci.yml
# 预期：<新增行数>	0	.github/workflows/ci.yml
#       第二列必须是 0。不是 0 ＝ 改到了既有内容 ＝ 违反 Global Constraint 1
```

- [ ] **3.4** 跑一遍 pre-commit（`hooks` job 的前一步，本地先过一遍，别让 CI 替你发现 YAML 缩进错）：

```bash
pre-commit run --all-files --show-diff-on-failure
# 预期：check-yaml Passed；其余钩子 Passed 或 Skipped
```

- [ ] **3.5** 回勾 `openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md` 的 7.1 与 7.2，写法与 7.3–7.6 已有的回勾格式一致（`- [x]` + 落点 + 一句实测证据）：

```markdown
- [x] 7.1 CI 检查：`app/` 下禁止出现 `from zhuopin_platform` / `import zhuopin_platform`；禁止 `sys.path` 指向 OneDrive 路径的注入 → **`scripts/check_boundary.py`**（`.github/workflows/ci.yml` 的 `hooks` job 追加一步调用。判据比字面从严两处：token 扫全文而非只扫 import 语句、`app/` 下任何 `sys.path` 访问均判违例——理由见脚本 `scan_app_tree()` docstring。反证 `tests/test_boundary_guard.py` 14 条）
- [x] 7.2 CI 检查：`requirements.txt` 与 `pyproject.toml` 不含 `zhuopin_platform`；本变更的依赖文件 diff 必须为空 → **同上脚本**（diff 判据写死基线 `e65f685`，**只锁 `requirements.txt`**：`pyproject.toml` 自立项起有 6 行 U6 加的 pytest `markers`，纳入即恒假；`pyproject.toml` 的依赖侧改用结构性检查「不得声明任何依赖表」。反证 15 条）
```

- [ ] **3.6** 收尾自查：

```bash
git status --short
# 预期只有这四条（别人的改动照常出现，⛔ 不要顺手提交）：
#   ?? scripts/check_boundary.py
#   ?? tests/test_boundary_guard.py
#   M  .github/workflows/ci.yml
#   M  openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md

python -m pytest -q                      # 零 failed、零 error
python scripts/check_boundary.py         # 退出码 0
```

- [ ] **3.7** 提交。**⛔ 只 `git add` 上面这四个路径**（并发协议，CLAUDE.md「多指令并行的硬规则」）：

```bash
git add scripts/check_boundary.py tests/test_boundary_guard.py \
        .github/workflows/ci.yml \
        openspec/changes/ai-audit-trail-and-outbound-gate/tasks.md
git commit -m "feat(ci): U7 边界守护——禁止 zhuopin_platform 依赖与跨仓库注入（7.1/7.2）"
git push origin main
# push 被拒 → git pull --rebase --autostash origin main 后重试，最多 3 次
```

**验收**：`grep -c '^- \[ \] 7\.' tasks.md` 为 0（第 7 章全勾）；CI 上 `hooks` job 出现名为「边界守护」的绿色步骤；`git diff --numstat` 对 `ci.yml` 的删除列为 0。

---

## ⏭ 本计划之外（⛔ 不要顺手做）

- **第 7 章全勾之后，变更包 `ai-audit-trail-and-outbound-gate` 的 53 项就全部勾完了**，按 `CLAUDE.md`「归档时限」应**当场**跑 `openspec-archive-change`。**但那是另一个交付单元、另一份 opener**，本计划 ⛔ 不做归档，只在收尾报告里提示。
- `docs/audit-and-outbound-ops.md` 第五节的三项 `.51` 上机留步 —— **不可代**，等 Shao Peishen。⛔ 不在本计划里闭合。
- 把 `hooks` job 的 `continue-on-error` / `openspec` job 的观察期状态改掉 —— 与本单元无关，⛔ 不动。

---

## 提取验证记录（`spec-to-plan` §6，2026-09-03 实跑）

本计划的全部代码在成文前已按 `spec-to-plan` 第 6 步跑过端到端提取验证，**不是纸面产物**：

| 项 | 结果 |
|---|---|
| 解释器 | `./venv/bin/python` = CPython **3.14.6**（与 `requires-python = ">=3.14,<3.15"` 一致） |
| Task 1 中间态 | `pytest tests/test_boundary_guard.py -q` → **14 passed** |
| Task 2 终态 | `pytest tests/test_boundary_guard.py -q` → **29 passed** |
| CLI（真实仓库） | `python scripts/check_boundary.py` → `边界守护：通过（基线=e65f685）`，退出码 **0** |
| 真反证（真实仓库） | 塞入 `app/_boundary_probe.py` 后 → **1 条违例，退出码 1**；删除后恢复 0 |
| 全量套件 | `pytest -q` → **814 passed, 1 skipped**（合入本计划的 29 条后应为 843 passed, 1 skipped） |
| CI YAML | 追加后 `yaml.safe_load` 通过；`test` job 6 步、`hooks` job 5 步（末位为新增），`fetch-depth: 0` 确认在位 |
| 代码块回提取 | 把本文件的 5 个 ` ```python ` 块原样抽出重建，与实跑过的文件**逐块字节一致**；在临时目录里跑得 **28 passed, 1 failed** —— 唯一那条红的是 `test_real_repository_passes_boundary_guard`，它的 `REPO_ROOT` 指向临时目录（没有 `requirements.txt`、没有 git 历史），**红在这里正说明它在真干活**。在真实仓库里同一份代码 29 passed |

**成文过程中被实跑纠正的两处**（写下来是为了 ⛔ 别再走回去）：

1. **`pyproject.toml` 一度被纳入 diff 判据，实跑当场红**（U6 的 6 行 pytest `markers`）。若不实跑，这份计划会交付一个从第一天就恒假的检查。→ 判据收缩为只锁 `requirements.txt` + 补 pyproject 结构性检查。
2. **`ast.parse` 一度未包 `warnings.catch_warnings()`**，扫描时喷出 `<unknown>:12: SyntaxWarning: "\z" is an invalid escape sequence`（源头是 `app/outbound/delivery.py:12` docstring 里的 Windows 路径）。→ 加抑制并传 `filename=rel`。

**边界（`spec-to-plan` §6 原话）**：测试与被测代码出自同一份文档、同一个作者，全通只证明**代码可执行且内部自洽**，不证明**符合 spec**。spec 合规由 `run-build` 的两阶段 review 负责。

---

## 偏离登记

| # | 偏离 | 依据 |
|---|---|---|
| 1 | `superpowers:writing-plans` **未真正调用**——本机 `~/.claude/skills/` 为空，该技能既不在技能清单也不在磁盘 | opener 预案：「superpowers 取不到 → 按磁盘 `SKILL.md` 手工走，产物必须带三级 `### Task N:`」。本文件 3 个三级 Task 标题，`grep -c '^### Task ' ` = 3 |
| 2 | 7.1/7.2 指不到任何 spec `### Requirement:` | 见本文顶部「⚠️ 与 spec 的关系」。真源是 `delivery-units.md` §4 约定 7，属工程边界而非行为契约 |
| 3 | 依赖 diff 判据只锁 `requirements.txt`，未含 `pyproject.toml` | opener Global Constraint 3 写的判据即 `-- requirements.txt`；实跑另证 `pyproject.toml` 纳入即恒假。缺口由 pyproject 结构性检查补上 |
| 4 | 7.1 的两条判据比 `tasks.md` 字面从严 | 见「已知的落地口径 ③」。字面判据可被一个变量绕开，且 `app/` 现状零命中，从严成本为零 |
