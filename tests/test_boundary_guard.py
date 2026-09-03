"""边界守护的反证（`tasks.md` 7.1 / 7.2）。

⚠️ **这个文件才是 7.1/7.2 的价值所在。** 真实仓库里这两条检查永远是绿的
（`app/` 下 `zhuopin_platform` 零命中、依赖 diff 零行），而"零命中"同时兼容
两种解释：**边界守住了**，和**检查根本没生效**。只有"故意造违例 → 必须失败"
能把这两种解释分开。这条判据 U6 的 6.7 已经确立过一次，同一形状。

⛔ 不要把这里的任何一条反证删掉换成"跑一遍真实仓库就够了"。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Sequence

import pytest

from scripts.check_boundary import (
    BASELINE_COMMIT,
    Violation,
    check_dependency_diff,
    run_all,
    scan_app_tree,
    scan_dependency_files,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

CLEAN_PYPROJECT = """\
[project]
name = "fixture"
version = "0.0.1"
requires-python = ">=3.14,<3.15"

[tool.pytest.ini_options]
pythonpath = ["."]
"""

CLEAN_REQUIREMENTS = "fastapi==0.115.6\npytest==8.3.4\n"


def make_repo(root: Path, app_files: dict[str, str] | None = None, **overrides: str) -> Path:
    """造一个最小的假仓库：`app/` + 两个依赖声明文件。"""
    (root / "app").mkdir(parents=True, exist_ok=True)
    for name, body in (app_files or {"__init__.py": "", "main.py": "X = 1\n"}).items():
        target = root / "app" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    (root / "requirements.txt").write_text(
        overrides.get("requirements", CLEAN_REQUIREMENTS), encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        overrides.get("pyproject", CLEAN_PYPROJECT), encoding="utf-8"
    )
    return root


def fake_git(stdout: str = "", returncode: int = 0, stderr: str = ""):
    def runner(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(args), returncode, stdout, stderr)

    return runner


def rules(violations: list[Violation]) -> set[str]:
    return {v.rule for v in violations}


# ── 基线：干净的 app/ 必须全过 ────────────────────────────────────────────


def test_clean_app_tree_has_no_violations(tmp_path: Path) -> None:
    assert scan_app_tree(make_repo(tmp_path)) == []


# ── 反证一（7.1）：import zhuopin_platform 必须被抓 ────────────────────────


@pytest.mark.parametrize(
    "snippet",
    [
        "import zhuopin_platform\n",
        "from zhuopin_platform import audit\n",
        "from zhuopin_platform.audit.sinks import JsonlSink\n",
        "import zhuopin_platform.audit as platform_audit\n",
        'mod = importlib.import_module("zhuopin_platform.audit")\n',
    ],
    ids=["import", "from-import", "deep-from", "import-as", "importlib-string"],
)
def test_zhuopin_platform_import_is_detected(tmp_path: Path, snippet: str) -> None:
    """opener 指定的反证：故意塞一行 import 进临时文件，检查必须失败。

    `importlib-string` 那条是裸 token 扫描换来的——AST 只看 import 语句的话，
    动态 import 会整条溜过去。
    """
    root = make_repo(tmp_path, app_files={"__init__.py": "", "leak.py": snippet})
    violations = scan_app_tree(root)
    assert violations, f"未抓到违例：{snippet!r}（检查恒真＝检查没生效）"
    assert "7.1-module" in rules(violations)
    assert violations[0].path == "app/leak.py"
    assert violations[0].line == 1


def test_zhuopin_platform_in_non_python_file_is_detected(tmp_path: Path) -> None:
    """`index.html` 里的一行 fetch 也能跨仓库——扫描不能只看 .py。"""
    root = make_repo(
        tmp_path,
        app_files={
            "__init__.py": "",
            "web/index.html": '<script>fetch("/zhuopin_platform/api")</script>\n',
        },
    )
    assert "7.1-module" in rules(scan_app_tree(root))


# ── 反证二（7.1）：sys.path 注入必须被抓 ──────────────────────────────────


@pytest.mark.parametrize(
    "snippet",
    [
        'import sys\nsys.path.insert(0, "/Users/x/OneDrive/Projects/企业AI转型")\n',
        'import sys\nsys.path.append(SISTER_REPO)\n',
        "import sys\nsys.path += [SISTER_REPO]\n",
        "from sys import path\npath.insert(0, SISTER_REPO)\n",
    ],
    ids=["onedrive-literal", "append-variable", "augmented-assign", "from-sys-import"],
)
def test_sys_path_injection_is_detected(tmp_path: Path, snippet: str) -> None:
    """后三条不含任何 OneDrive 字样。

    合取式判据（"sys.path **且** 指向 OneDrive"）在这三条上全部放行，而它们
    照样把姊妹仓库挂进 `app/`。这就是为什么判据收紧成"app/ 下任何 sys.path
    都算违例"。
    """
    root = make_repo(tmp_path, app_files={"__init__.py": "", "inject.py": snippet})
    assert "7.1-syspath" in rules(scan_app_tree(root))


def test_sys_path_in_comment_is_not_flagged(tmp_path: Path) -> None:
    """误报是检查被拆掉的最常见死因：注释里提一句 sys.path 必须放行。"""
    root = make_repo(
        tmp_path,
        app_files={
            "__init__.py": "",
            "ok.py": "# ⛔ 禁止 sys.path 注入，见 tasks.md 7.1\nVALUE = 1\n",
        },
    )
    assert "7.1-syspath" not in rules(scan_app_tree(root))


def test_onedrive_path_marker_is_detected(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        app_files={
            "__init__.py": "",
            "cfg.py": 'REF = "~/Library/CloudStorage/OneDrive-Personal/Projects"\n',
        },
    )
    assert "7.1-path" in rules(scan_app_tree(root))


def test_syntax_error_file_does_not_crash_the_check(tmp_path: Path) -> None:
    """AST 解析失败不能让整条检查抛异常——异常穿透到 CI 就是一个绿色的谎。"""
    root = make_repo(tmp_path, app_files={"__init__.py": "", "broken.py": "def (\n"})
    assert scan_app_tree(root) == []


# ── 基线：干净的树必须全过 ────────────────────────────────────────────────


def test_clean_tree_has_no_violations(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    assert run_all(root, skip_diff=True, runner=fake_git()) == []


# ── 反证三（7.2）：依赖声明里的 zhuopin_platform 必须被抓 ─────────────────


def test_zhuopin_platform_in_requirements_is_detected(tmp_path: Path) -> None:
    root = make_repo(tmp_path, requirements=CLEAN_REQUIREMENTS + "zhuopin_platform==1.0.0\n")
    violations = scan_dependency_files(root)
    assert "7.2-module" in rules(violations)


def test_zhuopin_platform_in_pyproject_is_detected(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        pyproject=CLEAN_PYPROJECT + '\n[tool.zhuopin_platform]\nenabled = true\n',
    )
    assert "7.2-module" in rules(scan_dependency_files(root))


def test_missing_dependency_file_is_a_violation(tmp_path: Path) -> None:
    """删掉 requirements.txt 不能等于"没有违例"。"""
    root = make_repo(tmp_path)
    (root / "requirements.txt").unlink()
    assert "7.2-missing" in rules(scan_dependency_files(root))


# ── 反证四（7.2）：pyproject 声明依赖必须被抓 ─────────────────────────────


@pytest.mark.parametrize(
    "table",
    [
        '[project]\nname = "f"\nversion = "0"\ndependencies = ["zhuopin-sdk"]\n',
        '[project]\nname = "f"\nversion = "0"\n[project.optional-dependencies]\ndev = ["x"]\n',
        '[project]\nname = "f"\nversion = "0"\n[dependency-groups]\ndev = ["x"]\n',
        '[project]\nname = "f"\nversion = "0"\n[tool.poetry.dependencies]\nx = "^1"\n',
    ],
    ids=["project", "optional", "groups", "poetry"],
)
def test_pyproject_dependency_table_is_detected(tmp_path: Path, table: str) -> None:
    """diff 判据只锁 requirements.txt，这条堵住由此产生的绕过口。"""
    root = make_repo(tmp_path, pyproject=table)
    assert "7.2-pyproject" in rules(scan_dependency_files(root))


def test_broken_pyproject_is_a_violation(tmp_path: Path) -> None:
    root = make_repo(tmp_path, pyproject="[project\nname =\n")
    assert "7.2-pyproject" in rules(scan_dependency_files(root))


# ── 反证五（7.2）：非空 diff 必须被抓 ─────────────────────────────────────


def test_non_empty_dependency_diff_is_detected(tmp_path: Path) -> None:
    """真实仓库里这条永远绿，只靠真实仓库测不出它会不会红。"""
    root = make_repo(tmp_path)
    violations = check_dependency_diff(
        root, runner=fake_git(stdout=" requirements.txt | 1 +\n 1 file changed, 1 insertion(+)\n")
    )
    assert "7.2-diff" in rules(violations)
    assert "requirements.txt" in violations[0].message


def test_empty_dependency_diff_passes(tmp_path: Path) -> None:
    assert check_dependency_diff(make_repo(tmp_path), runner=fake_git(stdout="\n")) == []


def test_git_failure_is_a_violation_not_a_pass(tmp_path: Path) -> None:
    """取不到基线 commit（CI 浅克隆）必须红。

    ⛔ 不许把 git 失败当成"没有 diff"——那正是 CI 上最容易出现的、
    看起来是绿色的静默失效。
    """
    violations = check_dependency_diff(
        make_repo(tmp_path),
        runner=fake_git(returncode=128, stderr="fatal: bad object"),
    )
    assert "7.2-diff" in rules(violations)
    assert "fetch-depth" in violations[0].message


# ── 真实仓库：今天必须是绿的 ──────────────────────────────────────────────


def test_real_repository_passes_boundary_guard() -> None:
    """反证证明"会红"，这条证明"今天不该红"。两条都要有。"""
    violations = run_all(REPO_ROOT, baseline=BASELINE_COMMIT)
    assert violations == [], "\n".join(v.render() for v in violations)


def test_cli_exits_1_on_violation(tmp_path: Path) -> None:
    """CI 靠退出码判成败，退出码本身要有测试。"""
    root = make_repo(tmp_path, app_files={"__init__.py": "", "leak.py": "import zhuopin_platform\n"})
    result = subprocess.run(
        [sys.executable, "scripts/check_boundary.py", "--root", str(root), "--skip-diff"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "7.1-module" in result.stderr


def test_cli_exits_0_on_clean_tree(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    result = subprocess.run(
        [sys.executable, "scripts/check_boundary.py", "--root", str(root), "--skip-diff"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
