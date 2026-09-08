"""spec 场景「版本管理中不含凭据」：受版本管理的文件里不得有真实凭据。

口径是 `git ls-files`——**受版本管理**，不是"磁盘上存在"。用 os.walk 会把
.gitignore 掉的 .env / data/ / .venv/ 一起扫进来，而那些地方**恰恰是**真实凭据
被允许存在的地方，扫出来只会得到一条永远红的断言。

⚠️ 本文件会扫到它自己、也会扫到 docs/superpowers/plans/ 下的实现计划——两者都
受版本管理。因此正则锚在行首并放行占位形态；写文档时用空值或 <占位>，
⛔ 不要在任何受版本管理的文件里写出行首的真实赋值。
"""

import pathlib
import re
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

CREDENTIAL_ENV_NAMES = (
    "HR_LIAISON_BOT_ID",
    "HR_LIAISON_BOT_SECRET",
    # 第 6 章（群通知外发）会用到，提前纳入扫描面：等到那一章再加，
    # 中间这段时间里它是没人守的。
    "HR_LIAISON_GROUP_WEBHOOK_URL",
)

_ASSIGNMENT = re.compile(
    r"^(?:export[ \t]+)?(?:" + "|".join(CREDENTIAL_ENV_NAMES) + r")[ \t]*=[ \t]*(\S+)",
    re.MULTILINE,
)

# 明显不是真值的占位形态，放行。⛔ 不要往这里加"看起来像假的"具体字符串——
# 那等于给真凭据开一条按字面量豁免的口子。
_PLACEHOLDER = re.compile(r"^(?:<.*>|\$\{[A-Za-z_]+\}|replace-me|your-[a-z-]+|\.\.\.)$")

# 企微群机器人 webhook 的完整地址（带 key）。
_WECOM_WEBHOOK_URL = re.compile(
    r"qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[0-9A-Za-z-]{8,}"
)

# 单文件读取上限：超过就跳过。大文件基本是二进制/数据，逐字节扫它没有收益。
_MAX_BYTES = 2 * 1024 * 1024


def _tracked_text_files() -> list[pathlib.Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=str(REPO_ROOT), capture_output=True, check=True,
    )
    paths = []
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        path = REPO_ROOT / raw.decode("utf-8")
        if not path.is_file():
            continue  # 已删但索引里还在
        if path.stat().st_size > _MAX_BYTES:
            continue
        paths.append(path)
    return paths


def _read_text(path: pathlib.Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None  # 二进制或读不了，跳过


def test_dotenv_itself_is_not_tracked():
    """.env 是真实凭据的唯一落点，它绝不能进版本管理。"""
    completed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".env"],
        cwd=str(REPO_ROOT), capture_output=True,
    )
    assert completed.returncode != 0, ".env 被 git 跟踪了，真实凭据正在入库"


def test_no_tracked_file_assigns_a_real_credential_value():
    offenders = []
    for path in _tracked_text_files():
        text = _read_text(path)
        if text is None:
            continue
        for match in _ASSIGNMENT.finditer(text):
            value = match.group(1)
            if _PLACEHOLDER.match(value):
                continue
            line_no = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{line_no}")
    assert not offenders, f"受版本管理的文件里出现了凭据赋值: {offenders}"


def test_no_tracked_file_contains_a_wecom_webhook_url():
    offenders = []
    for path in _tracked_text_files():
        text = _read_text(path)
        if text is None:
            continue
        if _WECOM_WEBHOOK_URL.search(text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"受版本管理的文件里出现了企微 webhook 完整地址: {offenders}"


@pytest.mark.parametrize("name", ("HR_LIAISON_BOT_ID", "HR_LIAISON_BOT_SECRET"))
def test_env_example_declares_the_placeholder_with_empty_value(name):
    """占位必须在 .env.example 里就位，且取值为空。

    为什么必须为空而不是写个假值：`.env.example` 会被 `sync-to-server.sh` 推到 .51。
    任何非空取值都可能被谁复制成 .env 用，而"看起来配好了、其实是假的"这种状态，
    正是 fail-closed 校验挡不住的那一种——它不缺失，它只是错的。
    """
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(name)}[ \t]*=[ \t]*$", re.MULTILINE)
    assert pattern.search(text), f".env.example 里缺 {name} 的空值占位"
