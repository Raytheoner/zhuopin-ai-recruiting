#!/usr/bin/env python3
"""给 .claude/settings.json 的 permissions.allow 加 run-lanes.sh 白名单（幂等）。

为什么要一个脚本：2026-09-03 实证，CC Desktop 的 Auto Mode 分类器把「无人值守起会自主
commit/push 的子 session」判为高风险，看护者的 `nohup run-lanes.sh` 被拦；而看护者（以及
Cowork 侧的 Claude）去改 settings.json 也被拦——修改安全配置属 Claude 不可代项。
⇒ 只能 Shao Peishen 本人执行。本脚本只增不删，跑多少次结果一样。

用法（在任意目录、任意终端）：
    python3 /Users/paulshao/Projects/HumanResource/scripts/allow_run_lanes.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RULES = [
    "Bash(git push:*)",
    "Bash(bash docs/openers/run-lanes.sh:*)",
    "Bash(nohup bash docs/openers/run-lanes.sh:*)",
]


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    path = repo / ".claude" / "settings.json"
    if not path.exists():
        print(f"✗ 找不到 {path}", file=sys.stderr)
        return 1

    before = path.read_text(encoding="utf-8")
    data = json.loads(before)
    perms = data.setdefault("permissions", {})
    allow = perms.setdefault("allow", [])
    if not isinstance(allow, list):
        print("✗ permissions.allow 不是数组，拒绝改动", file=sys.stderr)
        return 1

    added = [r for r in RULES if r not in allow]
    allow.extend(added)

    after = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    json.loads(after)  # 写盘前自验
    if added:
        path.write_text(after, encoding="utf-8")

    print(f"文件：{path}")
    print("本次新增：" + (", ".join(added) if added else "无（已全部存在）"))
    print("当前 permissions.allow：")
    for r in allow:
        print(f"  - {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
