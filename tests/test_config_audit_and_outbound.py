import pytest

from app.config import (
    Settings,
    _as_switch,
    get_settings,
    is_candidate_outbound_enabled,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """get_settings() 带 lru_cache，用例之间必须清干净，否则互相污染。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def switch_path(tmp_path, monkeypatch):
    path = tmp_path / "candidate_outbound.switch"
    monkeypatch.setenv("CANDIDATE_OUTBOUND_SWITCH_FILE", str(path))
    monkeypatch.delenv("CANDIDATE_OUTBOUND_ENABLED", raising=False)
    return path


# ── 审计 JSONL 路径 ─────────────────────────────────────────────────────


def test_audit_jsonl_path_has_a_default():
    """零配置即可用：.51 的 .env 不随代码同步，新键必须带默认值。"""
    assert Settings().audit_jsonl_path == "data/audit/decisions.jsonl"


def test_audit_jsonl_path_overridable_via_env(monkeypatch):
    monkeypatch.setenv("AUDIT_JSONL_PATH", "D:/hr/audit/decisions.jsonl")
    assert Settings().audit_jsonl_path == "D:/hr/audit/decisions.jsonl"


# ── 外发总开关：默认关闭 ────────────────────────────────────────────────


def test_candidate_outbound_is_closed_by_default(switch_path):
    """代码默认关闭（Shao Peishen 2026-08-26 拍板选项 A）。"""
    assert Settings().candidate_outbound_enabled is False
    assert is_candidate_outbound_enabled() is False


# ── 外发总开关：每次求值、热改立刻生效 ──────────────────────────────────


def test_switch_file_flips_to_closed_at_runtime_without_restart(switch_path):
    """
    守护测试（spec「总开关运行期间被关闭」）：进程已经跑起来、Settings 已经
    被 lru_cache 缓存，此时改开关文件，**下一次求值立刻按新值走**，全程不
    cache_clear、不重启。
    """
    switch_path.write_text("true", encoding="utf-8")
    assert is_candidate_outbound_enabled() is True

    switch_path.write_text("false", encoding="utf-8")

    assert is_candidate_outbound_enabled() is False


def test_switch_file_flips_back_and_forth(switch_path):
    for raw, expected in [("on", True), ("0", False), ("YES", True), ("no", False)]:
        switch_path.write_text(raw, encoding="utf-8")
        assert is_candidate_outbound_enabled() is expected, raw


def test_switch_file_removal_falls_back_to_baseline(switch_path):
    switch_path.write_text("true", encoding="utf-8")
    assert is_candidate_outbound_enabled() is True

    switch_path.unlink()

    assert is_candidate_outbound_enabled() is False


def test_env_var_is_read_every_call_not_cached_at_startup(switch_path, monkeypatch):
    """
    环境变量走 os.environ 直读，不经 get_settings() 的缓存：先把 Settings 缓存
    起来（默认关），再改环境变量，下一次求值必须已经是新值。
    """
    assert is_candidate_outbound_enabled() is False  # 此刻 Settings 已被缓存

    monkeypatch.setenv("CANDIDATE_OUTBOUND_ENABLED", "true")

    assert is_candidate_outbound_enabled() is True


def test_switch_file_wins_over_env(switch_path, monkeypatch):
    """
    .51 的 .env 写着开启时，出事要能靠一个文件立刻全拦——文件必须压过环境变量。
    """
    monkeypatch.setenv("CANDIDATE_OUTBOUND_ENABLED", "true")
    switch_path.write_text("false", encoding="utf-8")

    assert is_candidate_outbound_enabled() is False


# ── 外发总开关：未知即拦截 ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("", id="empty"),
        pytest.param("   \n\n", id="blank-lines"),
        pytest.param("maybe", id="garbage"),
        pytest.param("ture", id="typo"),
        pytest.param("2", id="number"),
    ],
)
def test_unrecognised_switch_content_is_closed(switch_path, content):
    switch_path.write_text(content, encoding="utf-8")

    assert is_candidate_outbound_enabled() is False


def test_unreadable_switch_path_is_closed(tmp_path, monkeypatch):
    """路径被一个目录占住 → 读不出来 → 关。出错只能往保守那一侧倒。"""
    blocked = tmp_path / "switch_as_dir"
    blocked.mkdir()
    monkeypatch.setenv("CANDIDATE_OUTBOUND_SWITCH_FILE", str(blocked))
    monkeypatch.setenv("CANDIDATE_OUTBOUND_ENABLED", "true")

    assert is_candidate_outbound_enabled() is False


def test_first_nonblank_line_decides(switch_path):
    switch_path.write_text("\n\n  true  \nfalse\n", encoding="utf-8")

    assert is_candidate_outbound_enabled() is True


def test_as_switch_none_is_closed():
    """
    补测（不在 task-4-brief 的 17 条原始清单内，mutation-check 步骤 7 发现的
    缺口）：当前两处调用点在传入 `_as_switch` 之前都已经排除了 `None`
    （`read_text()` 恒返回 str；环境变量分支有 `is not None` 前置判断），
    所以 `raw is None` 分支是死代码路径——没有任何既有用例真正走到它。把它
    从 `return False` 改成 `return True` 时，全套用例照样全绿，这正是一个
    会被静默放过的 fail-open 缺口。直接单测这个纯函数的输入输出，堵住这个
    路径，不依赖它是否被上层调用到。
    """
    assert _as_switch(None) is False


# ── 形态守护：不许被缓存成常量 ──────────────────────────────────────────


def test_switch_is_a_callable_not_a_value():
    """tasks 4.5「支持传 callable」：U4 拿到的必须是这个函数本身。"""
    assert callable(is_candidate_outbound_enabled)
    assert not isinstance(is_candidate_outbound_enabled, bool)


def test_switch_function_is_not_memoised():
    """
    结构性守护：给这个函数加 @lru_cache 是"看起来无害的优化"，但它会让热改
    彻底失效，而所有既有用例照样全绿（每个用例都是新进程状态）。
    """
    assert not hasattr(is_candidate_outbound_enabled, "cache_clear")
    assert not hasattr(is_candidate_outbound_enabled, "cache_info")
