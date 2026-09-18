"""Finding 2（final review）：resume_review.html 的高亮切片必须按码点（code
point）对齐后端 app/parsing/spans.py 的偏移语义，且渲染前必须核对切出来的
子串与持久化的 span.quote 逐字相等，不相等就不渲染成 <mark>。

这段逻辑本身是纯前端 JS（Array.from 切片 + 字符串比较），本仓库测试栈是
Python/pytest，没有浏览器或 Node 执行环境的既有测试基础设施（grep 全仓库
未见任何用例起 node/jsdom 跑前端脚本）。按 review 意见"use your judgment on
what's testable in this stack"，这里用两条能在本栈内做到、且分别覆盖行为
两面的用例：

1. `test_served_page_contains_codepoint_slicing_and_quote_verification`：
   字符串断言新代码路径确实在响应体里（沿用本项目 test_upload_page.py /
   test_resume_review_page.py 里"断言 fetch 调用字面量出现在页面"的既有写法，
   把断言对象换成这次新增的切片/校验代码）。
2. `test_codepoint_offset_survives_astral_char_but_naive_utf16_slice_does_not`：
   Python 侧复现"为什么必须按码点切"这件事本身——后端
   app/parsing/spans.py::split_into_spans/locate_quote 是本仓库现成的
   "span/quote 一致性"测试基础设施（tests/test_parsing_spans.py），Python
   str 的索引语义就是码点（不是 UTF-16 code unit）。本用例在 span 之前放一个
   代理对字符（surrogate pair，如 emoji），证明：
     a) 用 Python（=码点）语义切片，`text[start:end] == quote` 依然成立——
        这正是 JS 端 `Array.from(text).slice(start, end).join("")` 要复现的
        目标语义；
     b) 若改用"朴素 UTF-16 code unit 语义"去切同一个 start/end（修复前
        resume_review.html 用 `rawText.slice()` 实际做的事），切出来的子串
        与 quote **不再相等**——这就是 finding 2 描述的那类静默错位，也是
        为什么前端必须先转成码点数组、且渲染前必须核对 quote 才安全。
"""
from __future__ import annotations

from app.parsing.spans import locate_quote, split_into_spans


def test_served_page_contains_codepoint_slicing_and_quote_verification(make_test_client):
    client, _conn = make_test_client()
    resp = client.get("/resumes/r1/review")
    assert resp.status_code == 200
    html = resp.text
    # 码点数组切片，替代直接对 rawText 做 UTF-16 slice。
    assert "Array.from(text)" in html
    assert "chars.slice(span.start, span.end)" in html
    # 渲染前必须核对切片结果与持久化的 span.quote 逐字相等。
    assert "sliced === span.quote" in html
    # 不相等时不得渲染成 <mark>，走纯文本提示。
    assert "回指偏移与原文不一致" in html


def _utf16_code_unit_offsets(text: str) -> list[int]:
    """给出 text 里每个 Python 码点在 JS UTF-16 编码下的起始 code unit 下标。

    等价于「如果这段 text 是一个 JS 字符串，`Array.from(text)[i]` 这个字符在
    `text.charAt(k)` 里的 k 是多少」——非代理对字符占 1 个 unit，代理对字符
    （码点 > 0xFFFF）占 2 个 unit。用它来模拟"朴素 UTF-16 slice"这一步，不
    依赖真的起一个 JS 引擎。
    """
    offsets = []
    cursor = 0
    for ch in text:
        offsets.append(cursor)
        cursor += 2 if ord(ch) > 0xFFFF else 1
    offsets.append(cursor)  # 末尾哨兵，方便算 end
    return offsets


def _naive_utf16_slice(text: str, start: int, end: int) -> str:
    """把 start/end 当成 UTF-16 code unit 偏移（而不是码点偏移）去切 text——
    也就是修复前 resume_review.html 的 `rawText.slice(start, end)` 在 JS 引擎
    里实际发生的事：JS 字符串本身就是 UTF-16 code unit 数组，直接拿 Python
    算出来的码点偏移去 slice 一个 JS 字符串，等价于在这里按 code unit 偏移
    切。用 Python 复现同一件事：先编码成 UTF-16LE，按 2 字节一个 unit 切片，
    再解码回来（遇到落单的代理项时按 JS 的"保留孤立代理项"语义处理，errors
    参数选 surrogatepass 以免在这条模拟路径上崩溃）。
    """
    encoded = text.encode("utf-16-le")
    sliced = encoded[start * 2 : end * 2]
    return sliced.decode("utf-16-le", errors="surrogatepass")


def test_codepoint_offset_survives_astral_char_but_naive_utf16_slice_does_not():
    # U+1F600 GRINNING FACE：代理对字符，Python len() 记 1 个码点，
    # JS "😀".length 记 2 个 UTF-16 code unit——正是 finding 2 描述的分歧源头。
    astral = "\U0001F600"
    text = f"{astral}张三\n工作年限：5年\n"

    spans = split_into_spans(text)
    # "张三" 所在分片就是含 astral 字符的第一行。
    loc = locate_quote(spans[0], "张三")
    assert loc is not None
    start, end = loc

    # (a) 码点语义：Python 原生切片就是码点切片，这是后端持久化的 start/end
    #     所依据的语义，也是 JS 端 Array.from(...).slice(...) 要对齐的目标。
    assert text[start:end] == "张三"

    # (b) 朴素 UTF-16 code unit 语义（修复前 JS 直接 slice 字符串的效果）：
    #     同一对 start/end，切出来的不再是 "张三"——这就是 finding 2 说的
    #     "静默高亮错的子串"。修复后的 JS 会先核对 quote 再决定要不要渲染
    #     <mark>，遇到这种偏差会走纯文本提示而不是显示错误高亮。
    naive = _naive_utf16_slice(text, start, end)
    assert naive != "张三"

    # 佐证：用真的 UTF-16 code unit 下标表去核实 naive 切片确实切偏了一位
    # （因为 astral 字符在 UTF-16 里占了 2 个 unit，而 Python 端只记了 1 个
    # 码点），不是巧合碰对。
    unit_offsets = _utf16_code_unit_offsets(text)
    assert unit_offsets[start] != start  # 码点下标与 UTF-16 unit 下标已经错位
