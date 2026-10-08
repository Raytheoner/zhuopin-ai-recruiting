#!/usr/bin/env python3
"""跟进信 md → docx 附件渲染器（2026-10-08；统一字体版）。

为什么有它：`send-followup` 的附件 docx 之前由一次性脚本生成，只设了西文字体
（`font.name`）而**没设东亚字体**（`w:eastAsia`）——中文字符各自回退到默认字体，
一份文档里"标题一种、正文一种、加粗段又一种"，肉眼就是参差不齐（Shao Peishen
2026-10-08 指出的问题）。这里把**正文与各级标题的东亚/西文字体、字号、间距一并钉死**，
保证同一份文档全篇一致。

用法：
    python -m scripts.letter_md_to_docx <letter.md> <letter.docx>

字体选择（可在函数参数里覆盖）：正文＝微软雅黑（Windows 与企微/手机端渲染稳定），
等宽＝Consolas（行内代码）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import docx
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

DEFAULT_BODY_FONT = "微软雅黑"
DEFAULT_MONO_FONT = "Consolas"


def _pin_font(style, *, ascii_font: str, east_asia_font: str, size_pt: float,
              bold: bool | None = None, color: tuple[int, int, int] | None = None,
              space_after_pt: float | None = None) -> None:
    """把样式的东西文字体一起钉死（`w:rFonts` 的 ascii/hAnsi/eastAsia 三处）。"""
    style.font.name = ascii_font
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), ascii_font)
    rfonts.set(qn("w:hAnsi"), ascii_font)
    rfonts.set(qn("w:eastAsia"), east_asia_font)
    style.font.size = Pt(size_pt)
    if bold is not None:
        style.font.bold = bold
    if color is not None:
        style.font.color.rgb = RGBColor(*color)
    if space_after_pt is not None:
        style.paragraph_format.space_after = Pt(space_after_pt)


def _add_runs(paragraph, text: str, *, body_font: str, mono_font: str) -> None:
    """渲染 **粗体** 与 `行内码`；两种 run 都显式钉字体，避免继承到混搭。"""
    for part in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            run = paragraph.add_run(part[2:-2])
            run.bold = True
            run.font.name = body_font
            run._element.rPr.rFonts.set(qn("w:eastAsia"), body_font)
        elif part.startswith("`") and part.endswith("`"):
            run = paragraph.add_run(part[1:-1])
            run.font.name = mono_font
            run._element.rPr.rFonts.set(qn("w:eastAsia"), mono_font)
        else:
            run = paragraph.add_run(part)
            run.font.name = body_font
            run._element.rPr.rFonts.set(qn("w:eastAsia"), body_font)


def render_letter_docx(
    md_path: str | Path,
    docx_path: str | Path,
    *,
    body_font: str = DEFAULT_BODY_FONT,
    mono_font: str = DEFAULT_MONO_FONT,
) -> Path:
    lines = Path(md_path).read_text(encoding="utf-8").splitlines()
    if lines and lines[0].strip() == "---":  # 跳过 YAML frontmatter
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                lines = lines[i + 1 :]
                break

    document = docx.Document()
    _pin_font(document.styles["Normal"], ascii_font=body_font, east_asia_font=body_font,
              size_pt=11, space_after_pt=6)
    _pin_font(document.styles["Heading 1"], ascii_font=body_font, east_asia_font=body_font,
              size_pt=16, bold=True, color=(0x1F, 0x1F, 0x1F), space_after_pt=10)
    _pin_font(document.styles["Heading 2"], ascii_font=body_font, east_asia_font=body_font,
              size_pt=13.5, bold=True, color=(0x1F, 0x1F, 0x1F), space_after_pt=8)
    _pin_font(document.styles["Heading 3"], ascii_font=body_font, east_asia_font=body_font,
              size_pt=12, bold=True, color=(0x1F, 0x1F, 0x1F), space_after_pt=6)
    for list_style in ("List Bullet", "List Number"):
        _pin_font(document.styles[list_style], ascii_font=body_font, east_asia_font=body_font,
                  size_pt=11, space_after_pt=4)

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("### "):
            document.add_heading(line[4:].strip(), level=3)
        elif line.startswith("## "):
            document.add_heading(line[3:].strip(), level=2)
        elif line.startswith("# "):
            document.add_heading(line[2:].strip(), level=1)
        elif re.match(r"^\s*[-*] ", line):
            _add_runs(document.add_paragraph(style="List Bullet"),
                      re.sub(r"^\s*[-*] ", "", line), body_font=body_font, mono_font=mono_font)
        elif re.match(r"^\s*\d+\. ", line):
            _add_runs(document.add_paragraph(style="List Number"),
                      re.sub(r"^\s*\d+\. ", "", line), body_font=body_font, mono_font=mono_font)
        else:
            _add_runs(document.add_paragraph(), line, body_font=body_font, mono_font=mono_font)

    out = Path(docx_path)
    document.save(str(out))
    return out


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print("用法：python -m scripts.letter_md_to_docx <letter.md> <letter.docx>", file=sys.stderr)
        return 2
    out = render_letter_docx(argv[0], argv[1])
    print(f"saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
