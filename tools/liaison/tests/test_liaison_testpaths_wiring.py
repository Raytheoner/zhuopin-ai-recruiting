"""本目录必须被根 pyproject 的 testpaths 收进去。

不接进去，这套测试就只有"记得手敲路径"的人跑得到——而这种失效是静默的：
不报错、不失败，只是从此没人跑，直到某次改动悄悄破坏了依赖隔离才爆出来。

⛔ 不要改成"另起一套跑法"来绕过这条断言（独立 pytest.ini / 独立 CI 步骤）。
两条测试通道是同一个问题换了个更贵的形式（opener 约束 6）。
"""

import pathlib
import tomllib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

LIAISON_TESTPATH = "tools/liaison/tests"


def _ini_options() -> dict:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["tool"]["pytest"]["ini_options"]


def test_liaison_tests_are_in_root_testpaths():
    testpaths = _ini_options()["testpaths"]
    assert "tests" in testpaths, "根 tests/ 不能被挤掉"
    assert LIAISON_TESTPATH in testpaths, (
        "tools/liaison/tests 没进根 testpaths，全量 pytest 跑不到本服务的测试"
    )


def test_pythonpath_makes_the_tools_package_importable():
    """`from tools.liaison...` 能 import，靠的就是这一条。"""
    assert "." in _ini_options()["pythonpath"]


def test_no_liaison_dependency_leaked_into_pyproject():
    """本 Task 只许动 testpaths。依赖仍然只能在 tools/liaison/requirements.txt 里。"""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = " ".join(data["project"].get("dependencies", [])).lower()
    for forbidden in ("wecom-aibot-python-sdk", "websockets"):
        assert forbidden not in declared
