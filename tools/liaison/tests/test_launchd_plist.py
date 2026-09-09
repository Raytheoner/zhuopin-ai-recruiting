"""launchd plist 模板与安装脚本的单测（tasks 8.3 / design D12）。

这些用例全部只读模板、只在 tmp 目录里渲染，⛔ 不装、不 bootstrap、不碰
`~/Library/LaunchAgents`——起 LaunchAgent 属安全配置变更，由 Shao Peishen 本人
在 Terminal 跑一次（README「运行与守护」）。用例里凡是会走到 `launchctl` 的路径
都被 monkeypatch 掐断，掐断失败就让用例炸，⛔ 不让它"真的跑起来"。
"""

import plistlib
import subprocess

import pytest

from tools.liaison.scripts import install_launchd


@pytest.fixture
def no_launchctl(monkeypatch):
    """任何 subprocess 调用都算越界——用例不该真的动 launchd。"""

    def _boom(*args, **kwargs):
        raise AssertionError(f"用例里不该调用 subprocess: {args!r}")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(install_launchd.subprocess, "run", _boom)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """把 LaunchAgents 目录指到 tmp，杜绝用例写进真实的 ~/Library。"""
    agents = tmp_path / "LaunchAgents"
    monkeypatch.setattr(install_launchd, "agents_dir", lambda: agents)
    return agents


def test_template_itself_is_a_valid_plist():
    """模板里的占位符写在 <string> 里，因此模板未渲染时也应当能被 plistlib 读出。

    这条守的是"模板坏了但没人发现"——占位符若写到标签外面，渲染前后都是坏 XML，
    而渲染后的坏在安装那一刻才暴露，那时已经晚了。
    """
    data = plistlib.loads(install_launchd.TEMPLATE_PATH.read_bytes())
    assert data["Label"] == install_launchd.LABEL


def test_rendered_plist_reads_back_as_valid_plist(tmp_path):
    rendered = install_launchd.render_plist(tmp_path)
    data = plistlib.loads(rendered.encode("utf-8"))
    assert data["Label"] == install_launchd.LABEL


def test_keepalive_throttle_and_runatload_are_in_place(tmp_path):
    """D12 的三个关键项。ThrottleInterval 缺失时 launchd 用默认 10s，
    崩溃循环会烧 CPU；KeepAlive 缺失则根本不是守护。"""
    data = plistlib.loads(install_launchd.render_plist(tmp_path).encode("utf-8"))
    assert data["KeepAlive"] is True
    assert data["ThrottleInterval"] == 30
    assert data["RunAtLoad"] is True


def test_program_arguments_point_at_the_liaison_venv_python(tmp_path):
    data = plistlib.loads(install_launchd.render_plist(tmp_path).encode("utf-8"))
    args = data["ProgramArguments"]
    assert args[0] == str(tmp_path / "tools" / "liaison" / ".venv" / "bin" / "python")
    assert args[1:] == ["-m", "tools.liaison"]


def test_plist_never_runs_the_self_check_mode(tmp_path):
    """🔴 `--self-check` ⛔ 不许出现在 launchd 拉起的命令行里（TD-36）。

    自检模式跑完校验就 exit 0。写进 plist 的后果是：launchd 每次拉起服务，服务
    立刻"正常结束"，`KeepAlive` 再拉、再结束——值守通道**从来没有真正存在过**，
    而 ⛔ 没有任何症状：退出码 0、err 日志干净、进程列表里看不出异常。
    """
    from tools.liaison.__main__ import SELF_CHECK_ARG

    rendered = install_launchd.render_plist(tmp_path)
    data = plistlib.loads(rendered.encode("utf-8"))
    assert SELF_CHECK_ARG not in data["ProgramArguments"], (
        f"plist 里出现了 {SELF_CHECK_ARG}——服务会每次拉起就立刻 exit 0"
    )
    assert SELF_CHECK_ARG not in rendered, "模板全文里都 ⛔ 不该出现自检开关"


def test_all_paths_in_the_rendered_plist_are_absolute(tmp_path):
    """launchd 的 cwd 是 `/`，相对路径不会报错，只会指到错的地方。"""
    data = plistlib.loads(install_launchd.render_plist(tmp_path).encode("utf-8"))
    candidates = [
        data["ProgramArguments"][0],
        data["WorkingDirectory"],
        data["StandardOutPath"],
        data["StandardErrorPath"],
    ]
    for value in candidates:
        assert value.startswith("/"), f"不是绝对路径: {value}"
    assert "{{" not in install_launchd.render_plist(tmp_path), "还有占位符没被替换"


def test_logs_go_under_data_liaison_logs(tmp_path):
    data = plistlib.loads(install_launchd.render_plist(tmp_path).encode("utf-8"))
    log_dir = tmp_path / "data" / "liaison" / "logs"
    assert data["StandardOutPath"] == str(log_dir / "launchd.out.log")
    assert data["StandardErrorPath"] == str(log_dir / "launchd.err.log")


def test_working_directory_is_the_repo_root(tmp_path):
    """`python -m tools.liaison` 靠 cwd 进 sys.path，WorkingDirectory 错了就 import 不到。"""
    data = plistlib.loads(install_launchd.render_plist(tmp_path).encode("utf-8"))
    assert data["WorkingDirectory"] == str(tmp_path)


def test_dry_run_writes_nothing_and_calls_no_launchctl(tmp_path, capsys, fake_home, no_launchctl):
    rc = install_launchd.main(["--dry-run", "--repo-root", str(tmp_path)])
    assert rc == 0
    assert not fake_home.exists(), "--dry-run 不该建 LaunchAgents 目录"
    assert not (tmp_path / "data").exists(), "--dry-run 不该建日志目录"
    out = capsys.readouterr().out
    assert install_launchd.LABEL in out
    assert "ThrottleInterval" in out, "--dry-run 应当把渲染结果原样打出来"


def test_dry_run_does_not_require_the_venv_to_exist(tmp_path, fake_home, no_launchctl):
    """venv 还没建时也要能看渲染结果——否则"先看看会写什么"这件事做不了。"""
    assert install_launchd.main(["--dry-run", "--repo-root", str(tmp_path)]) == 0


def test_real_install_refuses_when_the_venv_python_is_missing(tmp_path, capsys, fake_home, no_launchctl):
    """fail-closed：解释器不存在时 launchd 会按 ThrottleInterval 无限重启一个必然
    失败的进程，且只在日志里留痕。⛔ 宁可装不上，不装一个空转的 job。"""
    rc = install_launchd.main(["--repo-root", str(tmp_path)])
    assert rc != 0
    assert not fake_home.exists()
    assert "venv" in capsys.readouterr().err
