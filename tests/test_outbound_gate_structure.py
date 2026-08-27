"""`app/outbound` 的**源码形状**守护（交付单元 U4）。

这几条测的不是"门禁判得对不对"，而是"门禁的源码有没有腐化成 fail-open
的形状"。它们读 .py 源码解析 AST——用 AST 而不是正则，是因为正则会被
字符串字面量、注释和换行骗过去。
"""

import ast
import pathlib

import app.outbound.contracts
import app.outbound.gate

_SOURCE_FILES = {
    "gate.py": pathlib.Path(app.outbound.gate.__file__),
    "contracts.py": pathlib.Path(app.outbound.contracts.__file__),
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
    for name, path in _SOURCE_FILES.items():
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
    for name, path in _SOURCE_FILES.items():
        for node in ast.walk(_tree(path)):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                if module.startswith(_BANNED_IMPORT_PREFIXES):
                    offenders.append(f"{name}:{node.lineno} {module}")

    assert offenders == []


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
    for name, path in _SOURCE_FILES.items():
        source = path.read_text(encoding="utf-8")
        for forbidden in ("@idempotent_effect", "INSERT INTO", "conn.execute", "channel.deliver"):
            assert forbidden not in source, f"{name} 里出现了 {forbidden}"
