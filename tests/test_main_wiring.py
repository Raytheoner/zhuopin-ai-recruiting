"""
留痕注入点的守护。delivery-units.md §2.U3 逐字：「U3 的注入点写死在
app/main.py:_gateway_factory()，不改 create_app 签名。回滚 = 换回一行。」

本文件用 AST 扫源码 + 一个真实子进程装配，两条路互补：AST 便宜、能钉住"写在
哪儿"，子进程贵、能证明"真的跑得起来"。只有 AST 的话，一个语法正确但运行时
炸掉的装配照样全绿。
"""

import ast
import inspect
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_SOURCE = (REPO_ROOT / "app" / "main.py").read_text(encoding="utf-8")


def _call_names_in_function(source: str, func_name: str) -> list[str]:
    """函数体内出现的所有被调用者名字（Name 取 id，Attribute 取 attr）。"""
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func_name:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            if isinstance(inner.func, ast.Name):
                names.append(inner.func.id)
            elif isinstance(inner.func, ast.Attribute):
                names.append(inner.func.attr)
    return names


def _top_level_call_names(source: str) -> list[str]:
    """模块级（不在任何 def / class 内）出现的被调用者名字。"""
    tree = ast.parse(source)
    names: list[str] = []
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for inner in ast.walk(stmt):
            if not isinstance(inner, ast.Call):
                continue
            if isinstance(inner.func, ast.Name):
                names.append(inner.func.id)
            elif isinstance(inner.func, ast.Attribute):
                names.append(inner.func.attr)
    return names


def _keywords_of_call_in_function(source: str, func_name: str, callee: str) -> list[str]:
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func_name:
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == callee
            ):
                return [kw.arg for kw in inner.keywords]
    return []


# ── 阳性对照：三个检查器各一条 ───────────────────────────────────────────


def test_call_name_detector_actually_detects():
    """
    ⭐ 没有阳性对照，下面那条"函数体内不出现 get_connection"在"检查器根本没生效"
    时同样是绿的——空列表同时兼容"约束守住了"和"检查没跑"两种解释。
    """
    offending = "def _gateway_factory():\n    conn = get_connection('x')\n    return conn\n"

    assert "get_connection" in _call_names_in_function(offending, "_gateway_factory")


def test_top_level_call_detector_actually_detects():
    """模块级与函数体内必须分得开——否则"构造一次"会被"每次调用构造"蒙混过去。"""
    offending = "hook = RecorderAuditHook(r, c)\n\ndef f():\n    return RecorderAuditHook(r, c)\n"

    assert _top_level_call_names(offending).count("RecorderAuditHook") == 1


def test_keyword_detector_actually_detects():
    offending = "def _gateway_factory():\n    return LLMGateway(model='m', audit_hook=h)\n"

    assert _keywords_of_call_in_function(offending, "_gateway_factory", "LLMGateway") == [
        "model",
        "audit_hook",
    ]


# ── 真正的守护 ───────────────────────────────────────────────────────────


def test_gateway_factory_opens_no_connection_per_call():
    """
    ⭐ gateway_factory() 被调用**两处**：启动时 app/web/server.py:66，以及每次
    请求 :278。把 get_connection() 写进工厂函数体 = 每个 HTTP 请求泄漏一条
    SQLite 连接，且每条连接各带一份哈希链游标，JSONL 链会开始互相打架。
    """
    assert "get_connection" not in _call_names_in_function(MAIN_SOURCE, "_gateway_factory")


def test_audit_hook_is_constructed_exactly_once_at_module_level():
    assert _top_level_call_names(MAIN_SOURCE).count("RecorderAuditHook") == 1
    assert MAIN_SOURCE.count("RecorderAuditHook(") == 1


def test_gateway_factory_injects_the_audit_hook():
    keywords = _keywords_of_call_in_function(MAIN_SOURCE, "_gateway_factory", "LLMGateway")

    assert "audit_hook" in keywords


def test_create_app_signature_is_untouched():
    """
    delivery-units.md §2.U3：⛔ 不改 create_app 签名——改了立刻与 M1 的 B/D 单元
    串行。签名是那条约束唯一测得到的形状。
    """
    from app.web.server import create_app

    assert list(inspect.signature(create_app).parameters) == [
        "db_path",
        "gateway_factory",
        "root_path",
    ]


def test_server_module_is_not_touched_by_this_unit():
    """U3 的 diff 里 app/web/server.py 必须为空（Global Constraints 头号约束）。"""
    server_source = (REPO_ROOT / "app" / "web" / "server.py").read_text(encoding="utf-8")

    assert "RecorderAuditHook" not in server_source
    assert "audit_hook" not in server_source


# ── 真实子进程装配 ───────────────────────────────────────────────────────


def test_importing_app_main_wires_a_real_recorder_hook(tmp_path):
    """
    ⭐ 唯一证明"这套装配真的跑得起来"的测试。AST 只看形状：一个 import 写错、
    一个参数顺序反了的 main.py 照样能通过上面全部断言。

    走子进程而不是直接 import：app.main 在导入期就会 setup_logging()、建库、
    create_app()，在测试进程里 import 会污染其余测试，而且 get_settings() 的
    lru_cache 会把第一次读到的路径钉死。
    """
    probe = (
        "import app.main as m\n"
        "gw = m._gateway_factory()\n"
        "hook = gw._audit_hook\n"
        "assert type(hook).__name__ == 'RecorderAuditHook', type(hook).__name__\n"
        # 第二次调用必须拿到同一个 hook 对象——每次新建就是每次新开连接
        "assert m._gateway_factory()._audit_hook is hook, '每次调用都新建了 hook'\n"
        "print('WIRED')\n"
    )
    env = {
        **os.environ,
        "DB_PATH": str(tmp_path / "wiring.db"),
        "AUDIT_JSONL_PATH": str(tmp_path / "decisions.jsonl"),
        "LOG_DIR": str(tmp_path / "logs"),
        "LLM_API_KEY": "test-key",
        "PYTHONPATH": str(REPO_ROOT),
    }

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "WIRED" in result.stdout
