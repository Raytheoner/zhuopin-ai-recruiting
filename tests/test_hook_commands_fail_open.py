"""项目 hook 命令在脚本缺失时必须放行（2026-09-16 0916S 实证）。

泳道在 worktree 里跑，`$CLAUDE_PROJECT_DIR` 指向 worktree；单元末条执行 `git worktree remove` 后，
hook 脚本路径随之消失，`python3 <不存在的文件>` 退出码 2 ＝「阻断」，于是本会话后续每一次 Bash 都被拦死。
所以每条 hook 命令都要先判脚本存在，不存在即 exit 0。
"""

import json
import subprocess
from pathlib import Path

SETTINGS = Path(__file__).resolve().parent.parent / ".claude" / "settings.json"


def commands():
    d = json.loads(SETTINGS.read_text(encoding="utf-8"))
    for entries in d.get("hooks", {}).values():
        for e in entries:
            for h in e.get("hooks", []):
                if h.get("type") == "command":
                    yield h["command"]


def test_every_hook_command_passes_when_project_dir_is_gone(tmp_path):
    cmds = list(commands())
    assert cmds
    for c in cmds:
        r = subprocess.run(["bash", "-c", c], input="{}", capture_output=True, text=True, timeout=20,
                           env={"CLAUDE_PROJECT_DIR": str(tmp_path / "removed-worktree"), "PATH": "/usr/bin:/bin"})
        assert r.returncode == 0, (c, r.stderr)
