"""`scripts/hooks/limit-output.py` 行为断言（Token 治理 Phase 4，0916G）。

这个 hook 挂在所有会话（含无头泳道）的每一次 Bash 调用上：漏拦只是少省点 token，
**误拦会让无头泳道卡在一条正常命令上**。所以放行用例比拦截用例多：管道、重定向、heredoc、
小文件、不存在的文件、解析失败、非 Bash 工具、显式放行注释，全部必须 exit 0。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "scripts" / "hooks" / "limit-output.py"


@pytest.fixture
def files(tmp_path):
    big = tmp_path / "big.md"
    big.write_text("".join(f"第{i}行 " + "x" * 90 + "\n" for i in range(1, 1001)), encoding="utf-8")  # ~100KB
    small = tmp_path / "small.md"
    small.write_text("hello\n" * 20, encoding="utf-8")
    return tmp_path


def run(cmd, cwd, tool="Bash", tool_input=None):
    payload = {"tool_name": tool, "tool_input": tool_input if tool_input is not None else {"command": cmd}, "cwd": str(cwd)}
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True, text=True, timeout=20)


@pytest.mark.parametrize("cmd", [
    "cat big.md",
    "cd . && cat big.md",
    "sed -n '1,900p' big.md",
    "sed -n '100,$p' big.md",
    "head -n 800 big.md",
    "tail -700 big.md",
    "git status; cat big.md",
])
def test_blocks_big_reads(files, cmd):
    r = run(cmd, files)
    assert r.returncode == 2, (cmd, r.stderr)
    assert "grep -n" in r.stderr and "allow-big-output" in r.stderr


@pytest.mark.parametrize("cmd", [
    "cat small.md",
    "sed -n '1,50p' big.md",
    "head -n 20 big.md",
    "tail -50 big.md",
    "cat big.md | grep 第5行",
    "cat big.md | head -5",
    "cat big.md > /tmp/copy.md",
    "cat > new.md <<'EOF'\nhi\nEOF",
    "cat nosuchfile.md",
    "sed -i 's/a/b/' big.md",
    "grep -n x big.md",
    "cat big.md  # allow-big-output",
    "echo 'unterminated",
    "",
])
def test_allows_safe_forms(files, cmd):
    r = run(cmd, files)
    assert r.returncode == 0, (cmd, r.stderr)


def test_other_tools_ignored(files):
    assert run("cat big.md", files, tool="Grep").returncode == 0


def test_read_whole_big_file_blocked(files):
    r = run(None, files, tool="Read", tool_input={"file_path": str(files / "big.md")})
    assert r.returncode == 2 and "offset" in r.stderr


@pytest.mark.parametrize("inp", [
    {"file_path": "big.md", "offset": 300, "limit": 100},
    {"file_path": "big.md", "limit": 200},
    {"file_path": "big.md", "offset": 10},
    {"file_path": "small.md"},
    {"file_path": "nosuch.md"},
    {"file_path": ""},
])
def test_read_allowed_forms(files, inp):
    inp = dict(inp)
    if inp["file_path"]:
        inp["file_path"] = str(files / inp["file_path"])
    assert run(None, files, tool="Read", tool_input=inp).returncode == 0


def test_read_big_limit_blocked(files):
    r = run(None, files, tool="Read", tool_input={"file_path": str(files / "big.md"), "limit": 2000})
    assert r.returncode == 2


def test_garbage_stdin_fails_open():
    r = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True, text=True, timeout=20)
    assert r.returncode == 0
