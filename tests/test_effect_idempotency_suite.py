"""
交付单元 4.4：`effect_*` 节点的幂等专项测试。

⭐ 本文件是**工程铁律 1 的全量守护**，不是"再写几个用例"。它要做到两件事：

1. 仓库里**每一个** `effect_*` 节点都有一条「业务写之后、事务提交之前强制中断
   → 进程重启 → 按 thread_id 恢复重跑」的用例，断言副作用恰好发生一次。
2. 将来**新增一个 effect 节点却忘了加用例时，这个文件必须变红**。清单靠 AST
   从源码里现扫，与下面的硬编码清单双向比对——两边不一致就失败，谁都别想
   悄悄溜过去。铁律 1 的失败是静默的（不报错、不失败，只是少做/多做一次副
   作用），能抓住它的只有"清单过期即变红"这一条机制。

⛔ **本文件不改 `app/` 任何代码。** 若某个节点在这里被测出真的不幂等，
登记进计划的「红灯与观察项」，由另一个交付单元修——在测试单元里顺手改
被测代码，等于让测试给自己开绿灯。

⚠️ 与既有三个文件的分工（⛔ 不重复造）：
- `tests/test_idempotency.py`     —— 装饰器**本身**的语义（短路、回滚、日志）
- `tests/test_transaction_ownership.py` —— 事务**归属**（checkpointer 不得共用连接）
- `tests/test_graph_idempotency.py`     —— `effect_persist_draft` /
  `effect_deliver_message` / `effect_confirm_profile` 三个节点的既有覆盖
本文件补的是**横向全量**：把同一条崩溃-恢复协议施加到全部 10 个节点上，
并让清单无法过期。既有用例一条都不删、一条都不改。
"""

import ast
import pathlib

# 仓库根 = tests/ 的上一级
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_APP_ROOT = _REPO_ROOT / "app"

# ⚠️ 硬编码清单。加了新的 effect 节点就往这里加一行，**同时**去
# build_recipes() 里加一条配方——两处都加了，测试才会绿。
# ⛔ 不要为了让测试变绿而删这里的名字：删掉等于宣布"这个节点不需要幂等
# 保护"，那是铁律 1 的例外，只有 Shao Peishen 能拍。
EFFECT_NODE_MANIFEST = frozenset(
    {
        "effect_persist_draft",
        "effect_deliver_message",
        "effect_confirm_profile",
        "effect_request_revision",
        "effect_abandon_profile",
        "effect_generate_and_persist_jd",
        "effect_enqueue_pending_approval",
        "effect_record_outbound_audit",
        "effect_update_jd_text",
        "effect_mark_jd_human_written",
    }
)


def collect_effect_nodes() -> dict[str, str]:
    """AST 扫 `app/` 下全部 .py，返回 {节点名: "相对路径:行号"}。

    ⭐ **用 AST 而不是 grep**：`app/audit/assertions.py` 的注释里、
    `app/outbound/delivery.py` 的 docstring 里都出现过 `@idempotent_effect`
    这串字面量（后者原文是"本函数**不是** effect_* 节点、⛔ 不加
    @idempotent_effect"）。grep 会把这两处当成节点，清单守卫就会为了一条
    注释而误报，几次之后没人再信它——一个总在误报的守卫等于没有守卫。
    AST 只看真正的 decorator 节点，从结构上没有这个问题。

    节点名取**装饰器的字面量参数**而非函数名：幂等键里存进 effect_log 的是
    这个字面量（见 app/storage/idempotency.py），它才是数据库里的事实。
    两者一致由 `app/audit/assertions.py` 另行保证，本文件不重复断言。
    """
    found: dict[str, str] = {}
    for path in sorted(_APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if not isinstance(deco, ast.Call):
                    continue
                func = deco.func
                deco_name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if deco_name != "idempotent_effect":
                    continue
                assert len(deco.args) == 1 and isinstance(deco.args[0], ast.Constant), (
                    f"{path}:{node.lineno} 的 @idempotent_effect 参数不是字面量字符串。"
                    "节点名必须是字面量——变量或表达式会让 effect_log 里的 node_name "
                    "无法从源码静态推断，这条清单守卫和 app/audit/assertions.py 的"
                    "断言就同时失效了。"
                )
                found[deco.args[0].value] = f"{path.relative_to(_REPO_ROOT)}:{node.lineno}"
    return found


def test_manifest_matches_the_source_tree():
    """⭐ 防清单过期：源码里有、清单里没有 → 失败（新节点漏测）；反之亦然（节点已删）。"""
    discovered = set(collect_effect_nodes())
    missing_from_manifest = discovered - set(EFFECT_NODE_MANIFEST)
    stale_in_manifest = set(EFFECT_NODE_MANIFEST) - discovered
    assert not missing_from_manifest, (
        f"源码里新增了 effect 节点但没进本文件的清单：{sorted(missing_from_manifest)}。"
        "把它们加进 EFFECT_NODE_MANIFEST，并在 build_recipes() 里各加一条崩溃-恢复"
        "配方——铁律 1 要求每个 effect_* 节点都被强制中断验证过。"
    )
    assert not stale_in_manifest, (
        f"清单里的节点在源码里已经不存在了：{sorted(stale_in_manifest)}。"
        "确认是被删/改名而不是被漏扫，然后同步更新 EFFECT_NODE_MANIFEST。"
    )


def test_collector_reports_where_each_node_lives():
    """收集器要给出位置，否则清单变红时没人知道该去哪个文件加配方。"""
    located = collect_effect_nodes()
    assert located["effect_persist_draft"].startswith("app/graph/nodes.py:")
    assert located["effect_update_jd_text"].startswith("app/graph/jd_nodes.py:")


def test_collector_ignores_the_literal_in_comments_and_docstrings():
    """
    回归守卫：`app/outbound/delivery.py` 的 docstring 里有一句
    "⛔ 不加 @idempotent_effect"，`app/audit/assertions.py` 的注释里也有。
    用 grep 实现收集器会把它们当成节点，这条断言把那种实现钉死在红灯上。
    """
    located = collect_effect_nodes()
    for name, where in located.items():
        assert name.startswith("effect_"), f"{name} @ {where} 不像节点名，收集器可能扫到了注释"
    assert not any(where.startswith("app/outbound/delivery.py:") for where in located.values()), (
        "app/outbound/delivery.py 里没有任何 effect_* 节点，只有一句说明它"
        "**不是**节点的 docstring——收集器扫到它说明用错了实现（应为 AST，非文本匹配）"
    )
