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
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, model_validator

from app.config import get_settings

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


_TABLE_HEADER = "| 项 | 环境指纹 | 结论 | 关键指标 | 阻塞点 | 耗时 ms | 时间戳(UTC) |"
_TABLE_SEP = "|---|---|---|---|---|---|---|"


def _escape_cell(value: str) -> str:
    """Escape pipe characters in markdown table cells to preserve split("|") integrity.
    Uses HTML entity encoding (&#124;) so split("|") won't break on escaped pipes.
    """
    return value.replace("|", "&#124;")


def _unescape_cell(value: str) -> str:
    """Unescape pipe characters from markdown table cells."""
    return value.replace("&#124;", "|")


def _format_metrics(metrics: dict[str, Any]) -> str:
    formatted = "; ".join(f"{k}={v}" for k, v in sorted(metrics.items()))
    return _escape_cell(formatted)


def _format_row(result: ProbeResult) -> str:
    return (
        f"| {_escape_cell(result.item)} | {_escape_cell(result.env_fingerprint)} | {_escape_cell(result.conclusion)} | "
        f"{_format_metrics(result.metrics)} | {_escape_cell(result.blocking_reason or '')} | "
        f"{round(result.duration_ms)} | {_escape_cell(result.timestamp)} |"
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
        if len(cells) > 2 and (_unescape_cell(cells[1]), _unescape_cell(cells[2])) == key:
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
    # livekit-server --dev 在真实连接场景下会持续写 stdout/stderr 日志；如果没有
    # 消费者持续读取，一旦超过 OS 管道缓冲区（macOS 上约 64KB）子进程的日志写入会
    # 阻塞，进而拖住它处理新连接的 goroutine，导致 SDK 端「signal timeout」——
    # 这是实测踩到的真坑（2026-09-18），不是网络/环境问题。用后台线程持续排空，
    # 只保留最后 200 行供早退出时诊断用。
    _tail: deque[str] = deque(maxlen=200)

    def _drain_stdout() -> None:
        if proc.stdout is None:
            return
        for line in proc.stdout:
            _tail.append(line)

    drain_thread = threading.Thread(target=_drain_stdout, daemon=True)
    drain_thread.start()
    try:
        time.sleep(2.0)  # 给单节点起服务的时间；--dev 模式本地启动通常 <1s
        if proc.poll() is not None:
            output = "".join(_tail)
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
            try:
                await room_a.connect("ws://127.0.0.1:7880", token_a)
                await room_b.connect("ws://127.0.0.1:7880", token_b)
                await room_a.local_participant.publish_data(b"probe-ping", reliable=True)
                for _ in range(20):
                    if received:
                        break
                    await asyncio_sleep(0.2)
            finally:
                # 尽力断开已建立的连接；不掩盖上面真正的异常（原异常经 finally 正常
                # 继续传播），只是避免"建连一半就失败"时把已连上的一端晾在那。
                for room in (room_a, room_b):
                    try:
                        await room.disconnect()
                    except Exception:  # noqa: BLE001 — 断开失败不是本探针关心的信息
                        pass

        import asyncio
        from asyncio import sleep as asyncio_sleep

        try:
            asyncio.run(_run())
        except Exception as exc:  # noqa: BLE001 — LiveKit SDK 连接/数据通道失败需要
            # 转成阻塞结论而不是让异常穿透 probe_p1_livekit，否则 main() 走不到
            # write_result，docs/m3-voice-probe.md 不会更新，操作者只看到裸 traceback。
            return ProbeResult(
                item="P1",
                env_fingerprint=fp,
                conclusion="阻塞",
                blocking_reason=f"LiveKit 客户端建连或数据通道交互失败: {exc!r}",
                duration_ms=(time.monotonic() - started) * 1000,
            )

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
        drain_thread.join(timeout=1)


def _add_p1_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--target", default="dev-machine")
    sub.add_argument("--network-label", default="company-wifi")
    sub.add_argument("--livekit-bin", default=None)
    sub.add_argument("--livekit-version-hint", default=None)


probe_p1_livekit.__wrapped_add_arguments__ = _add_p1_arguments  # type: ignore[attr-defined]


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

    # 这一段是本探针存在的意义所在的"资源可能不可用"三件套：模型构建触发 ModelScope
    # 首次下载（无网络/被墙会抛异常）、soundfile 读取依赖系统级 libsndfile（缺失会抛
    # 异常）、generate 调用本身可能因显存/依赖版本不匹配等原因失败。任何一步异常都要
    # 落成「阻塞」结论而不是穿透 write_result()，否则操作者只看到裸 traceback、
    # docs/m3-voice-probe.md 也不会有这一行——与 probe_p1_livekit 的既有模式一致。
    try:
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
    except Exception as exc:  # noqa: BLE001 — 模型下载/加载/推理的任何失败都要转成
        # 阻塞结论而不是让异常穿透 probe_p2_funasr，否则 main() 走不到 write_result，
        # docs/m3-voice-probe.md 不会更新，操作者只看到裸 traceback。
        return ProbeResult(
            item="P2",
            env_fingerprint=fp,
            conclusion="阻塞",
            blocking_reason=(
                f"模型加载/音频读取/流式推理失败: {type(exc).__name__}: {exc}"
            ),
            duration_ms=(time.monotonic() - started) * 1000,
        )

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

    # 模型构建（触发权重加载/显卡初始化）与流式推理调用都可能因显存不足、权重损坏、
    # 依赖版本不匹配等原因失败。任何一步异常都要落成「阻塞」结论而不是穿透
    # probe_p3_cosyvoice，否则 main() 走不到 write_result，docs/m3-voice-probe.md
    # 不会更新，操作者只看到裸 traceback——与 probe_p1_livekit/probe_p2_funasr 的
    # 既有模式一致（2026-09-18 task-review 对 P2 的发现同样适用于本探针）。
    try:
        model = CosyVoice(str(model_dir))
        sample_text = "你好，欢迎参加本次结构化面试，第一个问题是请简单介绍你的项目经验。"
        stream_started = time.monotonic()
        first_frame_latency_ms: float | None = None
        frame_count = 0
        missing_tts_speech = False
        for chunk in model.inference_sft(sample_text, "中文女", stream=True):
            if first_frame_latency_ms is None:
                first_frame_latency_ms = (time.monotonic() - stream_started) * 1000
            frame_count += 1
            if chunk.get("tts_speech") is None:
                missing_tts_speech = True
                break
    except Exception as exc:  # noqa: BLE001 — 模型加载/流式推理的任何失败都要转成
        # 阻塞结论而不是让异常穿透 probe_p3_cosyvoice。
        return ProbeResult(
            item="P3",
            env_fingerprint=fp,
            conclusion="阻塞",
            blocking_reason=f"模型加载/流式推理失败: {type(exc).__name__}: {exc}",
            duration_ms=(time.monotonic() - started) * 1000,
        )

    if missing_tts_speech:
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


def _schema_compliance_rate(
    gateway: LLMGateway, *, runs: int, system_prompt: str, user_prompt: str
) -> tuple[float, set[str]]:
    """返回 (合规率, 这条路径上见到的响应 model 集合)。用
    `extract_structured_with_meta` 而不是 `extract_structured`——后者的薄封装
    直接丢弃 `LLMCallMeta`（见 app/llm/gateway.py:339-356），而
    `LLMCallMeta.response_model` 正是铁律 5 要求持久化的"响应侧实际模型标识"，
    合规率这条路径不能因为调用了封装版就漏记它。
    """
    successes = 0
    response_models_seen: set[str] = set()
    for _ in range(runs):
        try:
            _parsed, meta = gateway.extract_structured_with_meta(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=FollowUpChoice,
                prompt_version="probe-p4-v1",
            )
            successes += 1
            if meta.response_model:
                response_models_seen.add(meta.response_model)
        except SchemaExtractionFailed:
            continue
    return successes / runs, response_models_seen


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
    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001 — .env 配置本身构不出来（字段类型不对、
        # validate_model_version() 拒绝了 latest 别名等）同样要转成阻塞结论，
        # 不能让异常穿透 probe_p4_llm_ttft。
        return ProbeResult(
            item="P4",
            env_fingerprint=fp,
            conclusion="阻塞",
            blocking_reason=f"get_settings() 失败（.env 配置有误）: {type(exc).__name__}: {exc}",
            duration_ms=(time.monotonic() - started) * 1000,
        )

    if not settings.llm_api_key:
        return ProbeResult(
            item="P4",
            env_fingerprint=fp,
            conclusion="阻塞",
            blocking_reason="Settings.llm_api_key 为空（.env 未配置 LLM_API_KEY），无法调用 DeepSeek",
            duration_ms=(time.monotonic() - started) * 1000,
        )

    # 流式/非流式调用、网关构造、合规率统计的任何一步都可能因网络故障、鉴权失败、
    # 供应商 5xx、流式响应从未出现非空 delta（_stream_first_token_latency_ms 的
    # RuntimeError）等原因失败。任何一步异常都要落成「阻塞」结论而不是穿透
    # probe_p4_llm_ttft，否则 main() 走不到 write_result，docs/m3-voice-probe.md
    # 不会更新，操作者只看到裸 traceback——与 probe_p1_livekit/probe_p2_funasr/
    # probe_p3_cosyvoice 的既有模式一致（brief 的 Step 7 参考代码本身没包这层，
    # Task 2/3/4 的 task-review 已经在前三个探针上发现并修过同一类缺口）。
    try:
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
        compliance_rate, compliance_response_models = _schema_compliance_rate(
            gateway, runs=args.runs, system_prompt=_SAMPLE_SYSTEM_PROMPT, user_prompt=user_prompt
        )
        response_models_seen |= compliance_response_models
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(
            item="P4",
            env_fingerprint=fp,
            conclusion="阻塞",
            blocking_reason=f"LLM 流式/非流式调用或合规率统计失败: {type(exc).__name__}: {exc}",
            duration_ms=(time.monotonic() - started) * 1000,
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
        try:
            subprocess.run([interp, "-m", "venv", str(venv_dir)], check=True, capture_output=True, text=True)
            venv_python = str(venv_dir / "bin" / "python")
            reports[label] = _pip_install_and_import(venv_python, "livekit-agents", "livekit.agents")
        except Exception as exc:  # noqa: BLE001 — venv 创建（check=True 失败时抛
            # CalledProcessError）或 pip/import 子进程本身（如 TimeoutExpired）的任何
            # 异常都要转成该解释器的失败结果，而不是让异常穿透 probe_p5_sdk_compat，
            # 否则 main() 走不到 write_result，docs/m3-voice-probe.md 不会更新——
            # 与 P1/P2/P3/P4 的既有降级模式一致。两个解释器的 venv 相互独立，一个
            # 失败不应阻断另一个继续被评估，所以异常只影响当前 label 这一条。
            reports[label] = {
                "pip_ok": None,
                "import_ok": None,
                "skipped": f"venv 创建或 pip/import 子进程失败: {type(exc).__name__}: {exc}",
            }

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
