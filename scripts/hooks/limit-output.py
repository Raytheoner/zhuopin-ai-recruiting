#!/usr/bin/env python3
"""Claude Code `PreToolUse(Bash)` hook —— 拦下「一次把大文件倒进上下文」的读法（Token 治理 Phase 4，0916G）。

依据（`docs/token治理/baseline-0901-0915.md`「大回显来源 Top 15」，09-01～09-15）：
  `Bash:sed` 114 次 / 1.72 MB 居首，`cat SKILL.md` 10 次 / 0.27 MB 也在榜——都是一次读几十 KB。
  读进来的字节会留在对话里，之后每一轮都要重付 cache read。
只拦**榜上真实出现、且能在执行前算出输出大小**的四种形态（⛔ 不凭想象加规则）：
  cat FILE… ／ sed -n 'A,Bp' FILE ／ head -n N FILE ／ tail -n N FILE
输出估算 > 20 KB 即拦（exit 2，stderr 反馈给模型，由它改成窄范围读法）。

放行（宁可漏拦，不许误拦——误拦会让无头泳道卡住）：
  - 管道里不是最后一段（后面有 grep/head/wc 等收窄）、或带重定向（> file，输出不进上下文）
  - 文件不存在 / 解析不了 / 任何异常 —— 一律放行（fail-open）
  - 命令里写了 `# allow-big-output` —— 显式声明「确需全文」
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

LIMIT = 20_000
ESCAPE = "# allow-big-output"


def fsize(path, cwd):
    p = path if os.path.isabs(path) else os.path.join(cwd, path)
    return os.path.getsize(p) if os.path.isfile(p) else None


def lines_bytes(path, cwd, start, end):
    p = path if os.path.isabs(path) else os.path.join(cwd, path)
    if not os.path.isfile(p):
        return None
    n = 0
    with open(p, "rb") as fh:
        for i, line in enumerate(fh, 1):
            if i < start:
                continue
            if end is not None and i > end:
                break
            n += len(line)
    return n


def tail_bytes(path, cwd, count):
    p = path if os.path.isabs(path) else os.path.join(cwd, path)
    if not os.path.isfile(p):
        return None
    with open(p, "rb") as fh:
        data = fh.readlines()
    return sum(len(l) for l in data[-count:]) if count > 0 else 0


def estimate(tokens, cwd):
    """返回 (估算字节, 描述)；无法判断返回 (None, None)。"""
    if not tokens:
        return None, None
    cmd, args = os.path.basename(tokens[0]), tokens[1:]
    if cmd == "cat":
        files = [a for a in args if not a.startswith("-")]
        if not files or any(a == "-" for a in files):
            return None, None
        sizes = [fsize(f, cwd) for f in files]
        if any(s is None for s in sizes):
            return None, None
        return sum(sizes), f"cat {' '.join(files)}"
    if cmd == "sed":
        if "-n" not in args and not any(a.startswith("-n") for a in args):
            return None, None
        rest = [a for a in args if not a.startswith("-")]
        if len(rest) != 2:
            return None, None
        script, f = rest
        m = re.fullmatch(r"(\d+),(\d+|\$)p", script.strip())
        if not m:
            return None, None
        end = None if m.group(2) == "$" else int(m.group(2))
        return lines_bytes(f, cwd, int(m.group(1)), end), f"sed -n '{script}' {f}"
    if cmd in ("head", "tail"):
        count, f, i = 10, None, 0
        while i < len(args):
            a = args[i]
            if a == "-n" and i + 1 < len(args):
                count = args[i + 1]; i += 2; continue
            if re.fullmatch(r"-n?\d+", a):
                count = a.lstrip("-n"); i += 1; continue
            if a.startswith("-"):
                return None, None          # -c 等其它形态不判
            if f is not None:
                return None, None          # 多文件不判
            f = a; i += 1
        if f is None:
            return None, None
        try:
            count = int(str(count).lstrip("+"))
        except ValueError:
            return None, None
        if cmd == "head":
            return lines_bytes(f, cwd, 1, count), f"head -n {count} {f}"
        return tail_bytes(f, cwd, count), f"tail -n {count} {f}"
    return None, None


def check(command, cwd):
    if ESCAPE in command:
        return None
    # 按 ; && || 切成独立命令；带管道的只看不了收窄与否，整条放行
    for part in re.split(r"&&|\|\||;|\n", command):
        part = part.strip()
        if not part or "|" in part or re.search(r"(^|\s)\d*>{1,2}", part) or "<<" in part:
            continue
        try:
            tokens = shlex.split(part)
        except ValueError:
            continue
        if tokens and tokens[0] == "cd" and len(tokens) > 1:
            nxt = tokens[1] if os.path.isabs(tokens[1]) else os.path.join(cwd, tokens[1])
            cwd = nxt if os.path.isdir(nxt) else cwd
            continue
        size, desc = estimate(tokens, cwd)
        if size is not None and size > LIMIT:
            return (f"⛔ 已拦截：`{desc}` 会把约 {size // 1024} KB 倒进上下文（上限 {LIMIT // 1000} KB），之后每一轮都要重付这部分。\n"
                    f"改法：先 `grep -n '<关键词>' <文件>` 定位行号，再 `sed -n '起,止p'` 只读需要的几十行；或用 Read 工具带 offset/limit 分段读。\n"
                    f"确需一次读全文，在命令末尾加注释 `{ESCAPE}` 放行。（Token 治理 Phase 4，scripts/hooks/limit-output.py）")
    return None


def main():
    try:
        data = json.load(sys.stdin)
        if data.get("tool_name") != "Bash":
            return 0
        msg = check(str((data.get("tool_input") or {}).get("command", "")), data.get("cwd") or os.getcwd())
    except Exception:
        return 0                      # fail-open
    if msg:
        print(msg, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
