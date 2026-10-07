"""scripts/desensitize_resume.py：脱敏规则、fail-closed 与产物形态的纯函数测试。

全部使用合成假数据（`13800138000` 一类测试号码），⛔ 不使用任何真实候选人信息。
"""
from __future__ import annotations

import json

from app.parsing.extract_text import extract_docx
from scripts.desensitize_resume import desensitize_text, main

SAMPLE = """姓名：张三
性别：男   出生日期：1990年5月3日
手机：13800138000   座机：0510-88887777
邮箱：zhangsan@example.com   微信：zhangsan_wx
身份证号：110101199005031234
现居住地：江苏省无锡市示例小区 1 号
紧急联系人：李四 13700137000

工作经历
2017年9月-2021年6月   示例汽车电子有限公司   嵌入式软件工程师

教育经历
2013年9月-2017年6月   示例工业大学   电子信息工程   本科

技能：C / AUTOSAR CP / UDS
银行卡：6222020200112233445
"""


def test_all_direct_identifiers_are_masked() -> None:
    out, counts = desensitize_text(SAMPLE, name="张三", code="候选人")
    for leaked in (
        "张三",
        "13800138000",
        "0510-88887777",
        "zhangsan@example.com",
        "zhangsan_wx",
        "110101199005031234",
        "1990年5月3日",
        "示例小区",
        "李四",
        "13700137000",
        "6222020200112233445",
    ):
        assert leaked not in out, f"直接标识泄漏：{leaked}"
    assert "候选人" in out
    assert counts["手机号"] == 2
    assert counts["邮箱"] == 1
    assert counts["身份证号"] == 1
    assert counts["长号码"] == 1


def test_work_and_education_years_are_preserved() -> None:
    """评测标注需要工作/教育年份；只有带「出生/生日」标签的日期才脱敏。"""
    out, _ = desensitize_text(SAMPLE, name="张三", code="候选人")
    assert "2017年9月-2021年6月" in out
    assert "2013年9月-2017年6月" in out
    assert "[已脱敏]" in out  # 出生日期那行


def test_evaluation_relevant_fields_are_preserved() -> None:
    out, _ = desensitize_text(SAMPLE, name="张三", code="候选人")
    for kept in ("示例汽车电子有限公司", "示例工业大学", "无锡", "AUTOSAR CP"):
        assert kept in out
    # 城市保留到「市」一级，门牌等仍必须抹掉
    assert "江苏省无锡市" in out
    assert "示例小区" not in out


def test_desensitize_is_idempotent_for_non_name_tokens() -> None:
    out1, _ = desensitize_text(SAMPLE, name="张三", code="候选人")
    out2, counts2 = desensitize_text(out1, name="候选人", code="候选人")
    assert out2 == out1
    assert counts2 == {} or "姓名" in counts2


def test_name_missing_fails_closed(tmp_path) -> None:
    src = tmp_path / "r.txt"
    src.write_text("姓名：王五\n", encoding="utf-8")
    rc = main(["--input", str(src), "--name", "张三", "--out-dir", str(tmp_path / "out")])
    assert rc == 2
    assert not (tmp_path / "out").exists(), "姓名未命中时不得产出任何文件"


def test_txt_run_writes_output_and_report_without_original_values(tmp_path) -> None:
    src = tmp_path / "旧简历.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    out_dir = tmp_path / "out"
    rc = main(
        [
            "--input",
            str(src),
            "--name",
            "张三",
            "--code",
            "候选人A",
            "--out-dir",
            str(out_dir),
            "--format",
            "txt",
        ]
    )
    assert rc == 0
    produced = (out_dir / "旧简历-脱敏.txt").read_text(encoding="utf-8")
    assert "张三" not in produced and "候选人A" in produced
    report_text = (out_dir / "旧简历-脱敏报告.json").read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert report["counts"]["手机号"] == 2
    for leaked in ("张三", "13800138000", "zhangsan@example.com", "110101199005031234"):
        assert leaked not in report_text


def test_docx_output_roundtrip(tmp_path) -> None:
    src = tmp_path / "旧简历.txt"
    src.write_text(SAMPLE, encoding="utf-8")
    out_dir = tmp_path / "out"
    rc = main(
        [
            "--input",
            str(src),
            "--name",
            "张三",
            "--out-dir",
            str(out_dir),
            "--format",
            "docx",
        ]
    )
    assert rc == 0
    text = extract_docx(out_dir / "旧简历-脱敏.docx")
    assert "13800138000" not in text
    assert "示例工业大学" in text


def test_unsupported_suffix_rejected(tmp_path) -> None:
    src = tmp_path / "旧简历.doc"
    src.write_bytes(b"\xd0\xcf\x11\xe0")
    rc = main(["--input", str(src), "--name", "张三", "--out-dir", str(tmp_path / "out")])
    assert rc == 3
