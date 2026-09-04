"""挂起状态的可恢复性（tasks 6.3 / 6.9 / 1.6b）。

⚠️ **这三条缺的从来不是实现，是断言。** tasks.md 原话：持久化那一半是成立的
（画像草案、对话记录、outbox 全在 SQLite），但"验证进程重启后可恢复"没有任何
测试断言过。checkpoint 结构上重启可恢复——"结构上可以"和"验过了"是两件事。

⛔ 不用 mock 冒充重启（opener 约束 6）。两个层次各验一遍：
  ① 真开一个**新的操作系统进程**，只给它数据库路径，看它能不能把挂起的
     thread 读回来（LLM 网关换成一调用就炸的假货 —— 状态必须完全来自磁盘）
  ② 同一进程内**新建一套 app / conn / graph / checkpointer**，走完整的 HTTP
     路径把确认做完（这一层验的是"用户关掉页面第二天再打开"）

⛔ 不引 freezegun 之类的时间库做"7 天推进"：scripts/check_boundary.py 的依赖
diff 检查会拦下 requirements.txt 的任何新增行。改库里的时间戳字符串就够了，
而且更接近真实——真实场景里变老的正是这些行。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from app.storage.db import get_connection
from tests.test_approval_branches import COMPLETE_PROFILE_RESPONSE, JD_RESPONSE
from tests.test_web_api import make_app_with_scripted_client

REPO_ROOT = Path(__file__).resolve().parents[1]

# 新进程里跑的探针。它**只读磁盘**：网关是一调用就抛 AssertionError 的假货，
# 所以"能读回挂起状态"这个结论不可能是 LLM 又跑了一遍伪造出来的。
_RECOVERY_PROBE = '''
import json
import sys

from app.channels.web_channel import WebChannel
from app.graph.build import build_intake_graph
from app.llm.gateway import LLMGateway
from app.storage.db import get_connection, init_schema


class _ExplodingCompletions:
    def create(self, **kwargs):
        raise AssertionError("恢复路径不该调用 LLM：挂起状态必须完全来自磁盘")


class _ExplodingChat:
    completions = _ExplodingCompletions()


class _ExplodingClient:
    chat = _ExplodingChat()


db_path, job_id = sys.argv[1], sys.argv[2]
conn = get_connection(db_path)
init_schema(conn)
graph = build_intake_graph(
    db_path,
    gateway=LLMGateway(
        api_key="k",
        base_url="https://example.com",
        model="deepseek-chat-241226",
        supports_json_schema=False,
        client=_ExplodingClient(),
    ),
    conn=conn,
    channel=WebChannel(conn),
)
snapshot = graph.get_state({"configurable": {"thread_id": job_id}})
values = snapshot.values or {}
latest = WebChannel(conn).latest(job_id)
print(
    json.dumps(
        {
            "has_checkpoint": bool(values),
            "is_complete": bool(values.get("is_complete")),
            "job_title": values.get("profile_patch_accumulated", {}).get("job_title"),
            "history_len": len(values.get("history", [])),
            "latest_type": None if latest is None else latest.type,
            "summary_labels": [
                item["label"]
                for item in (latest.payload.get("profile_summary", []) if latest else [])
            ],
        },
        ensure_ascii=False,
    )
)
'''


def _suspend_a_job(tmp_path) -> str:
    """跑到 confirmation_prompt 就停手，然后把整个应用关干净（触发 lifespan
    shutdown，checkpointer 的独立连接在那里关闭）。返回 job_id。"""
    client, _scripted = make_app_with_scripted_client(tmp_path, [COMPLETE_PROFILE_RESPONSE])
    with client:
        body = client.post("api/jobs", json={"message": "要个做 ECU 底层软件的"}).json()
        assert body["message"]["type"] == "confirmation_prompt"
        return body["job_id"]


def _rewind_seven_days(db_path: str) -> None:
    """把所有业务时间戳往前推 7 天，模拟"挂起了一周没人管"。

    ⛔ 不碰 LangGraph 自己的 checkpoint 表：那些是编排引擎的内部结构，
    伪造它的时间等于在测一个我们没有契约的东西。业务侧变老就够了——
    spec 关心的是"挂起状态不因超时而丢失"，而超时判定要看的正是这些行。
    """
    conn = get_connection(db_path)
    for table, column in (
        ("job_profile", "created_at"),
        ("job_profile", "turn_started_at"),
        ("conversation", "updated_at"),
        ("effect_log", "applied_at"),
        ("outbox", "created_at"),
    ):
        conn.execute(
            f"UPDATE {table} SET {column} = datetime({column}, '-7 days') "
            f"WHERE {column} IS NOT NULL"
        )
    conn.commit()
    conn.close()


def test_a_brand_new_process_recovers_the_suspended_thread(tmp_path):
    """tasks 1.6b / 6.3：**跨进程**按 thread_id 恢复。

    既有的 test_graph_replay_from_scratch_does_not_duplicate_effects 验的是
    "同进程内同 thread_id 重复 invoke 不重复产生副作用"，**不是**这件事。
    """
    job_id = _suspend_a_job(tmp_path)
    probe = tmp_path / "probe.py"
    probe.write_text(_RECOVERY_PROBE, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(probe), str(tmp_path / "web.db"), job_id],
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        capture_output=True,
        text=True,
        check=True,
    )
    recovered = json.loads(result.stdout.strip().splitlines()[-1])

    assert recovered["has_checkpoint"], "新进程按 thread_id 读不回 checkpoint"
    assert recovered["is_complete"] is True
    assert recovered["job_title"] == "底层软件工程师"
    assert recovered["history_len"] >= 2, "对话历史没跨进程活下来"
    assert recovered["latest_type"] == "confirmation_prompt"
    assert "岗位名称" in recovered["summary_labels"], "画像摘要没跨进程活下来"


def test_reopening_the_page_after_seven_days_still_confirms(tmp_path):
    """tasks 6.9 / spec「流程长时间挂起」：挂起 7 天后仍能正确恢复，
    挂起状态不因超时而丢失。"""
    job_id = _suspend_a_job(tmp_path)
    _rewind_seven_days(str(tmp_path / "web.db"))

    # 全新一套 app / conn / graph / checkpointer，指向同一个数据库文件。
    client, _scripted = make_app_with_scripted_client(tmp_path, [JD_RESPONSE])
    with client:
        got = client.get(f"api/jobs/{job_id}")
        assert got.status_code == 200
        assert got.json()["message"]["type"] == "confirmation_prompt"
        assert got.json()["message"]["payload"]["profile_summary"], "7 天后画像摘要丢了"

        confirmed = client.post(f"api/jobs/{job_id}/confirm")
        assert confirmed.status_code == 200
        assert confirmed.json()["jd_text"]

    conn = get_connection(str(tmp_path / "web.db"))
    try:
        assert conn.execute(
            "SELECT decision_type FROM human_review WHERE job_id = ?", (job_id,)
        ).fetchall() == [("approved",)]
    finally:
        conn.close()


def test_idempotency_survives_seven_days(tmp_path):
    """幂等键**不因时间流逝而过期**。

    effect_log 若被按时间清理（1.7 那条清理任务将来会做），7 天后的重试就会
    重新执行一次副作用。这条断言把"清理任务不得动未完结流程的 effect_log"
    这个约束提前钉住。
    """
    job_id = _suspend_a_job(tmp_path)
    _rewind_seven_days(str(tmp_path / "web.db"))

    client, scripted = make_app_with_scripted_client(tmp_path, [JD_RESPONSE])
    with client:
        assert client.post(f"api/jobs/{job_id}/confirm").status_code == 200
        calls = scripted.chat.completions.call_count
        assert client.post(f"api/jobs/{job_id}/confirm").status_code == 200
        assert scripted.chat.completions.call_count == calls, "7 天后的重试又调了一次 LLM"


def test_revise_and_abandon_also_survive_a_restart(tmp_path):
    """三个分支都要能在重启后走完，⛔ 不只验确认那一条。"""
    job_id = _suspend_a_job(tmp_path)

    client, _scripted = make_app_with_scripted_client(tmp_path, [])
    with client:
        assert client.post(f"api/jobs/{job_id}/abandon").status_code == 200

    conn = get_connection(str(tmp_path / "web.db"))
    try:
        assert conn.execute("SELECT status FROM job WHERE id = ?", (job_id,)).fetchone()[0] == (
            "abandoned"
        )
    finally:
        conn.close()
