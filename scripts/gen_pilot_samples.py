# scripts/gen_pilot_samples.py
"""
合成简历样本生成器（tasks 1.2 的替身；真实脱敏样本到位后同目录替换，见计划「待裁决」#2）。

样本全部虚构：姓名／公司／院校来自本文件的固定池，不对应任何真人；⛔ 不含手机号／邮箱／身份证。
输出到 data/eval/m2-pilot/（已在 .gitignore）：<id>.txt / .docx / .pdf / _scan.pdf ＋ truth.json。
「人工排序」在合成样本上 = 设计的匹配度排序（designed_fit），只用于离线指标，⛔ 不进任何 prompt 优化。

用法：python -m scripts.gen_pilot_samples --out data/eval/m2-pilot --n 20
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SEED = 20260917

SURNAMES = ["赵", "钱", "孙", "李", "周", "吴", "郑", "王", "冯", "陈", "褚", "卫"]
GIVEN_NAMES = ["明远", "子墨", "思远", "若曦", "浩然", "雨桐", "嘉豪", "欣怡", "俊杰", "语嫣", "宇航", "梓涵"]
CITIES = ["无锡", "苏州", "上海", "南京", "常州"]
SCHOOLS = [
    ("江南大学", "本科"),
    ("南京理工大学", "硕士"),
    ("东南大学", "本科"),
    ("合肥工业大学", "本科"),
    ("哈尔滨工业大学", "硕士"),
    ("苏州大学", "本科"),
    ("常州信息职业技术学院", "大专"),
]
MAJORS = ["车辆工程", "电子信息工程", "自动化", "计算机科学与技术", "测控技术与仪器"]
COMPANIES = [
    "无锡华芯车控科技有限公司",
    "苏州澜盾汽车电子有限公司",
    "上海泓驰智能装备股份有限公司",
    "南京擎川电子有限公司",
    "常州锐驰微电子有限公司",
    "合肥启程新能源科技有限公司",
    "杭州云栈软件有限公司",
]
TITLES_CORE = ["嵌入式软件工程师", "底层软件工程师", "BSP 工程师"]
TITLES_OTHER = ["测试工程师", "硬件工程师", "应用软件工程师", "Java 开发工程师"]
CORE_SKILLS = ["C", "AUTOSAR CP", "CAN", "LIN", "UDS", "MCAL", "Bootloader", "英飞凌 TC3xx", "NXP S32K", "ISO 26262"]
OTHER_SKILLS = ["Python", "Java", "Linux", "Qt", "MATLAB/Simulink", "PLC", "SQL", "前端开发", "Android", "Docker"]
DUTIES = [
    "负责 ECU 底层驱动开发与 MCAL 配置，参与两个量产项目的 SOP。",
    "负责 CAN/LIN 通信协议栈移植与 UDS 诊断服务实现。",
    "负责 Bootloader 开发与刷写流程验证。",
    "负责整车控制器应用层功能开发与台架测试。",
    "负责产线测试工装软件开发与维护。",
    "负责后台服务开发与数据库设计。",
    "负责嵌入式 Linux 板级支持包移植。",
]
SELF_EVAL = [
    "熟悉汽车电子开发流程，具备较强的问题定位能力。",
    "工作认真负责，能独立承担模块开发。",
    "有良好的团队协作与文档习惯。",
]

RUBRIC: dict = {
    "job_title": "底层软件工程师",
    "criteria": [
        {"key": "skill_match", "description": "C／AUTOSAR CP／MCAL／Bootloader／CAN-LIN-UDS 等底层技能覆盖度"},
        {"key": "experience_depth", "description": "嵌入式底层开发年限与深度"},
        {"key": "project_relevance", "description": "汽车电子量产项目经历与本岗相关性"},
        {"key": "domain_knowledge", "description": "汽车 ECU、功能安全等领域知识"},
        {"key": "education_fit", "description": "本科及以上、车辆／电子／自动化相关专业"},
    ],
    "profile_text": (
        "岗位：底层软件工程师（汽车 ECU）。要求：本科及以上，车辆／电子／自动化相关专业；"
        "3 年以上嵌入式 C 开发；熟悉 AUTOSAR CP、MCAL、Bootloader；熟悉 CAN/LIN/UDS；"
        "有量产项目经历者优先；了解 ISO 26262。工作地点无锡。"
    ),
}


@dataclass(frozen=True)
class Job:
    company: str
    start_year: int
    end_year: int
    title: str
    duty: str


@dataclass(frozen=True)
class Profile:
    sample_id: str
    name: str
    city: str | None
    school: str
    degree: str
    major: str
    grad_year: int
    jobs: tuple[Job, ...]
    skills: tuple[str, ...]
    years: int
    explicit_years: bool
    self_eval: str
    fit: float
    human_rank: int


def _designed_fit(core_n: int, years: int, degree: str, jobs: tuple[Job, ...]) -> float:
    fit = core_n * 1.0 + min(years, 8) * 0.3
    if degree in ("本科", "硕士"):
        fit += 0.5
    if any(j.title in TITLES_CORE for j in jobs):
        fit += 0.4
    return round(fit, 2)


def build_profiles(n: int = 20, seed: int = SEED) -> list[Profile]:
    rng = random.Random(seed)
    names = rng.sample([s + g for s in SURNAMES for g in GIVEN_NAMES], n)
    drafts = []
    for i in range(n):
        core_n = rng.randint(0, 6)
        skills = rng.sample(CORE_SKILLS, core_n) + rng.sample(OTHER_SKILLS, rng.randint(1, 4))
        rng.shuffle(skills)
        n_jobs = rng.randint(1, 3)
        companies = rng.sample(COMPANIES, n_jobs)
        year = 2026
        jobs: list[Job] = []
        for company in companies:
            duration = rng.randint(1, 5)
            title = rng.choice(TITLES_CORE if rng.random() < 0.6 else TITLES_OTHER)
            jobs.append(Job(company, year - duration, year, title, rng.choice(DUTIES)))
            year -= duration
        years = 2026 - jobs[-1].start_year
        school, degree = rng.choice(SCHOOLS)
        city = rng.choice(CITIES) if rng.random() > 0.25 else None
        explicit_years = rng.random() < 0.5
        drafts.append(
            dict(
                sample_id=f"S{i + 1:02d}",
                name=names[i],
                city=city,
                school=school,
                degree=degree,
                major=rng.choice(MAJORS),
                grad_year=jobs[-1].start_year,
                jobs=tuple(jobs),
                skills=tuple(skills),
                years=years,
                explicit_years=explicit_years,
                self_eval=rng.choice(SELF_EVAL),
                fit=_designed_fit(core_n, years, degree, tuple(jobs)),
            )
        )
    order = sorted(range(n), key=lambda k: (-drafts[k]["fit"], drafts[k]["sample_id"]))
    rank_of = {k: r + 1 for r, k in enumerate(order)}
    return [Profile(**drafts[k], human_rank=rank_of[k]) for k in range(n)]


def render_text(p: Profile) -> str:
    lines = [p.name, ""]
    if p.city is not None:
        lines.append(f"期望工作城市：{p.city}")
    if p.explicit_years:
        lines.append(f"工作年限：{p.years} 年")
    lines += ["", "教育背景", f"{p.school}｜{p.degree}｜{p.major}｜{p.grad_year - 4}—{p.grad_year}", "", "工作经历"]
    for j in p.jobs:
        lines.append(f"{j.company}（{j.start_year}年—{j.end_year}年）｜{j.title}")
        lines.append(f"· {j.duty}")
    lines += ["", "技能", "、".join(p.skills), "", "自我评价", p.self_eval, ""]
    return "\n".join(lines)


def truth_fields(p: Profile) -> dict:
    return {
        "name": p.name,
        "years_of_experience": p.years,
        "skills": list(p.skills),
        "companies": [j.company for j in p.jobs],
        "education": {"degree": p.degree, "school": p.school},
        "expected_city": p.city,
    }


_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def find_cjk_font() -> Path | None:
    for candidate in _FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    return None


def _write_docx(text: str, path: Path) -> None:
    import docx

    document = docx.Document()
    for line in text.split("\n"):
        document.add_paragraph(line)
    document.save(str(path))


def _write_pdf(text: str, path: Path) -> None:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    c = canvas.Canvas(str(path))
    y = 800
    for line in text.split("\n"):
        if y < 60:
            c.showPage()
            y = 800
        c.setFont("STSong-Light", 11)
        c.drawString(50, y, line)
        y -= 16
    c.save()


def _write_scan_pdf(text: str, path: Path, font: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    image_font = ImageFont.truetype(str(font), 22)
    lines = text.split("\n")
    image = Image.new("RGB", (1240, max(1754, 40 + 30 * len(lines))), "white")
    draw = ImageDraw.Draw(image)
    y = 40
    for line in lines:
        draw.text((60, y), line, fill="black", font=image_font)
        y += 30
    image.save(str(path), "PDF", resolution=150)


def write_all(out_dir: Path, profiles: list[Profile], *, font: Path | None, seed: int = SEED) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in profiles:
        text = render_text(p)
        (out_dir / f"{p.sample_id}.txt").write_text(text, encoding="utf-8")
        _write_docx(text, out_dir / f"{p.sample_id}.docx")
        _write_pdf(text, out_dir / f"{p.sample_id}.pdf")
        scan_name = None
        if font is not None:
            scan_name = f"{p.sample_id}_scan.pdf"
            _write_scan_pdf(text, out_dir / scan_name, font)
        rows.append(
            {
                "sample_id": p.sample_id,
                "files": {"txt": f"{p.sample_id}.txt", "docx": f"{p.sample_id}.docx", "pdf": f"{p.sample_id}.pdf", "scan": scan_name},
                "fields": truth_fields(p),
                "human_rank": p.human_rank,
                "designed_fit": p.fit,
            }
        )
    truth = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "sample_class": "synthetic",
        "rubric": RUBRIC,
        "samples": rows,
    }
    (out_dir / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    return truth


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成合成简历样本")
    parser.add_argument("--out", type=Path, default=Path("data/eval/m2-pilot"))
    parser.add_argument("--n", type=int, default=20)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    font = find_cjk_font()
    truth = write_all(args.out, build_profiles(args.n, seed=args.seed), font=font, seed=args.seed)
    print(f"写入 {len(truth['samples'])} 份样本到 {args.out}；扫描件：{'已生成' if font else '未生成（未找到中文字体）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
