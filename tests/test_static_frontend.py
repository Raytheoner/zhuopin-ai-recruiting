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


def test_options_render_with_ai_disclosure_and_degrade_to_plain_text():
    """
    tasks 4.1 / 4.3。**弱断言**——本仓库没有 JS 测试运行器，这里只能证明
    "这几段代码还在文件里"，证明不了"点一下会发生什么"。真正的验收是
    Task 3 的手工验证清单（对应 tasks 8.4）。

    尽管如此，这几条仍然值得写：它们锁住的是"被人顺手改回去"这一类回退，
    而这类回退在单文件前端里既容易发生、又不会有任何其它信号。
    """
    # 有档位 → 渲染 checkbox 控件
    assert 'box.type = "checkbox"' in INDEX_HTML
    # 档位为空 → 走不进渲染分支（既不渲染控件也不渲染孤立标识）
    assert "if (options.length > 0)" in INDEX_HTML
    # 三种"没有 options"的输入都要归一到 []，不能对 undefined 取 .length
    assert "function questionOptions" in INDEX_HTML
    assert "if (!Array.isArray(options)) return [];" in INDEX_HTML
    # 历史 outbox 行里 questions 是裸字符串，前端也要兜一层
    assert 'typeof q === "string"' in INDEX_HTML


def test_ai_generated_options_carry_disclosure_label():
    """
    《AI 生成合成内容标识办法》（2025-09-01 施行）+ proposal.md「合规影响说明」：
    档位是 AI 生成内容，UI 上 MUST 标明是"建议选项"而非既定要求。

    断言标识文案存在，且它与选项控件在同一个分支里创建——标识出现在
    `if (options.length > 0)` 之后、`.opts` 容器创建之前，不存在"先渲染选项、
    后补标识"的中间态。
    """
    assert "AI_OPTIONS_HINT" in INDEX_HTML
    assert "建议选项" in INDEX_HTML
    assert "不是既定要求" in INDEX_HTML

    branch = INDEX_HTML.split("if (options.length > 0)", 1)
    assert len(branch) == 2, "选项渲染的条件分支不见了，标识与选项的绑定关系失效"
    body = branch[1]
    hint_at = body.find("AI_OPTIONS_HINT")
    opts_at = body.find('"opts"')
    assert hint_at != -1 and opts_at != -1
    assert hint_at < opts_at, "AI 建议标识必须先于选项控件创建，不允许后补"


def test_no_option_is_pre_selected():
    """
    合规红线「AI 不得代替业务经理做决定」在前端的机械判据：
    任何档位都不得默认勾选。预勾选等于系统替用户做了选择——用户直接点发送，
    AI 的建议就进了画像。

    对应 spec 的 Requirement:「候选档位不得代替用户做决定」/ Scenario:「未选定不入画像」。
    """
    assert ".checked = true" not in INDEX_HTML
    assert ".checked=true" not in INDEX_HTML
    assert "checked=" not in INDEX_HTML  # HTML 属性形式的预勾选


def test_reask_prefix_stays_in_sync_with_backend():
    """
    **强断言**（区别于本文件里其它几条字符串弱断言）。

    重问前缀在前后端各有一份字面量：后端 app/agents/intake_question.py 的
    _REASK_PREFIX 负责写进 conversation history，前端负责显示给用户。前端无构建、
    拿不到后端常量，重复不可避免——但"重复了就会漂移"是可以被机械挡住的。

    后端改了前缀而前端没跟上时，用户看到的问题文本会与系统记下的那一份不一致，
    而这个不一致**没有任何其它信号**（不报错、不失败，只是悄悄对不上）。
    """
    from app.agents.intake_question import _REASK_PREFIX

    assert _REASK_PREFIX in INDEX_HTML, (
        f"后端重问前缀是 {_REASK_PREFIX!r}，index.html 里没有这个字面量——"
        "两边已经漂移。改前端的 REASK_PREFIX 常量与后端对齐，不要改本测试。"
    )


def test_reply_api_contract_has_no_selected_options():
    """
    **强断言**（本单元最有价值的一条自动化测试，与前端怎么写完全无关）。

    delivery-units.md §5 跨单元接口约定 2：「C 的点选提交不改 API 契约」。
    点选形态一旦改成请求体新增 selected_options 字段，就会碰 app/web/server.py 的
    ReplyRequest，单元 C 与并行进行的 B/D 从并行变串行——而这个代价在代码评审里
    看不出来（改动本身很小、很自然），只会表现为"另一条分支莫名其妙冲突了"。

    ⚠️ 这条测试将来若失败，是一次设计对话，不是一个可以删掉的测试。
    要给采集接口加字段，先回去看 delivery-units.md §2.C 与 §5，确认没有并行分支
    正在等这两个文件。
    """
    from app.web.server import CreateJobRequest, ReplyRequest

    assert set(ReplyRequest.model_fields) == {"message"}
    assert set(CreateJobRequest.model_fields) == {"message"}


def test_selection_and_free_text_compose_one_message():
    """
    tasks 4.2 / 4.5。**弱断言**——拼装逻辑在浏览器里跑，这里只能证明代码还在。
    真正的验收是 Task 3 手工验证清单的第 3、4、5 条。

    锁住三件事：
      1. 勾选结果与自由文本合并成**一条** message（而不是两次请求、或新字段）
      2. 空 + 空才 return，不再是改动前"文本框空就 return"（那会让纯点选提交失效）
      3. 请求体仍然是 {message: ...}
    """
    assert "function collectSelections" in INDEX_HTML
    assert "if (typed) parts.push(typed);" in INDEX_HTML
    assert 'const message = parts.join("\\n");' in INDEX_HTML
    assert "if (!message) return;" in INDEX_HTML
    assert "JSON.stringify({ message: message })" in INDEX_HTML
    # 改动前的短路条件必须已经消失，否则"只点选不打字"会被静默丢弃
    assert "if (!text) return;" not in INDEX_HTML
    # 提交后上一轮的选项要冻结，防止用户点到两轮之前的档位
    assert "function freezeActiveQuestions" in INDEX_HTML
    assert "box.disabled = true;" in INDEX_HTML
