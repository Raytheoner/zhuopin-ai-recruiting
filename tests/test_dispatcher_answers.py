"""已答定夺 ⇒ 台账任务（[Mac]0917AQ）。

缺口：`docs/roadmap/定夺队列.md` 里状态＝已答的行既没解除「阻塞的任务 id」的阻塞，也没生成答复蕴含的新动作，
调度器三轮 ready=0；且 Q-02 已答后仍被文本启发式（🔴／Shao Peishen）永久重判「决策」。
本文件钉住四条：① 已答行解阻塞（队列覆盖文本启发式）② 映射幂等 ③ 无映射项进队列而非静默 ④ 待答行按队列列的阻塞类型压阻塞。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts import dispatcher_answers as A
from scripts import dispatcher_backlog as B
from scripts.dispatcher_backlog import Entry
from tests.test_dispatcher_backlog import by_id, load, make_repo, run

HEADER = (
    "| 编号 | 场景 | 阻塞类型 | 问题 | 选项与代价 | 推荐 | 来源 | 阻塞的任务 id | 状态 | 答复 |\n"
    "|---|---|---|---|---|---|---|---|---|---|\n"
)


def queue_md(rows: list[str], far: list[str] | None = None) -> str:
    text = "# 定夺队列（R5）\n\n## 一、待答\n\n" + HEADER + "".join(r + "\n" for r in rows)
    if far:
        text += (
            "\n## 二、远期定夺\n\n| 编号 | 场景 | 阻塞类型 | 问题 | 前置 | 来源 | 阻塞的任务 id |\n|---|---|---|---|---|---|---|\n"
            + "".join(r + "\n" for r in far)
        )
    return text


def row(no: str, scene: str, btype: str, ids: str, status: str, reply: str, question: str = "问题") -> str:
    return f"| {no} | {scene} | {btype} | {question} | (a) 甲；(b) 乙 | a | 来源 | {ids} | {status} | {reply} |"


def ent(id: str, 状态: str = "待开", 阻塞类型: str = "无", 阶段: str = "build", 场景: str = "M2") -> Entry:
    return Entry(id=id, 场景=场景, 阶段=阶段, 标题=id, 来源="x", 状态=状态, 阻塞类型=阻塞类型, 产出判据="判据")


# ── 答复键解析 ──


@pytest.mark.parametrize(
    "reply,key",
    [
        ("a：现在启动（Shao Peishen 17:5x）", "a"),
        ("b：底层软件工程师", "b"),
        ("①：评分 prompt 只返回 offset", "①"),
        ("作废：两份答复单为 Cowork 所写", "作废"),
        ("定", "定"),
        ("(a) 补映射", "a"),
        ("A：大写也认", "a"),
        ("是，照推荐", "是"),
        ("ab：两个字母不是选项", ""),
        ("", ""),
    ],
)
def test_reply_key_takes_leading_option_token(reply, key):
    assert A.reply_key(reply) == key


@pytest.mark.parametrize(
    "key,expected",
    [
        ("Q-38b", "Q-38"),
        ("Q-38a", "Q-38"),
        ("Q-38", "Q-38"),
        ("Q-380", "Q-380"),  # 数字结尾，不是选项字母，不剥
        ("Q-19①", "Q-19"),
    ],
)
def test_normalize_map_key_strips_trailing_option_letter_but_not_digit(key, expected):
    assert A.normalize_map_key(key) == expected


def test_apply_answers_falls_back_to_normalized_key_when_exact_key_misses():
    e = ent("answer:Q-07", 状态="待开", 阻塞类型="无")
    q = queue_md([row("Q-38", "M2", "决策（预算与外部采购，不可代）", "`answer:Q-07`", "已答", "b：继续搁置")])
    amap = {"Q-38": A.Mapping(说明="空操作", 保持阻塞={"answer:Q-07": ("外部", "维持搁置")})}
    rep = A.apply_answers([e], q, new_entry=Entry, answer_map=amap)
    assert (e.状态, e.阻塞类型) == ("阻塞", "外部")
    assert rep.保持阻塞 == ["answer:Q-07"]
    assert rep.缺映射 == []


# ── ① 已答行解阻塞，队列覆盖文本启发式 ──


def test_answered_row_unblocks_listed_tasks_even_if_text_says_decision():
    e = ent("m2/0.9", 状态="阻塞", 阻塞类型="决策")
    q = queue_md([row("Q-90", "M2", "决策（不可代）", "`m2/0.9`", "已答", "a：去做")])
    rep = A.apply_answers([e], q, new_entry=Entry, answer_map={"Q-90a": A.Mapping(说明="只解阻塞")})
    assert (e.状态, e.阻塞类型) == ("待开", "无")
    assert e.队列 == "Q-90 已答 a"
    assert rep.解阻塞 == ["m2/0.9"]
    assert rep.缺映射 == []


def test_completed_or_running_tasks_are_never_touched_by_queue():
    done, running = ent("m2/0.3", 状态="完成"), ent("m2/0.4", 状态="在跑")
    q = queue_md([
        row("Q-91", "M2", "决策", "`m2/0.3`", "已答", "b：选它"),
        row("Q-92", "M2", "外部输入（汤丽萍）", "`m2/0.4`", "待答", ""),
    ])
    A.apply_answers([done, running], q, new_entry=Entry, answer_map={"Q-91b": A.Mapping(说明="只解阻塞")})
    assert (done.状态, running.状态) == ("完成", "在跑")


def test_keep_blocked_override_switches_type_and_writes_reason():
    e = ent("m2/1.4", 状态="阻塞", 阻塞类型="决策")
    q = queue_md([row("Q-93", "M2", "决策（预算）", "`m2/1.4`", "已答", "a：注册两家")])
    amap = {"Q-93a": A.Mapping(说明="等 key", 保持阻塞={"m2/1.4": ("外部", "等本人写 .env")})}
    rep = A.apply_answers([e], q, new_entry=Entry, answer_map=amap)
    assert (e.状态, e.阻塞类型) == ("阻塞", "外部")
    assert e.产出判据.startswith("【Q-93 已答 a·仍阻塞：等本人写 .env】")
    assert rep.保持阻塞 == ["m2/1.4"] and rep.解阻塞 == []


def test_keep_blocked_is_sticky_across_rows_so_a_later_answer_cannot_unblock_it():
    e = ent("m2/8.10", 状态="阻塞", 阻塞类型="决策")
    q = queue_md([
        row("Q-03", "M2", "决策", "`m2/8.10`", "已答", "b：选它"),
        row("Q-30", "M2", "决策", "`m2/8.10`", "已答", "a：ok"),
    ])
    amap = {"Q-03b": A.Mapping(说明="x", 保持阻塞={"m2/8.10": ("决策", "入库闸未开")}), "Q-30a": A.Mapping(说明="只解阻塞")}
    rep = A.apply_answers([e], q, new_entry=Entry, answer_map=amap)
    assert (e.状态, e.阻塞类型) == ("阻塞", "决策")
    assert rep.保持阻塞 == ["m2/8.10"] and rep.解阻塞 == []
    assert e.产出判据.count("仍阻塞") == 1  # 前缀不叠加


def test_voided_row_completes_listed_tasks_with_void_note():
    e = ent("relay:x", 状态="阻塞", 阻塞类型="决策")
    q = queue_md([row("Q-94", "M0", "决策", "`relay:x`", "作废", "作废：非越界")])
    rep = A.apply_answers([e], q, new_entry=Entry, answer_map={})
    assert (e.状态, e.阻塞类型) == ("完成", "无")
    assert e.产出判据.startswith("【作废：Q-94】")
    assert rep.作废 == ["relay:x"] and rep.缺映射 == []


# ── ④ 待答行按队列列压阻塞，且压过同 id 的已答解阻塞 ──


def test_pending_row_blocks_with_queue_column_type_and_wins_over_answered():
    e = ent("m2/U6")
    q = queue_md([
        row("Q-95", "M2", "外部输入（本人发测试文件）", "`m2/U6`", "待答", ""),
        row("Q-96", "M2", "决策", "`m2/U6`", "已答", "b：选它"),
    ])
    rep = A.apply_answers([e], q, new_entry=Entry, answer_map={"Q-96b": A.Mapping(说明="只解阻塞")})
    assert (e.状态, e.阻塞类型) == ("阻塞", "外部")
    assert e.队列 == "Q-95 待答"
    assert rep.待答阻塞 == ["m2/U6"]


def test_far_future_rows_block_as_well_and_gate_rows_are_left_to_gates():
    far, gated = ent("m2/8.8"), ent("pkg/U8/plan", 阶段="plan")
    q = queue_md(
        [row("Q-97", "M1", "决策（G2 闸门·design／spec 定稿，不可代）", "`pkg/U8/plan`", "已答", "定", question="【G2 design／spec 定稿】`pkg`")],
        far=["| Q-F9 | M2 | 决策（不可代） | 8.8 入库闸 | 0.2 通过 | tasks.md | `m2/8.8` |"],
    )
    rep = A.apply_answers([far, gated], q, new_entry=Entry, answer_map={})
    assert (far.状态, far.阻塞类型) == ("阻塞", "决策")
    assert gated.状态 == "待开" and gated.队列 == ""  # 闸门行由 gates.py 判，本模块不碰
    assert rep.缺映射 == []


# ── ② 映射生成任务，幂等 ──


def test_mapping_generates_task_with_full_fields_and_blocked_task_depends_on_it():
    e = ent("m2/0.2", 状态="阻塞", 阻塞类型="决策", 阶段="gate")
    q = queue_md([row("Q-98", "M2", "决策（合规）", "`m2/0.2`", "已答", "a：现在启动")])
    amap = {
        "Q-98a": A.Mapping(
            说明="起草三份草稿",
            任务=[A.Task(id="answer:Q-98", 阶段="build", 标题="起草草稿", 触碰区=["docs/compliance/"], 产出判据="三份草稿落 docs/compliance/")],
            前置于=["m2/0.2"],
        )
    }
    entries = [e]
    rep = A.apply_answers(entries, q, new_entry=Entry, answer_map=amap)
    assert rep.生成 == ["answer:Q-98"]
    new = {x.id: x for x in entries}["answer:Q-98"]
    d = new.to_dict()
    for k in ("id", "场景", "阶段", "标题", "依赖", "触碰区", "状态", "阻塞类型", "产出判据", "来源"):
        assert k in d, k
    assert d["场景"] == "M2" and d["状态"] == "待开" and d["阻塞类型"] == "无"
    assert d["来源"].startswith("docs/roadmap/定夺队列.md:") and "Q-98a" in d["来源"]
    assert d["队列"] == "Q-98 已答 a"
    assert new.truth_known is False  # 真身判不了草稿是否已起、调度器写的状态说了算
    assert e.依赖 == ["answer:Q-98"] and (e.状态, e.阻塞类型) == ("待开", "无")


def test_mapping_is_idempotent_across_runs_and_does_not_duplicate_existing_id():
    e = ent("m2/0.2", 状态="阻塞", 阻塞类型="决策")
    q = queue_md([row("Q-98", "M2", "决策", "`m2/0.2`", "已答", "a：现在启动")])
    amap = {"Q-98a": A.Mapping(说明="x", 任务=[A.Task(id="answer:Q-98", 阶段="build", 标题="t", 产出判据="j")], 前置于=["m2/0.2"])}
    entries = [e]
    A.apply_answers(entries, q, new_entry=Entry, answer_map=amap)
    A.apply_answers(entries, q, new_entry=Entry, answer_map=amap)
    assert [x.id for x in entries].count("answer:Q-98") == 1
    assert e.依赖 == ["answer:Q-98"]


# ── ③ 无映射 ⇒ 保持阻塞 ＋ 登记，不静默 ──


def test_unmapped_answer_keeps_block_and_is_reported():
    e = ent("m2/0.1", 状态="阻塞", 阻塞类型="决策")
    q = queue_md([row("Q-99", "M2", "外部输入（本人）", "`m2/0.1`", "已答", "a：发了")])
    rep = A.apply_answers([e], q, new_entry=Entry, answer_map={})
    assert (e.状态, e.阻塞类型) == ("阻塞", "外部")  # 类型仍以队列列为准
    assert rep.缺映射 == [("Q-99", "Q-99a", "a：发了")]
    assert e.产出判据.startswith("【Q-99 已答 a·缺任务映射】")


def test_register_unmapped_appends_dedup_row_and_no_action_reply_unblocks(tmp_path):
    q = tmp_path / "队列.md"
    q.write_text(queue_md([row("Q-99", "M2", "外部输入（本人）", "`m2/0.1`", "已答", "a：发了")]), encoding="utf-8")
    assert A.register_unmapped(q, [("Q-99", "Q-99a", "a：发了")]) == ["Q-100"]
    assert A.register_unmapped(q, [("Q-99", "Q-99a", "a：发了")]) == []  # 去重
    assert A.register_unmapped(q, [("Q-9", "Q-9a", "a：前缀相同的另一题")]) == ["Q-101"]  # Q-9 ≠ Q-99
    text = q.read_text(encoding="utf-8")
    assert text.count("【答复→任务映射缺失】Q-99") == 1
    assert "| Q-100 | M2 | 决策（答复→任务映射缺失）" in text
    # 该登记行答「无新动作」⇒ Q-99 视作「只解阻塞」
    text = text.replace("| — | 待答 | |", "| — | 已答 | b：无新动作 |", 1)
    e = ent("m2/0.1", 状态="阻塞", 阻塞类型="决策")
    rep = A.apply_answers([e], text, new_entry=Entry, answer_map={})
    assert (e.状态, e.阻塞类型) == ("待开", "无") and rep.缺映射 == []


# ── 首版映射表：覆盖 2026-09-17 全部已答行，且与 rules.md §7 一致 ──


REAL_QUEUE = Path(__file__).resolve().parent.parent / "docs/roadmap/定夺队列.md"
RULES = Path(__file__).resolve().parent.parent / ".claude/skills/task-dispatcher/rules.md"


ANSWERED_20260917 = ["Q-02a", "Q-03b", "Q-05b", "Q-06a", "Q-07", "Q-09a", "Q-12a", "Q-13b", "Q-14a", "Q-15a", "Q-17a", "Q-18b", "Q-19①", "Q-20a", "Q-21a"]


def test_first_version_map_covers_the_15_rows_answered_on_20260917():
    # ⛔ 不对活队列断言「全部已答行都有映射」：新答复缺映射是设计内路径（登记进队列），不能让全量 pytest 红掉挡住发车
    missing = [k for k in ANSWERED_20260917 if k not in A.ANSWER_MAP]
    assert missing == [], f"首版映射缺：{missing}"


def test_real_queue_answered_rows_all_parse_to_a_key_and_first_version_rows_are_still_mapped():
    text = REAL_QUEUE.read_text(encoding="utf-8")
    rows = {r["编号"]: r for r in A.answered_rows(text)}
    for key in ANSWERED_20260917:
        no = key[:4]
        if no in rows:  # 行可能已被归档到「三、已答」，那就不在本模块范围内
            assert A.map_key(rows[no]) == key, f"{no} 的答复键变了：{A.map_key(rows[no])}（若改了答复，须同步 ANSWER_MAP）"


def test_every_map_key_is_documented_in_rules_section_7():
    rules = RULES.read_text(encoding="utf-8")
    assert "## 7. 答复→任务映射" in rules
    sec = rules.split("## 7. 答复→任务映射", 1)[1]
    for key in A.ANSWER_MAP:
        assert f"`{key}`" in sec, f"rules.md §7 缺 {key}"
    for t in A.all_mapped_tasks():
        assert f"`{t.id}`" in sec, f"rules.md §7 缺任务 {t.id}"


# ── `0918V`：Q-27／Q-38 补映射（Q-38 走键归一化命中） ──


def test_q27_mapping_is_registered_and_generates_no_task():
    relay = ent("relay:R-9", 状态="完成", 阻塞类型="无")
    q = queue_md([row("Q-27", "M2/`.51`", "决策（环境操作）", "`relay:R-9`", "已答", "放行：派无头任务在 `.51` 装 VC++")])
    rep = A.apply_answers([relay], q, new_entry=Entry)
    assert A.map_key({"编号": "Q-27", "答复": "放行：派无头任务在 `.51` 装 VC++"}) == "Q-27"
    assert relay.状态 == "完成"  # 已完成任务不被队列碰
    assert rep.缺映射 == [] and rep.生成 == []


def test_q38_mapping_keeps_answer_q07_blocked_via_real_answer_map():
    e = ent("answer:Q-07", 状态="待开", 阻塞类型="无")
    q = queue_md([row("Q-38", "M2", "决策（预算与外部采购，不可代）", "`answer:Q-07`", "已答", "b：继续搁置，维持 Q-07「以后再补」，⛔ 不再追问")])
    rep = A.apply_answers([e], q, new_entry=Entry)
    assert A.map_key({"编号": "Q-38", "答复": "b：继续搁置"}) == "Q-38b"  # 生成器实际解析出的键带字母后缀
    assert (e.状态, e.阻塞类型) == ("阻塞", "外部")
    assert rep.保持阻塞 == ["answer:Q-07"]
    assert rep.缺映射 == []


def test_map_task_ids_are_unique_and_prefixed():
    ids = [t.id for t in A.all_mapped_tasks()]
    assert len(ids) == len(set(ids))
    assert all(i.startswith("answer:Q-") for i in ids)


# ── 端到端：真身生成器 ──


Q02_TASKS = """**进度：0/3**

## 0. 前置门槛

- [ ] 0.2 🔴 **合规验收 #1 启动**（Shao Peishen 发起）。判据：三份文档落 `docs/compliance/`。阻塞 U1 的 2.3 开闸

## 1. U0 模型对比定型

- [ ] 1.4 对 ≥3 家境内 LLM 跑对比（API key 由本人配置）
- [ ] 1.7 🔴 **定型确认**（Shao Peishen 签认）
"""


def _repo_with_queue(tmp_path: Path, rows: list[str]) -> Path:
    repo = make_repo(tmp_path, with_plan=False)
    (repo / "openspec/changes/m2-resume-parse-and-rank/tasks.md").write_text(Q02_TASKS, encoding="utf-8")
    (repo / "docs/roadmap/定夺队列.md").write_text(queue_md(rows), encoding="utf-8")
    return repo


def test_generator_q02_no_longer_rejudged_and_answer_task_is_ready(tmp_path):
    repo = _repo_with_queue(tmp_path, [
        row("Q-02", "M2", "决策（合规红线相关，不可代）", "`m2-resume-parse-and-rank/0.2`", "已答", "a：现在启动"),
        row("Q-07", "M2", "决策（预算）", "`m2-resume-parse-and-rank/1.4`", "已答", "a：注册两家"),
        row("Q-05", "M2", "决策", "`m2-resume-parse-and-rank/1.7`", "已答", "b：先只定抽取模型"),
    ])
    first = run(repo).stdout
    doc = load(repo)
    t = by_id(doc)
    # 0.2 本身要本人签认（G5 类）⇒ 仍阻塞·决策，但不再是文本启发式的「永久重判」：队列字段写明缘由、依赖草稿任务、不记 conflicts
    assert (t["m2-resume-parse-and-rank/0.2"]["状态"], t["m2-resume-parse-and-rank/0.2"]["阻塞类型"]) == ("阻塞", "决策")
    assert t["m2-resume-parse-and-rank/0.2"]["队列"] == "Q-02 已答 a"
    assert t["m2-resume-parse-and-rank/0.2"]["产出判据"].startswith("【Q-02 已答 a·仍阻塞：")
    assert t["m2-resume-parse-and-rank/0.2"]["依赖"] == ["answer:Q-02"]
    assert "conflicts" not in t["m2-resume-parse-and-rank/0.2"]
    assert t["answer:Q-02"]["状态"] == "待开" and "answer:Q-02" in doc["summary"]["ready"]
    assert (t["m2-resume-parse-and-rank/1.4"]["状态"], t["m2-resume-parse-and-rank/1.4"]["阻塞类型"]) == ("阻塞", "外部")
    assert (t["m2-resume-parse-and-rank/1.7"]["状态"], t["m2-resume-parse-and-rank/1.7"]["阻塞类型"]) == ("阻塞", "决策")
    assert doc["summary"]["缺任务映射"] == []
    # 幂等：第二次字节相同、conflicts 仍为空、answer 任务不重复
    before = (repo / "docs/roadmap/任务台账.yaml").read_bytes()
    run(repo)
    assert (repo / "docs/roadmap/任务台账.yaml").read_bytes() == before
    assert [x["id"] for x in load(repo)["tasks"]].count("answer:Q-02") == 1
    assert "answer:Q-02" in first


def test_generator_ledger_status_of_answer_task_wins_silently(tmp_path):
    repo = _repo_with_queue(tmp_path, [row("Q-02", "M2", "决策", "`m2-resume-parse-and-rank/0.2`", "已答", "a：现在启动")])
    run(repo)
    p = repo / "docs/roadmap/任务台账.yaml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    for x in doc["tasks"]:
        if x["id"] == "answer:Q-02":
            x["状态"] = "完成"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    run(repo)
    t = by_id(load(repo))
    assert t["answer:Q-02"]["状态"] == "完成" and "conflicts" not in t["answer:Q-02"]
    assert "m2-resume-parse-and-rank/0.2" not in load(repo)["summary"]["ready"]  # 草稿完成也不派 0.2：签认是人闸


def test_generator_unmapped_answer_lands_in_summary_and_register_flag_appends_queue_row(tmp_path):
    repo = _repo_with_queue(tmp_path, [row("Q-40", "M2", "外部输入（本人）", "`m2-resume-parse-and-rank/1.4`", "已答", "a：发了")])
    run(repo)
    doc = load(repo)
    assert doc["summary"]["缺任务映射"] == ["Q-40a"]
    t = by_id(doc)["m2-resume-parse-and-rank/1.4"]
    assert (t["状态"], t["阻塞类型"]) == ("阻塞", "外部")
    out = run(repo, "--register-unmapped").stdout
    assert "Q-41" in out
    text = (repo / "docs/roadmap/定夺队列.md").read_text(encoding="utf-8")
    assert "【答复→任务映射缺失】Q-40" in text
    run(repo, "--register-unmapped")
    assert (repo / "docs/roadmap/定夺队列.md").read_text(encoding="utf-8").count("【答复→任务映射缺失】Q-40") == 1


def test_pending_row_blocks_in_generator_without_conflict(tmp_path):
    repo = _repo_with_queue(tmp_path, [row("Q-50", "M2", "外部输入（汤丽萍）", "`m2-resume-parse-and-rank/1.4`", "待答", "")])
    run(repo)
    t = by_id(load(repo))["m2-resume-parse-and-rank/1.4"]
    assert (t["状态"], t["阻塞类型"], t["队列"]) == ("阻塞", "外部", "Q-50 待答")
    # 台账被手改成「待开」也不记 conflicts、不放行：待开↔阻塞 轴由队列决定
    p = repo / "docs/roadmap/任务台账.yaml"
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    for x in doc["tasks"]:
        if x["id"] == "m2-resume-parse-and-rank/1.4":
            x["状态"], x["阻塞类型"] = "待开", "无"
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    run(repo)
    t = by_id(load(repo))["m2-resume-parse-and-rank/1.4"]
    assert t["状态"] == "阻塞" and "conflicts" not in t
