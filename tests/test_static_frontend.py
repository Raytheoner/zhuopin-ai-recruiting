import re
from pathlib import Path

INDEX_HTML = Path("app/web/static/index.html").read_text(encoding="utf-8")

# 逐行去掉 "// 到行尾" 的 JS 注释，避免注释里出现的示例字符串（比如说明"不要带
# 开头的 / "时写的 "/"）被当成真代码扫进来产生假阳性。本文件目前没有任何
# "://" 这类合法场景会被这一步误伤，如果将来引入外链需要重新评估这个假设。
_WITHOUT_LINE_COMMENTS = "\n".join(
    line.split("//", 1)[0] for line in INDEX_HTML.splitlines()
)

# 扫描 HTML 属性值与 JS 字符串/模板字面量：单引号、双引号、反引号包住的内容。
_STRING_LITERAL_RE = re.compile(r"""(["'`])((?:\\.|(?!\1).)*)\1""")


def test_index_html_has_no_absolute_paths():
    r"""
    部署约束 1：前端资源与接口调用一律相对路径，禁止硬编码 /static/… /api/…。
    挂在 root_path=/hr/recruit-agent 下时，绝对路径会打到门户根上去。

    旧版断言是摆设：`(src|href)` 在 index.html 里根本不出现，永远不会失败；
    `(?!\s*$)` 在没有 re.MULTILINE 时等价于"字符串结尾"，是个空操作；
    `fetch\(\s*["'`]/` 只认字面量直接传给 fetch 的写法，漏掉了本文件实际的
    主调用点 `fetch(url, …)`——url 是两行前拼好的模板字符串变量。

    改成扫描文件里每一个引号/反引号字符串字面量（模板字符串按 `${` 之前的首段
    文本判断），任何一段以 "/" 开头就判失败——这样不管字面量是直接传给 fetch()
    还是先赋值给变量再用，都逃不掉。
    """
    literals = [content for _, content in _STRING_LITERAL_RE.findall(_WITHOUT_LINE_COMMENTS)]
    assert literals, "扫描不到任何字符串字面量，测试范围本身失效了"
    absolute = [lit for lit in literals if lit.split("${", 1)[0].startswith("/")]
    assert not absolute, f"发现硬编码的绝对路径字符串字面量: {absolute!r}"

    # fetch 调用点本身也要落在扫描范围内，防止将来新增的调用点连字符串字面量
    # 都不用（比如整段拼接），彻底绕开上面的扫描。
    assert len(re.findall(r"\bfetch\(", _WITHOUT_LINE_COMMENTS)) >= 2

    assert "api/jobs" in INDEX_HTML  # 相对路径写法仍在


def test_index_html_renders_structured_questions_and_tolerates_legacy_strings():
    """
    弱断言（本仓库没有 JS 测试运行器，单文件前端无构建）：只保证适配新 payload
    的那几行没被改回去。真正的验证是 Task 6 的手工跑通那一步。
    """
    assert "questions_text" in INDEX_HTML
    # 历史 outbox 行里 questions 是裸字符串，前端也要兜一层
    assert 'typeof q === "string"' in INDEX_HTML
