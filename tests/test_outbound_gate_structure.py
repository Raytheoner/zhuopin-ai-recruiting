"""`app/outbound` 的**源码形状**守护（交付单元 U4）。

这几条测的不是"门禁判得对不对"，而是"门禁的源码有没有腐化成 fail-open
的形状"。它们读 .py 源码解析 AST——用 AST 而不是正则，是因为正则会被
字符串字面量、注释和换行骗过去。
"""

import ast
import pathlib

import app.outbound.contracts
import app.outbound.gate

# ⚠️ 按目录枚举，不是手写清单（review round 1 发现 7）：原来漏了
# __init__.py，往它里面写一句 `from app.config import ...` 全套结构守护
# 一条都不响。新增模块也会自动进入扫描面。
_PACKAGE_DIR = pathlib.Path(app.outbound.gate.__file__).parent
_SOURCE_FILES = {path.name: path for path in sorted(_PACKAGE_DIR.glob("*.py"))}

# U5（tasks 5.1）在同一个包目录里新增了 messages.py（候选人消息契约）与
# queue.py（待审批队列的持久化读写）。这两个文件是刻意的例外，不是漏网之鱼：
# queue.py 天生要 `import sqlite3`、写 `INSERT INTO`（design D5：待审批队列
# 不复用 outbox，是独立的持久化落点）；messages.py 天生要 import
# `app.channels.base.OutboundMessage`（`to_outbound_message()` 的返回形状）。
#
# U5（tasks 5.5）又新增了 delivery.py：它是 L4 编排层（判一次 → 分流 → 留痕 →
# 提交后镜像），必然要 import `sqlite3`、`app.audit.*`（构造/写 DecisionEvent）
# 与 `app.graph.nodes`（调用三个 effect_* 节点）——这些正是纯度检查要防的东西,
# 但 delivery.py 本就不是纯度检查要保的对象：三条纯度检查测的是**门禁本身**
# 有没有腐化成 fail-open 的形状（compute_outbound_gate 是否还纯、还 fail-closed），
# delivery.py 从不判定、只编排既有判定结果，categorically 不在这条检查的管辖范围。
#
# ⚠️ 这是**排除清单**，不是白名单：纯度扫描面 = `_SOURCE_FILES` 减去这个显式
# 登记的排除集合，⛔ 不是"只扫这几个文件"的手写允许清单。往 app/outbound/
# 下新增任何模块（无论叫什么名字）都会自动落进三条纯度检查——这正是 review
# round 1（第 14-16 行）要保的性质："新增模块也会自动进入扫描面"。只有显式
# 写进 `_NON_GATE_MODULES` 的文件名会被豁免；忘了登记 = 新文件默认被纯度检查
# 覆盖、大概率跑出违规，而不是默认被悄悄放过。方向反过来（fail-closed）。
_NON_GATE_MODULES = {"messages.py", "queue.py", "delivery.py"}

_GATE_PURITY_FILES = {
    name: path
    for name, path in _SOURCE_FILES.items()
    if name not in _NON_GATE_MODULES
}

_BANNED_IMPORT_PREFIXES = (
    "app.config",
    "app.storage",
    "app.channels",
    "app.graph",
    "app.audit",
    "app.web",
    "sqlite3",
)


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def test_gate_source_has_no_defaulted_attribute_reads():
    """
    delivery-units §3.3 逐字：`compute_outbound_gate` 内禁止出现带默认值的
    属性读取（getattr(x, k, <default>) / dict.get(k, <default>)）。
    取不到就是未知，未知就是拦截，**默认值这个概念本身与 fail-closed 互斥**。

    这是"后来者写一句 getattr(msg, 'requires_confirmation', False) 当作
    合理默认值"那种一行重构的机器判据。
    """
    offenders = []
    for name, path in _GATE_PURITY_FILES.items():
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 3:
                offenders.append(f"{name}:{node.lineno} 三参 getattr")
            if isinstance(func, ast.Attribute) and func.attr == "get" and len(node.args) >= 2:
                offenders.append(f"{name}:{node.lineno} 两参 .get")

    assert offenders == []


def test_outbound_package_imports_nothing_stateful():
    """
    U1 plan 点名要求：compute_outbound_gate 内部**不得** import app.config，
    开关只能由调用方以 callable 传入。delivery-units §2.U4 另要求 U4
    「逻辑上不依赖 U2/U3」——所以 app.audit 也在黑名单里。
    """
    offenders = []
    for name, path in _GATE_PURITY_FILES.items():
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # ⛔ 相对 import 一律不许（review round 1 发现 7）：
                # `from ..config import ...` 在 AST 里是 module='config',
                # level=2，前缀匹配看不见它，黑名单形同虚设。本包只用绝对
                # import，规则简单到没有灰区。
                if node.level:
                    offenders.append(f"{name}:{node.lineno} 相对 import（level={node.level}），本包只许绝对 import")
                    continue
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                if module.startswith(_BANNED_IMPORT_PREFIXES):
                    offenders.append(f"{name}:{node.lineno} {module}")

    assert offenders == []


def test_the_import_guard_actually_sees_every_module_in_the_package():
    """
    发现 7 的元测试：守护自己得先扫到东西。手写清单漏文件是静默失效——
    测试照样绿，因为它压根没读那个文件。
    """
    assert set(_SOURCE_FILES) >= {"__init__.py", "contracts.py", "gate.py"}


def test_gate_purity_scope_excludes_only_the_registered_non_gate_modules():
    """
    守着 `_NON_GATE_MODULES` 本身，两个方向都要防：

    1) 排除集合被悄悄加大——比如以后哪个模块图省事被顺手塞进
       `_NON_GATE_MODULES` 来避开纯度检查，而不是老老实实通过检查。逐字钉死
       当前登记为 `{"messages.py", "queue.py", "delivery.py"}`（U5 tasks 5.5
       新增了 delivery.py，2026-08-28 裁决登记），多一个就得有人主动改这行，
       不能顺手加。
    2) `gate.py` / `contracts.py` / `__init__.py` 被误排除出扫描面——断言
       三者都还在 `_GATE_PURITY_FILES`（即 `_SOURCE_FILES` 减排除集合）里。

    ⚠️ 与旧版 `test_gate_purity_scope_is_exactly_the_gate_owned_files` 的区别：
    旧版拿 `_GATE_PURITY_FILES` 与算它时用的同一个字面量集合比较，是重言式，
    在 `_GATE_PURITY_FILES` 是"白名单过滤"时永远不可能失败。改成排除清单后，
    这条改成分别校验排除集合与扫描面结果，两者都是能失败的断言。
    """
    assert _NON_GATE_MODULES == {"messages.py", "queue.py", "delivery.py"}
    assert {"__init__.py", "contracts.py", "gate.py"} <= set(_GATE_PURITY_FILES)


def test_ai_label_source_is_the_jd_agent_constant():
    """
    tasks 4.4：**复用** app/agents/jd_agent.py 现有的 AI_LABEL_TEMPLATE
    机制判定，⛔ 不另写一套标识逻辑。断言的是**同一个对象**，
    照抄一份字面量过来会当场变红。
    """
    from app.agents.jd_agent import AI_LABEL_TEMPLATE

    assert app.outbound.gate.AI_LABEL_TEMPLATE is AI_LABEL_TEMPLATE


def test_ai_label_prefix_is_pinned_verbatim():
    """
    合规标识文案（《AI 生成合成内容标识办法》2025-09-01 施行）是红线资产，
    不该被静默改掉。这条把当前判定前缀逐字钉死——jd_agent 那句模板一变，
    这里就红，改动必须是有人看着的。
    """
    assert (
        app.outbound.gate.AI_LABEL_PREFIX
        == "【AI 生成】本文案由系统基于岗位画像自动生成，生成时间 "
    )


def test_gate_has_no_side_effect_vocabulary():
    """
    铁律 2：compute_* 无副作用。源码里出现这几个词就说明副作用爬进来了。
    """
    for name, path in _GATE_PURITY_FILES.items():
        source = path.read_text(encoding="utf-8")
        for forbidden in ("@idempotent_effect", "INSERT INTO", "conn.execute", "channel.deliver"):
            assert forbidden not in source, f"{name} 里出现了 {forbidden}"


def test_every_reason_the_gate_can_return_is_in_the_closed_set():
    """
    review round 1 发现 8：原来只有一条把 ALL_BLOCK_REASONS 和字面量集合
    对比的 pin 测试——它保证不了**门禁真正返回的原因**都在集合里。加一条
    `blocked(REASON_NEW)` 而忘了往集合里补，U6 的 6.5 会把它静默地漏掉。

    这里用 AST 把 gate.py 里所有 `blocked(<名字>)` 的实参名收出来，加上
    外壳里那条 REASON_GATE_ERROR，逐个回查模块常量的取值是否在闭集合中。
    """
    tree = _tree(_SOURCE_FILES["gate.py"])

    reason_names = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "blocked"
            and node.args
            and isinstance(node.args[0], ast.Name)
        ):
            reason_names.add(node.args[0].id)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.keyword)
            and node.arg == "reason"
            and isinstance(node.value, ast.Name)
            and node.value.id.startswith("REASON_")
        ):
            # `reason=reason`（blocked() 内部那个形参）不是常量名，跳过；
            # 外壳里的 `reason=REASON_GATE_ERROR` 才是要查的。
            reason_names.add(node.value.id)

    assert reason_names, "一个 blocked(<名字>) 都没扫到，这条守护是哑的"
    for reason_name in sorted(reason_names):
        assert reason_name.startswith("REASON_"), (
            f"{reason_name} 不叫 REASON_*，本条守护按这个命名约定扫描，"
            "换名字会让新原因从 U6 的统计口径里溜掉"
        )

    for reason_name in sorted(reason_names):
        value = getattr(app.outbound.gate, reason_name)
        assert value in app.outbound.gate.ALL_BLOCK_REASONS, (
            f"{reason_name} = {value!r} 不在 ALL_BLOCK_REASONS 里，U6 的 6.5 会漏掉它"
        )
