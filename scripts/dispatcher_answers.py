#!/usr/bin/env python3
"""定夺队列 ⇒ 台账（[Mac]0917AQ，任务驱动 workflow R5 → R3 的回环）。

为什么：`docs/roadmap/定夺队列.md` 里状态＝已答的行原来既不解除「阻塞的任务 id」的阻塞，也不生成答复蕴含的新动作；
`dispatcher_backlog.py` 只有文本启发式（🔴／Shao Peishen ⇒ 决策），Q-02 答了也会被永久重判——调度器三轮 ready=0。
本模块被 `dispatcher_backlog.generate` 在 `apply_gates` **之前**调用，以队列为准覆盖文本启发式：

  1. **待答／远期行**：所列任务 id 若 待开／阻塞 ⇒ `阻塞`，阻塞类型取该行「阻塞类型」列（决策｜外部输入 ⇒ 外部）。
     同 id 同时出现在待答行与已答行 ⇒ 待答压过已答（还有别的问题没答）。
  2. **已答行**：答复键（`a`／`b`／`①`…）＋编号查 `ANSWER_MAP`：
       - 有映射 ⇒ 生成映射里的任务条目（id `answer:Q-xx`，字段齐全、来源＝队列行号、同 id 不重复生成），
         所列任务解阻塞（`待开／无`）；映射的 `前置于` 让被阻塞任务依赖新任务；`保持阻塞` 逐 id 改类型并写原因。
       - 无映射 ⇒ **不解阻塞**（保守：答复往往蕴含还没写进机器的后续动作），记 `缺映射`，
         `dispatcher_backlog.py --register-unmapped` 把它登记进定夺队列「【答复→任务映射缺失】Q-xx」行而非静默；
         该登记行答「无新动作」⇒ 视作只解阻塞。
  3. **作废行**（状态或答复为「作废」）⇒ 所列任务 `完成`，产出判据前缀 `【作废：Q-xx】`。
  4. **闸门行**（问题列 `【G<n> …】`）不碰——由 `gates.py` 判。

映射真源：本文件 `ANSWER_MAP`（机器读）＋ `.claude/skills/task-dispatcher/rules.md` §7（人读），测试钉住两边一致。
队列决定的 待开↔阻塞 轴写在条目 `队列:` 字段，`merge` 对它不记 conflicts（与 `闸门:` 同理）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    from scripts import gates
except ImportError:  # 直跑时 scripts/ 自己在 sys.path
    import gates  # type: ignore[no-redef]

QUEUE_REL = gates.QUEUE_REL
UNMAPPED_TAG = "【答复→任务映射缺失】"
NO_ACTION_REPLY = "无新动作"

_KEY_RE = re.compile(r"^\s*[（(]?\s*(作废|[a-zA-Z]|[①②③④⑤⑥⑦⑧⑨]|定|发|签|是|否)\s*[)）]?(?=\s*[：:，,、。（(\s]|$)")
_ID_RE = re.compile(r"`([^`]+)`")


# ───────────────────────── 映射表数据结构 ─────────────────────────


@dataclass(frozen=True)
class Task:
    """映射生成的任务条目（字段对齐 dispatcher_backlog.Entry；场景／来源／队列由生成时从队列行补）。"""

    id: str
    阶段: str
    标题: str
    产出判据: str
    依赖: tuple[str, ...] = ()
    触碰区: tuple[str, ...] = ()
    状态: str = "待开"
    阻塞类型: str = "无"
    场景: str = ""  # 空 ⇒ 取队列行的场景列


@dataclass(frozen=True)
class Mapping:
    说明: str
    任务: tuple[Task, ...] | list[Task] = ()
    前置于: tuple[str, ...] | list[str] = ()  # 这些被阻塞任务在解阻塞后依赖本映射生成的任务
    保持阻塞: dict[str, tuple[str, str]] = field(default_factory=dict)  # id -> (阻塞类型, 原因)


@dataclass
class Report:
    生成: list[str] = field(default_factory=list)
    解阻塞: list[str] = field(default_factory=list)
    保持阻塞: list[str] = field(default_factory=list)
    作废: list[str] = field(default_factory=list)
    待答阻塞: list[str] = field(default_factory=list)
    缺映射: list[tuple[str, str, str]] = field(default_factory=list)  # (编号, 键, 答复)


# ───────────────────────── 首版映射（2026-09-17 已答行逐条写死） ─────────────────────────

M2 = "m2-resume-parse-and-rank"
_RELEASE_51 = "随下次 `.51` 发版一并执行（G3 发版决定不可代，⛔ 不单独发车）"

ANSWER_MAP: dict[str, Mapping] = {
    "Q-02a": Mapping(
        说明="现在启动合规验收 #1：起草三份草稿（法务对接人＝谷雨），产出后进定夺队列待 Shao Peishen 签认（G5 类）",
        任务=[
            Task(
                id="answer:Q-02",
                阶段="build",
                标题="起草 PIA 报告／候选人同意条款（单列 AI 评估）／留存与删除策略三份草稿进 docs/compliance/（对接人谷雨）",
                触碰区=["docs/compliance/"],
                产出判据=(
                    "`docs/compliance/` 下三份草稿（PIA／候选人同意条款单列 AI 评估／留存与删除策略）落档、标注「草稿·待签认」；"
                    "定夺队列追加 G5 类签认行（`gates.py request G5 合规验收#1 …`，阻塞的任务 id＝`m2-resume-parse-and-rank/0.2`）；"
                    "⛔ 不代签、不对外发送"
                ),
            )
        ],
        前置于=[f"{M2}/0.2"],
        保持阻塞={f"{M2}/0.2": ("决策", "三份草稿（answer:Q-02）产出后进 G5 待本人签认；签认后由 Cowork 勾 tasks.md 0.2，⛔ 不派泳道")},
    ),
    "Q-03b": Mapping(
        说明="试运行岗位＝底层软件工程师；0.3 已落档（05fd82c）。8.10 仍受真实简历入库闸（Q-F2）阻塞",
        保持阻塞={f"{M2}/8.10": ("决策", "真实简历入库闸未开（Q-F2，8.8 由本人亲自改配置）")},
    ),
    "Q-05b": Mapping(
        说明="先只定抽取模型＝deepseek-flash，其余待真实样本二次签认",
        保持阻塞={f"{M2}/1.7": ("决策", "抽取模型已定，精排／阈值等行待真实脱敏样本到位后复算并二次签认")},
    ),
    "Q-06a": Mapping(
        说明="真实脱敏样本来源＝历史离职候选人简历脱敏",
        保持阻塞={
            f"{M2}/1.2": ("外部", "等 HR 提供历史离职候选人简历＋脱敏脚本先过本人审；M2 门槛（登录＋访问留痕）就位前不得入库"),
            f"{M2}/1.6": ("外部", "「决策」节余下各行待 1.2 真实样本复算"),
        },
    ),
    "Q-07a": Mapping(
        说明="注册火山方舟＋阿里百炼由本人办理并写 .env（原答，2026-09-17 21:5x 已撤回，见 `Q-07`）",
        保持阻塞={f"{M2}/1.4": ("外部", "等本人注册火山方舟（ARK_API_KEY）＋阿里百炼（DASHSCOPE_API_KEY）并写入 .env")},
    ),
    "Q-07": Mapping(
        说明="改：近期只用 DeepSeek（两款）起步，第 3／4 家（火山方舟／阿里百炼）后补，不阻塞主航道；"
        "1.4 解除对下游阻塞（Q-26 同意，原答 `Q-07a`「注册两家」撤回）",
        任务=[
            Task(
                id="answer:Q-07",
                阶段="build",
                标题="后补（非阻塞）：注册火山方舟（ARK_API_KEY）＋阿里百炼（DASHSCOPE_API_KEY）并补对比表第 3／4 家",
                触碰区=["docs/m2-model-comparison.md"],
                产出判据="两家 API key 写入 `.env`、`docs/m2-model-comparison.md` 补齐第 3／4 家对比数据；⛔ 不阻塞 1.4 主线",
            )
        ],
    ),
    "Q-09a": Mapping(说明="一期扫描件进不可读队列＋技术债，不建 3.13 sidecar（U0 已按此执行）"),
    "Q-12a": Mapping(
        说明="H-1 现网重建表补 CHECK 随下次 .51 发版",
        任务=[
            Task(
                id="answer:Q-12",
                阶段="release",
                场景=".51",
                标题="下次 .51 发版清单：`liaison_group_notify` 重建表补跨字段 CHECK（H-1／TD-28）",
                触碰区=["docs/deploy-51-server.md"],
                产出判据=f"{_RELEASE_51}；`.51` 上该表建表语句含跨字段 CHECK，发版记录落 `docs/deploy-51-server.md`",
            )
        ],
        保持阻塞={"relay:H-1": ("决策", _RELEASE_51)},
        前置于=["relay:H-1"],
    ),
    "Q-13b": Mapping(说明="不查 worktree 被清理的机制，沿用收口前转写台账规矩；无新动作"),
    "Q-14a": Mapping(
        说明="每日快照 Windows 计划任务随下次 .51 发版装",
        任务=[
            Task(
                id="answer:Q-14",
                阶段="release",
                场景=".51",
                标题="下次 .51 发版清单：每日快照 Windows 计划任务（脚本由发版泳道写，装到 `C:\\apps\\backups\\`）",
                触碰区=["docs/deploy-51-server.md", "docs/audit-and-outbound-ops.md"],
                产出判据=f"{_RELEASE_51}；`.51` 计划任务列表含每日快照项且次日目录出现新快照，落 `docs/audit-and-outbound-ops.md` 第五节",
            )
        ],
    ),
    "Q-15a": Mapping(
        说明=".51 整机重启窗口 2026-09-20 22:00–23:00，须内网执行、执行前本线确认",
        任务=[
            Task(
                id="answer:Q-15",
                阶段="build",
                场景=".51",
                标题="`.51` 整机重启（窗口 2026-09-20 周日 22:00–23:00，opener＝编排文件 `[Mac] 0820-9R`）",
                状态="阻塞",
                阻塞类型="外部",
                触碰区=["docs/deploy-51-server.md"],
                产出判据=(
                    "外部输入：须在内网执行，且执行前经本线再确认一次（⛔ 调度器不发车）；"
                    "重启后 7 个服务全部回来、`CBS RebootPending=False`，结论落 `docs/deploy-51-server.md`"
                ),
            )
        ],
    ),
    "Q-17a": Mapping(
        说明="06 清单 3.3／9.1–9.3 作废，沟通线并入 `人事部#2` 之后的跟进信",
        任务=[
            Task(
                id="answer:Q-17",
                阶段="build",
                标题="06 清单 §3.3／§9.1–9.3 标作废（沟通线并入 `人事部#2` 之后的跟进信）",
                触碰区=["06-企业AI转型资产借鉴清单.md"],
                产出判据="`06-企业AI转型资产借鉴清单.md` 的 3.3、9.1、9.2、9.3 标题与末尾清单对应项标「⚰️ 作废（Q-17a）」并写去向，⛔ 不删原文",
            )
        ],
    ),
    "Q-18b": Mapping(说明="TD-1 不删列，条目关闭；U1/plan 是否放行由 G2 闸门判"),
    "Q-19①": Mapping(
        说明="评分 prompt 只返回 offset 区间不返回引文，evidence_ref 承担定位——写进 M2 U4 plan 约束",
        任务=[
            Task(
                id="answer:Q-19",
                阶段="build",
                标题="把 TD-5 答复①写进 M2 U4 plan 的 Global Constraints（评分 prompt 只返回 offset 区间，不返回引文）",
                依赖=[f"{M2}/U4/plan"],
                触碰区=[f"docs/superpowers/plans/*-{M2}-unit4-*.md"],
                产出判据="U4 plan 的 Global Constraints 段含「评分 prompt 只返回 offset 区间不返回引文，`evidence_ref` 承担定位（Q-19①）」一条",
            )
        ],
    ),
    "Q-20a": Mapping(
        说明="TD-13 接受方案 C，派小泳道",
        任务=[
            Task(
                id="answer:Q-20",
                阶段="build",
                标题="TD-13 方案 C：`app/web/server.py` 调用点 catch `discard_thread_checkpoints` 异常、ERROR 日志、照常返回引导语（TDD）",
                触碰区=["app/web/server.py", "tests/test_web_api.py", "docs/tech-debt.md"],
                产出判据="`tests/test_web_api.py` 新增用例：checkpointer 删除抛异常时接口仍返回引导语且日志含 ERROR；`docs/tech-debt.md` TD-13 销账",
            )
        ],
    ),
    "Q-21a": Mapping(
        说明="requirement-grill 移植 15 分钟无答复按推荐生效；G1 intent 定稿仍为人闸，超时生效条目逐项标出",
        任务=[
            Task(
                id="answer:Q-21",
                阶段="build",
                标题="requirement-grill SKILL.md 移植「答复模板发出 15 分钟无答复 ⇒ 按推荐生效」默认＋补测试",
                触碰区=[".claude/skills/requirement-grill/SKILL.md", "tests/test_requirement_grill_skill.py"],
                产出判据=(
                    "SKILL.md 含「⏸️ 默认项」行与 15 分钟规则、落档措辞「超时按默认项生效（未获明确答复，<会话> <时刻>）」，"
                    "并保留 G1 intent 定稿人闸与「判据／口径／阈值类永不默认生效」；`tests/test_requirement_grill_skill.py` 覆盖"
                ),
            )
        ],
    ),
}


def all_mapped_tasks(answer_map: dict[str, Mapping] | None = None) -> list[Task]:
    return [t for m in (answer_map if answer_map is not None else ANSWER_MAP).values() for t in m.任务]


# ───────────────────────── 队列行解析 ─────────────────────────


def reply_key(reply: str) -> str:
    """答复首个选项字：`a`／`(a)`／`A：`／`①`／`作废`／`定`／`是`…；取不到 ⇒ ""（连编号一起查表必然无映射 ⇒ 登记）。"""
    m = _KEY_RE.match(reply or "")
    return m.group(1).lower() if m else ""


def map_key(row: dict[str, str]) -> str:
    return f"{row.get('编号', '')}{reply_key(row.get('答复', ''))}"


def task_ids(row: dict[str, str]) -> list[str]:
    return [i.strip() for i in _ID_RE.findall(row.get("阻塞的任务 id", "")) if i.strip()]


def block_type(row: dict[str, str]) -> str:
    return "外部" if "外部" in row.get("阻塞类型", "") else "决策"


def _is_gate_row(row: dict[str, str]) -> bool:
    return bool(re.match(r"^\s*【G\d", row.get("问题", "")))


def _is_unmapped_tag_row(row: dict[str, str]) -> bool:
    return row.get("问题", "").lstrip().startswith(UNMAPPED_TAG)


def _status(row: dict[str, str]) -> str:
    """待答｜已答｜作废；「三、已答 / 作废」表没有状态列 ⇒ 不进本模块（那是落档记录，不带任务 id）。"""
    st = row.get("状态", "").strip()
    reply = row.get("答复", "").strip()
    if "作废" in st or reply.startswith("作废"):
        return "作废"
    if st == "已答" and reply:
        return "已答"
    return "待答"


def queue_rows(text: str) -> list[dict[str, str]]:
    """带任务 id 列的非闸门行（一、待答 ＋ 二、远期）。"""
    return [r for r in gates.parse_queue(text) if "阻塞的任务 id" in r and r.get("编号", "").startswith("Q-") and not _is_gate_row(r)]


def answered_rows(text: str) -> list[dict[str, str]]:
    return [r for r in queue_rows(text) if _status(r) == "已答" and not _is_unmapped_tag_row(r)]


def _no_action_numbers(text: str) -> set[str]:
    """「【答复→任务映射缺失】Q-xx」登记行已答「无新动作」的 Q-xx 集合。"""
    out: set[str] = set()
    for r in queue_rows(text):
        if _is_unmapped_tag_row(r) and _status(r) == "已答" and NO_ACTION_REPLY in r.get("答复", ""):
            m = re.search(r"(Q-\d+)", r["问题"][len(UNMAPPED_TAG):])
            if m:
                out.add(m.group(1))
    return out


# ───────────────────────── 应用到台账条目 ─────────────────────────


def apply_answers(entries: list, queue_text: str, *, new_entry, answer_map: dict[str, Mapping] | None = None) -> Report:
    """就地改 entries（duck-typed dispatcher_backlog.Entry），映射生成的新条目 append 进 entries。返回 Report。"""
    amap = ANSWER_MAP if answer_map is None else answer_map
    rep = Report()
    byid = {e.id: e for e in entries}
    no_action = _no_action_numbers(queue_text)
    keep: dict[str, tuple[str, str, str]] = {}  # id -> (阻塞类型, 原因, tag)：跨行粘性——任一已答行要求保持阻塞，别的已答行解不开

    def prefix(e, head: str) -> None:
        if not e.产出判据.startswith(head):
            e.产出判据 = head + e.产出判据

    def touchable(tid: str):
        e = byid.get(tid)
        return e if e is not None and e.状态 in ("待开", "阻塞") else None

    # 2＋3：已答／作废行
    for row in queue_rows(queue_text):
        if _is_unmapped_tag_row(row):
            continue
        st = _status(row)
        no = row.get("编号", "")
        if st == "作废":
            for tid in task_ids(row):
                e = touchable(tid)
                if e is None:
                    continue
                e.状态, e.阻塞类型, e.队列 = "完成", "无", f"{no} 作废"
                prefix(e, f"【作废：{no}】")
                rep.作废.append(tid)
            continue
        if st != "已答":
            continue
        key = map_key(row)
        mapping = amap.get(key)
        if mapping is None and no in no_action:
            mapping = Mapping(说明="登记行已答「无新动作」：只解阻塞")
        tag = f"{no} 已答 {reply_key(row.get('答复', ''))}".rstrip()
        if mapping is None:
            rep.缺映射.append((no, key, row.get("答复", "")))
            for tid in task_ids(row):
                e = touchable(tid)
                if e is None:
                    continue
                e.状态, e.阻塞类型, e.队列 = "阻塞", block_type(row), tag
                prefix(e, f"【{tag}·缺任务映射】")
            continue
        new_ids: list[str] = []
        for t in mapping.任务:
            new_ids.append(t.id)
            if t.id in byid:
                continue  # 幂等：同 id 不重复生成（也可能来自台账合并前的上一轮）
            e = new_entry(
                id=t.id,
                场景=t.场景 or row.get("场景", ""),
                阶段=t.阶段,
                标题=t.标题,
                来源=f"{QUEUE_REL}:{row.get('_line', '')} {key}",
                依赖=list(t.依赖),
                触碰区=list(t.触碰区),
                状态=t.状态,
                阻塞类型=t.阻塞类型,
                产出判据=t.产出判据,
                truth_known=False,
                队列=tag,
            )
            entries.append(e)
            byid[t.id] = e
            rep.生成.append(t.id)
        for tid in task_ids(row):
            e = touchable(tid)
            if e is None:
                continue
            if tid in mapping.保持阻塞:
                btype, why = mapping.保持阻塞[tid]
                keep.setdefault(tid, (btype, why, tag))
            else:
                e.状态, e.阻塞类型, e.队列 = "待开", "无", tag
                rep.解阻塞.append(tid)
            if tid in mapping.前置于:
                e.依赖 = sorted(set(e.依赖) | set(new_ids))
    for tid, (btype, why, tag) in keep.items():
        e = touchable(tid)
        if e is None:
            continue
        e.状态, e.阻塞类型, e.队列 = "阻塞", btype, tag
        prefix(e, f"【{tag}·仍阻塞：{why}】")
        rep.保持阻塞.append(tid)
        if tid in rep.解阻塞:
            rep.解阻塞.remove(tid)

    # 1：待答／远期行压阻塞（最后做 ⇒ 压过已答）
    for row in queue_rows(queue_text):
        if _is_unmapped_tag_row(row) or _status(row) != "待答":
            continue
        for tid in task_ids(row):
            e = touchable(tid)
            if e is None:
                continue
            e.状态, e.阻塞类型, e.队列 = "阻塞", block_type(row), f"{row.get('编号', '')} 待答"
            rep.待答阻塞.append(tid)
    return rep


# ───────────────────────── 缺映射登记（追加定夺队列，去重） ─────────────────────────


def build_unmapped_row(number: str, scene: str, no: str, key: str, reply: str) -> str:
    short = re.sub(r"\s+", " ", reply.strip())[:80]
    problem = (
        f"{UNMAPPED_TAG}{no}：已答「{short}」，`rules.md` §7 无 `{key}` 映射，"
        f"该行「阻塞的任务 id」保持阻塞（保守），⛔ 调度器不猜后续动作"
    )
    options = (
        f"(a) 补映射：在 `scripts/dispatcher_answers.py` `ANSWER_MAP` 与 `rules.md` §7 增加 `{key}`（新任务／保持阻塞原因）后重跑生成器；"
        f"(b) 答「{NO_ACTION_REPLY}」⇒ 只解阻塞，不生成任务"
    )
    return (
        f"| {number} | {scene} | 决策（答复→任务映射缺失） | {problem} | {options} | a "
        f"| `scripts/dispatcher_answers.py` ｜ `.claude/skills/task-dispatcher/rules.md` §7 | — | 待答 | |"
    )


def register_unmapped(queue: Path, missing: list[tuple[str, str, str]]) -> list[str]:
    """把 Report.缺映射 逐条追加到「一、待答」表末（同 Q-xx 只登记一次）。返回新增的编号。"""
    queue = Path(queue)
    text = queue.read_text(encoding="utf-8") if queue.is_file() else ""
    scenes = {r.get("编号", ""): r.get("场景", "") for r in gates.parse_queue(text)}
    added: list[str] = []
    for no, key, reply in missing:
        if re.search(re.escape(f"{UNMAPPED_TAG}{no}") + r"(?!\d)", text):
            continue
        number = gates._next_number(text)
        text = gates.append_pending_row(text, build_unmapped_row(number, scenes.get(no, ""), no, key, reply))
        added.append(number)
    if added:
        queue.parent.mkdir(parents=True, exist_ok=True)
        queue.write_text(text, encoding="utf-8")
    return added
