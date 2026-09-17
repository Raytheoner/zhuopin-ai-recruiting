"""`scripts/install_liaison_tick.py` 生成的 LaunchAgent（0917AA）——Mac 侧调度 tick 的 plist 契约。

tick 由 launchd `StartCalendarInterval` 按日历拉起 `python -m tools.liaison tick`（0917AE 起：
每日 09:00、14:00 两个时点；此前是 `StartInterval` 300 秒轮询，泳道结果改由 run-lanes.sh 事件入队
后轮询没有存在理由），跑完即退。这里钉住的几项都属于「错了不报错」：
- `StartInterval` 残留 ⇒ 两种触发并存，tick 又变回 5 分钟轮询；
- `AbandonProcessGroup` 缺失 ⇒ tick 退出时 launchd 连坐 SIGKILL 它开的子进程（0909Y 实测）；
- `EnvironmentVariables.PATH` 缺失或带字面量 `~` ⇒ launchd 的极简 PATH 找不到工具，且 plist 不展开波浪号；
- `WorkingDirectory` 不是仓库根 ⇒ `python -m tools.liaison` import 不到 tools 包；
- 解释器不是 `tools/liaison/.venv/bin/python` ⇒ 缺依赖只在 err 日志里出现。

⛔ 用例不装、不 bootstrap、不写 ~/Library：`--print` 路径必须一个 subprocess 都不调。
"""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest

from scripts import install_liaison_tick
from scripts.install_liaison_tick import LABEL, build_plist

FAKE_HOME = Path("/Users/fake-home-0917AA")


@pytest.fixture
def plist(tmp_path: Path) -> dict:
    return build_plist(tmp_path, FAKE_HOME)


def test_build_plist_runs_the_tick_subcommand_with_the_liaison_venv(tmp_path: Path, plist: dict) -> None:
    assert plist["Label"] == LABEL
    assert plist["ProgramArguments"] == [
        str(tmp_path / "tools" / "liaison" / ".venv" / "bin" / "python"),
        "-m",
        "tools.liaison",
        "tick",
    ]


def test_build_plist_fires_on_calendar_not_on_interval(plist: dict) -> None:
    """0917AE：按日历触发（每日 09:00、14:00），⛔ 不再有 StartInterval 轮询。"""
    assert "StartInterval" not in plist, "StartInterval 残留 ⇒ 又变回 5 分钟轮询"
    slots = plist["StartCalendarInterval"]
    assert isinstance(slots, list) and len(slots) == 2
    assert slots == [{"Hour": 9, "Minute": 0}, {"Hour": 14, "Minute": 0}]
    assert slots == [
        {"Hour": h, "Minute": m} for h, m in install_liaison_tick.CALENDAR_SLOTS
    ], "plist 的时点必须与 CALENDAR_SLOTS 常量一致（常量可调，plist 跟着走）"
    # ⛔ 不是常驻进程：KeepAlive 会让 launchd 在 tick 正常退出后立刻再拉起，等价于忙循环。
    assert "KeepAlive" not in plist


def test_build_plist_abandons_process_group(plist: dict) -> None:
    assert plist["AbandonProcessGroup"] is True


def test_build_plist_path_is_absolute_and_has_no_tilde(plist: dict) -> None:
    path = plist["EnvironmentVariables"]["PATH"]
    entries = path.split(":")
    assert entries, "PATH 不能为空"
    assert all(e.startswith("/") for e in entries), entries
    assert "~" not in path
    assert str(FAKE_HOME / ".local" / "bin") in entries
    assert "/opt/homebrew/bin" in entries and "/usr/bin" in entries


def test_build_plist_working_directory_is_the_repo_root(tmp_path: Path, plist: dict) -> None:
    assert plist["WorkingDirectory"] == str(tmp_path)


def test_build_plist_logs_under_data_liaison_logs(tmp_path: Path, plist: dict) -> None:
    for key in ("StandardOutPath", "StandardErrorPath"):
        assert plist[key].startswith(str(tmp_path / "data" / "liaison" / "logs")), plist[key]


def test_build_plist_never_carries_credentials(plist: dict) -> None:
    env = plist["EnvironmentVariables"]
    assert not any(k.startswith("HR_LIAISON_") for k in env), env


def test_build_plist_round_trips_through_plistlib(plist: dict) -> None:
    assert plistlib.loads(plistlib.dumps(plist)) == plist


def test_build_plist_is_pure(tmp_path: Path) -> None:
    before = sorted(tmp_path.rglob("*"))
    build_plist(tmp_path, FAKE_HOME)
    assert sorted(tmp_path.rglob("*")) == before


def test_print_mode_writes_nothing_and_calls_no_launchctl(tmp_path: Path, monkeypatch, capsys) -> None:
    def boom(*args, **kwargs):
        raise AssertionError(f"--print 不该调用 subprocess: {args!r}")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(install_liaison_tick, "agents_dir", lambda: tmp_path / "LaunchAgents")
    monkeypatch.setattr(install_liaison_tick, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(install_liaison_tick.Path, "home", classmethod(lambda cls: FAKE_HOME))

    assert install_liaison_tick.main(["--print"]) == 0
    out = capsys.readouterr().out
    parsed = plistlib.loads(out.encode("utf-8"))
    assert "StartInterval" not in parsed and "StartInterval" not in out
    assert parsed["StartCalendarInterval"] == [{"Hour": 9, "Minute": 0}, {"Hour": 14, "Minute": 0}]
    assert parsed["AbandonProcessGroup"] is True
    assert not (tmp_path / "LaunchAgents").exists()
    assert not (tmp_path / "data").exists()


def test_print_mode_does_not_require_the_venv_to_exist(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(install_liaison_tick, "repo_root", lambda: tmp_path)
    assert install_liaison_tick.main(["--print"]) == 0


def test_real_install_refuses_when_the_venv_python_is_missing(tmp_path: Path, monkeypatch, capsys) -> None:
    """无害预检：venv 不在就退回人工并说明，⛔ 不写一份必然起不来的 plist。"""

    def boom(*args, **kwargs):
        raise AssertionError(f"预检失败后不该调用 subprocess: {args!r}")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(install_liaison_tick, "agents_dir", lambda: tmp_path / "LaunchAgents")
    monkeypatch.setattr(install_liaison_tick, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(install_liaison_tick.sys, "platform", "darwin")

    assert install_liaison_tick.main([]) != 0
    assert ".venv" in capsys.readouterr().err
    assert not (tmp_path / "LaunchAgents").exists()


def test_unknown_arguments_are_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(install_liaison_tick, "repo_root", lambda: tmp_path)
    assert install_liaison_tick.main(["--install-now"]) != 0
