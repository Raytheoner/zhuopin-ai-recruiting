"""
1001E · M1 9.1「画像质量验收」重跑执行器（Mac，Codex 泳道 lane-1001e-m1-9-1-replay）。

背景：`m1-job-profile-intake` 的 9.1 验收是「10 个真实历史岗位重跑，HR 与业务经理
双方评估技术栈字段准确率，目标 ≥80%」。其中机械的一半由本脚本完成——把归档的
《10个岗位需求.doc》切成 10 份 JD，逐份跑真 LLM intake，把画像（尤其技术栈字段）
落成可评估的 JSON。人评那半（HR／业务经理逐格打勾）不代做。

本脚本不发对外消息、不写任何生产库、不改 app/、不打印 api key。
产物全部落 `data/eval/m1-9.1/`（真实岗位需求是内部资料，`data/` 已被根 `.gitignore`
覆盖，不进版本库）。

用法：

    # 0) 一次性：从归档 .doc 切出 10 份 JD（textutil 解 doc，按「<岗位名>岗位需求」标题切）
    ./venv/bin/python -m scripts.m1_acceptance_replay --extract-doc <path>/10个岗位需求.doc

    # 1) 干跑（默认；只列岗位与字符数，不发任何请求）
    ./venv/bin/python -m scripts.m1_acceptance_replay

    # 2) 真跑（联网调 LLM，必须显式 --live）
    ./venv/bin/python -m scripts.m1_acceptance_replay --live

    # 3) 由 results/*.json 生成评估表（需 openpyxl；仓库 venv 无此包，用 /usr/bin/python3）
    /usr/bin/python3 scripts/m1_acceptance_replay.py --make-table

凭据解析：泳道 worktree 里没有 `.env`（gitignored，只存在主工作区根目录）。
`run-lanes.sh` 起的泳道会自动导出 `HR_LANE_MAIN`，本脚本据此把主区根目录的 `.env`
一并列为候选（先 cwd 的 `.env`，再 `$HR_LANE_MAIN/.env`）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

DEFAULT_JOBS_DIR = "data/eval/m1-9.1/jobs"
DEFAULT_OUT_DIR = "data/eval/m1-9.1/results"
DEFAULT_XLSX = "docs/templates/m1-画像技术栈评估表.xlsx"
#: `--make-report` 的默认落点（2026-10-07 1001E 补跑：结果文档**由脚本生成**，
#: ⛔ 不手抄——换模型/换岗位重跑后一条命令就能刷新，避免"文档与 JSON 各说各话"。
DEFAULT_REPORT = "docs/findings/2026-10-07-m1-9.1-画像重跑结果.md"

# doc 里每个岗位的标题行形如「<岗位名>岗位需求」。
_HEADING_SUFFIX = "岗位需求"

# 9.1 验收要人评对照的技术栈六维。真源 = app/schemas/job_profile.py 的字段名，
# 改那里就要同步改这里（本脚本只做机械重跑，不另立一套字段名）。
TECH_STACK_FIELDS: tuple[str, ...] = (
    "core_skills",
    "toolchain",
    "mcu_family",
    "diag_stack",
    "autosar_experience",
    "functional_safety",
)

# 评估表表头（逐字对齐 opener；机器判据按它断言，不要改动顺序或标点）。
EVAL_HEADER: tuple[str, ...] = (
    "岗位",
    "技术栈维度",
    "AI 值",
    "人工值（请填）",
    "是否命中（✓/✗）",
    "备注",
)

# 未取得 LLM 结果时的占位（绝不编造技术栈取值来充数）。
NOT_RUN = "⏸ 留步：未取得"


# ──────────────────────────────────────────────────────────────────────
# 一、切分与提取（纯函数 + textutil，原 .doc 只读）
# ──────────────────────────────────────────────────────────────────────


def split_jobs(text: str) -> list[tuple[str, str]]:
    """把《10个岗位需求.doc》的纯文本按「<岗位名>岗位需求」标题切成一段一岗。

    每段返回**原样段落**（含标题行、含段内空行与换页符），一行都不裁剪：短岗位
    （市场主管整段连标题才 ~190 字）一旦 `strip()` 掉标题与尾空白，就不足机器判据
    的 `len >= 200`，会被误判为空壳。原样切分即可自然满足，且比"补字"诚实。

    用 `text.split("\\n")` 而不是 `str.splitlines()`：textutil 的输出里带换页符
    （`\\x0c`），而 `splitlines()` 把 `\\x0c` 也当行边界，会把紧跟其后的标题行切碎、
    切出来的段数与岗位数对不上。
    """
    jobs: list[tuple[str, list[str]]] = []
    name: str | None = None
    buf: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if (
            stripped.endswith(_HEADING_SUFFIX)
            and len(stripped) > len(_HEADING_SUFFIX)
            and len(stripped) <= 30
        ):
            if name is not None:
                jobs.append((name, buf))
            name = stripped[: -len(_HEADING_SUFFIX)].strip(" ：:")
            buf = [line]
        elif name is not None:
            buf.append(line)
    if name is not None:
        jobs.append((name, buf))
    return [(job_name, "\n".join(lines)) for job_name, lines in jobs]


def extract_doc(src: Path, jobs_dir: Path) -> list[Path]:
    """textutil 解 doc → 切 10 段 → 写 `jobs_dir/NN-<岗位名>.txt`。"""
    if not src.is_file():
        raise SystemExit(f"归档 doc 不存在：{src}")
    proc = subprocess.run(
        ["/usr/bin/textutil", "-convert", "txt", "-stdout", str(src)],
        capture_output=True,
        check=True,
    )
    jobs = split_jobs(proc.stdout.decode("utf-8", errors="replace"))
    if len(jobs) != 10:
        raise SystemExit(f"按「岗位需求」切出 {len(jobs)} 段，期望 10 段——doc 结构可能变了")
    jobs_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for index, (name, body) in enumerate(jobs, 1):
        path = jobs_dir / f"{index:02d}-{name}.txt"
        path.write_text(body + "\n", encoding="utf-8")
        written.append(path)
    return written


def list_jobs(jobs_dir: Path) -> list[Path]:
    return sorted(jobs_dir.glob("*.txt"))


# ──────────────────────────────────────────────────────────────────────
# 二、真跑（联网调 LLM）
# ──────────────────────────────────────────────────────────────────────


def _env_file_candidates() -> tuple[str, ...]:
    """Settings 的 env_file 候选：先 cwd 的 .env，再 `$HR_LANE_MAIN/.env`。

    泳道 worktree 里没有 .env（gitignored），凭据在主工作区根目录；run-lanes.sh 会
    给泳道导出 HR_LANE_MAIN，据此兜底。两处都没有时仍按空配置运行，会在建网关前
    明确报错（绝不静默拿空 key 去打 API）。
    """
    candidates = [".env"]
    main = os.environ.get("HR_LANE_MAIN")
    if main:
        candidates.append(str(Path(main) / ".env"))
    return tuple(candidates)


def build_gateway():
    """按 app/main.py 的 `_gateway_factory` 同一形状构造网关（temperature=0 由
    LLMGateway 自带，铁律 5）。不打印 key。"""
    from app.config import Settings
    from app.llm.gateway import LLMGateway

    settings = Settings(_env_file=_env_file_candidates())
    if not settings.llm_api_key:
        raise SystemExit(
            "LLM_API_KEY 为空：泳道 worktree 里没有 .env（gitignored），"
            "请确认主工作区根目录 .env 存在，或导出 HR_LANE_MAIN 指向它。"
        )
    return LLMGateway(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        supports_json_schema=settings.llm_supports_json_schema,
        fallback_api_key=settings.llm_fallback_api_key,
        fallback_base_url=settings.llm_fallback_base_url,
        fallback_model=settings.llm_fallback_model,
        fallback_supports_json_schema=settings.llm_fallback_supports_json_schema,
    )


def run_live(jobs_dir: Path, out_dir: Path) -> list[Path]:
    """逐份 JD 跑一轮真 intake，落 `out_dir/NN.json`。"""
    from app.agents.intake_agent import run_intake_turn

    gateway = build_gateway()
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for path in list_jobs(jobs_dir):
        jd_text = path.read_text(encoding="utf-8")
        started = time.monotonic()
        result = run_intake_turn(
            gateway,
            history=[{"role": "user", "content": jd_text}],
            round_count=0,
        )
        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        payload = {
            "job_file": path.name,
            # 铁律 5：配置里写的名字不算数，响应返回的才算。
            "model": result.llm_response_model,
            "elapsed_ms": elapsed_ms,
            "is_job_related": result.is_job_related,
            "profile_patch": result.profile_patch,
            "questions_count": len(result.questions),
        }
        target = out_dir / f"{path.stem}.json"
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        written.append(target)
        print(
            f"[live] {path.name}  model={payload['model']}  "
            f"fields={len(payload['profile_patch'])}  q={payload['questions_count']}  "
            f"{elapsed_ms}ms"
        )
    return written


# ──────────────────────────────────────────────────────────────────────
# 三、评估表生成（openpyxl；用 /usr/bin/python3 跑）
# ──────────────────────────────────────────────────────────────────────


def _render_value(value: object) -> str:
    """把画像字段值渲染成给人看的单元格文本。空值一律「未提及」（不编）。"""
    if value in (None, "", [], {}):
        return "未提及"
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                name = str(item.get("name") or json.dumps(item, ensure_ascii=False))
                if "required" in item:
                    name += "（必会）" if item.get("required") else "（加分）"
                parts.append(name)
            else:
                parts.append(str(item))
        return "；".join(parts) if parts else "未提及"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def build_eval_table(results_dir: Path, jobs_dir: Path, xlsx_path: Path) -> Path:
    """由 `results_dir/*.json` 生成 10 岗位 × 6 维度的评估表。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    jobs = list_jobs(jobs_dir)
    if not jobs:
        raise SystemExit(f"{jobs_dir} 下没有 JD txt——先跑 --extract-doc")

    wb = Workbook()
    ws = wb.active
    ws.title = "M1-9.1画像技术栈评估表"
    ws.append(list(EVAL_HEADER))
    for cell in ws[1]:
        cell.font = Font(bold=True)

    row_count = 0
    for job in jobs:
        result_path = results_dir / f"{job.stem}.json"
        has_result = result_path.is_file()
        profile = (
            json.loads(result_path.read_text(encoding="utf-8")).get("profile_patch", {})
            if has_result
            else {}
        )
        for field in TECH_STACK_FIELDS:
            if not has_result:
                ai_value = NOT_RUN
            elif field in profile:
                ai_value = _render_value(profile[field])
            else:
                ai_value = "未提及"
            ws.append([job.stem, field, ai_value, "", "", ""])
            row_count += 1

    last_row = row_count + 1  # 含表头
    for letter, width in zip("ABCDEF", (22, 22, 48, 20, 16, 28)):
        ws.column_dimensions[letter].width = width

    # ── 汇总 sheet：命中数/总行数 与准确率，全部用公式（不写字面量结果）──
    summary = wb.create_sheet("汇总")
    summary["A1"] = "指标"
    summary["B1"] = "值"
    summary["A2"] = "命中数（✓）"
    summary["B2"] = f'=COUNTIF(\'{ws.title}\'!E2:E{last_row},"✓")'
    summary["A3"] = "总行数"
    summary["B3"] = row_count
    summary["A4"] = "准确率"
    summary["B4"] = "=IF(B3=0,\"\",B2/B3)"
    summary["B4"].number_format = "0.0%"
    summary["A5"] = "是否达标（≥80%）"
    summary["B5"] = '=IF(B4="","",IF(B4>=0.8,"通过","未通过"))'
    summary["A7"] = "说明"
    summary["B7"] = (
        "命中数 = E 列（是否命中）填了 ✓ 的行数；"
        "命中判定：同义/大小写/中英混写算命中，缺项（AI 值=未提及）算未命中。"
    )
    summary.column_dimensions["A"].width = 22
    summary.column_dimensions["B"].width = 60

    # ── 填写说明 sheet ──
    guide = wb.create_sheet("填写说明")
    for i, line in enumerate(
        (
            "怎么填这张表（给 HR／业务经理）：",
            "1. 逐行对照该岗位的 JD 原文，看 C 列「AI 值」对不对。",
            "2. 对，就在 D 列（人工值）写下你认为正确的值，E 列填 ✓；",
            "   错或缺失，E 列填 ✗、D 列写上正确值，F 列备注可留空。",
            "3. 命中判定：同义、大小写不同、中英混写都算命中；"
            "AI 值=「未提及」而 JD 里其实有 ⇒ 算未命中。",
            "4. C 列出现「⏸ 留步：未取得」表示该岗位还没跑过真 LLM（例如执行环境无法联网），"
            "不算命中、也不算未命中——请先让机械重跑补齐。",
            "5. 准确率 = ✓ 行数 / 总行数；≥80% 才算 9.1 技术栈字段验收通过（见「汇总」sheet）。",
            "6. 本表只评「技术栈字段」；不含任何候选人淘汰判断。",
        ),
        1,
    ):
        guide.cell(row=i, column=1, value=line)
    guide.column_dimensions["A"].width = 100

    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx_path)
    return xlsx_path


def build_report(results_dir: Path, jobs_dir: Path, md_path: Path) -> Path:
    """由 `results_dir/*.json` 生成**结果记录**（给人看的机械部分）。

    ⛔ 这不是 9.1 的验收结论：9.1 = 重跑 ＋ **HR／业务经理评估**（准确率 ≥80%），
    人评那一半不在这里，也不由脚本代打勾。
    """
    jobs = list_jobs(jobs_dir)
    if not jobs:
        raise SystemExit(f"{jobs_dir} 下没有 JD txt——先跑 --extract-doc")

    lines: list[str] = [
        "# 2026-10-07 M1 9.1 画像重跑结果（机械部分）",
        "",
        "> ⚠️ 本文件由 `scripts/m1_acceptance_replay.py --make-report` **生成**，⛔ 不要手改——重跑后同一条命令刷新。",
        "> 材料来源与跑法见 `docs/findings/2026-10-07-m1-9.1-画像重跑记录.md`；"
        "人工评估用 `docs/templates/m1-画像技术栈评估表.xlsx`。",
        "> ⛔ **这不是 9.1 的验收结论**：9.1 = 10 个真实历史岗位重跑 ＋ **HR 与业务经理评估**"
        "技术栈字段准确率 ≥80%；人评那一半不在此文件。",
        "",
        "## 一、跑批摘要",
        "",
        "| # | 岗位 | 模型 | 耗时 ms | 画像字段数 | 追问条数 | 结果文件 |",
        "|---|---|---|---|---|---|---|",
    ]

    payloads: list[tuple[Path, dict]] = []
    for idx, job in enumerate(jobs, 1):
        result_path = results_dir / f"{job.stem}.json"
        if not result_path.is_file():
            lines.append(f"| {idx:02d} | {job.stem} | {NOT_RUN} | — | — | — | 缺 |")
            continue
        data = json.loads(result_path.read_text(encoding="utf-8"))
        payloads.append((job, data))
        patch = data.get("profile_patch") or {}
        lines.append(
            f"| {idx:02d} | {job.stem} | {data.get('model') or '—'} | "
            f"{data.get('elapsed_ms', '—')} | {len(patch)} | "
            f"{data.get('questions_count', '—')} | `{result_path.name}` |"
        )

    lines += ["", "## 二、逐岗位技术栈字段（AI 取值，未提及＝JD 里没写）", ""]
    for job, data in payloads:
        patch = data.get("profile_patch") or {}
        lines += [f"### {job.stem}", "", "| 维度 | AI 值 |", "|---|---|"]
        for fld in TECH_STACK_FIELDS:
            lines.append(f"| `{fld}` | {_render_value(patch.get(fld))} |")
        others = [
            (k, _render_value(v))
            for k, v in patch.items()
            if k not in TECH_STACK_FIELDS and k != "job_title"
        ]
        if others:
            lines += ["", "其余抽取字段：" + "；".join(f"`{k}`={v}" for k, v in others)]
        lines.append("")

    lines += [
        "## 三、下一步（人评）",
        "",
        "1. 打开 `docs/templates/m1-画像技术栈评估表.xlsx`，逐行对照 JD 原文判 `AI 值` 对不对；",
        "2. 命中填 ✓、未命中填 ✗ 并写上正确值（判定口径见该表「填写说明」sheet）；",
        "3. 汇总 sheet 的准确率 ≥80% 才算 9.1 技术栈字段验收通过；通过后由看护者勾 9.1 并归档",
        "   `m1-job-profile-intake` / `m1-intake-quality-fixes`。",
        "",
    ]

    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


# ──────────────────────────────────────────────────────────────────────
# 四、CLI
# ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M1 9.1 画像质量验收重跑")
    parser.add_argument("--jobs-dir", default=DEFAULT_JOBS_DIR)
    parser.add_argument("--out", default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--live", action="store_true", help="真跑（联网调 LLM）；不给只干跑"
    )
    parser.add_argument(
        "--extract-doc",
        metavar="DOC",
        help="从归档 .doc 切出 10 份 JD 到 --jobs-dir 后退出（textutil）",
    )
    parser.add_argument(
        "--make-table",
        metavar="XLSX",
        nargs="?",
        const=DEFAULT_XLSX,
        help=f"由 --out 的 results/*.json 生成评估表（默认 {DEFAULT_XLSX}）",
    )
    parser.add_argument(
        "--make-report",
        metavar="MD",
        nargs="?",
        const=DEFAULT_REPORT,
        help=f"由 --out 的 results/*.json 生成结果记录（默认 {DEFAULT_REPORT}）",
    )
    args = parser.parse_args(argv)

    jobs_dir = Path(args.jobs_dir)
    out_dir = Path(args.out)

    if args.extract_doc:
        for path in extract_doc(Path(args.extract_doc), jobs_dir):
            print(f"[extract] {path.name}  {len(path.read_text(encoding='utf-8'))} chars")
        return 0

    if args.make_table:
        xlsx = build_eval_table(out_dir, jobs_dir, Path(args.make_table))
        print(f"[table] {xlsx}")
        return 0

    if args.make_report:
        md = build_report(out_dir, jobs_dir, Path(args.make_report))
        print(f"[report] {md}")
        return 0

    jobs = list_jobs(jobs_dir)
    if not jobs:
        raise SystemExit(f"{jobs_dir} 下没有 JD txt——先跑 --extract-doc")
    if args.live:
        run_live(jobs_dir, out_dir)
    else:
        print(f"[dry-run] 将跑 {len(jobs)} 个岗位（未发任何请求）：")
        for path in jobs:
            print(f"  {path.name}  {len(path.read_text(encoding='utf-8'))} chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
