# channel-resume-intake U1：导出包解包与来源识别 Implementation Plan

> **For agentic workers（Codex 引擎）：** 用 `run-build`（`scripts/codex_sdd_runner.py`）按 `### Task N:` 三级标题逐任务执行；本计划是 spec-to-plan 的唯一输出，⛔ 不在本会话里开始写代码。执行前先读本计划的「架构决策」与「Global Constraints」，reviewer 以它们为注意力透镜。

**Goal:** HR 把一个 ZIP 导出包直接传进既有 `POST /api/resumes/upload`，系统替 HR 拆包（标准库 `zipfile`）、逐文件走 M2 既有单文件接收路径，并为每份简历标注来源（确定性规则识别 ∥ 上传时默认来源 ∥ `unknown`），来源可被 HR 事后改正且留痕；`live` 类别在展开前先求值真实简历入库闸。

**Architecture:** 单进程同步管线（沿用 M2 design Non-Goals，不引入异步任务队列）。ZIP 分支是 M2 上传接口的一个分支，不是第二个接口：`.zip` 文件先 `unpack_bundle(bytes) -> list[FileEntry]`（纯函数）→ 逐文件调既有 `_ingest_one_resume`（只新增可选 `source`/`source_origin` 入参）→ 包级幂等由 `effect_ingest_bundle` 承担。来源识别是确定性纯函数，不调模型；规则表是占位版（文件名正则，首页关键词留待 U4 补）。

**Tech Stack:** Python 3.14、FastAPI、SQLite（`app/storage/db.py` 的 `SCHEMA`/`_ADDED_COLUMNS` 双轨迁移）、pydantic v2、标准库 `zipfile`/`hashlib`/`io`、pypdf/python-docx（已有依赖，来源识别首页文本只读它们）、pytest、httpx。无新增第三方依赖。

**Spec:**

- `openspec/changes/channel-resume-intake/specs/export-bundle-intake/spec.md`
- `openspec/changes/channel-resume-intake/specs/resume-source-tagging/spec.md`
- `openspec/changes/channel-resume-intake/design.md`（决策 D1/D2/D7，Open Questions OQ1/OQ2/OQ5/OQ6 仅标记、不阻塞）
- `openspec/changes/channel-resume-intake/tasks.md` 第 1 章（1.1–1.7，仅用于确认 U1 边界，⛔ 不作为计划输入）

## 需求覆盖表

| spec 能力 | `### Requirement:` | 覆盖 Task |
|---|---|---|
| export-bundle-intake | 接受 ZIP 导出包 | Task 1（解包/白名单/超限/损坏/层级）、Task 4（接口分支）、Task 6（页面）、Task 7（e2e） |
| export-bundle-intake | 真实简历入库闸对导出包同样生效 | Task 4 |
| export-bundle-intake | 包内重复文件只入一次 | Task 4 |
| export-bundle-intake | 展开动作幂等 | Task 4 |
| resume-source-tagging | 来源值域 | Task 2（`Source` 枚举）、Task 3（`resume.source`＋`candidate_source`）、Task 5（改正值域校验） |
| resume-source-tagging | 自动识别来源 | Task 2、Task 4（`default_source` 接线） |
| resume-source-tagging | HR 事后改正留痕 | Task 3（`source_correction_log` 表）、Task 5（改正接口） |

## Global Constraints

以下逐字摘自 `CLAUDE.md`「工程铁律」「合规红线」「部署约束」，与本交付单元的适用判断一并列出。**每个 Task 的验收隐含包含本节全部适用条目。**

1. **工程铁律 1**：LangGraph 恢复时节点从头整个重跑。每个有副作用的动作（发消息、写库、建工单）必须独占一个节点，并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引。幂等记录与业务写必须在同一个事务里提交——同一连接、同一个 `BEGIN`，且该连接上不得存在第二个事务管理者（如与 checkpointer 共用连接）。reviewer 判据：每个 `effect_*` 节点的 `effect_log` 条数与其业务表行数按 thread 恒等，且这条不变式有测试覆盖。✅ **适用**：`effect_ingest_bundle` 是本单元新增的副作用节点，用既有 `app/storage/idempotency.py::idempotent_effect` 装饰器（幂等键 `{job_id}:effect_ingest_bundle:{bundle_sha256}`）。它自己的原子业务写只有 `bundle_ingest_result` 一行（与 `effect_log` 同事务提交）；逐文件简历写入委托给 M2 既有单文件接收，后者按文件哈希去重并由 `effect_persist_parse` 承担解析写入幂等，见「架构决策」第 2 条。

2. **工程铁律 2**：L3 Agent 全部是无副作用纯函数，副作用只在 L4 编排层的 `effect_*` 节点执行。节点命名区分 `compute_*` / `effect_*`。✅ **适用**：`unpack_bundle`、`detect_source`、`first_page_text` 都是无副作用纯函数（不写库、不调模型、不写 storage）；唯一写库的是 `effect_ingest_bundle`。

3. **工程铁律 3**：所有 AI 评分必须持久化：模型标识 + 模型版本 + prompt 版本 + temperature + 输入哈希 + rubric 快照 + 原始响应。✅ **适用，且已由既有基础设施满足**：本单元不新增评分，但 ZIP 分支复用的 M2 解析路径 `compute_parse`/`effect_persist_parse` 已满足；本单元只透传既有 `audit_context`，不改评分落库。

4. **工程铁律 4**：每条 `criterion_score` 必须有 `evidence_ref`（回指简历原文或面试 turn 的 offset）。`evidence_ref` 为空不允许写入。✅ **适用，但不改**：本单元不写 `criterion_score`；来源识别不产生评分项。

5. **工程铁律 5**：`temperature=0`；模型版本优先显式锁定，禁止 `latest` 类别名。供应商不提供带版本号快照时（如 DeepSeek 公开 API 只有 `deepseek-chat` 这类会漂移的别名），必须从 API 响应里取回实际的 `model` 字段并持久化——配置里写的名字不算数，响应返回的才算。✅ **适用，但本单元无新增模型调用**：`detect_source` 是确定性规则、⛔ 不调模型；ZIP 分支复用的 M2 解析路径已满足本铁律。

6. **工程铁律 6**：企微回调先落库再处理：只推一次、5 秒无响应即丢弃。回调接口只做签名校验 + 落库 + 返回 200。⛔ **不适用，理由**：本单元没有企微回调路径。

7. **工程铁律 7**：`langgraph >= 1.0.10`（GHSA-g48c-2wqr-h844）。✅ **适用，环境约束**：`requirements.txt` 已锁 `langgraph==1.0.10`，本单元不降级、不新增图节点。

8. **合规红线·AI 只做排序推荐，不做自动淘汰**：淘汰必须有人工确认节点并留痕。审计断言：`rejection_record` 中 `reason_type='ai_score'` 的记录数恒为 0。✅ **适用**：来源识别只做「打标」，绝不淘汰、不写 `rejection_record`；`detect_source` 是确定性规则不是评分。

9. **合规红线·禁止人脸/表情分析**：⛔ **不适用，理由**：本单元不处理任何影像/声学信号。

10. **合规红线·AI 生成的 JD、拒信、邀约须带标识**：⛔ **不适用，理由**：本单元不产出任何 AI 生成的对外文本。

11. **合规红线·模型全部走境内，简历数据不出境**：✅ **适用**：`detect_source` 是本地确定性计算，不把简历内容发给任何外部服务；ZIP 分支复用的 M2 解析路径使用境内 LLM。

12. **合规红线·绝不用历史录用结果做监督信号**：⛔ **不适用，理由**：本单元无训练、无监督信号。

13. **合规红线·候选人入口一律用一次性邀请链接**：⛔ **不适用，理由**：本单元是 HR 侧上传，不是候选人对外入口。

14. **合规红线·主观描述不得进入硬门槛规则**：⛔ **不适用，理由**：本单元不做硬门槛判定。

15. **部署约束 1·路径前缀就绪**：FastAPI `root_path=/hr/recruit-agent`，前端资源与接口调用一律相对路径，禁止硬编码 `/static/…` `/api/…`。✅ **适用**：Task 6 的 `upload.html` 改动沿用既有 `<base href>` 相对路径写法；新增路由走 `router`（挂载时统一加 `root_path` 前缀）。

16. **部署约束 4·目标服务器是 Windows，没有 Docker，Python venv 部署，不引入容器**：✅ **适用**：本单元零新增第三方依赖，只用标准库 `zipfile` 与既有 pypdf/python-docx。

17. **部署约束 5·M2 起处理真实简历前必须具备可识别到人的登录 + 简历访问留痕**：✅ **适用，但已由 M2 满足**：`live` 导出包的入库闸复用 M2 的 `is_live_resume_intake_enabled()`（其两个结构性前置＝可识别登录＋`resume_access_log` 可写），本单元不新增真实简历处理能力。

## 架构决策（先读，避免和 `tasks.md`/`design.md` 的字面表述对不上）

**1. `candidate.source` 不做物理列，做查询函数 `candidate_source(conn, candidate_id) -> str`。**
`design.md` 上下文里写「`candidate.source` 列已在 `02` §2.1，M2 U1 建表时带」，但当前 `app/storage/db.py` 的 `candidate` 表实际没有 `source` 列；`tasks.md` 1.3 把口径定成「`candidate.source` 重算为最早简历来源的查询函数」。本计划以 `tasks.md` 为 U1 边界真源：不给 `candidate` 加列，新增 `app/storage/source.py::candidate_source()` 按「最早一份简历」（`resume.uploaded_at ASC, resume.id ASC`）经 `application` JOIN 取 `resume.source`，`NULL` 视为 `unknown`。来源改正后候选人来源随查询自动变化，无需触发器。

**2. `effect_ingest_bundle` 的原子业务写是 `bundle_ingest_result` 一行，逐文件简历写入委托给 M2 既有单文件接收。**
`tasks.md` 1.4 要求「逐文件调既有单文件接收」，而 `_ingest_one_resume`（`server.py` 闭包）内部在多个点 `conn.commit()`。因此 `effect_ingest_bundle` 无法把逐文件写入包进一个 `BEGIN` 里（铁律 1 的严格形式会被破坏）。解法：`effect_ingest_bundle` 用 `@idempotent_effect("effect_ingest_bundle")` 装饰，它自己的业务写只有 `bundle_ingest_result`（结果快照）一行，与 `effect_log` 由装饰器同一事务提交；逐文件简历写入由 `_ingest_one_resume` 承担，其内部的文件哈希去重＋`effect_persist_parse` 幂等保证「重跑不产生第二批简历」。`bundle_ingest_result` 同时是「重跑返回上次逐文件结果」与「`live` 闸关闭时留痕一次被拒尝试」的落点。

**3. `source_origin` 只描述「机制」，不描述「unknown」这个值。**
`source_origin CHECK IN ('detected','default','corrected')` 三个取值对应三个赋值机制。识别不出且未指定默认来源时，`resume.source = 'unknown'`、`resume.source_origin = NULL`——「unknown」不是某个机制识别出来的结果，而是系统的回退值，用 `NULL` 表达「没有机制赋过具体来源」最诚实。老库既有行 `source` 与 `source_origin` 均为 `NULL`，`candidate_source()` 一律读作 `unknown`。

**4. `detect_source` 只返回「可识别的平台/内推值」，永不返回 `OTHER`/`UNKNOWN`。**
`Source` 枚举含 7 个值（含 `other`/`unknown`），但 `detect_source(filename, first_page_text) -> Source | None` 只可能返回 `boss/liepin/51job/zhaopin/referral` 五者之一或 `None`。`other` 只经 HR 的 `default_source` 或改正接口赋值；`unknown` 只由系统回退赋值。HR 可选值固定为 `SOURCE_VALUES = ('boss','liepin','51job','zhaopin','referral','other')`，⛔ 不含 `unknown`（HR 指定「unknown」与「不指定」语义相同）。

**5. 占位识别规则只匹配文件名，首页关键词留空但代码流已接线。**
`SOURCE_RULES` 是占位版：文件名正则按公开可见的导出命名模式起步（`boss`/`boss直聘`/`liepin`/`猎聘`/`51job`/`前程无忧`/`zhaopin`/`智联`/`referral`/`内推`），`first_page_keywords` 全空。`detect_source` 的签名与「文件名 → 首页关键词」两段求值已写死，U4 拿到人事部#3 脱敏样例后只补规则与夹具，不改函数签名与调用方。

**6. 损坏 PDF 在 `unpack_bundle` 层拒收，不进 `_ingest_one_resume`。**
spec「损坏件拒收'无法读取'」指的是 ZIP 条目损坏（CRC/读取出错）。`unpack_bundle` 对 `zf.read()` 抛 `BadZipFile`/`zlib.error`/`RuntimeError`/`EOFError` 的条目标 `unreadable`，路由直接拒收「无法读取」且不建 `resume` 行。合法 ZIP 条目但内容不是合法 PDF 的极端情况不在 U1 范围（属 M2 单文件路径既有边界），本单元夹具不会制造它。

**7. `default_source` 只在 ZIP 分支生效，单文件上传行为与 M2 逐字一致。**
`tasks.md` 1.4 的「不传 ZIP／不传 `default_source` 时行为与 M2 完全一致」按保守方向实现：单文件上传（非 `.zip`）忽略 `default_source`、不设 `resume.source`（保持 `NULL`），回归测试固定 M2 既有断言绿。`default_source` 只作为整包回退值。

## File Structure

| 文件 | 责任 |
|---|---|
| `app/intake/__init__.py`（新建） | 空包 |
| `app/intake/bundle.py`（新建） | `BundleLimits`、`BundleTooLarge`、`FileEntry`、`unpack_bundle` 纯函数 |
| `app/intake/source.py`（新建） | `Source` 枚举、`SOURCE_VALUES`、`SourceRule`、`SOURCE_RULES` 占位规则、`detect_source`、`first_page_text` |
| `app/intake/ingest_bundle.py`（新建） | `effect_ingest_bundle`（`@idempotent_effect`）＋ `_ingest_entry`/`_resolve_source` |
| `app/storage/db.py`（改） | `resume` 加 `source`/`source_origin` 列；新增 `source_correction_log`、`bundle_ingest_result` 表；`_ADDED_COLUMNS` 登记两列 |
| `app/storage/source.py`（新建） | `candidate_source(conn, candidate_id) -> str` |
| `app/web/server.py`（改） | 上传接口 ZIP 分支＋`default_source`；`_ingest_one_resume` 加可选 `source`/`source_origin`；来源改正接口 |
| `app/web/static/upload.html`（改） | 接受 `.zip`、默认来源下拉、逐文件结果表新增「来源／包内重复／不可读」列 |
| `tests/test_bundle_unpack.py`（新建） | Task 1 测试 |
| `tests/test_source_detect.py`（新建）＋`tests/fixtures/source_rules/filename_cases.json` | Task 2 测试与夹具 |
| `tests/test_resume_source_schema.py`（新建） | Task 3 测试 |
| `tests/test_resume_upload_zip.py`（新建） | Task 4 测试 |
| `tests/test_resume_source_correction.py`（新建） | Task 5 测试 |
| `tests/test_upload_page_zip.py`（新建） | Task 6 测试 |
| `tests/test_channel_bundle_e2e.py`（新建） | Task 7 e2e |

---

### Task 1: `unpack_bundle` 纯函数（ZIP 解包、白名单、超限、损坏条目、目录层级）

**Files:**

- Create: `app/intake/__init__.py`（空文件）
- Create: `app/intake/bundle.py`
- Test: `tests/test_bundle_unpack.py`

**Interfaces:**

- Produces: `unpack_bundle(data: bytes, limits: BundleLimits | None = None) -> list[FileEntry]`
- Raises: `BundleTooLarge`（文件数／总大小／压缩比超限，或空包、压缩包损坏）
- `FileEntry(name, filename, kind, data)`：`kind ∈ {"ok","unsupported","unreadable","too_deep"}`；`FileEntry.reason` 返回中文拒收原因

- [ ] **Step 1: 创建 `app/intake/__init__.py`**

```python
# app/intake/__init__.py
```

- [ ] **Step 2: 创建 `app/intake/bundle.py`**

```python
"""ZIP 导出包解包（channel-resume-intake U1 tasks 1.1）。

纯函数：只读输入字节、只产出 FileEntry 列表，⛔ 不写任何 storage、不调模型、
不碰数据库。标准库 zipfile；允许一层子目录；白名单 pdf/docx；超限抛
BundleTooLarge；损坏条目标 unreadable；两层以上目录标 too_deep。
"""
from __future__ import annotations

import io
import zipfile
import zlib
from dataclasses import dataclass
from typing import Literal

SUPPORTED_BUNDLE_SUFFIXES = (".pdf", ".docx")


@dataclass(frozen=True)
class BundleLimits:
    max_files: int = 200
    max_total_bytes: int = 200 * 1024 * 1024
    max_ratio: float = 100.0


class BundleTooLarge(ValueError):
    """文件数 / 总大小 / 压缩比任一超限（design D1 上限可配）。"""


@dataclass(frozen=True)
class FileEntry:
    name: str  # 包内路径，如 "子目录/张三.pdf"
    filename: str  # 包内文件名（展示用）
    kind: Literal["ok", "unsupported", "unreadable", "too_deep"]
    data: bytes = b""

    @property
    def reason(self) -> str:
        if self.kind == "unsupported":
            return "不支持的类型"
        if self.kind == "unreadable":
            return "无法读取"
        if self.kind == "too_deep":
            return "目录层级过深"
        return ""


def _dir_depth(name: str) -> int:
    # 文件名本身不算目录：dir/file.pdf → 1，a/b/file.pdf → 2。
    return len(name.split("/")) - 1


def unpack_bundle(data: bytes, limits: BundleLimits | None = None) -> list[FileEntry]:
    limits = limits or BundleLimits()
    if not data:
        raise BundleTooLarge("空压缩包")

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise BundleTooLarge(f"压缩包损坏: {exc}") from exc

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > limits.max_files:
            raise BundleTooLarge(f"文件数超限：{len(infos)} > {limits.max_files}")

        total_uncompressed = sum(i.file_size for i in infos)
        if total_uncompressed > limits.max_total_bytes:
            raise BundleTooLarge("总大小超限")

        compressed_size = len(data)
        if compressed_size > 0:
            ratio = total_uncompressed / compressed_size
            if ratio > limits.max_ratio:
                raise BundleTooLarge("压缩比超限")

        entries: list[FileEntry] = []
        for info in infos:
            filename = info.filename.rsplit("/", 1)[-1]
            depth = _dir_depth(info.filename)
            if depth >= 2:
                entries.append(FileEntry(name=info.filename, filename=filename, kind="too_deep"))
                continue

            suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if suffix not in SUPPORTED_BUNDLE_SUFFIXES:
                entries.append(FileEntry(name=info.filename, filename=filename, kind="unsupported"))
                continue

            try:
                content = zf.read(info)
            except (zipfile.BadZipFile, RuntimeError, zlib.error, EOFError):
                entries.append(FileEntry(name=info.filename, filename=filename, kind="unreadable"))
                continue

            entries.append(FileEntry(name=info.filename, filename=filename, kind="ok", data=content))
        return entries
```

- [ ] **Step 3: 创建 `tests/test_bundle_unpack.py`**

```python
from __future__ import annotations

import io
import zipfile

import pytest

from app.intake.bundle import BundleLimits, BundleTooLarge, unpack_bundle


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _stored_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _corrupt_entry(data: bytes, name: str) -> bytes:
    """把 STORED 条目的数据区第一字节翻转，制造 CRC 损坏条目。"""
    raw = bytearray(data)
    zf = zipfile.ZipFile(io.BytesIO(raw))
    info = zf.getinfo(name)
    data_offset = info.header_offset + 30 + len(info.filename.encode("utf-8")) + len(info.extra)
    raw[data_offset] ^= 0xFF
    return bytes(raw)


def test_unpack_one_level_and_whitelist():
    entries = unpack_bundle(_zip({"a.pdf": b"%PDF", "sub/b.docx": b"PK"}))
    assert [(e.filename, e.kind) for e in entries] == [("a.pdf", "ok"), ("b.docx", "ok")]


def test_unsupported_and_deep_and_corrupt_are_marked():
    raw = _stored_zip({
        "report.xlsx": b"not xlsx",
        "bad.pdf": b"X" * 64,
        "a/b/c.pdf": b"%PDF",
        "ok.pdf": b"%PDF",
    })
    raw = _corrupt_entry(raw, "bad.pdf")
    entries = unpack_bundle(raw)
    assert {e.filename: e.kind for e in entries} == {
        "report.xlsx": "unsupported",
        "bad.pdf": "unreadable",
        "c.pdf": "too_deep",
        "ok.pdf": "ok",
    }


def test_over_file_count_rejects_whole_bundle():
    data = _zip({f"{i}.pdf": b"%PDF" for i in range(5)})
    with pytest.raises(BundleTooLarge):
        unpack_bundle(data, BundleLimits(max_files=3, max_total_bytes=10_000_000, max_ratio=100.0))


def test_over_total_size_rejects_whole_bundle():
    data = _zip({"a.pdf": b"%PDF" * 10_000})
    with pytest.raises(BundleTooLarge):
        unpack_bundle(data, BundleLimits(max_files=10, max_total_bytes=10_000, max_ratio=100.0))


def test_over_ratio_rejects_zip_bomb():
    data = _zip({"a.pdf": b"\x00" * 200_000})
    with pytest.raises(BundleTooLarge):
        unpack_bundle(data, BundleLimits(max_files=10, max_total_bytes=10_000_000, max_ratio=5.0))


def test_unpack_bundle_writes_no_files(tmp_path):
    unpack_bundle(_zip({"a.pdf": b"%PDF", "sub/b.docx": b"PK"}))
    assert list(tmp_path.iterdir()) == []
```

- [ ] **Step 4: 验证**

```bash
python -m pytest tests/test_bundle_unpack.py -q
```

预期输出：`6 passed`，exit code 0。

---

### Task 2: `detect_source` 纯函数与占位规则表

**Files:**

- Create: `app/intake/source.py`
- Create: `tests/fixtures/source_rules/filename_cases.json`
- Test: `tests/test_source_detect.py`

**Interfaces:**

- Produces: `Source`（`str, Enum`，7 值）、`SOURCE_VALUES`（6 值，HR 可选）、`detect_source(filename: str, first_page_text: str) -> Source | None`、`first_page_text(data: bytes, suffix: str) -> str`

- [ ] **Step 1: 创建 `app/intake/source.py`**

```python
"""渠道来源识别（channel-resume-intake U1 tasks 1.2）。

纯函数：只读文件名与首页文本，产出 Source 枚举或 None，⛔ 不写库、不调模型、
不提取平台水印之外字段。规则表是占位版（文件名正则），首页关键词在人事部#3
脱敏样例到位后（U4）补齐。
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from enum import Enum


class Source(str, Enum):
    BOSS = "boss"
    LIEPIN = "liepin"
    JOB_51 = "51job"
    ZHAOPIN = "zhaopin"
    REFERRAL = "referral"
    OTHER = "other"
    UNKNOWN = "unknown"


# HR 可指定的来源值域（默认来源下拉与改正接口共用）。"unknown" 是系统回退值，
# ⛔ 不在 HR 可选值里——HR 指定 "unknown" 与"不指定"语义相同。
SOURCE_VALUES = ("boss", "liepin", "51job", "zhaopin", "referral", "other")


@dataclass(frozen=True)
class SourceRule:
    source: Source
    filename_patterns: tuple[re.Pattern[str], ...]
    first_page_keywords: tuple[str, ...]


# 占位规则（design.md 风险表：各平台导出格式无样例，规则空转）。文件名正则按
# 公开可见的导出命名模式起步，U4 拿到脱敏样例后迭代并补夹具与首页关键词。
SOURCE_RULES: tuple[SourceRule, ...] = (
    SourceRule(
        Source.BOSS,
        (re.compile(r"boss", re.IGNORECASE), re.compile(r"boss直聘", re.IGNORECASE)),
        (),
    ),
    SourceRule(
        Source.LIEPIN,
        (re.compile(r"liepin", re.IGNORECASE), re.compile(r"猎聘", re.IGNORECASE)),
        (),
    ),
    SourceRule(
        Source.JOB_51,
        (re.compile(r"51job", re.IGNORECASE), re.compile(r"前程无忧", re.IGNORECASE)),
        (),
    ),
    SourceRule(
        Source.ZHAOPIN,
        (re.compile(r"zhaopin", re.IGNORECASE), re.compile(r"智联", re.IGNORECASE)),
        (),
    ),
    SourceRule(
        Source.REFERRAL,
        (re.compile(r"referral", re.IGNORECASE), re.compile(r"内推", re.IGNORECASE)),
        (),
    ),
)


def detect_source(filename: str, first_page_text: str) -> Source | None:
    """确定性来源识别。文件名规则命中返回对应 Source；否则看首页关键词（占位
    为空）；都未命中返回 None——由调用方落到 default_source 或 unknown。
    ⛔ 只返回可识别的平台/内推值，绝不返回 OTHER/UNKNOWN（那两个由人/调用方赋值）。"""
    for rule in SOURCE_RULES:
        if any(p.search(filename) for p in rule.filename_patterns):
            return rule.source
    text = first_page_text or ""
    for rule in SOURCE_RULES:
        if rule.first_page_keywords and any(k in text for k in rule.first_page_keywords):
            return rule.source
    return None


def first_page_text(data: bytes, suffix: str) -> str:
    """从内存字节取首页文本，供 detect_source 的第二参。U1 占位规则不读它，
    但签名与数据流在 U4 补首页关键词后不再改动。任何解析失败都返回空串（宁可
    识别不出，不让一个坏文件拖垮整包）。"""
    suffix = suffix.lower()
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            if not reader.pages:
                return ""
            return reader.pages[0].extract_text() or ""
        if suffix == ".docx":
            import docx
            document = docx.Document(io.BytesIO(data))
            for p in document.paragraphs:
                if p.text.strip():
                    return p.text
            return ""
    except Exception:
        return ""
    return ""
```

- [ ] **Step 2: 创建夹具 `tests/fixtures/source_rules/filename_cases.json`**

```json
{
  "候选人-张三-boss直聘.pdf": "boss",
  "候选人-李四-liepin-猎聘.pdf": "liepin",
  "候选人-王五-51job-前程无忧.pdf": "51job",
  "候选人-赵六-zhaopin-智联.pdf": "zhaopin",
  "内推-孙七.pdf": "referral",
  "候选人-周八-简历.pdf": null
}
```

- [ ] **Step 3: 创建 `tests/test_source_detect.py`**

```python
from __future__ import annotations

import json
from pathlib import Path

from app.intake.source import Source, detect_source

_FIXTURE = Path(__file__).parent / "fixtures" / "source_rules" / "filename_cases.json"


def test_filename_cases_fixture():
    cases = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    for filename, expected in cases.items():
        got = detect_source(filename, "")
        assert (got.value if got else None) == expected, filename


def test_detect_source_returns_only_enum_or_none():
    assert detect_source("boss直聘-张三.pdf", "") is Source.BOSS
    assert detect_source("张三-简历.pdf", "猎聘 首页页眉 无锡") is None


def test_first_page_keywords_placeholder_is_inert():
    assert detect_source("张三-简历.pdf", "智联招聘") is None
```

- [ ] **Step 4: 验证**

```bash
python -m pytest tests/test_source_detect.py -q
```

预期输出：`3 passed`，exit code 0。

---

### Task 3: 数据模型——`resume.source/source_origin`、`source_correction_log`、`bundle_ingest_result`、`candidate_source`

**Files:**

- Modify: `app/storage/db.py`
- Create: `app/storage/source.py`
- Test: `tests/test_resume_source_schema.py`

**Interfaces:**

- Produces: `candidate_source(conn: sqlite3.Connection, candidate_id: str) -> str`
- Schema: `resume.source TEXT`、`resume.source_origin TEXT CHECK (...)`（走 `_ADDED_COLUMNS`）；新表 `source_correction_log`、`bundle_ingest_result`

- [ ] **Step 1: 在 `resume` 表 `CREATE TABLE` 加两列**

在 `app/storage/db.py` 的 `resume` 表定义里，把结尾两行改为（在 `uploaded_at` 之后追加两列）：

```sql
    uploaded_by TEXT NOT NULL,
    uploaded_at TEXT NOT NULL DEFAULT (datetime('now')),
    source TEXT,
    source_origin TEXT CHECK (source_origin IN ('detected', 'default', 'corrected'))
);
```

- [ ] **Step 2: 在 SCHEMA 里新增两张表**

在 `resume_parse_version` 表定义之后、`stage` 表定义之前插入：

```sql
-- 来源改正留痕（channel-resume-intake U1 tasks 1.3）。from_source 允许 NULL：
-- 老库/单文件上传的简历 source 本来就是 NULL，第一次改正的"原值"就是 NULL。
-- corrected_by 的 CHECK 与 human_review.reviewer 同一手法（trim 第二参数显式
-- 列出空格/制表/换行/回车）：空改正人等于没留痕。
CREATE TABLE IF NOT EXISTS source_correction_log (
    id TEXT PRIMARY KEY NOT NULL,
    resume_id TEXT NOT NULL REFERENCES resume(id),
    from_source TEXT,
    to_source TEXT NOT NULL,
    corrected_by TEXT NOT NULL CHECK (
        corrected_by IS NOT NULL
        AND trim(corrected_by, ' ' || char(9) || char(10) || char(13)) != ''
    ),
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_source_correction_log_resume
    ON source_correction_log (resume_id);

-- 导出包接收结果快照（channel-resume-intake U1 tasks 1.4）。一行 = 一个
-- (job_id, bundle_sha256) 的接收结果：completed 存逐文件结果 JSON，重跑时原样
-- 返回；rejected_gate 是 live 闸关闭时的一次被拒尝试留痕（不存任何文件内容）。
-- 唯一索引是幂等第二道防线（第一道是 effect_log 唯一键）。
CREATE TABLE IF NOT EXISTS bundle_ingest_result (
    id TEXT PRIMARY KEY NOT NULL,
    job_id TEXT NOT NULL,
    bundle_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('completed', 'rejected_gate')),
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_bundle_ingest_result_job_hash
    ON bundle_ingest_result (job_id, bundle_sha256);
```

- [ ] **Step 3: 在 `_ADDED_COLUMNS` 里登记两列**

在 `app/storage/db.py` 的 `_ADDED_COLUMNS` 元组末尾追加：

```python
    # channel-resume-intake U1 tasks 1.3：resume 加来源与来源赋值机制。source 值域
    # 由应用层约束（Source 枚举），source_origin 三态由 DB CHECK 兜底。
    ("resume", "source", "TEXT"),
    ("resume", "source_origin", "TEXT CHECK (source_origin IN ('detected', 'default', 'corrected'))"),
)
```

- [ ] **Step 4: 创建 `app/storage/source.py`**

```python
"""候选人来源查询（channel-resume-intake U1 tasks 1.3）。

candidate.source 不做物理列：它恒等于该候选人最早一份简历的来源（design D2
「视图或触发式重算」）。本函数是唯一真源，任何要展示/统计候选人来源的调用方
都读它，⛔ 不要各自写一遍 JOIN。老库既有简历 source 为 NULL 视为 unknown。
"""
from __future__ import annotations

import sqlite3


def candidate_source(conn: sqlite3.Connection, candidate_id: str) -> str:
    row = conn.execute(
        "SELECT r.source FROM application a "
        "JOIN resume r ON r.id = a.resume_id "
        "WHERE a.candidate_id = ? "
        "ORDER BY r.uploaded_at ASC, r.id ASC LIMIT 1",
        (candidate_id,),
    ).fetchone()
    if row is None:
        return "unknown"
    return row[0] or "unknown"
```

- [ ] **Step 5: 创建 `tests/test_resume_source_schema.py`**

```python
from __future__ import annotations

import sqlite3

import pytest

from app.storage.db import _existing_columns, init_schema
from app.storage.source import candidate_source


def _conn():
    c = sqlite3.connect(":memory:")
    init_schema(c)
    return c


def test_fresh_resume_has_source_columns():
    conn = _conn()
    cols = _existing_columns(conn, "resume")
    assert {"source", "source_origin"} <= cols


def test_source_origin_check_rejects_invalid():
    conn = _conn()
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', 't')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'h', 'alice')"
    )
    conn.execute("UPDATE resume SET source_origin = 'detected' WHERE id = 'r1'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE resume SET source_origin = 'bogus' WHERE id = 'r1'")


def test_source_correction_log_and_bundle_ingest_result_tables_exist():
    conn = _conn()
    assert _existing_columns(conn, "source_correction_log") >= {
        "id", "resume_id", "from_source", "to_source", "corrected_by", "at",
    }
    assert _existing_columns(conn, "bundle_ingest_result") >= {
        "id", "job_id", "bundle_sha256", "status", "result_json", "created_at",
    }


def test_candidate_source_returns_earliest_resume_source():
    conn = _conn()
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', 't')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute("INSERT INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial')")
    for rid, source, at in [("r-old", "boss", "2026-01-01 00:00:00"),
                            ("r-new", "liepin", "2026-01-02 00:00:00")]:
        conn.execute(
            "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
            "uploaded_by, source, uploaded_at) VALUES (?, 'j1', 'synthetic', ?, ?, 'alice', ?, ?)",
            (rid, rid + ".pdf", "h-" + rid, source, at),
        )
        conn.execute(
            "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
            "VALUES (?, 'c1', 'j1', ?, 'initial')",
            ("app-" + rid, rid),
        )
    assert candidate_source(conn, "c1") == "boss"


def test_candidate_source_treats_null_and_missing_as_unknown():
    conn = _conn()
    assert candidate_source(conn, "missing") == "unknown"
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', 't')")
    conn.execute("INSERT INTO candidate (id, name) VALUES ('c1', '张三')")
    conn.execute("INSERT INTO stage (id, name, stage_type) VALUES ('initial', '初筛', 'initial')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'h', 'alice')"
    )
    conn.execute(
        "INSERT INTO application (id, candidate_id, job_id, resume_id, current_stage_id) "
        "VALUES ('a1', 'c1', 'j1', 'r1', 'initial')"
    )
    assert candidate_source(conn, "c1") == "unknown"
```

- [ ] **Step 6: 验证**

```bash
python -m pytest tests/test_resume_source_schema.py tests/test_db_migration.py -q
```

预期输出：全部 passed（新增 5 条 + 既有迁移守卫），exit code 0。`tests/test_db_migration.py` 的漂移守卫会自动把 `resume` 的新两列纳入「fresh vs migrated 列集合一致」检查。

---

### Task 4: 上传接口 ZIP 分支与 `effect_ingest_bundle`

**Files:**

- Create: `app/intake/ingest_bundle.py`
- Modify: `app/web/server.py`
- Test: `tests/test_resume_upload_zip.py`

**Interfaces:**

- Produces: `effect_ingest_bundle(...) -> dict | None`（`@idempotent_effect("effect_ingest_bundle")`）
- `_ingest_one_resume` 新增可选参数 `source: str | None = None, source_origin: str | None = None`

- [ ] **Step 1: 创建 `app/intake/ingest_bundle.py`**

```python
"""ZIP 导出包接收的副作用执行单元（channel-resume-intake U1 tasks 1.4）。

effect_ingest_bundle 是独占的副作用执行单元：幂等键
{job_id}:effect_ingest_bundle:{bundle_sha256}，重跑不产生第二批简历。本函数
自己的业务写只有 bundle_ingest_result 一行（与 effect_log 同事务提交，工程
铁律 1）；逐文件简历写入委托给调用方注入的 ingest_one（既有单文件接收，其
内部按文件哈希去重并由 effect_persist_parse 承担解析写入的幂等）。
"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Callable

from app.intake.bundle import FileEntry
from app.intake.source import detect_source, first_page_text
from app.storage.idempotency import idempotent_effect


class _BytesUpload:
    """给既有单文件接收函数的最小 UploadFile 适配器（只需 .filename 与 .file）。"""
    def __init__(self, filename: str, data: bytes) -> None:
        self.filename = filename
        self.file = io.BytesIO(data)


@idempotent_effect("effect_ingest_bundle")
def effect_ingest_bundle(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    job_id: str,
    sample_class: str,
    uploaded_by: str,
    entries: list[FileEntry],
    default_source: str | None,
    ingest_one: Callable[..., dict],
) -> dict:
    results: list[dict] = []
    seen_hashes: set[str] = set()
    for entry in entries:
        results.append(_ingest_entry(
            entry=entry,
            seen_hashes=seen_hashes,
            job_id=job_id,
            sample_class=sample_class,
            uploaded_by=uploaded_by,
            default_source=default_source,
            ingest_one=ingest_one,
        ))
    result_json = json.dumps({"results": results}, ensure_ascii=False)
    conn.execute(
        "INSERT INTO bundle_ingest_result (id, job_id, bundle_sha256, status, result_json) "
        "VALUES (?, ?, ?, 'completed', ?)",
        (str(uuid.uuid4()), job_id, business_key, result_json),
    )
    return {"results": results}


def _ingest_entry(*, entry, seen_hashes, job_id, sample_class, uploaded_by, default_source, ingest_one) -> dict:
    if entry.kind != "ok":
        return {"file_name": entry.filename, "status": "rejected", "reason": entry.reason}

    content_hash = hashlib.sha256(entry.data).hexdigest()
    if content_hash in seen_hashes:
        return {"file_name": entry.filename, "status": "intra_bundle_duplicate"}
    seen_hashes.add(content_hash)

    suffix = Path(entry.filename).suffix.lower()
    source, source_origin = _resolve_source(entry.filename, entry.data, suffix, default_source)
    result = ingest_one(
        job_id=job_id,
        sample_class=sample_class,
        uploaded_by=uploaded_by,
        upload=_BytesUpload(entry.filename, entry.data),
        source=source,
        source_origin=source_origin,
    )
    if result.get("status") == "accepted":
        result["source"] = source
        result["source_origin"] = source_origin
    return result


def _resolve_source(filename: str, data: bytes, suffix: str, default_source: str | None) -> tuple[str, str | None]:
    detected = detect_source(filename, first_page_text(data, suffix))
    if detected is not None:
        return detected.value, "detected"
    if default_source is not None:
        return default_source, "default"
    return "unknown", None
```

- [ ] **Step 2: `server.py` 顶部导入**

在 `app/web/server.py` 的 import 区追加：

```python
from app.intake.bundle import BundleTooLarge, unpack_bundle
from app.intake.ingest_bundle import effect_ingest_bundle
from app.intake.source import SOURCE_VALUES
```

- [ ] **Step 3: 新增请求模型 `SourceCorrectionRequest`**

在 `class FieldReviewRequest` 之后追加：

```python
class SourceCorrectionRequest(BaseModel):
    source: str
```

- [ ] **Step 4: 给 `_ingest_one_resume` 加可选来源参数并写进 INSERT**

把 `_ingest_one_resume` 的签名改为：

```python
    def _ingest_one_resume(*, job_id: str, sample_class: str, uploaded_by: str,
                            upload: UploadFile, source: str | None = None,
                            source_origin: str | None = None) -> dict:
```

把其中的 INSERT 改为（新增 `source, source_origin` 两列与两个占位符）：

```python
        conn.execute(
            "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, "
            "uploaded_by, source, source_origin) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (resume_id, job_id, sample_class, upload.filename, content_hash, uploaded_by,
             source, source_origin),
        )
```

其余 `_ingest_one_resume` 逻辑逐字不动。

- [ ] **Step 5: 重写 `upload_resumes` 路由并新增 `_ingest_single_file`/`_ingest_bundle` 闭包**

把现有 `upload_resumes` 路由整体替换为：

```python
    @router.post("/api/resumes/upload")
    def upload_resumes(
        request: Request,
        job_id: str = Form(...),
        sample_class: str = Form(...),
        default_source: str | None = Form(None),
        files: list[UploadFile] = File(...),
    ):
        if sample_class not in _VALID_SAMPLE_CLASSES:
            raise HTTPException(status_code=422, detail="sample_class 取值非法")
        if default_source is not None and default_source not in SOURCE_VALUES:
            raise HTTPException(status_code=422, detail="default_source 取值非法")
        job = conn.execute("SELECT id FROM job WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")

        uploader = reviewer_of(request)
        results: list[dict] = []
        for f in files:
            if Path(f.filename or "").suffix.lower() == ".zip":
                results.extend(_ingest_bundle(
                    request=request, job_id=job_id, sample_class=sample_class,
                    uploaded_by=uploader, upload=f, default_source=default_source,
                ))
            else:
                results.append(_ingest_single_file(
                    request=request, job_id=job_id, sample_class=sample_class,
                    uploaded_by=uploader, upload=f,
                ))
        return {"results": results}

    def _ingest_single_file(*, request: Request, job_id: str, sample_class: str,
                            uploaded_by: str, upload: UploadFile) -> dict:
        if sample_class == "live":
            gate_open = is_live_resume_intake_enabled(auth=request.state.auth, conn=conn)
            if not gate_open:
                logger.warning(
                    "闸关闭时的 live 上传尝试：uploader=%s job_id=%s file=%s",
                    uploaded_by, job_id, upload.filename,
                )
                return {"file_name": upload.filename, "status": "rejected",
                        "reason": "真实简历入库闸未开启"}
        return _ingest_one_resume(job_id=job_id, sample_class=sample_class,
                                  uploaded_by=uploaded_by, upload=upload)

    def _ingest_bundle(*, request: Request, job_id: str, sample_class: str,
                       uploaded_by: str, upload: UploadFile, default_source: str | None) -> list[dict]:
        data = upload.file.read()
        bundle_sha256 = hashlib.sha256(data).hexdigest()

        existing = conn.execute(
            "SELECT result_json FROM bundle_ingest_result WHERE job_id = ? AND bundle_sha256 = ?",
            (job_id, bundle_sha256),
        ).fetchone()
        if existing is not None:
            return json.loads(existing[0])["results"]

        if sample_class == "live":
            gate_open = is_live_resume_intake_enabled(auth=request.state.auth, conn=conn)
            if not gate_open:
                results = [{"file_name": upload.filename, "status": "rejected",
                            "reason": "真实简历入库闸未开启"}]
                conn.execute(
                    "INSERT INTO bundle_ingest_result (id, job_id, bundle_sha256, status, result_json) "
                    "VALUES (?, ?, ?, 'rejected_gate', ?)",
                    (str(uuid.uuid4()), job_id, bundle_sha256,
                     json.dumps({"results": results}, ensure_ascii=False)),
                )
                conn.commit()
                logger.warning(
                    "闸关闭时的 live 导出包上传尝试：uploader=%s job_id=%s",
                    uploaded_by, job_id,
                )
                return results

        try:
            entries = unpack_bundle(data)
        except BundleTooLarge as exc:
            logger.warning("导出包超限拒收：job_id=%s reason=%s", job_id, exc)
            return [{"file_name": upload.filename, "status": "rejected", "reason": str(exc)}]

        result = effect_ingest_bundle(
            conn,
            thread_id=job_id,
            business_key=bundle_sha256,
            job_id=job_id,
            sample_class=sample_class,
            uploaded_by=uploaded_by,
            entries=entries,
            default_source=default_source,
            ingest_one=_ingest_one_resume,
        )
        if result is None:
            row = conn.execute(
                "SELECT result_json FROM bundle_ingest_result WHERE job_id = ? AND bundle_sha256 = ?",
                (job_id, bundle_sha256),
            ).fetchone()
            return json.loads(row[0])["results"] if row else []
        return result["results"]
```

- [ ] **Step 6: 创建 `tests/test_resume_upload_zip.py`**

```python
from __future__ import annotations

import io
import zipfile

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _blank_pdf() -> bytes:
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _client_and_job(make_test_client):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.commit()
    return client, conn


def test_zip_accepts_whitelist_and_rejects_non_whitelist(make_test_client):
    client, conn = _client_and_job(make_test_client)
    zdata = _zip({"a.pdf": _blank_pdf(), "report.xlsx": b"not xlsx"})
    resp = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=[("files", ("bundle.zip", zdata, "application/zip"))],
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0]["status"] == "accepted"
    assert results[1]["status"] == "rejected"
    assert "不支持的类型" in results[1]["reason"]


def test_zip_intra_bundle_duplicate(make_test_client):
    client, conn = _client_and_job(make_test_client)
    pdf = _blank_pdf()
    zdata = _zip({"same1.pdf": pdf, "same2.pdf": pdf})
    resp = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=[("files", ("bundle.zip", zdata, "application/zip"))],
    )
    results = resp.json()["results"]
    assert results[0]["status"] == "accepted"
    assert results[1]["status"] == "intra_bundle_duplicate"
    assert conn.execute("SELECT COUNT(*) FROM resume").fetchone()[0] == 1


def test_zip_live_gate_closed_rejected_with_trace(make_test_client):
    client, conn = _client_and_job(make_test_client)
    zdata = _zip({"a.pdf": _blank_pdf()})
    resp = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "live"},
        files=[("files", ("bundle.zip", zdata, "application/zip"))],
    )
    assert resp.status_code == 200
    assert resp.json()["results"][0]["status"] == "rejected"
    assert conn.execute("SELECT COUNT(*) FROM resume").fetchone()[0] == 0
    row = conn.execute("SELECT status FROM bundle_ingest_result WHERE job_id = 'j1'").fetchone()
    assert row is not None and row[0] == "rejected_gate"


def test_zip_replay_returns_same_results_and_no_new_resumes(make_test_client):
    client, conn = _client_and_job(make_test_client)
    zdata = _zip({"a.pdf": _blank_pdf()})
    first = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=[("files", ("bundle.zip", zdata, "application/zip"))],
    ).json()["results"]
    second = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=[("files", ("bundle.zip", zdata, "application/zip"))],
    ).json()["results"]
    assert second == first
    assert conn.execute("SELECT COUNT(*) FROM resume").fetchone()[0] == 1
```

- [ ] **Step 7: 验证**

```bash
python -m pytest tests/test_resume_upload_zip.py -q
```

预期输出：`4 passed`，exit code 0。

---

### Task 5: 来源改正接口 `POST /api/resumes/{resume_id}/source`

**Files:**

- Modify: `app/web/server.py`
- Test: `tests/test_resume_source_correction.py`

**Interfaces:**

- Produces: `POST /api/resumes/{resume_id}/source`，请求体 `{"source": "..."}`，值域 `SOURCE_VALUES`
- 幂等：`current == new_source` 时无第二条留痕

- [ ] **Step 1: 在 `reparse_resume` 路由之前新增改正路由**

在 `app/web/server.py` 里 `_ingest_bundle` 闭包之后、`@router.post("/api/resumes/{resume_id}/reparse")` 之前插入：

```python
    @router.post("/api/resumes/{resume_id}/source")
    def correct_resume_source(request: Request, resume_id: str, req: SourceCorrectionRequest):
        new_source = req.source
        if new_source not in SOURCE_VALUES:
            raise HTTPException(status_code=422, detail="source 取值非法")
        row = conn.execute("SELECT source FROM resume WHERE id = ?", (resume_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="resume not found")

        current = row[0]
        if current == new_source:
            return {"resume_id": resume_id, "source": new_source, "already_corrected": True}

        corrected_by = reviewer_of(request)
        conn.execute(
            "INSERT INTO source_correction_log (id, resume_id, from_source, to_source, corrected_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), resume_id, current, new_source, corrected_by),
        )
        conn.execute(
            "UPDATE resume SET source = ?, source_origin = 'corrected' WHERE id = ?",
            (new_source, resume_id),
        )
        conn.commit()
        return {"resume_id": resume_id, "source": new_source, "already_corrected": False}
```

- [ ] **Step 2: 创建 `tests/test_resume_source_correction.py`**

```python
from __future__ import annotations

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _client_and_resume(make_test_client):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.execute(
        "INSERT INTO resume (id, job_id, sample_class, file_name, content_sha256, uploaded_by) "
        "VALUES ('r1', 'j1', 'synthetic', 'a.pdf', 'h', 'alice')"
    )
    conn.commit()
    return client, conn, "r1"


def test_correct_source_writes_log_and_updates(make_test_client):
    client, conn, resume_id = _client_and_resume(make_test_client)
    resp = client.post(f"/api/resumes/{resume_id}/source", json={"source": "referral"})
    assert resp.status_code == 200
    assert resp.json() == {"resume_id": resume_id, "source": "referral", "already_corrected": False}
    row = conn.execute("SELECT from_source, to_source FROM source_correction_log WHERE resume_id = ?", (resume_id,)).fetchone()
    assert row is not None and row[0] is None and row[1] == "referral"
    source, origin = conn.execute("SELECT source, source_origin FROM resume WHERE id = ?", (resume_id,)).fetchone()
    assert source == "referral" and origin == "corrected"


def test_same_value_is_idempotent(make_test_client):
    client, conn, resume_id = _client_and_resume(make_test_client)
    client.post(f"/api/resumes/{resume_id}/source", json={"source": "referral"})
    resp = client.post(f"/api/resumes/{resume_id}/source", json={"source": "referral"})
    assert resp.status_code == 200
    assert resp.json()["already_corrected"] is True
    count = conn.execute("SELECT COUNT(*) FROM source_correction_log WHERE resume_id = ?", (resume_id,)).fetchone()[0]
    assert count == 1


def test_invalid_source_422_and_unknown_resume_404(make_test_client):
    client, conn, resume_id = _client_and_resume(make_test_client)
    assert client.post(f"/api/resumes/{resume_id}/source", json={"source": "unknown"}).status_code == 422
    assert client.post("/api/resumes/nope/source", json={"source": "referral"}).status_code == 404
```

- [ ] **Step 3: 验证**

```bash
python -m pytest tests/test_resume_source_correction.py -q
```

预期输出：`3 passed`，exit code 0。

---

### Task 6: 上传页增量（接受 `.zip`、默认来源下拉、结果表新列）

**Files:**

- Modify: `app/web/static/upload.html`
- Test: `tests/test_upload_page_zip.py`

**Interfaces:**

- 页面 `GET /resumes/upload`：`accept=".pdf,.docx,.zip"`、默认来源下拉（值域 `SOURCE_VALUES`＋「不指定」）、结果表新增「来源／包内重复／不可读」列，全部走 `<base href>` 相对路径

- [ ] **Step 1: 整体替换 `app/web/static/upload.html`**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <!--BASE_HREF-->
  <link rel="stylesheet" href="static/app.css?v=20261007">
  <title>上传简历 · 卓品智能招聘助手</title>
</head>
<body>
  <header class="topbar">
    <div class="topbar-inner">
      <span class="brand">卓品智能招聘助手</span>
      <span class="brand-sub">HR 工作台 · 简历上传</span>
    </div>
  </header>
  <main class="page">
    <h1 class="page-title">上传简历</h1>
    <p class="notice">只能上传 <strong>合成替身 / 脱敏 / 历史离职</strong> 样本，真实在招简历（live）当前不开放上传。支持 PDF / Word / ZIP 导出包。</p>
    <div class="card">
      <form id="upload-form">
        <div class="field">
          <label class="field-label" for="job-select">目标岗位</label>
          <select id="job-select" required></select>
          <span class="field-hint">只列已审批画像的岗位</span>
        </div>
        <div class="field">
          <span class="field-label">样本类别</span>
          <label><input type="radio" name="sample_class" value="synthetic" checked> 合成替身</label>
          <label><input type="radio" name="sample_class" value="anonymized"> 脱敏样本</label>
          <label><input type="radio" name="sample_class" value="departed"> 历史离职候选人</label>
        </div>
        <div class="field">
          <label class="field-label" for="default-source">默认来源（识别不出时生效）</label>
          <select id="default-source">
            <option value="">不指定（识别不出标 unknown）</option>
            <option value="boss">Boss直聘</option>
            <option value="liepin">猎聘</option>
            <option value="51job">前程无忧</option>
            <option value="zhaopin">智联</option>
            <option value="referral">内推</option>
            <option value="other">其它</option>
          </select>
        </div>
        <div class="field">
          <label class="field-label" for="file-input">选择文件</label>
          <input id="file-input" type="file" multiple accept=".pdf,.docx,.zip" required>
          <span class="field-hint">可多选，支持 PDF / Word / ZIP 导出包</span>
        </div>
        <div class="toolbar">
          <button type="submit" class="btn btn-primary">上传</button>
        </div>
      </form>
      <p id="error" style="color:red"></p>
    </div>
    <div class="card">
      <table id="result-table" class="data-table" style="display:none">
        <thead><tr><th>文件名</th><th>结果</th><th>来源</th><th>包内重复</th><th>不可读</th></tr></thead>
        <tbody id="result-body"></tbody>
      </table>
      <p><a id="to-list-link" class="link" href="#">上传完成后查看解析结果列表</a></p>
    </div>
  </main>

  <script>
    const jobSelect = document.getElementById("job-select");
    const toListLink = document.getElementById("to-list-link");

    function redirectIfUnauthorized(resp) {
      if (resp.status === 401) {
        window.location.href = "login";
        return true;
      }
      return false;
    }

    async function loadJobs() {
      const resp = await fetch("api/jobs");
      const body = await resp.json();
      const approved = (body.jobs || []).filter((j) => j.status === "approved");
      jobSelect.innerHTML = "";
      if (approved.length === 0) {
        const opt = document.createElement("option");
        opt.textContent = "暂无已审批画像的岗位";
        opt.disabled = true;
        jobSelect.appendChild(opt);
        return;
      }
      for (const job of approved) {
        const opt = document.createElement("option");
        opt.value = job.job_id;
        opt.textContent = job.title;
        jobSelect.appendChild(opt);
      }
      toListLink.href = `jobs/${approved[0].job_id}/resumes`;
    }

    document.getElementById("upload-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      document.getElementById("error").textContent = "";
      const jobId = jobSelect.value;
      if (!jobId) {
        document.getElementById("error").textContent = "请先选择岗位";
        return;
      }
      const sampleClass = document.querySelector('input[name="sample_class"]:checked').value;
      const defaultSource = document.getElementById("default-source").value;
      const files = document.getElementById("file-input").files;
      const formData = new FormData();
      formData.append("job_id", jobId);
      formData.append("sample_class", sampleClass);
      if (defaultSource) {
        formData.append("default_source", defaultSource);
      }
      for (const f of files) {
        formData.append("files", f);
      }
      const resp = await fetch("api/resumes/upload", { method: "POST", body: formData });
      if (redirectIfUnauthorized(resp)) return;
      if (!resp.ok) {
        document.getElementById("error").textContent = "上传失败，请确认已登录";
        return;
      }
      const body = await resp.json();
      const tbody = document.getElementById("result-body");
      tbody.innerHTML = "";
      for (const r of body.results) {
        const tr = document.createElement("tr");
        const nameTd = document.createElement("td");
        nameTd.textContent = r.file_name;
        const statusTd = document.createElement("td");
        let label = r.status;
        if (r.status === "accepted") label = `接收（${r.parse_status || "处理中"}）`;
        if (r.status === "duplicate") label = "重复：已存在同一份简历";
        if (r.status === "intra_bundle_duplicate") label = "包内重复";
        if (r.status === "rejected") label = `拒收：${r.reason || ""}`;
        statusTd.textContent = label;
        const sourceTd = document.createElement("td");
        sourceTd.textContent = r.source || "—";
        const dupTd = document.createElement("td");
        dupTd.textContent = r.status === "intra_bundle_duplicate" ? "✓" : "—";
        const unreadTd = document.createElement("td");
        unreadTd.textContent = r.parse_status === "unreadable" ? "✓" : "—";
        tr.appendChild(nameTd);
        tr.appendChild(statusTd);
        tr.appendChild(sourceTd);
        tr.appendChild(dupTd);
        tr.appendChild(unreadTd);
        tbody.appendChild(tr);
      }
      document.getElementById("result-table").style.display = "";
      toListLink.href = `jobs/${jobId}/resumes`;
    });

    loadJobs();
  </script>
</body>
</html>
```

- [ ] **Step 2: 创建 `tests/test_upload_page_zip.py`**

```python
from __future__ import annotations


def test_upload_page_accepts_zip_and_has_source_columns(make_test_client):
    client, _conn = make_test_client()
    resp = client.get("/resumes/upload")
    assert resp.status_code == 200
    html = resp.text
    assert 'accept=".pdf,.docx,.zip"' in html
    assert 'id="default-source"' in html
    assert 'value="referral"' in html
    assert "包内重复" in html
    assert "不可读" in html
    assert 'fetch("api/resumes/upload"' in html
    assert 'value="live"' not in html
```

- [ ] **Step 3: 验证**

```bash
python -m pytest tests/test_upload_page_zip.py tests/test_upload_page.py -q
```

预期输出：全部 passed（新增 1 条 + 既有 upload 页面断言保持绿），exit code 0。

---

### Task 7: U1 端到端（合成 ZIP 上传 → 逐文件结果 → 重传幂等）

**Files:**

- Test: `tests/test_channel_bundle_e2e.py`

**Interfaces:**

- 覆盖 spec 四个 Scenario ＋ 包内重复 ＋ 重传幂等

- [ ] **Step 1: 创建 `tests/test_channel_bundle_e2e.py`**

```python
from __future__ import annotations

import io
import zipfile

from app.storage.auth_session import create_session
from app.storage.hr_account import upsert_account


def _blank_pdf() -> bytes:
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _stored_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _corrupt_entry(data: bytes, name: str) -> bytes:
    raw = bytearray(data)
    zf = zipfile.ZipFile(io.BytesIO(raw))
    info = zf.getinfo(name)
    data_offset = info.header_offset + 30 + len(info.filename.encode("utf-8")) + len(info.extra)
    raw[data_offset] ^= 0xFF
    return bytes(raw)


def _bundle() -> bytes:
    pdf = _blank_pdf()
    raw = _stored_zip({
        "report.xlsx": b"not xlsx",
        "bad.pdf": b"X" * 64,
        "same1.pdf": pdf,
        "same2.pdf": pdf,
        "a/b/resume.pdf": pdf,
    })
    return _corrupt_entry(raw, "bad.pdf")


def _client_and_job(make_test_client):
    client, conn = make_test_client()
    account_id = upsert_account(conn, username="alice", password="s3cret!")
    token = create_session(conn, hr_account_id=account_id)
    client.cookies.set("hr_session", token)
    conn.execute("INSERT INTO job (id, title) VALUES ('j1', '嵌入式工程师')")
    conn.commit()
    return client, conn


def test_bundle_upload_and_replay(make_test_client):
    client, conn = _client_and_job(make_test_client)
    zdata = _bundle()

    first = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=[("files", ("bundle.zip", zdata, "application/zip"))],
    ).json()["results"]

    by_name = {r["file_name"]: r for r in first}
    assert by_name["report.xlsx"]["status"] == "rejected"
    assert "不支持的类型" in by_name["report.xlsx"]["reason"]
    assert by_name["bad.pdf"]["status"] == "rejected"
    assert "无法读取" in by_name["bad.pdf"]["reason"]
    assert by_name["resume.pdf"]["status"] == "rejected"
    assert "目录层级过深" in by_name["resume.pdf"]["reason"]
    assert by_name["same1.pdf"]["status"] == "accepted"
    assert by_name["same2.pdf"]["status"] == "intra_bundle_duplicate"

    resume_count_before = conn.execute("SELECT COUNT(*) FROM resume").fetchone()[0]
    assert resume_count_before == 1

    second = client.post(
        "/api/resumes/upload",
        data={"job_id": "j1", "sample_class": "synthetic"},
        files=[("files", ("bundle.zip", zdata, "application/zip"))],
    ).json()["results"]

    assert second == first
    assert conn.execute("SELECT COUNT(*) FROM resume").fetchone()[0] == resume_count_before
```

- [ ] **Step 2: 验证**

```bash
python -m pytest tests/test_channel_bundle_e2e.py -q
```

预期输出：`1 passed`，exit code 0。

---

## 收口自检（run-build 前）

```bash
python -m pytest \
  tests/test_bundle_unpack.py \
  tests/test_source_detect.py \
  tests/test_resume_source_schema.py \
  tests/test_resume_upload_zip.py \
  tests/test_resume_source_correction.py \
  tests/test_upload_page_zip.py \
  tests/test_channel_bundle_e2e.py \
  tests/test_upload_page.py \
  tests/test_db_migration.py -q
```

预期：全部 passed，exit code 0。

**下一步：** 用 `run-build` 执行本计划（`scripts/codex_sdd_runner.py` 按 `### Task N:` 抽取任务、两阶段 review）。本计划含全部实现与测试代码，run-build 会先提取到临时目录做端到端提取验证（spec-to-plan 第 6 节的动作后移到执行期，因为本会话边界禁止写 `app/**`/`tests/**`）。
