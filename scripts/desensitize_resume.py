#!/usr/bin/env python3
"""历史离职候选人简历脱敏脚本（M2 评测集前置；D4a 草稿，规则待 Shao Peishen 审）。

用途：把 HR 提供的**历史离职候选人**简历，去掉"直接标识"，产出一份可进 M2 评测集的样本。
规则与需要审稿人拍板的取舍见 `docs/templates/历史离职简历脱敏-规则与使用说明.md`。

本脚本的硬性质：
  1. **纯本地、确定性、无 LLM、不联网**——同样的输入永远得到同样的输出，方便事后复核；
  2. **只脱直接标识**（姓名/手机/座机/邮箱/身份证/即时通讯号/出生日期/住址/紧急联系人/长号码），
     **保留**评测标注需要的公司、学校、技能、城市与工作/教育年份；
  3. **报告里不留原值**——只写替换计数与告警，不写任何被替换掉的原文（脱敏工具自己不能变成泄漏点）；
  4. **fail-closed**：`--name` 在原文里一次都没命中 ⇒ 直接失败退出（不退化成"看起来脱敏了"）。

用法：
    python -m scripts.desensitize_resume --input /path/旧简历.docx --name 张三 \
        --out-dir /path/out [--code 候选人] [--format docx|txt] [--dry-run]

退出码：0 成功；2 参数/输入问题（含姓名未命中、输出会覆盖输入）；3 文件不可读（不支持的类型/扫描件）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.parsing.extract_text import (  # noqa: E402
    OcrUnavailable,
    UnsupportedFileType,
    extract_text,
)

SUPPORTED_INPUT_SUFFIXES = (".pdf", ".docx", ".txt")
TOKEN = lambda kind: f"[{kind}已脱敏]"  # noqa: E731 —— 固定文案，集中一处方便审


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    replacement: str | Callable[[re.Match[str]], str]


def _mask_address(match: re.Match[str]) -> str:
    """住址只暴露到「市」一级（评测集要按城市标注 expected_city），门牌号等一律抹掉。"""
    prefix, rest = match.group(1), match.group(2)
    city = re.match(r"^.{0,14}?市", rest)
    province = re.match(r"^.{0,14}?省", rest)
    keep = city.group(0) if city else (province.group(0) if province else "")
    return f"{prefix}{keep}{TOKEN('住址')}"


def build_rules(name: str, code: str) -> list[Rule]:
    """顺序即优先级：先处理姓名与身份证（长数字），再处理其它。"""
    return [
        Rule("姓名", re.compile(re.escape(name)), code),
        Rule(
            "姓名标签",
            re.compile(r"((?:姓\s*名|名字)\s*[:：]\s*)([^\s,，。;；|]{2,6})"),
            rf"\1{code}",
        ),
        Rule("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), TOKEN("手机号")),
        Rule("座机", re.compile(r"(?<!\d)0\d{2,3}-?\d{7,8}(?!\d)"), TOKEN("电话")),
        Rule(
            "邮箱",
            re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
            TOKEN("邮箱"),
        ),
        Rule("身份证号", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), TOKEN("身份证号")),
        Rule(
            "即时通讯号",
            re.compile(
                r"((?:微信|WeChat|QQ|企微)\s*(?:号)?\s*[:：]?\s*)([A-Za-z][A-Za-z0-9_-]{4,})"
            ),
            r"\1[已脱敏]",
        ),
        Rule(
            "出生日期",
            re.compile(
                r"((?:出生|生日)(?:日期|年月|时间)?\s*[:：]?\s*)"
                r"(?:19|20)\d{2}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?"
            ),
            r"\1[已脱敏]",
        ),
        Rule(
            "住址",
            re.compile(
                r"((?:地址|住址|现居住地|现居|户籍|家庭住址)\s*[:：]?\s*)([^\n]{4,})"
            ),
            _mask_address,
        ),
        Rule(
            "紧急联系人",
            re.compile(r"((?:紧急联系人|联系人)\s*[:：]?\s*)[^\n]{2,}"),
            r"\1[已脱敏]",
        ),
        Rule("长号码", re.compile(r"(?<!\d)\d{16,19}(?!\d)"), TOKEN("长号码")),
    ]


def desensitize_text(text: str, *, name: str, code: str) -> tuple[str, dict[str, int]]:
    """纯函数：返回（脱敏后文本，{规则名: 命中次数}）。"""
    counts: dict[str, int] = {}
    for rule in build_rules(name, code):
        text, n = rule.pattern.subn(rule.replacement, text)
        if n:
            counts[rule.name] = n
    return text, counts


def _read_input(path: Path) -> tuple[str, list[str]]:
    """读入原文；返回（文本，告警）。txt 直读，pdf/docx 走既有抽取层。"""
    warnings: list[str] = []
    suffix = path.suffix.lower()
    if suffix == ".txt":
        text = path.read_text(encoding="utf-8", errors="replace")
    else:
        try:
            extracted = extract_text(path)
        except OcrUnavailable as exc:
            raise SystemExit(
                f"✗ 该 PDF 无文本层（扫描件），本脚本不做 OCR：{exc}\n"
                "  处置：人工核对后改用文本版，或等 PaddleOCR 可用后再处理。"
            ) from exc
        text = extracted.text
        if not extracted.readable:
            warnings.append(
                f"抽取文本有效字数仅 {extracted.effective_chars}（< {50}），可能是扫描件/空白件，请人工复核"
            )
    return text, warnings


def _write_output(text: str, out_path: Path, fmt: str) -> None:
    if fmt == "txt":
        out_path.write_text(text, encoding="utf-8")
        return
    import docx

    document = docx.Document()
    for line in text.split("\n"):
        document.add_paragraph(line)
    document.save(str(out_path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="原始简历文件（pdf/docx/txt）")
    parser.add_argument("--name", required=True, help="候选人在该文件里的真实姓名（必填，用于定位替换）")
    parser.add_argument("--code", default="候选人", help="姓名替换成的代称（默认「候选人」）")
    parser.add_argument("--out-dir", required=True, help="输出目录")
    parser.add_argument("--format", choices=("docx", "txt"), default="docx")
    parser.add_argument("--dry-run", action="store_true", help="只统计与告警，不写文件")
    args = parser.parse_args(argv)

    src = Path(args.input)
    if not src.is_file():
        print(f"✗ 输入文件不存在：{src}", file=sys.stderr)
        return 2
    if src.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
        print(
            f"✗ 只收 {SUPPORTED_INPUT_SUFFIXES}；.doc 请在 Word 里另存为 .docx 后再跑",
            file=sys.stderr,
        )
        return 3

    name = args.name.strip()
    if not name:
        print("✗ --name 不能为空", file=sys.stderr)
        return 2

    try:
        text, warnings = _read_input(src)
    except UnsupportedFileType as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 3

    if name not in text:
        print(
            f"✗ 姓名「{name}」在原文里一次都没命中——请核对 --name 是否与文件一致；"
            "⛔ 不产出『看起来脱敏了』的文件。",
            file=sys.stderr,
        )
        return 2

    out_text, counts = desensitize_text(text, name=name, code=args.code)
    report = {
        "input_file": src.name,
        "code": args.code,
        "counts": counts,
        "effective_chars_before": sum(1 for ch in text if not ch.isspace()),
        "warnings": warnings + [
            "照片/水印/页眉页脚里的标识本脚本处理不到，导出后请人工抽查一遍",
        ],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": "本报告只含计数，不含任何被替换掉的原文",
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.dry_run:
        return 0

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = unicodedata.normalize("NFC", src.stem)
    out_path = out_dir / f"{stem}-脱敏.{args.format}"
    if out_path.resolve() == src.resolve():
        print("✗ 输出路径与输入相同，拒绝覆盖原文件", file=sys.stderr)
        return 2
    _write_output(out_text, out_path, args.format)
    (out_dir / f"{stem}-脱敏报告.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"✓ 已写出：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
