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


def _add_p1_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--target", default="dev-machine")
    sub.add_argument("--network-label", default="company-wifi")
    sub.add_argument("--livekit-bin", default=None)
    sub.add_argument("--livekit-version-hint", default=None)


probe_p1_livekit.__wrapped_add_arguments__ = _add_p1_arguments  # type: ignore[attr-defined]


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
