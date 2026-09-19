"""结构性守卫：
1. voice_host/ 包不得出现对 `.51` 专属数据/存储/候选人联系方式的引用
   （design D19"无库访问、无简历"）。
2. voice_host/_vendor/signing.py 与 app/live_voice/signing.py 的核心签名
   算法逐字一致（本计划「设计决策 2」：两侧必须同源，不能各自实现）。
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_TOKENS = ("app.storage", "app.outbound", "candidate_contact_vault", "resume_storage_dir")

# 三引号字符串（docstring）在扫描前先剥离：queue_store.py（Task 8）的模块
# docstring 里出于说明目的写了"⛔ 不 import app.storage"这句话本身就含有
# 被禁 token 的字面文本，属于合规的文档说明而非真实引用，不应被当成违规
# 命中。剥离后只扫描真正的代码主体（import 语句、属性访问等）。
_TRIPLE_QUOTED_STRING = re.compile(r'"""(?:.|\n)*?"""|\'\'\'(?:.|\n)*?\'\'\'')


def _strip_docstrings(text: str) -> str:
    return _TRIPLE_QUOTED_STRING.sub("", text)


def test_voice_host_package_has_no_forbidden_references():
    voice_host_dir = REPO_ROOT / "voice_host"
    offending = []
    for path in voice_host_dir.rglob("*.py"):
        text = _strip_docstrings(path.read_text(encoding="utf-8"))
        for token in FORBIDDEN_TOKENS:
            if token in text:
                offending.append(f"{path.relative_to(REPO_ROOT)}: {token}")
    assert offending == [], f"voice_host/ 下出现禁止引用: {offending}"


def test_vendored_signing_module_matches_canonical_source():
    canonical = (REPO_ROOT / "app" / "live_voice" / "signing.py").read_text(encoding="utf-8")
    vendored = (REPO_ROOT / "voice_host" / "_vendor" / "signing.py").read_text(encoding="utf-8")

    def _body_after_docstring(text: str) -> str:
        # 跳过模块级 docstring（两份文件的 docstring 第一段允许不同，见
        # Task 9 Step 1 的说明），只比对代码主体。
        marker = '"""'
        first = text.index(marker)
        second = text.index(marker, first + 3)
        return text[second + 3 :]

    assert _body_after_docstring(canonical) == _body_after_docstring(vendored)
