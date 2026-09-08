"""结构性门禁断言：本服务的代码与依赖必须到不了 `.51`。

这三条断言守的是 design D10 的核心手法——**让清单本身承担约束**。它们全部只读
仓库里已有的文件，不依赖任何运行时状态，因此在任何机器上结果都一样。

⛔ 断言失败时不要改断言。失败意味着有人把本服务的依赖塞进了会被同步的清单，
或者把 `tools` 加进了同步白名单——那正是这几条要挡住的事。
"""

import pathlib
import re
import tomllib

# tools/liaison/tests/test_x.py → parents[0]=tests, [1]=liaison, [2]=tools, [3]=仓库根
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

# 本服务的依赖里，任何一个出现在会被同步的清单里都是事故。
FORBIDDEN_IN_SYNCED_MANIFESTS = (
    "wecom-aibot-python-sdk",
    "wecom_aibot_python_sdk",
    "websockets",
)


def _sync_paths() -> list[str]:
    """从 sync-to-server.sh 里解析出 SYNC_PATHS 数组的字面量条目。"""
    text = (REPO_ROOT / "sync-to-server.sh").read_text(encoding="utf-8")
    match = re.search(r"SYNC_PATHS=\((.*?)\n\)", text, re.DOTALL)
    assert match is not None, "sync-to-server.sh 里找不到 SYNC_PATHS=( ... ) 数组"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_tools_is_not_in_sync_paths():
    """`tools/` 不得进同步白名单——这是本服务"不可能被误部署"的唯一依据。"""
    entries = _sync_paths()
    assert entries, "SYNC_PATHS 解析出来是空的，解析逻辑坏了"
    assert "tools" not in entries
    assert not any(e == "tools/" or e.startswith("tools/") for e in entries)


def test_root_requirements_has_no_liaison_dependency():
    """根 requirements.txt 会被同步且在 .51 上被 pip install，本服务的依赖不得进入。"""
    text = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for forbidden in FORBIDDEN_IN_SYNCED_MANIFESTS:
        assert forbidden not in text, f"根 requirements.txt 里出现了本服务的依赖: {forbidden}"


def test_pyproject_declares_no_runtime_dependencies():
    """pyproject.toml 也在 SYNC_PATHS 里。本服务的依赖同样不得从这里溜过去。"""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    declared = project.get("dependencies", [])
    joined = " ".join(declared).lower()
    for forbidden in FORBIDDEN_IN_SYNCED_MANIFESTS:
        assert forbidden not in joined, f"pyproject 依赖里出现了本服务的依赖: {forbidden}"


def test_liaison_requirements_file_exists_and_is_independent():
    """本服务的依赖清单必须独立存在，且不是空文件（空清单等于没有隔离对象）。"""
    path = REPO_ROOT / "tools" / "liaison" / "requirements.txt"
    assert path.is_file(), "缺 tools/liaison/requirements.txt"
    content = path.read_text(encoding="utf-8")
    meaningful = [
        line for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert meaningful, "tools/liaison/requirements.txt 里一条依赖都没有"


def test_readme_states_the_three_boundaries():
    """README 必须写死三条边界，评审时不需要去翻 proposal 才知道这是什么东西。"""
    text = (REPO_ROOT / "tools" / "liaison" / "README.md").read_text(encoding="utf-8")
    for phrase in ("开发期值守工具", "永不部署", "不是产品功能"):
        assert phrase in text, f"README 缺边界表述: {phrase}"
