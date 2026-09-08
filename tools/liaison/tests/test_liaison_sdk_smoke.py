"""路线 ① 的冒烟测试：SDK 装在 liaison venv 里时，它必须真的能用。

⚠️ **在根 venv 里这条用例会 skip**，这是刻意的：aibot SDK 只进 tools/liaison/.venv，
⛔ 不进根 requirements.txt（design D10 的依赖隔离）。skip 不是漏测——它在
tools/liaison/.venv 的那次运行里会真跑。两处运行方式见 tools/liaison/README.md。

⛔ 不要为了"让根 venv 也能跑"把 SDK 加进根 requirements.txt，那会让本服务的依赖
被推到 .51 并在那边安装，正是整个 D10 要挡住的事。
"""

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
FINDINGS = REPO_ROOT / "docs" / "findings" / "2026-09-08-aibot-sdk-py314-兼容性.md"

# ⚠️ 发行名 ≠ import 名：PyPI 上是 wecom-aibot-python-sdk，import 进来是 aibot。
DISTRIBUTION_NAME = "wecom-aibot-python-sdk"
MODULE_NAME = "aibot"


def test_findings_records_route_one():
    """路线 ① 成立的唯一依据是 findings 里那一行结论，⛔ 不靠谁记得。"""
    assert FINDINGS.is_file(), "缺 SDK 兼容性 findings，Task 2 没做完"
    lines = [
        line for line in FINDINGS.read_text(encoding="utf-8").splitlines()
        if line.startswith("**路线结论：")
    ]
    assert len(lines) == 1, f"findings 里的路线结论行不唯一: {lines}"
    assert "①" in lines[0], f"当前结论不是路线 ①，本文件不该被执行: {lines[0]}"


def test_requirements_pins_exact_sdk_version():
    """⛔ 不许写 >= / ~= / 不带版本号。版本漂移会让'实测过的那份'与'装上的那份'不是一回事。"""
    text = (REPO_ROOT / "tools" / "liaison" / "requirements.txt").read_text(encoding="utf-8")
    pins = re.findall(rf"^{re.escape(DISTRIBUTION_NAME)}==([0-9][^\s#]*)", text, re.MULTILINE)
    assert len(pins) == 1, f"requirements.txt 里没有恰好一条钉死版本的 SDK 依赖: {pins}"
    loose = re.findall(rf"^{re.escape(DISTRIBUTION_NAME)}(?!==)", text, re.MULTILINE)
    assert not loose, "SDK 依赖出现了非 == 的版本约束"


def test_sdk_imports_and_exposes_connection_surface():
    """SDK 真装上时（liaison venv），建连所需的三样参数必须都在。"""
    module = pytest.importorskip(
        MODULE_NAME,
        reason="aibot SDK 只装在 tools/liaison/.venv，根 venv 里 skip 是预期行为",
    )
    assert hasattr(module, "WSClient")
    assert hasattr(module, "WSClientOptions")
    annotations = module.WSClientOptions.__annotations__
    for field in ("bot_id", "secret", "heartbeat_interval", "max_reconnect_attempts"):
        assert field in annotations, f"WSClientOptions 缺字段 {field}"


def test_installed_sdk_version_matches_the_pin():
    """装上的发行版本必须与钉死的那个一致。

    ⚠️ 校验对象是**发行版本**（importlib.metadata），⛔ 不是模块的 __version__——
    实测两者对不上（包里写 1.0.0，发行版本是 1.0.2），拿 __version__ 校验会误判。
    """
    pytest.importorskip(MODULE_NAME, reason="根 venv 不装 SDK，skip 是预期行为")
    import importlib.metadata

    installed = importlib.metadata.version(DISTRIBUTION_NAME)
    text = (REPO_ROOT / "tools" / "liaison" / "requirements.txt").read_text(encoding="utf-8")
    pinned = re.findall(rf"^{re.escape(DISTRIBUTION_NAME)}==([0-9][^\s#]*)", text, re.MULTILINE)[0]
    assert installed == pinned, f"装上的是 {installed}，清单钉的是 {pinned}"
