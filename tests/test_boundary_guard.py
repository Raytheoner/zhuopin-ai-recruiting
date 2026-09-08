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
    Violation,
    check_registered_dependencies,
    parse_requirement_names,
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


# ── 反证（7.1）：非 UTF-8 文件不许被静默跳过 ──────────────────────────────


@pytest.mark.parametrize(
    "encoding",
    ["utf-16", "utf-16-le", "utf-16-be", "gbk", "utf-8-sig"],
    ids=["utf16-bom", "utf16le-nobom", "utf16be-nobom", "gbk", "utf8-bom"],
)
def test_non_utf8_file_is_still_scanned(tmp_path: Path, encoding: str) -> None:
    """`.51` 是 Windows，PowerShell 的 `Out-File` / `>` 默认写 UTF-16LE。

    旧实现把 `UnicodeDecodeError` 吞成 `None` 后 `continue`，于是在服务器上
    顺手改一个 `app/` 下的文件，就能让这条边界对该文件**永久失明且不留痕迹**。
    ⚠️ 只加 `errors="replace"` 修不好 UTF-16——ASCII 字符之间夹着的 `\\x00`
    会让 `"zhuopin_platform" in line` 匹配不上，所以必须走兜底解码。
    """
    root = make_repo(tmp_path)
    (root / "app" / "leak_bin.py").write_bytes("import zhuopin_platform\n".encode(encoding))
    violations = scan_app_tree(root)
    assert "7.1-module" in rules(violations), f"{encoding} 编码的违例被静默跳过了"


@pytest.mark.parametrize("encoding", ["gbk", "utf-16"], ids=["gbk", "utf16-bom"])
def test_chinese_marker_in_non_utf8_file_is_detected(tmp_path: Path, encoding: str) -> None:
    """GBK 与带 BOM 的 UTF-16 走的是**正解**路径，中文 marker 在这两条上必须生效。

    ⚠️ 已知限制（脚本 `_decode()` 的 docstring 里也写了）：**无 BOM 的
    UTF-16 中文**落在 latin-1 兜底上，只保证 ASCII token 匹配，中文 marker
    匹配不到。⛔ 不要把这条限制当成"已覆盖"。
    """
    root = make_repo(tmp_path)
    (root / "app" / "cfg.py").write_bytes('REF = "../企业AI转型/资产"\n'.encode(encoding))
    assert "7.1-path" in rules(scan_app_tree(root))


def test_binary_file_is_not_a_false_positive(tmp_path: Path) -> None:
    """兜底解码不能把二进制文件解成违例——误报是检查被拆掉的最常见死因。"""
    root = make_repo(tmp_path)
    (root / "app" / "logo.png").write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00"
    )
    assert scan_app_tree(root) == []


# ── 反证（7.1）：app/ 不存在不等于没有违例 ────────────────────────────────


def test_missing_app_dir_is_a_violation(tmp_path: Path) -> None:
    """把树挪到 `src/app/` 后旧实现返回 `[]` 并照打"边界守护：通过"。

    仓库重构、或 CLI 从错误 cwd 起跑，7.1 就静默变成恒真。与 `7.2-missing`
    对称补一条。
    """
    root = make_repo(tmp_path, app_files={"__init__.py": "", "leak.py": "import zhuopin_platform\n"})
    (root / "src").mkdir()
    (root / "app").rename(root / "src" / "app")
    violations = run_all(root)
    assert "7.1-missing" in rules(violations)


# ── 反证（7.1）：symlink 必须被抓 ─────────────────────────────────────────


def test_symlink_dir_into_sister_repo_is_detected(tmp_path: Path) -> None:
    """目录软链是"不跨仓库引用"最字面的违反形态，而旧实现完全看不见它。

    `Path.rglob` 默认 `recurse_symlinks=False`，且 symlink 目录过不了
    `is_file()` —— 文件软链能抓到，目录软链零违例。
    """
    sister = tmp_path / "other_repo"
    (sister / "audit").mkdir(parents=True)
    (sister / "audit" / "sinks.py").write_text("import zhuopin_platform\n", encoding="utf-8")

    root = make_repo(tmp_path / "repo")
    (root / "app" / "sister").symlink_to(sister, target_is_directory=True)

    violations = scan_app_tree(root)
    assert "7.1-symlink" in rules(violations)
    assert any(v.path == "app/sister" for v in violations)


def test_symlink_file_is_detected(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "repo")
    outside = tmp_path / "outside.py"
    outside.write_text("VALUE = 1\n", encoding="utf-8")
    (root / "app" / "linked.py").symlink_to(outside)
    assert "7.1-symlink" in rules(scan_app_tree(root))


def test_clean_tree_has_no_symlink_violation(tmp_path: Path) -> None:
    """从严判据的另一半：没有软链时 ⛔ 不许报。"""
    assert "7.1-symlink" not in rules(scan_app_tree(make_repo(tmp_path)))


# ── 反证（7.1）：跳过目录名只对 app/ 以内生效 ─────────────────────────────


def test_skip_dir_names_do_not_match_ancestor_dirs(tmp_path: Path) -> None:
    """把仓库放进一个名叫 `.mypy_cache` 的祖先目录里，扫描不许变成空转。

    旧实现用 `SKIP_DIR_NAMES & set(path.parts)` 匹配**绝对路径**的全部路径段，
    于是整个仓库被跳过，带着真实违例全绿。
    """
    root = make_repo(
        tmp_path / ".mypy_cache" / "repo",
        app_files={"__init__.py": "", "leak.py": "import zhuopin_platform\n"},
    )
    assert "7.1-module" in rules(scan_app_tree(root))


def test_skip_dir_names_still_apply_inside_app(tmp_path: Path) -> None:
    """`app/__pycache__/*.pyc` 仍然要跳过——否则 .pyc 里的字符串常量会误报。"""
    root = make_repo(tmp_path)
    cache = root / "app" / "__pycache__"
    cache.mkdir()
    (cache / "leak.cpython-314.pyc").write_bytes(b"import zhuopin_platform\n")
    assert scan_app_tree(root) == []


# ── 基线：干净的树必须全过 ────────────────────────────────────────────────


def test_clean_tree_has_no_violations(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    assert run_all(root) == []


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


def test_unreadable_dependency_file_is_a_violation(tmp_path: Path) -> None:
    """读不出内容 ⛔ 不许当成空文件——`_read_text(path) or ""` 那种写法等于全绿。"""
    root = make_repo(tmp_path)
    (root / "requirements.txt").unlink()
    (root / "requirements.txt").mkdir()  # 存在但读不出（IsADirectoryError）
    assert "7.2-unreadable" in rules(scan_dependency_files(root))


def test_sister_repo_marker_in_requirements_is_detected(tmp_path: Path) -> None:
    """VCS 直装姊妹仓库不含 `zhuopin_platform` 这个 token，只靠 diff 判据拦。

    而 diff 判据钉死在立项 commit，是有保质期的；marker 判据没有。
    """
    root = make_repo(
        tmp_path,
        requirements=(
            CLEAN_REQUIREMENTS
            + "git+https://github.com/Raytheoner/zhuopin-ai-transformation.git@master\n"
        ),
    )
    violations = scan_dependency_files(root)
    assert "7.2-path" in rules(violations)


def test_onedrive_marker_in_pyproject_is_detected(tmp_path: Path) -> None:
    root = make_repo(
        tmp_path,
        pyproject=CLEAN_PYPROJECT + '\n[tool.ref]\npath = "~/OneDrive/Projects/企业AI转型"\n',
    )
    assert "7.2-path" in rules(scan_dependency_files(root))


def test_clean_dependency_files_have_no_path_violation(tmp_path: Path) -> None:
    assert "7.2-path" not in rules(scan_dependency_files(make_repo(tmp_path)))


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


# ── 反证五（7.2）：未登记依赖必须被抓 ─────────────────────────────────────
#
# 2026-09-08：判据从「相对立项 commit 的 diff 必须为空」换成「每条依赖都必须
# 登记」，理由见 scripts/check_boundary.py 的 REGISTERED_DEPENDENCIES 注释与
# TD-10。⛔ 换判据不是放松——旧判据在变更包归档后已经变成"任何正当新增依赖都
# 让所有分支永久变红"，那种恒假的检查必然被人整条注释掉。


def _write_requirements(root: Path, body: str) -> Path:
    (root / "requirements.txt").write_text(body, encoding="utf-8")
    return root


def test_unregistered_dependency_is_detected(tmp_path: Path) -> None:
    """真实仓库里这条永远绿，只靠真实仓库测不出它会不会红。"""
    root = _write_requirements(make_repo(tmp_path), "fastapi==0.115.6\nrequests==2.32.0\n")
    violations = check_registered_dependencies(root)

    assert "7.2-unregistered" in rules(violations)
    assert "requests" in violations[0].message
    assert violations[0].line == 2, "违例要指到具体行，否则大文件里没法定位"


def test_all_registered_dependencies_pass(tmp_path: Path) -> None:
    root = _write_requirements(
        make_repo(tmp_path), "fastapi==0.115.6\n# 注释行\n\nuvicorn[standard]==0.34.0\n"
    )
    assert check_registered_dependencies(root) == []


def test_unreadable_requirements_is_a_violation_not_a_pass(tmp_path: Path) -> None:
    """读不到 ≠ 没问题。⛔ 这条不许折成通过——静默绿是本文件全部反证的靶子。"""
    root = make_repo(tmp_path)
    (root / "requirements.txt").unlink()

    assert "7.2-unregistered" in rules(check_registered_dependencies(root))


def test_vcs_url_cannot_masquerade_as_a_registered_name(tmp_path: Path) -> None:
    """`git+https://…/zhuopin-ai-transformation.git` 不许被切成 `git` 混过去。

    ⛔ 这是把包名正则放宽后最先出现的洞：跨仓库直连依赖正是 7.2 要挡的东西，
    而它一旦被解析成某个恰好已登记的短名字，就会安静地通过。
    """
    root = _write_requirements(
        make_repo(tmp_path),
        "git+https://github.com/Raytheoner/zhuopin-ai-transformation.git\n",
    )
    violations = check_registered_dependencies(root)

    assert "7.2-unregistered" in rules(violations)
    assert "zhuopin-ai-transformation" in violations[0].message


def test_parser_skips_comments_and_blanks_but_never_unknown_lines() -> None:
    parsed = parse_requirement_names("# 头注释\n\nfastapi==0.115.6  # 行尾注释\n???\n")

    assert parsed == [(3, "fastapi"), (4, "???")], (
        "看不懂的行必须原样报出来去撞登记表，⛔ 不许静默跳过"
    )


# ── 真实仓库：今天必须是绿的 ──────────────────────────────────────────────


def test_real_repository_passes_boundary_guard() -> None:
    """反证证明"会红"，这条证明"今天不该红"。两条都要有。

    2026-09-08 起**全套判据恒跑**：依赖判据改成只读工作区文件后不再需要 git
    历史，浅克隆的 `test` job 上也成立。旧版这里要按基线可达性 `pytest.skip`，
    那个 skip 是真实存在的覆盖缺口（`test` job 默认 `fetch-depth: 1`），
    换判据顺带把它消掉了——⛔ 不要再加回任何形式的条件跳过。
    """
    violations = run_all(REPO_ROOT)
    assert violations == [], "\n".join(v.render() for v in violations)


def test_every_declared_dependency_is_registered_with_a_reason() -> None:
    """登记表里不许有空理由——"登记"的价值全在那句为什么。"""
    from scripts.check_boundary import REGISTERED_DEPENDENCIES

    blank = [name for name, reason in REGISTERED_DEPENDENCIES.items() if not reason.strip()]
    assert blank == [], f"这些依赖登记了但没写理由：{blank}"


def test_cli_exits_1_on_violation(tmp_path: Path) -> None:
    """CI 靠退出码判成败，退出码本身要有测试。"""
    root = make_repo(tmp_path, app_files={"__init__.py": "", "leak.py": "import zhuopin_platform\n"})
    result = subprocess.run(
        [sys.executable, "scripts/check_boundary.py", "--root", str(root)],
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
        [sys.executable, "scripts/check_boundary.py", "--root", str(root)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
