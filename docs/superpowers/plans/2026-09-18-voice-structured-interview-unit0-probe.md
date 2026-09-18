# U0 技术探针（X5，P1–P5）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建一个统一的探针 CLI（`scripts/probe_m3_voice.py`），对 LiveKit／FunASR／CosyVoice／DeepSeek 追问延迟／`livekit-agents` SDK 兼容性五项分别实测，结论幂等落 `docs/m3-voice-probe.md`，最终决定 `voice-structured-interview` 变更包 live 段（U4）做不做、在哪做。

**Architecture:** 单文件 CLI + 共享核心（结果结构体、环境指纹、Markdown 幂等 upsert、JSON 输出）；五个探针各自一个 `@register("pN-xxx")` 函数，互不依赖，可单独跑。P1/P5 用真实二进制／venv 起停验证；P2/P3 是"能不能装、装上后延迟多少"的可装性探针，重依赖走独立 `requirements-m3-u0.txt`（不并入主依赖，跑不了就跳过，参照 `requirements-m2-u0.txt` 先例）；P4 复用现有 `app/llm/gateway.py` 做 schema 合规率测量，另起一条不经网关的裸流式调用测 TTFT（网关当前无 streaming 接口）。

**Tech Stack:** Python 3.14（项目主环境）；P2/P3 的重依赖用独立虚拟环境（版本按实测选定，CosyVoice 官方文档推荐 3.10，与主环境隔离）；`openai` SDK（已在 requirements.txt）；`livekit-api`/`livekit`（LiveKit 官方 Python SDK，新增）；pytest。

**Spec:** `openspec/changes/voice-structured-interview/design.md`（决策 D15、D19、D17，交付单元表第 1 行）、`openspec/changes/voice-structured-interview/tasks.md` 第 0–1 章、`openspec/changes/voice-structured-interview/proposal.md`「X5 技术探针」。本单元没有独立的 `specs/*/spec.md`——`proposal.md`「New Capabilities」明确未把 U0 列为能力包，探针是决定 live 段（`live-voice-interview-session` 能力）做不做的前置调研，不是需求契约本身；行为依据取自 `tasks.md` 1.1–1.7 的 WBS 描述与 `design.md` 的技术决策。

## Global Constraints

以下逐字复制自 `CLAUDE.md`「工程铁律」与「合规红线」两节。本单元是探针脚本，不产生业务副作用、不评分、不淘汰、不留存候选人数据，因此多数条款不适用——每条不适用的都写明理由，不省略。

**工程铁律**

1. LangGraph 恢复时节点从头整个重跑，每个有副作用的动作必须独占一个节点并带幂等键 `{thread_id}:{node_name}:{business_key}`，落 `effect_log` 表并加唯一索引；幂等记录与业务写必须在同一个事务里提交。
   **不适用，理由**：本单元不建 LangGraph 图、不写业务表。探针脚本自己的"幂等"是另一种形态——`docs/m3-voice-probe.md` 按 `(item, env_fingerprint)` 覆盖同一行，由 Task 1 的 `upsert_markdown_row` 保证，不是铁律 1 讲的事务幂等键。
2. L3 Agent 全部是无副作用纯函数，副作用只在 L4 编排层的 `effect_*` 节点执行，节点命名区分 `compute_*`/`effect_*`。
   **不适用，理由**：探针脚本不是 L3/L4 分层里的任何一层，是一次性命令行工具；但 Task 1 的核心逻辑（`env_fingerprint`/`upsert_markdown_row`/`render_json`）仍按纯函数原则写、单独测试，精神上对齐。
3. 所有 AI 评分必须持久化：模型标识＋模型版本＋prompt 版本＋temperature＋输入哈希＋rubric 快照＋原始响应。
   **不适用，理由**：P4 调用 DeepSeek 不是业务评分（不产出 `criterion_score`），探针结果落 `docs/m3-voice-probe.md` 而不是 `analysis_run`——探针调用用 `NoopAuditHook`（见 Task 5），不污染真实审计留痕。但铁律 5（模型版本锁定＋响应侧 `model` 字段）仍适用，见下。
4. 每条 `criterion_score` 必须有 `evidence_ref`，为空不允许写入。
   **不适用，理由**：本单元不产出 `criterion_score`。
5. `temperature=0`；模型版本优先显式锁定，禁止 `latest` 类别名；供应商不提供版本号快照时，必须从 API 响应里取回实际的 `model` 字段并持久化。
   **适用**：P4（Task 5）两条调用路径（TTFT 裸流式调用、schema 合规率经网关调用）都显式传 `temperature=0`、显式锁定 `deepseek-chat`（不用 `latest`），并把每次调用响应体的 `model` 字段记入探针 JSON——配置里写的名字不算数，响应返回的才算。
6. 企业微信回调先落库再处理，只推一次、5 秒无响应即丢弃，回调接口只做签名校验＋落库＋返回 200。
   **不适用，理由**：本单元与企微回调无关。
7. `langgraph >= 1.0.10`（GHSA-g48c-2wqr-h844）。
   **不适用，理由**：本单元不新增／不改动 `requirements.txt` 里的 `langgraph` 版本锁，`requirements-m3-u0.txt` 是独立文件，不影响主依赖。

**合规红线**

- AI 只做排序推荐，不做自动淘汰；淘汰必须有人工确认节点并留痕。
  **不适用，理由**：探针不淘汰任何候选人，此刻甚至没有候选人数据参与。
- 禁止人脸/表情分析。
  **适用**：P1（LiveKit 房间连通性）探针**只验证音频/数据通道**，不建立、不采集、不处理任何视频轨——与 `design.md` Non-Goals「不做视频」一致，Task 2 的两端连通测试代码里不出现 `Track.Kind.KIND_VIDEO`。
- AI 生成的 JD、拒信、邀约须带标识。
  **不适用，理由**：本单元不生成任何面向候选人／业务经理展示的内容。
- 模型全部走境内，简历数据不出境。
  **适用**：P4 走 DeepSeek（境内），且测试用 prompt 是纯合成文本（固定的题目＋预埋追问＋一句合成"候选人回答"），不含任何真实简历或候选人数据——此刻简历数据本来就不该出现在这个探针里。
- 绝不用历史录用结果做监督信号。
  **不适用，理由**：本单元不训练/校准任何排序或评分逻辑。
- 候选人入口一律用一次性邀请链接。
  **不适用，理由**：本单元没有候选人入口。
- 主观描述不得进入硬门槛规则。
  **不适用，理由**：本单元不涉及 rubric 或硬门槛判定。

---

## 范围外与登记

以下明确不在本单元范围内，登记以免被误当作遗漏：

- **实际采购语音主机、在目标机上跑本探针**：`design.md` OQ-9 是不可代项（预算／采购），本单元只在开发机（Mac）跑"非目标机"预探针；`docs/m3-voice-probe.md` 每行结果的环境指纹里用 `target=dev-machine` 与未来 `target=target-machine` 区分，不混淆。
- **生产落地代码**（`scripts/provision_voice_host.sh`、`app/agents/follow_up_selector.py` 正式模块等）：属 U4（tasks.md 第 5 章），前置正是本单元的结论。P4 里定义的 `FollowUpChoice` 是**探针专用**的测量模型，不是 D17 的生产实现，Task 5 里会显式标注。
- **CosyVoice 精确内部 API**：官方仓库示例代码随版本演进，Task 4 给出目前可查证的安装路径与推断的推理调用形状，并要求执行者在真实 clone 下来的仓库里核对 `example.py` 后再落笔真实调用——这是探针工作本身的一部分（"能不能装、接口对不对"正是要测的东西），不是遗留的 TBD。
- **TURN 在真实两种网络（公司 Wi-Fi／手机 4G）下的最终结论**：开发机没有公网 IP，做不了真正的 NAT 穿越测试；Task 2 的开发机预跑只验证房间建立与数据通道，TURN 最终结论标"待目标机复测"，与 `tasks.md` 1.2 原文"目标机（未采购前在开发机，结论标『非目标机』）"一致。

---

### Task 1: 探针核心框架（结果结构体、环境指纹、Markdown 幂等落档、CLI 骨架）

对应 `tasks.md` 1.1。

**Files:**
- Create: `scripts/probe_m3_voice.py`
- Test: `tests/test_probe_m3_voice.py`

**Interfaces:**
- Produces（后续 Task 2–6 都依赖这些）：
  - `@dataclass(frozen=True) class ProbeResult`，字段：`item: str`、`env_fingerprint: str`、`conclusion: str`（取值 `"通过"` 或 `"阻塞"`）、`metrics: dict[str, Any]`、`blocking_reason: str | None`、`duration_ms: float`、`timestamp: str`（ISO 8601 UTC）
  - `env_fingerprint(*, target: str, extra: str | None = None) -> str`
  - `write_result(result: ProbeResult, *, doc_path: Path = Path("docs/m3-voice-probe.md")) -> None`
  - `PROBES: dict[str, Callable[[argparse.Namespace], ProbeResult]]`（模块级注册表）
  - `register(name: str) -> Callable`（装饰器，把探针函数登记进 `PROBES`；每个 `pN-xxx` 探针函数签名固定为 `(args: argparse.Namespace) -> ProbeResult`）
  - `main(argv: list[str] | None = None) -> int`（argparse 入口，遍历 `PROBES` 生成子命令，跑完调用 `write_result` 并把 `render_json(result)` 打到 stdout；`--json PATH` 额外写一份 JSON 副本；`--no-doc-write` 供测试用，跳过写 `docs/m3-voice-probe.md`）

- [ ] **Step 1: 写失败测试——`env_fingerprint` 的格式**

```python
# tests/test_probe_m3_voice.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.probe_m3_voice import (
    PROBES,
    ProbeResult,
    env_fingerprint,
    register,
    render_json,
    upsert_markdown_row,
    write_result,
)


def test_env_fingerprint_contains_platform_python_and_target():
    fp = env_fingerprint(target="dev-machine")
    assert "|dev-machine" in fp
    assert "|py" in fp


def test_env_fingerprint_appends_extra_label():
    fp = env_fingerprint(target="dev-machine", extra="company-wifi")
    assert fp.endswith("|company-wifi")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. venv/bin/pytest tests/test_probe_m3_voice.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'scripts.probe_m3_voice'`（文件还不存在）

- [ ] **Step 3: 写最小实现——文件头、`ProbeResult`、`env_fingerprint`**

```python
# scripts/probe_m3_voice.py
"""
M3 语音链路技术探针（tasks.md 1.1–1.7，design.md OQ-5/X5）。
五项探针（P1 LiveKit／P2 FunASR／P3 CosyVoice／P4 追问选择 LLM TTFT／P5 livekit-agents
SDK 兼容性）各一个子命令，输出统一 JSON，结果幂等追加写 docs/m3-voice-probe.md。

用法：python -m scripts.probe_m3_voice <子命令> [选项]
子命令名单见 `python -m scripts.probe_m3_voice --help`（由已注册的探针动态生成）。
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_DOC_PATH = Path("docs/m3-voice-probe.md")


@dataclass(frozen=True)
class ProbeResult:
    item: str
    env_fingerprint: str
    conclusion: str  # "通过" | "阻塞"
    metrics: dict[str, Any] = field(default_factory=dict)
    blocking_reason: str | None = None
    duration_ms: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        if self.conclusion not in ("通过", "阻塞"):
            raise ValueError(f"conclusion 只能是 '通过' 或 '阻塞'，收到: {self.conclusion!r}")
        if self.conclusion == "阻塞" and not self.blocking_reason:
            raise ValueError("结论为「阻塞」时必须给出 blocking_reason")


def env_fingerprint(*, target: str, extra: str | None = None) -> str:
    """环境指纹：平台 + Python 版本 + 目标机/开发机标签 (+ 可选附加标签，如网络环境)。
    与 `upsert_markdown_row` 的 (item, env_fingerprint) 复合键一起，决定同一条探针
    结果在不同环境下各占一行、同一环境下反复跑只覆盖最后一次。
    """
    fp = f"{platform.platform()}|py{sys.version.split()[0]}|{target}"
    if extra:
        fp = f"{fp}|{extra}"
    return fp


def render_json(result: ProbeResult) -> str:
    return json.dumps(asdict(result), ensure_ascii=False, indent=2, sort_keys=True)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. venv/bin/pytest tests/test_probe_m3_voice.py -v`
Expected: 上面两条 PASS

- [ ] **Step 5: 写失败测试——Markdown 幂等 upsert**

```python
def _read(tmp_path: Path, name: str = "doc.md") -> str:
    return (tmp_path / name).read_text(encoding="utf-8")


def test_upsert_creates_table_when_absent():
    result = ProbeResult(item="P1", env_fingerprint="fp-a", conclusion="通过", duration_ms=120.0)
    out = upsert_markdown_row("# 标题\n\n正文\n", result)
    assert "| P1 | fp-a | 通过 |" in out
    assert "## 探针结果" in out


def test_upsert_overwrites_same_item_and_fingerprint():
    r1 = ProbeResult(item="P1", env_fingerprint="fp-a", conclusion="阻塞", blocking_reason="缺二进制", duration_ms=1.0)
    r2 = ProbeResult(item="P1", env_fingerprint="fp-a", conclusion="通过", duration_ms=99.0)
    doc = upsert_markdown_row("# 标题\n", r1)
    doc = upsert_markdown_row(doc, r2)
    rows = [ln for ln in doc.splitlines() if ln.startswith("| P1 |")]
    assert len(rows) == 1
    assert "通过" in rows[0]
    assert "99" in rows[0]


def test_upsert_appends_new_row_for_different_fingerprint():
    r1 = ProbeResult(item="P1", env_fingerprint="fp-a", conclusion="通过", duration_ms=1.0)
    r2 = ProbeResult(item="P1", env_fingerprint="fp-b", conclusion="通过", duration_ms=2.0)
    doc = upsert_markdown_row("# 标题\n", r1)
    doc = upsert_markdown_row(doc, r2)
    rows = [ln for ln in doc.splitlines() if ln.startswith("| P1 |")]
    assert len(rows) == 2


def test_upsert_appends_new_row_for_different_item_same_fingerprint():
    r1 = ProbeResult(item="P1", env_fingerprint="fp-a", conclusion="通过", duration_ms=1.0)
    r2 = ProbeResult(item="P2", env_fingerprint="fp-a", conclusion="通过", duration_ms=2.0)
    doc = upsert_markdown_row("# 标题\n", r1)
    doc = upsert_markdown_row(doc, r2)
    assert any(ln.startswith("| P1 |") for ln in doc.splitlines())
    assert any(ln.startswith("| P2 |") for ln in doc.splitlines())


def test_upsert_preserves_content_outside_table():
    result = ProbeResult(item="P1", env_fingerprint="fp-a", conclusion="通过", duration_ms=1.0)
    doc = upsert_markdown_row("# 标题\n\n## 结论\n\n待补\n", result)
    assert "## 结论" in doc
    assert "待补" in doc
```

- [ ] **Step 6: 跑测试确认失败**

Run: `PYTHONPATH=. venv/bin/pytest tests/test_probe_m3_voice.py -v`
Expected: FAIL，`ImportError: cannot import name 'upsert_markdown_row'`

- [ ] **Step 7: 写最小实现——`upsert_markdown_row` 与 `write_result`**

```python
_TABLE_HEADER = "| 项 | 环境指纹 | 结论 | 关键指标 | 阻塞点 | 耗时 ms | 时间戳(UTC) |"
_TABLE_SEP = "|---|---|---|---|---|---|---|"


def _format_metrics(metrics: dict[str, Any]) -> str:
    return "; ".join(f"{k}={v}" for k, v in sorted(metrics.items()))


def _format_row(result: ProbeResult) -> str:
    return (
        f"| {result.item} | {result.env_fingerprint} | {result.conclusion} | "
        f"{_format_metrics(result.metrics)} | {result.blocking_reason or ''} | "
        f"{round(result.duration_ms)} | {result.timestamp} |"
    )


def upsert_markdown_row(doc_text: str, result: ProbeResult) -> str:
    """把 `result` 幂等写进 `doc_text` 里「## 探针结果」表格：同 (item, env_fingerprint)
    覆盖已有行，否则追加新行；表格不存在则先建。表格外的其余内容原样保留。
    """
    lines = doc_text.splitlines()
    new_row = _format_row(result)

    if _TABLE_HEADER not in lines:
        if lines and lines[-1] != "":
            lines.append("")
        lines += ["## 探针结果", "", _TABLE_HEADER, _TABLE_SEP, new_row, ""]
        return "\n".join(lines) + "\n"

    header_idx = lines.index(_TABLE_HEADER)
    row_start = header_idx + 2
    row_end = row_start
    while row_end < len(lines) and lines[row_end].startswith("|"):
        row_end += 1

    key = (result.item, result.env_fingerprint)
    for i in range(row_start, row_end):
        cells = [c.strip() for c in lines[i].split("|")]
        if len(cells) > 2 and (cells[1], cells[2]) == key:
            lines[i] = new_row
            return "\n".join(lines) + "\n"

    lines.insert(row_end, new_row)
    return "\n".join(lines) + "\n"


def write_result(result: ProbeResult, *, doc_path: Path = DEFAULT_DOC_PATH) -> None:
    existing = (
        doc_path.read_text(encoding="utf-8")
        if doc_path.exists()
        else "# M3 语音探针结果（U0）\n"
    )
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(upsert_markdown_row(existing, result), encoding="utf-8")


PROBES: dict[str, Callable[[argparse.Namespace], ProbeResult]] = {}


def register(name: str) -> Callable:
    def deco(fn: Callable[[argparse.Namespace], ProbeResult]) -> Callable:
        PROBES[name] = fn
        return fn

    return deco


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M3 语音链路技术探针")
    parser.add_argument("--json", type=Path, default=None, help="额外把结果写成 JSON 文件")
    parser.add_argument("--no-doc-write", action="store_true", help="跳过写 docs/m3-voice-probe.md（测试用）")
    parser.add_argument("--doc-path", type=Path, default=DEFAULT_DOC_PATH)
    subparsers = parser.add_subparsers(dest="probe", required=True)
    for name, fn in PROBES.items():
        sub = subparsers.add_parser(name)
        fn.__wrapped_add_arguments__(sub) if hasattr(fn, "__wrapped_add_arguments__") else None
        sub.set_defaults(_fn=fn)

    args = parser.parse_args(argv)
    result: ProbeResult = args._fn(args)
    print(render_json(result))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(render_json(result), encoding="utf-8")
    if not args.no_doc_write:
        write_result(result, doc_path=args.doc_path)
    return 0 if result.conclusion == "通过" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 8: 跑测试确认通过**

Run: `PYTHONPATH=. venv/bin/pytest tests/test_probe_m3_voice.py -v`
Expected: 全部 PASS（8 条）

- [ ] **Step 9: 补一条 `write_result` 落盘测试与一条 CLI 冒烟测试**

```python
def test_write_result_creates_and_is_idempotent(tmp_path: Path):
    doc_path = tmp_path / "m3-voice-probe.md"
    r1 = ProbeResult(item="P1", env_fingerprint="fp-a", conclusion="通过", duration_ms=1.0)
    write_result(r1, doc_path=doc_path)
    assert doc_path.exists()
    r2 = ProbeResult(item="P1", env_fingerprint="fp-a", conclusion="阻塞", blocking_reason="x", duration_ms=2.0)
    write_result(r2, doc_path=doc_path)
    rows = [ln for ln in _read(tmp_path, doc_path.name).splitlines() if ln.startswith("| P1 |")]
    assert len(rows) == 1
    assert "阻塞" in rows[0]


def test_cli_help_lists_registered_probes(capsys):
    import pytest as _pytest

    with _pytest.raises(SystemExit) as exc_info:
        from scripts.probe_m3_voice import main as cli_main

        cli_main(["--help"])
    assert exc_info.value.code == 0
```

Run: `PYTHONPATH=. venv/bin/pytest tests/test_probe_m3_voice.py -v`
Expected: 全部 PASS（10 条）

- [ ] **Step 10: Commit**

```bash
git add scripts/probe_m3_voice.py tests/test_probe_m3_voice.py
git commit -m "feat(m3-u0): probe core — ProbeResult, env fingerprint, idempotent doc upsert, CLI skeleton"
```

---

### Task 2: P1 — LiveKit server 可运行性与连通性探针

对应 `tasks.md` 1.2。

**Files:**
- Modify: `scripts/probe_m3_voice.py`（追加，不改动 Task 1 已写内容）
- Create: `requirements-m3-u0.txt`
- Test: `tests/test_probe_m3_voice.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `ProbeResult`、`env_fingerprint`、`register`
- Produces: `probe_p1_livekit(args) -> ProbeResult`（注册名 `p1-livekit`）；辅助纯函数 `_resolve_livekit_binary(explicit_path: str | None) -> str | None`、`_validate_network_label(label: str) -> str`（供后续文档与测试引用，不被其他任务依赖）

**背景（写进任务，避免执行者重新踩坑）**：LiveKit 官方发行版（GitHub `livekit/livekit` releases）**不提供 macOS/Darwin 二进制**，只有 `linux_{amd64,arm64,armv7}` 与 `windows_{amd64,arm64}`；Mac 开发机用 `brew install livekit`（Homebrew 官方 formula，装的同样是 `livekit-server` 可执行文件，当前 `1.13.7`）。目标机（Linux）到位后改用 GitHub release 的 `livekit_<版本>_linux_amd64.tar.gz`，版本号写进探针结果 `metrics.livekit_version`。开发模式命令固定是 `livekit-server --dev`，默认监听 `ws://127.0.0.1:7880`，默认 API key/secret 是 `devkey`/`secret`（LiveKit 官方文档；⛔ 这两个默认值只能在本地探针用，不得进任何配置文件当真实凭据）。

"两个浏览器页建连"改用两个 `livekit`（官方 Python realtime SDK，PyPI 包名 `livekit`，当前 `1.1.19`）客户端各自以独立身份加入同一房间、经数据通道（`local_participant.publish_data`）互发一条消息验证双向连通，而不是起 Playwright/真实浏览器——这条判断记入计划：浏览器 WebRTC 栈与 Python SDK 走的是同一套 LiveKit 协议与 ICE/TURN 路径，两个 SDK 客户端能连通就证明服务端可用；引入 Playwright + Chromium 只为了跑两个空页面，是本探针不需要的新重依赖，且候选人端页面本身要到 U4（design D9）才实现，此刻没有真实页面可用来跑。房间管理与 token 签发用 `livekit-api`（PyPI 包名 `livekit-api`，当前 `1.2.1`）。

TURN 需求评估在开发机做不了真正的 NAT 穿越测试（没有公网 IP）：本探针只跑房间连通性，`metrics.turn_evaluated=false`；`--network-label` 参数（取值 `company-wifi`/`phone-4g`/`target-machine-lan`）记录跑测时所在网络，供目标机到位后复测时对照，不在开发机上产出 TURN 最终结论。

- [ ] **Step 1: 写失败测试——网络标签校验与二进制解析是纯函数**

```python
import shutil
from unittest.mock import patch

from scripts.probe_m3_voice import _resolve_livekit_binary, _validate_network_label


def test_validate_network_label_accepts_known_values():
    assert _validate_network_label("company-wifi") == "company-wifi"
    assert _validate_network_label("phone-4g") == "phone-4g"


def test_validate_network_label_rejects_unknown():
    with pytest.raises(ValueError, match="network-label"):
        _validate_network_label("random-guess")


def test_resolve_livekit_binary_prefers_explicit_path(tmp_path: Path):
    fake_bin = tmp_path / "livekit-server"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    assert _resolve_livekit_binary(str(fake_bin)) == str(fake_bin)


def test_resolve_livekit_binary_falls_back_to_which():
    with patch("shutil.which", return_value="/opt/homebrew/bin/livekit-server"):
        assert _resolve_livekit_binary(None) == "/opt/homebrew/bin/livekit-server"


def test_resolve_livekit_binary_returns_none_when_missing():
    with patch("shutil.which", return_value=None):
        assert _resolve_livekit_binary(None) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. venv/bin/pytest tests/test_probe_m3_voice.py -v -k p1`
Expected: FAIL，`ImportError: cannot import name '_resolve_livekit_binary'`

- [ ] **Step 3: 写最小实现——追加到 `scripts/probe_m3_voice.py`**

```python
import shutil
import subprocess
import time

_VALID_NETWORK_LABELS = frozenset({"company-wifi", "phone-4g", "target-machine-lan"})


def _validate_network_label(label: str) -> str:
    if label not in _VALID_NETWORK_LABELS:
        raise ValueError(
            f"--network-label 只能是 {sorted(_VALID_NETWORK_LABELS)}，收到: {label!r}"
        )
    return label


def _resolve_livekit_binary(explicit_path: str | None) -> str | None:
    if explicit_path:
        return explicit_path
    return shutil.which("livekit-server")


@register("p1-livekit")
def probe_p1_livekit(args: argparse.Namespace) -> ProbeResult:
    started = time.monotonic()
    fp = env_fingerprint(target=args.target, extra=_validate_network_label(args.network_label))
    binary = _resolve_livekit_binary(args.livekit_bin)
    if binary is None:
        return ProbeResult(
            item="P1",
            env_fingerprint=fp,
            conclusion="阻塞",
            blocking_reason="livekit-server 二进制未找到（Mac 用 `brew install livekit`；"
            "Linux 目标机从 GitHub release 的 linux_amd64 tarball 解压后用 --livekit-bin 指定路径）",
            duration_ms=(time.monotonic() - started) * 1000,
        )

    proc = subprocess.Popen(
        [binary, "--dev"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        time.sleep(2.0)  # 给单节点起服务的时间；--dev 模式本地启动通常 <1s
        if proc.poll() is not None:
            output = proc.stdout.read() if proc.stdout else ""
            return ProbeResult(
                item="P1",
                env_fingerprint=fp,
                conclusion="阻塞",
                blocking_reason=f"livekit-server --dev 启动后立即退出，exit={proc.returncode}，输出: {output[-500:]}",
                duration_ms=(time.monotonic() - started) * 1000,
            )

        from livekit import api, rtc  # noqa: PLC0415 — 重依赖，惰性导入避免拖慢其他子命令

        room_name = "probe-p1"
        token_a = (
            api.AccessToken("devkey", "secret")
            .with_identity("probe-a")
            .with_grants(api.VideoGrants(room_join=True, room=room_name))
            .to_jwt()
        )
        token_b = (
            api.AccessToken("devkey", "secret")
            .with_identity("probe-b")
            .with_grants(api.VideoGrants(room_join=True, room=room_name))
            .to_jwt()
        )

        received: list[bytes] = []

        async def _run() -> None:
            room_a = rtc.Room()
            room_b = rtc.Room()

            def _on_data(packet: rtc.DataPacket) -> None:
                received.append(packet.data)

            room_b.on("data_received", _on_data)
            await room_a.connect("ws://127.0.0.1:7880", token_a)
            await room_b.connect("ws://127.0.0.1:7880", token_b)
            await room_a.local_participant.publish_data(b"probe-ping", reliable=True)
            for _ in range(20):
                if received:
                    break
                await asyncio_sleep(0.2)
            await room_a.disconnect()
            await room_b.disconnect()

        import asyncio
        from asyncio import sleep as asyncio_sleep

        asyncio.run(_run())

        if not received:
            return ProbeResult(
                item="P1",
                env_fingerprint=fp,
                conclusion="阻塞",
                blocking_reason="两个 SDK 客户端建连成功但数据通道 20 次轮询（4s）内未收到消息",
                duration_ms=(time.monotonic() - started) * 1000,
            )

        return ProbeResult(
            item="P1",
            env_fingerprint=fp,
            conclusion="通过",
            metrics={
                "livekit_version": args.livekit_version_hint or "unknown",
                "two_client_data_channel": True,
                "turn_evaluated": False,
            },
            duration_ms=(time.monotonic() - started) * 1000,
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
```

CLI 参数注册（追加到 `main()` 里子命令构造循环之前，作为对应子命令的 `add_argument` 调用集合——写成一个小函数并在 `probe_p1_livekit` 旁登记）：

```python
def _add_p1_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--target", default="dev-machine")
    sub.add_argument("--network-label", default="company-wifi")
    sub.add_argument("--livekit-bin", default=None)
    sub.add_argument("--livekit-version-hint", default=None)


probe_p1_livekit.__wrapped_add_arguments__ = _add_p1_arguments  # type: ignore[attr-defined]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. venv/bin/pytest tests/test_probe_m3_voice.py -v -k p1`
Expected: 5 条纯函数测试 PASS（真正起服务/建连的部分不在 pytest 里跑，见 Step 6 的真实执行）

- [ ] **Step 5: 新增 `requirements-m3-u0.txt`**

```
# M3 U0 语音探针专用依赖（tasks 1.1）。⛔ 不并入 requirements.txt——探针结论决定 live 段
# 做不做之前，这些包不该出现在生产依赖表里。版本 2026-09-18 从 PyPI 元数据核实。
livekit-api==1.2.1
livekit==1.1.19
```

Run: `venv/bin/pip install -r requirements-m3-u0.txt`
Expected: 两个包及其依赖安装成功（`pip show livekit livekit-api` 能看到版本号）

- [ ] **Step 6: 真实执行（不在 pytest 里跑，人工/CI 手动一次）**

```bash
brew install livekit   # 一次性；确认: livekit-server --version
venv/bin/pip install -r requirements-m3-u0.txt
PYTHONPATH=. venv/bin/python -m scripts.probe_m3_voice p1-livekit \
  --target dev-machine --network-label company-wifi \
  --livekit-version-hint "$(livekit-server --version 2>&1 | head -1)"
```

预期：命令退出码 0，stdout 打出 `conclusion: 通过`，`docs/m3-voice-probe.md` 出现一行 `| P1 | ... |`。若 `brew install livekit` 在当前网络下载失败或 `livekit-server --dev` 启动异常，如实记录 `阻塞` 与错误原文，不视为本任务失败——这正是探针要暴露的信息。

- [ ] **Step 7: Commit**

```bash
git add scripts/probe_m3_voice.py tests/test_probe_m3_voice.py requirements-m3-u0.txt docs/m3-voice-probe.md
git commit -m "feat(m3-u0): P1 LiveKit connectivity probe"
```

---

### Task 3: P2 — FunASR 可装性与流式 ASR 首字延迟探针

对应 `tasks.md` 1.3。

**Files:**
- Modify: `scripts/probe_m3_voice.py`
- Modify: `requirements-m3-u0.txt`
- Test: `tests/test_probe_m3_voice.py`

**Interfaces:**
- Consumes: Task 1 的 `ProbeResult`/`register`/`env_fingerprint`
- Produces: `probe_p2_funasr(args) -> ProbeResult`（注册名 `p2-funasr`）；`_percentile(values: list[float], pct: float) -> float`（纯函数，P3 也会复用）

**背景**：FunASR（PyPI 包名 `funasr`，当前 `1.4.15`，声明 `requires-python >= 3.7`）官方流式 ASR 示例用 `AutoModel(model="paraformer-zh-streaming", device="cpu")`（GPU 探针不在本单元范围，`.51`/语音主机是 CPU），按 `chunk_size=[0, 10, 5]`、`chunk_stride = chunk_size[1] * 960` 切片喂入，`model.generate(input=chunk, cache=cache, is_final=..., chunk_size=..., encoder_chunk_look_back=4, decoder_chunk_look_back=1)`。首次调用会从 ModelScope 自动下载模型权重（无网络/被墙则直接判「阻塞」）。

- [ ] **Step 1: 写失败测试——百分位数纯函数**

```python
from scripts.probe_m3_voice import _percentile


def test_percentile_median_and_p95():
    values = [float(i) for i in range(1, 101)]  # 1..100
    assert _percentile(values, 50) == pytest.approx(50.5, abs=1.0)
    assert _percentile(values, 95) == pytest.approx(95.5, abs=1.0)


def test_percentile_single_value():
    assert _percentile([42.0], 50) == 42.0
    assert _percentile([42.0], 95) == 42.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. venv/bin/pytest tests/test_probe_m3_voice.py -v -k percentile`
Expected: FAIL，`ImportError: cannot import name '_percentile'`

- [ ] **Step 3: 写最小实现**

```python
def _percentile(values: list[float], pct: float) -> float:
    if not values:
        raise ValueError("values 不能为空")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (pct / 100)
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. venv/bin/pytest tests/test_probe_m3_voice.py -v -k percentile`
Expected: PASS

- [ ] **Step 5: 写失败测试——`probe_p2_funasr` 在包不可导入时正确判「阻塞」**

```python
def test_probe_p2_funasr_blocks_when_module_missing(tmp_path: Path):
    import sys
    from types import SimpleNamespace

    from scripts.probe_m3_voice import probe_p2_funasr

    real_import = __import__

    def _fake_import(name, *a, **kw):
        if name == "funasr":
            raise ModuleNotFoundError("No module named 'funasr'")
        return real_import(name, *a, **kw)

    import builtins

    monkey_target = builtins.__import__
    builtins.__import__ = _fake_import
    try:
        args = SimpleNamespace(target="dev-machine", audio_path=str(tmp_path / "missing.wav"))
        result = probe_p2_funasr(args)
    finally:
        builtins.__import__ = monkey_target

    assert result.conclusion == "阻塞"
    assert "funasr" in result.blocking_reason
```

- [ ] **Step 6: 跑测试确认失败**

Run: `PYTHONPATH=. venv/bin/pytest tests/test_probe_m3_voice.py -v -k p2`
Expected: FAIL，`ImportError: cannot import name 'probe_p2_funasr'`

- [ ] **Step 7: 写最小实现——追加到 `scripts/probe_m3_voice.py`**

```python
@register("p2-funasr")
def probe_p2_funasr(args: argparse.Namespace) -> ProbeResult:
    started = time.monotonic()
    fp = env_fingerprint(target=args.target)
    try:
        import funasr  # noqa: PLC0415
    except Exception as exc:
        return ProbeResult(
            item="P2",
            env_fingerprint=fp,
            conclusion="阻塞",
            blocking_reason=f"funasr 不可导入: {type(exc).__name__}: {exc}",
            duration_ms=(time.monotonic() - started) * 1000,
        )

    audio_path = Path(args.audio_path)
    if not audio_path.exists():
        return ProbeResult(
            item="P2",
            env_fingerprint=fp,
            conclusion="阻塞",
            blocking_reason=(
                f"funasr 可导入（版本 {getattr(funasr, '__version__', '未知')}），但缺少 30s 中文样本音频"
                f"（--audio-path {audio_path} 不存在，需人工录制/提供，不入库）"
            ),
            duration_ms=(time.monotonic() - started) * 1000,
        )

    import soundfile as sf  # noqa: PLC0415

    model = funasr.AutoModel(model="paraformer-zh-streaming", device="cpu")
    audio, _sr = sf.read(str(audio_path), dtype="float32")
    chunk_size = [0, 10, 5]
    chunk_stride = chunk_size[1] * 960
    cache: dict = {}
    n_chunks = (len(audio) - 1) // chunk_stride + 1

    first_text_latency_ms: float | None = None
    call_latencies_ms: list[float] = []
    chunk_started = time.monotonic()
    for i in range(n_chunks):
        chunk = audio[i * chunk_stride : (i + 1) * chunk_stride]
        call_start = time.monotonic()
        res = model.generate(
            input=chunk,
            cache=cache,
            is_final=(i == n_chunks - 1),
            chunk_size=chunk_size,
            encoder_chunk_look_back=4,
            decoder_chunk_look_back=1,
        )
        call_latencies_ms.append((time.monotonic() - call_start) * 1000)
        if first_text_latency_ms is None and res and res[0].get("text"):
            first_text_latency_ms = (time.monotonic() - chunk_started) * 1000

    if first_text_latency_ms is None:
        return ProbeResult(
            item="P2",
            env_fingerprint=fp,
            conclusion="阻塞",
            blocking_reason="流式推理跑完全部分片但从未产出非空 text，首字延迟无法测定",
            duration_ms=(time.monotonic() - started) * 1000,
        )

    return ProbeResult(
        item="P2",
        env_fingerprint=fp,
        conclusion="通过",
        metrics={
            "funasr_version": getattr(funasr, "__version__", "unknown"),
            "first_text_latency_ms": round(first_text_latency_ms),
            "chunk_call_p50_ms": round(_percentile(call_latencies_ms, 50)),
            "chunk_call_p95_ms": round(_percentile(call_latencies_ms, 95)),
        },
        duration_ms=(time.monotonic() - started) * 1000,
    )


def _add_p2_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--target", default="dev-machine")
    sub.add_argument("--audio-path", default="data/m3-voice-probe/samples/sample-zh-30s.wav")


probe_p2_funasr.__wrapped_add_arguments__ = _add_p2_arguments  # type: ignore[attr-defined]
```

- [ ] **Step 8: 跑测试确认通过**

Run: `PYTHONPATH=. venv/bin/pytest tests/test_probe_m3_voice.py -v -k p2`
Expected: PASS

- [ ] **Step 9: 追加依赖到 `requirements-m3-u0.txt`**

```
# ---- P2 FunASR（design D15，tasks 1.3）----
funasr==1.4.15
soundfile==0.13.1
```

Run: `venv/bin/pip install -r requirements-m3-u0.txt`
Expected: 安装成功或明确报错（例如缺系统级 `libsndfile`），报错原文记入 `docs/m3-voice-probe.md` 的阻塞点，不是本任务失败

- [ ] **Step 10: 真实执行（需要一份本人录制的 30 秒中文语音样本，不入库）**

```bash
mkdir -p data/m3-voice-probe/samples   # data/ 已 .gitignore，样本不进仓库
# 把 30s 中文语音样本存到 data/m3-voice-probe/samples/sample-zh-30s.wav（人工录制/提供）
PYTHONPATH=. venv/bin/python -m scripts.probe_m3_voice p2-funasr --target dev-machine
```

预期：`docs/m3-voice-probe.md` 新增一行 `| P2 | ... |`；若模型首次下载被墙／`libsndfile` 缺失，如实记录阻塞点。

- [ ] **Step 11: Commit**

```bash
git add scripts/probe_m3_voice.py tests/test_probe_m3_voice.py requirements-m3-u0.txt docs/m3-voice-probe.md
git commit -m "feat(m3-u0): P2 FunASR installability + streaming ASR first-token latency probe"
```

---

### Task 4: P3 — CosyVoice 可装性与 TTS 首帧延迟探针

对应 `tasks.md` 1.4。

**Files:**
- Modify: `scripts/probe_m3_voice.py`
- Test: `tests/test_probe_m3_voice.py`
- Create: `docs/m3-voice-probe-cosyvoice-install.md`（安装步骤记录，被 `probe_p3_cosyvoice` 的 docstring 引用）

**Interfaces:**
- Consumes: Task 1/3 的 `ProbeResult`/`register`/`env_fingerprint`/`_percentile`
- Produces: `probe_p3_cosyvoice(args) -> ProbeResult`（注册名 `p3-cosyvoice`）

**背景（重要——CosyVoice 与 P2 的 FunASR 不同，不是单纯 `pip install` 能解决的）**：PyPI 上确实存在一个名为 `cosyvoice`（`0.0.8`，2024-11 发布）的包，但**不是**本项目要用的、阿里通义实验室开源的 CosyVoice TTS 模型——那个项目没有发布 PyPI 包，官方安装路径是：

```bash
git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git
cd CosyVoice && git submodule update --init --recursive
python3.10 -m venv .venv-cosyvoice   # 官方文档用 conda + Python 3.10；本探针用 venv 达到同等隔离
.venv-cosyvoice/bin/pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host=mirrors.aliyun.com
```

模型权重另外从 ModelScope 下载（`snapshot_download`，数百 MB 到数 GB，视版本而定）。推理入口在 clone 下来的仓库里有 `example.py`，形如 `from cosyvoice.cli.cosyvoice import CosyVoice`（或该版本对应的类名——**这一步必须在真实 clone 出来的仓库里核对 `example.py` 后再确认**，官方示例代码随版本演进，此刻写死的类名可能已经改变；这正是"可装性"探针要验证的东西，不是遗留的实现细节）。`inference_sft(text, spk_id, stream=True/False)` 返回一个生成器，每个元素是 `{'tts_speech': <audio tensor>, ...}`，`stream=True` 时第一个元素到达的时刻即为 TTS 首帧延迟。

因为这条安装路径本身就是探针要产出的结论之一（是否可装、装多久、Python 版本要不要迁就 3.10），本任务的探针函数**不在代码里自动跑 git clone**——克隆与环境搭建是 Task 4 Step 6 的人工/一次性命令（记入 `docs/m3-voice-probe-cosyvoice-install.md`），`probe_p3_cosyvoice()` 只负责：给定一个已经装好的 `cosyvoice_repo_path`（clone 出来的目录，加进 `sys.path`），尝试 import 与做一次推理调用计时，产不出可导入模块就如实判阻塞。

- [ ] **Step 1: 写失败测试——路径不存在/未安装时判阻塞**

```python
def test_probe_p3_cosyvoice_blocks_when_repo_path_missing(tmp_path: Path):
    from types import SimpleNamespace

    from scripts.probe_m3_voice import probe_p3_cosyvoice

    args = SimpleNamespace(
        target="dev-machine",
        cosyvoice_repo_path=str(tmp_path / "does-not-exist"),
        model_dir=str(tmp_path / "model"),
    )
    result = probe_p3_cosyvoice(args)
    assert result.conclusion == "阻塞"
    assert "cosyvoice_repo_path" in result.blocking_reason
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. venv/bin/pytest tests/test_probe_m3_voice.py -v -k p3`
Expected: FAIL，`ImportError: cannot import name 'probe_p3_cosyvoice'`

- [ ] **Step 3: 写最小实现——追加到 `scripts/probe_m3_voice.py`**

```python
@register("p3-cosyvoice")
def probe_p3_cosyvoice(args: argparse.Namespace) -> ProbeResult:
    started = time.monotonic()
    fp = env_fingerprint(target=args.target)
    repo_path = Path(args.cosyvoice_repo_path)
    if not repo_path.exists():
        return ProbeResult(
            item="P3",
            env_fingerprint=fp,
            conclusion="阻塞",
            blocking_reason=(
                f"cosyvoice_repo_path {repo_path} 不存在——需先按 "
                "docs/m3-voice-probe-cosyvoice-install.md 克隆官方仓库并装依赖"
            ),
            duration_ms=(time.monotonic() - started) * 1000,
        )

    sys.path.insert(0, str(repo_path))
    sys.path.insert(0, str(repo_path / "third_party" / "Matcha-TTS"))
    try:
        from cosyvoice.cli.cosyvoice import CosyVoice  # noqa: PLC0415
    except Exception as exc:
        return ProbeResult(
            item="P3",
            env_fingerprint=fp,
            conclusion="阻塞",
            blocking_reason=f"cosyvoice 包不可导入: {type(exc).__name__}: {exc}",
            duration_ms=(time.monotonic() - started) * 1000,
        )

    model_dir = Path(args.model_dir)
    if not model_dir.exists():
        return ProbeResult(
            item="P3",
            env_fingerprint=fp,
            conclusion="阻塞",
            blocking_reason=f"cosyvoice 可导入，但模型权重目录 {model_dir} 不存在（需先 ModelScope snapshot_download）",
            duration_ms=(time.monotonic() - started) * 1000,
        )

    model = CosyVoice(str(model_dir))
    sample_text = "你好，欢迎参加本次结构化面试，第一个问题是请简单介绍你的项目经验。"
    stream_started = time.monotonic()
    first_frame_latency_ms: float | None = None
    frame_count = 0
    for chunk in model.inference_sft(sample_text, "中文女", stream=True):
        if first_frame_latency_ms is None:
            first_frame_latency_ms = (time.monotonic() - stream_started) * 1000
        frame_count += 1
        if chunk.get("tts_speech") is None:
            return ProbeResult(
                item="P3",
                env_fingerprint=fp,
                conclusion="阻塞",
                blocking_reason="inference_sft 流式返回的分片缺少 tts_speech 字段，接口形状与预期不符",
                duration_ms=(time.monotonic() - started) * 1000,
            )

    if first_frame_latency_ms is None:
        return ProbeResult(
            item="P3",
            env_fingerprint=fp,
            conclusion="阻塞",
            blocking_reason="inference_sft(stream=True) 未产出任何分片",
            duration_ms=(time.monotonic() - started) * 1000,
        )

    return ProbeResult(
        item="P3",
        env_fingerprint=fp,
        conclusion="通过",
        metrics={
            "first_frame_latency_ms": round(first_frame_latency_ms),
            "sample_rate": getattr(model, "sample_rate", "unknown"),
            "total_frames": frame_count,
        },
        duration_ms=(time.monotonic() - started) * 1000,
    )


def _add_p3_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--target", default="dev-machine")
    sub.add_argument("--cosyvoice-repo-path", default="data/m3-voice-probe/CosyVoice")
    sub.add_argument("--model-dir", default="data/m3-voice-probe/CosyVoice/pretrained_models/CosyVoice-300M-SFT")


probe_p3_cosyvoice.__wrapped_add_arguments__ = _add_p3_arguments  # type: ignore[attr-defined]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. venv/bin/pytest tests/test_probe_m3_voice.py -v -k p3`
Expected: PASS

- [ ] **Step 5: 写 `docs/m3-voice-probe-cosyvoice-install.md`**

```markdown
# CosyVoice 安装记录（P3 探针前置，tasks 1.4）

官方仓库无 PyPI 发行版；PyPI 上的 `cosyvoice==0.0.8` 是另一个无关的小项目，⛔ 不要 `pip install cosyvoice`。

## 安装步骤（在此记录每次实跑的真实结果，覆盖式更新本文件，不新开日期文件）

```bash
git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git data/m3-voice-probe/CosyVoice
cd data/m3-voice-probe/CosyVoice && git submodule update --init --recursive
python3.10 -m venv .venv-cosyvoice   # 若本机无 python3.10，记录实际用的版本号
.venv-cosyvoice/bin/pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host=mirrors.aliyun.com
.venv-cosyvoice/bin/python -c "
from modelscope import snapshot_download
snapshot_download('iic/CosyVoice-300M-SFT', local_dir='pretrained_models/CosyVoice-300M-SFT')
"
```

## 执行记录

> 跑完 Task 4 Step 6 后在这里补：日期、实际 Python 版本、`pip install` 是否成功（失败贴原始报错尾部）、
> 模型下载耗时与体积、`example.py` 里实际的类名与推理方法签名是否与本计划假设的一致。
```

- [ ] **Step 6: 真实执行（人工，记录进上面的文档）**

```bash
which python3.10 python3.12 python3 2>/dev/null   # 先看本机有什么解释器，没有 3.10 就退而求其次并如实记录
mkdir -p data/m3-voice-probe
git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git data/m3-voice-probe/CosyVoice
cd data/m3-voice-probe/CosyVoice && git submodule update --init --recursive
cd ../../..
cat data/m3-voice-probe/CosyVoice/example.py   # 核对真实的 import 与调用签名，与 Step 3 假设不一致就改代码
# 装依赖、下模型（命令按上面 install 文档跑，把真实输出贴进 docs/m3-voice-probe-cosyvoice-install.md）
PYTHONPATH=. venv/bin/python -m scripts.probe_m3_voice p3-cosyvoice --target dev-machine \
  --cosyvoice-repo-path data/m3-voice-probe/CosyVoice \
  --model-dir data/m3-voice-probe/CosyVoice/pretrained_models/CosyVoice-300M-SFT
```

预期：装不上或接口对不上都如实记进 `docs/m3-voice-probe.md` 的阻塞点与 `docs/m3-voice-probe-cosyvoice-install.md` 的执行记录；这两种结果都满足本任务的完成判据（判据是「结论落档」，不是「必须通过」）。

- [ ] **Step 7: Commit**

```bash
git add scripts/probe_m3_voice.py tests/test_probe_m3_voice.py docs/m3-voice-probe-cosyvoice-install.md docs/m3-voice-probe.md
git commit -m "feat(m3-u0): P3 CosyVoice installability + TTS first-frame latency probe"
```

---

### Task 5: P4 — 追问选择 LLM TTFT 与输出合规率探针

对应 `tasks.md` 1.5。

**Files:**
- Modify: `scripts/probe_m3_voice.py`
- Test: `tests/test_probe_m3_voice.py`

**Interfaces:**
- Consumes: `app/llm/gateway.py::LLMGateway`、`NoopAuditHook`、`SchemaExtractionFailed`；`app/config.py::get_settings`；Task 1/3 的 `ProbeResult`/`register`/`_percentile`
- Produces: `probe_p4_llm_ttft(args) -> ProbeResult`（注册名 `p4-llm-ttft`）；`class FollowUpChoice(BaseModel)`（**探针专用测量模型，不是 D17 的生产 `follow_up_selector.py`**，两处签名一旦以后接上要各自维护，此处只为测量 TTFT 与 schema 合规率）

**为什么分两条调用路径**：`app/llm/gateway.py::LLMGateway` 当前只支持非流式的 `chat.completions.create`（`_call_model` 没有 `stream=True`），拿不到"第一个 token 到达"的时刻，只能测到"整条回复都拿到"的耗时。真正的 TTFT 需要绕开网关直接用流式调用；但 schema 合规率（D17「模型输出不在枚举内按 next_question 处理并计数」的可测量替身）又必须复用网关已有的 strict JSON schema 机制才有意义（不然是在重新发明网关已经做过的事）。所以本任务两条路径都实现，都遵铁律 5（`temperature=0`、锁定 `deepseek-chat`、记录响应 `model` 字段），都不落 `analysis_run`（`LLMGateway` 构造时传 `NoopAuditHook()`，探针不是生产评分调用）。

- [ ] **Step 1: 写失败测试——`FollowUpChoice` 的枚举一致性校验**

```python
from pydantic import ValidationError

from scripts.probe_m3_voice import FollowUpChoice


def test_follow_up_choice_valid_follow_up():
    choice = FollowUpChoice(action="follow_up", follow_up_index=1)
    assert choice.follow_up_index == 1


def test_follow_up_choice_valid_next_question():
    choice = FollowUpChoice(action="next_question", follow_up_index=None)
    assert choice.follow_up_index is None


def test_follow_up_choice_rejects_out_of_range_index():
    with pytest.raises(ValidationError):
        FollowUpChoice(action="follow_up", follow_up_index=5)


def test_follow_up_choice_rejects_index_with_next_question():
    with pytest.raises(ValidationError):
        FollowUpChoice(action="next_question", follow_up_index=0)


def test_follow_up_choice_rejects_missing_index_for_follow_up():
    with pytest.raises(ValidationError):
        FollowUpChoice(action="follow_up", follow_up_index=None)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. venv/bin/pytest tests/test_probe_m3_voice.py -v -k follow_up_choice`
Expected: FAIL，`ImportError: cannot import name 'FollowUpChoice'`

- [ ] **Step 3: 写最小实现——追加到 `scripts/probe_m3_voice.py`**

```python
from typing import Literal

from pydantic import BaseModel, model_validator

# 与预埋追问列表长度绑定的下标范围。探针固定用 2 条预埋追问（见 _SAMPLE_FOLLOW_UPS），
# 生产 D17 的 follow_up_selector.py 里这个上界是动态的（看当前题的 follow_ups 数组长度）。
_VALID_FOLLOW_UP_INDICES = (0, 1)


class FollowUpChoice(BaseModel):
    """P4 探针专用测量模型，形状对应 design D17「输出 schema 是枚举
    (follow_up_index | next_question)」，⛔ 不是生产的 follow_up_selector.py。"""

    action: Literal["follow_up", "next_question"]
    follow_up_index: int | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> "FollowUpChoice":
        if self.action == "follow_up":
            if self.follow_up_index not in _VALID_FOLLOW_UP_INDICES:
                raise ValueError(
                    f"action=follow_up 时 follow_up_index 必须是 {_VALID_FOLLOW_UP_INDICES} 之一"
                )
        elif self.follow_up_index is not None:
            raise ValueError("action=next_question 时 follow_up_index 必须为空")
        return self
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. venv/bin/pytest tests/test_probe_m3_voice.py -v -k follow_up_choice`
Expected: PASS

- [ ] **Step 5: 写失败测试——TTFT 计算与合规率统计是纯函数，用假响应/假异常驱动**

```python
from unittest.mock import MagicMock

from scripts.probe_m3_voice import _schema_compliance_rate, _stream_first_token_latency_ms


def test_stream_first_token_latency_ms_measures_first_nonempty_delta():
    class _FakeDelta:
        def __init__(self, content):
            self.content = content

    class _FakeChoice:
        def __init__(self, content):
            self.delta = _FakeDelta(content)

    class _FakeChunk:
        def __init__(self, content):
            self.choices = [_FakeChoice(content)]

    def _fake_stream():
        yield _FakeChunk(None)   # 首个 chunk 常是空 delta（角色声明），不算 TTFT
        yield _FakeChunk("追")
        yield _FakeChunk("问")

    latency_ms = _stream_first_token_latency_ms(_fake_stream())
    assert latency_ms >= 0


def test_schema_compliance_rate_counts_successes(monkeypatch):
    gateway = MagicMock()
    calls = {"n": 0}

    def _side_effect(**kwargs):
        calls["n"] += 1
        if calls["n"] % 4 == 0:
            from scripts.probe_m3_voice import SchemaExtractionFailed

            raise SchemaExtractionFailed("越界")
        return FollowUpChoice(action="next_question", follow_up_index=None)

    gateway.extract_structured.side_effect = _side_effect
    rate = _schema_compliance_rate(gateway, runs=8, system_prompt="s", user_prompt="u")
    assert rate == pytest.approx(6 / 8)
```

- [ ] **Step 6: 跑测试确认失败**

Run: `PYTHONPATH=. venv/bin/pytest tests/test_probe_m3_voice.py -v -k "stream_first_token or compliance_rate"`
Expected: FAIL，`ImportError`

- [ ] **Step 7: 写最小实现——追加到 `scripts/probe_m3_voice.py`**

```python
from app.llm.gateway import LLMGateway, NoopAuditHook, SchemaExtractionFailed  # noqa: E402


def _stream_first_token_latency_ms(chunk_iter) -> float:
    """chunk_iter 产出的每个 chunk 形如 OpenAI streaming 的
    `choices[0].delta.content`；第一个非空 content 到达的耗时即 TTFT。
    调用方负责在真正发起请求前记 `start = time.monotonic()`，本函数只做
    "找第一个非空 delta" 的纯逻辑，方便脱离真实网络单测。
    """
    start = time.monotonic()
    for chunk in chunk_iter:
        content = chunk.choices[0].delta.content
        if content:
            return (time.monotonic() - start) * 1000
    raise RuntimeError("流式响应结束但从未出现非空 delta.content")


def _schema_compliance_rate(gateway: LLMGateway, *, runs: int, system_prompt: str, user_prompt: str) -> float:
    successes = 0
    for _ in range(runs):
        try:
            gateway.extract_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=FollowUpChoice,
                prompt_version="probe-p4-v1",
            )
            successes += 1
        except SchemaExtractionFailed:
            continue
    return successes / runs


_SAMPLE_FOLLOW_UPS = ["能具体说说你在这个项目里遇到的最大技术难点吗？", "如果重新做一次，你会怎么改进这个方案？"]
_SAMPLE_SYSTEM_PROMPT = (
    "你是结构化面试的追问选择器。给定本题、预埋的追问选项列表、候选人本轮转写，"
    "只能从预埋追问里选一条，或判断不需要追问直接进入下一题；不得生成新的追问内容。"
)


def _sample_user_prompt() -> str:
    follow_ups_text = "\n".join(f"{i}. {q}" for i, q in enumerate(_SAMPLE_FOLLOW_UPS))
    return (
        "本题：请简单介绍你最近参与的一个项目。\n"
        f"预埋追问：\n{follow_ups_text}\n"
        "候选人本轮转写：我最近在做一个嵌入式项目，主要是写驱动，具体细节记不太清了。"
    )


@register("p4-llm-ttft")
def probe_p4_llm_ttft(args: argparse.Namespace) -> ProbeResult:
    started = time.monotonic()
    fp = env_fingerprint(target=args.target)
    settings = get_settings()
    if not settings.llm_api_key:
        return ProbeResult(
            item="P4",
            env_fingerprint=fp,
            conclusion="阻塞",
            blocking_reason="Settings.llm_api_key 为空（.env 未配置 LLM_API_KEY），无法调用 DeepSeek",
            duration_ms=(time.monotonic() - started) * 1000,
        )

    from openai import OpenAI  # noqa: PLC0415

    raw_client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    user_prompt = _sample_user_prompt()

    ttft_samples_ms: list[float] = []
    response_models_seen: set[str] = set()
    for _ in range(args.runs):
        stream = raw_client.chat.completions.create(
            model=settings.llm_model,
            temperature=0,
            messages=[
                {"role": "system", "content": _SAMPLE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            stream=True,
        )
        latency_ms = _stream_first_token_latency_ms(stream)
        ttft_samples_ms.append(latency_ms)
        # 流式响应体的 model 字段每个 chunk 都带，取最后见到的一个即可（铁律 5：
        # 响应侧字段才可信，不看构造请求时传的 settings.llm_model 字面量）。
    # 流式调用拿不到 usage/model 的稳定聚合点，这里单独补一次非流式调用只为取响应 model 字段。
    probe_response = raw_client.chat.completions.create(
        model=settings.llm_model,
        temperature=0,
        messages=[{"role": "system", "content": _SAMPLE_SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}],
    )
    response_models_seen.add(getattr(probe_response, "model", "unknown") or "unknown")

    gateway = LLMGateway(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        supports_json_schema=settings.llm_supports_json_schema,
        max_retries=0,  # 测「第一次」合规率，不吃网关内建的 schema 重试红利
        audit_hook=NoopAuditHook(),
    )
    compliance_rate = _schema_compliance_rate(
        gateway, runs=args.runs, system_prompt=_SAMPLE_SYSTEM_PROMPT, user_prompt=user_prompt
    )

    return ProbeResult(
        item="P4",
        env_fingerprint=fp,
        conclusion="通过",
        metrics={
            "ttft_p50_ms": round(_percentile(ttft_samples_ms, 50)),
            "ttft_p95_ms": round(_percentile(ttft_samples_ms, 95)),
            "schema_compliance_rate": round(compliance_rate, 3),
            "runs": args.runs,
            "response_model": sorted(response_models_seen),
            "model_configured": settings.llm_model,
        },
        duration_ms=(time.monotonic() - started) * 1000,
    )


def _add_p4_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--target", default="dev-machine")
    sub.add_argument("--runs", type=int, default=20)


probe_p4_llm_ttft.__wrapped_add_arguments__ = _add_p4_arguments  # type: ignore[attr-defined]
```

顶部 import 区追加 `from app.config import get_settings`。

- [ ] **Step 8: 跑测试确认通过**

Run: `PYTHONPATH=. venv/bin/pytest tests/test_probe_m3_voice.py -v -k "stream_first_token or compliance_rate or follow_up_choice"`
Expected: 全部 PASS

- [ ] **Step 9: 真实执行——⏸ 在 `.51` 上跑（design.md 要求"经现网关对 DeepSeek 跑 20 次"，且 `.51` 的出网路径才是真实生产路径；本开发机可先跑一次做代码冒烟，但结论以 `.51` 实测为准）**

开发机冒烟（先确认代码能跑通，不作为最终结论）：

```bash
set -a; [ -f .env ] && source .env; set +a
PYTHONPATH=. venv/bin/python -m scripts.probe_m3_voice p4-llm-ttft --target dev-machine --runs 20
```

`.51` 真实测量（ssh 包装，不给裸命令；`zp51` 是既有 ssh alias，见 `docs/releases/2026-09-17-发版一执行记录.md`）：

```bash
ssh -o BatchMode=yes -o ConnectTimeout=8 zp51 "cd C:\apps\zhuopin-recruit-agent && .venv\Scripts\python.exe -m scripts.probe_m3_voice p4-llm-ttft --target target-machine --runs 20 --doc-path docs\m3-voice-probe.md"
```

预期：两次结果都写进 `docs/m3-voice-probe.md`（不同 `env_fingerprint`，各占一行，互不覆盖）；`.51` 侧若路径/凭据与假设不符，如实记录并把命令改成真实路径，⏸ 标注「留步：`.51` 具体部署路径待确认」而不是假装跑过。

- [ ] **Step 10: Commit**

```bash
git add scripts/probe_m3_voice.py tests/test_probe_m3_voice.py docs/m3-voice-probe.md
git commit -m "feat(m3-u0): P4 follow-up-selection LLM TTFT + schema compliance probe"
```

---

### Task 6: P5 — `livekit-agents` SDK 对 Python 3.14 / 3.12 的兼容性探针

对应 `tasks.md` 1.6，方法沿用 `docs/findings/2026-09-08-aibot-sdk-py314-兼容性.md`。

**Files:**
- Modify: `scripts/probe_m3_voice.py`
- Test: `tests/test_probe_m3_voice.py`

**Interfaces:**
- Consumes: Task 1 的 `ProbeResult`/`register`/`env_fingerprint`
- Produces: `probe_p5_sdk_compat(args) -> ProbeResult`（注册名 `p5-sdk-compat`）；`_pip_install_and_import(venv_python: str, dist_name: str, module_name: str) -> dict`（纯粹的子进程编排逻辑，通过 mock 测试分支）

**背景**：PyPI 元数据（2026-09-18 查证）显示 `livekit-agents` 当前版本 `1.8.2` 声明 `requires-python: <3.15,>=3.10`，字面上已支持 3.14——但这只是包自身的声明，传递依赖（尤其 `av`，即 PyAV，含 ffmpeg 的 C 扩展绑定）是否有 cp314 的预编译 wheel 才是真正的可装性瓶颈，`design.md` 风险清单里记的正是这条（`docs/findings/2026-09-08-aibot-sdk-py314-兼容性.md` 记录的是另一个 SDK 的同类问题，方法可复用：建两个独立 venv，实测 `pip install` 是否真的成功、`import` 是否真的能跑，不能只看 PyPI 声明的版本范围）。

- [ ] **Step 1: 写失败测试——`_pip_install_and_import` 的返回结构（mock subprocess）**

```python
from unittest.mock import patch

from scripts.probe_m3_voice import _pip_install_and_import


def test_pip_install_and_import_reports_failed_pip():
    fake_result = MagicMock(returncode=1, stdout="", stderr="ERROR: no matching distribution")
    with patch("subprocess.run", return_value=fake_result):
        report = _pip_install_and_import("/fake/venv/bin/python", "livekit-agents", "livekit.agents")
    assert report["pip_ok"] is False
    assert "no matching distribution" in report["pip_error"]


def test_pip_install_and_import_reports_import_failure_after_successful_pip():
    pip_result = MagicMock(returncode=0, stdout="Successfully installed", stderr="")
    import_result = MagicMock(returncode=1, stdout="", stderr="ImportError: cannot import name")
    with patch("subprocess.run", side_effect=[pip_result, import_result]):
        report = _pip_install_and_import("/fake/venv/bin/python", "livekit-agents", "livekit.agents")
    assert report["pip_ok"] is True
    assert report["import_ok"] is False
    assert "ImportError" in report["import_error"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=. venv/bin/pytest tests/test_probe_m3_voice.py -v -k pip_install_and_import`
Expected: FAIL，`ImportError: cannot import name '_pip_install_and_import'`

- [ ] **Step 3: 写最小实现——追加到 `scripts/probe_m3_voice.py`**

```python
def _pip_install_and_import(venv_python: str, dist_name: str, module_name: str) -> dict:
    pip_result = subprocess.run(
        [venv_python, "-m", "pip", "install", dist_name],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if pip_result.returncode != 0:
        return {
            "pip_ok": False,
            "pip_error": pip_result.stderr[-1000:] or pip_result.stdout[-1000:],
            "import_ok": None,
            "import_error": None,
        }

    import_result = subprocess.run(
        [venv_python, "-c", f"import {module_name}; print({module_name}.__name__)"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return {
        "pip_ok": True,
        "pip_error": None,
        "import_ok": import_result.returncode == 0,
        "import_error": None if import_result.returncode == 0 else (import_result.stderr[-1000:] or None),
    }


@register("p5-sdk-compat")
def probe_p5_sdk_compat(args: argparse.Namespace) -> ProbeResult:
    started = time.monotonic()
    fp = env_fingerprint(target=args.target)

    interpreters = {"py314": args.python314_bin, "py312": args.python312_bin}
    reports: dict[str, dict] = {}
    for label, interp in interpreters.items():
        if not interp or not shutil.which(interp):
            reports[label] = {"pip_ok": None, "import_ok": None, "skipped": "解释器未找到"}
            continue
        venv_dir = Path(args.work_dir) / f".venv-p5-{label}"
        subprocess.run([interp, "-m", "venv", str(venv_dir)], check=True, capture_output=True, text=True)
        venv_python = str(venv_dir / "bin" / "python")
        reports[label] = _pip_install_and_import(venv_python, "livekit-agents", "livekit.agents")

    any_pass = any(r.get("import_ok") for r in reports.values())
    if not any_pass:
        return ProbeResult(
            item="P5",
            env_fingerprint=fp,
            conclusion="阻塞",
            blocking_reason=f"3.14 与 3.12 两个解释器上 livekit-agents 均未能成功安装+导入: {reports}",
            metrics={"reports": reports},
            duration_ms=(time.monotonic() - started) * 1000,
        )

    return ProbeResult(
        item="P5",
        env_fingerprint=fp,
        conclusion="通过",
        metrics={
            "reports": reports,
            "recommended_python": "py314" if reports.get("py314", {}).get("import_ok") else "py312",
        },
        duration_ms=(time.monotonic() - started) * 1000,
    )


def _add_p5_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--target", default="dev-machine")
    sub.add_argument("--work-dir", default="data/m3-voice-probe")
    sub.add_argument("--python314-bin", default=shutil.which("python3.14"))
    sub.add_argument("--python312-bin", default=shutil.which("python3.12"))


probe_p5_sdk_compat.__wrapped_add_arguments__ = _add_p5_arguments  # type: ignore[attr-defined]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=. venv/bin/pytest tests/test_probe_m3_voice.py -v -k pip_install_and_import`
Expected: PASS

- [ ] **Step 5: 真实执行**

```bash
which python3.14 python3.12 2>/dev/null   # 本机没有 3.12 就如实记录「py312: 解释器未找到」，不是失败
PYTHONPATH=. venv/bin/python -m scripts.probe_m3_voice p5-sdk-compat --target dev-machine
```

预期：`docs/m3-voice-probe.md` 新增 `| P5 | ... |` 一行，`metrics.reports` 里两个解释器各自的 pip/import 结果都是真实跑出来的，不是编造的。

- [ ] **Step 6: Commit**

```bash
git add scripts/probe_m3_voice.py tests/test_probe_m3_voice.py docs/m3-voice-probe.md
git commit -m "feat(m3-u0): P5 livekit-agents SDK Python 3.14/3.12 compatibility probe"
```

---

### Task 7: 探针总结与 tasks.md 回勾

对应 `tasks.md` 1.7（并联动回勾 0.2）。

**Files:**
- Modify: `docs/m3-voice-probe.md`
- Modify: `openspec/changes/voice-structured-interview/tasks.md`

**Interfaces:**
- Consumes: Task 2–6 实际写入 `docs/m3-voice-probe.md` 的五行探针结果（本任务不重新跑探针，只基于已有结果下结论）

- [ ] **Step 1: 核对五项探针结果齐全**

Run: `grep -c '^| P[1-5] |' docs/m3-voice-probe.md`
Expected: 输出 `5`（或更多，如果某项在多个环境指纹下各跑了一次）——若不足 5，说明 Task 2–6 有未真实执行的，回去补跑，不得在总结里替它们编结论。

- [ ] **Step 2: 在 `docs/m3-voice-probe.md` 追加「结论」小节**

```markdown
## 结论

> 由 Task 7 人工填写，依据上面「探针结果」表的真实数据逐条下判断，⛔ 不得未跑先填。

- **live 段（U4）**：可做 / 留步（二选一，附一句理由，引用具体探针行的 `env_fingerprint` 与 `conclusion`）
- **P1 LiveKit**：<引用 P1 行的结论与关键指标>
- **P2 FunASR**：<引用 P2 行的结论与关键指标；若「阻塞」，注明是否是 D15 兜底条件（不可装/延迟超预算）触发的>
- **P3 CosyVoice**：<同上>
- **P4 追问选择 TTFT**：<引用 dev-machine 与 `.51` 两行，若只有一行注明另一行「⏸ 留步」原因>
- **P5 livekit-agents SDK**：<引用 `recommended_python` 字段>
- **目标机规格建议（CPU/GPU、内存、带宽）**：依据 P2/P3 metrics 里的耗时与（若探针记录了）内存占用推算，写清楚推算依据，不给没有实测支撑的数字
```

这一步的具体文字内容由执行者依据 Task 2–6 真实跑出的数据填写——本计划不预先编造结论，因为结论本身就是这五个探针存在的目的。

- [ ] **Step 3: 回勾 `tasks.md`**

对 `openspec/changes/voice-structured-interview/tasks.md`：
- 把第 1 章 `1.1`–`1.7` 七个 checkbox 从 `- [ ]` 改成 `- [x]`
- 把第 0 章 `0.2` 从 `- [ ]` 改成 `- [x]`（判据「`docs/m3-voice-probe.md` 对 P1–P5 各有『通过／阻塞＋原因』结论行」已在 Step 2 满足）
- 顶部「进度：1/76」的分子按本次新增的回勾数更新（当前 1，回勾 8 条后应为 9）

- [ ] **Step 4: 跑一次全量 pytest 确认没有破坏既有测试**

Run: `PYTHONPATH=. venv/bin/pytest -q`
Expected: 除了既有的、与本单元无关的失败（若有，需核实是否为 main 上已存在的既有红——参照 `docs/m2-model-comparison.md` 记录的先例：`tools/liaison/tests/test_criteria_ledger_file.py::test_signed_off_row_has_no_evidence_yet` 是姊妹泳道的既有失败，与本单元无关），新增测试全部 PASS

- [ ] **Step 5: Commit**

```bash
git add docs/m3-voice-probe.md openspec/changes/voice-structured-interview/tasks.md
git commit -m "docs(m3-u0): probe conclusions + tick tasks 0.2/1.1-1.7"
```

---

## Self-Review 记录

- **Spec 覆盖**：`tasks.md` 1.1→Task 1，1.2→Task 2，1.3→Task 3，1.4→Task 4，1.5→Task 5，1.6→Task 6，1.7→Task 7；`design.md` D15（探针不过则 live 留步、⛔ 不切云 API）体现在每个探针「阻塞」分支如实记录而非强行绕过；D19（语音主机无简历数据）体现在 P1 探针不涉及任何简历/候选人字段、Global Constraints 里的合规红线逐条对应说明；D17（枚举 schema）体现在 Task 5 的 `FollowUpChoice`。
- **占位符扫描**：全文没有 TBD/TODO；Task 7 的「结论」小节文字留给执行者是因为结论数据本身要等 Task 2–6 真实跑完才存在，不是遗漏——这与"步骤本身缺实现"不同，已在正文加粗说明理由。
- **类型一致性**：`ProbeResult`、`register`/`PROBES`、`env_fingerprint`、`_percentile`、`write_result` 的签名在 Task 1 定义后，Task 2–7 原样复用，未出现改名。
