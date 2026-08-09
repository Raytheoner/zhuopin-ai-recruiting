from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.agents.jd_agent import generate_jd
from app.channels.web_channel import WebChannel
from app.graph.build import build_intake_graph
from app.graph.nodes import effect_confirm_profile
from app.middleware.auth import AuthMiddleware
from app.schemas.job_profile import JobProfile
from app.storage.db import get_connection, init_schema

STATIC_DIR = Path(__file__).parent / "static"
INDEX_TEMPLATE_PATH = STATIC_DIR / "index.html"


class CreateJobRequest(BaseModel):
    message: str


class ReplyRequest(BaseModel):
    message: str


def _render_index(root_path: str) -> str:
    """把 <!--BASE_HREF--> 占位符换成真实 <base href>，让前端相对路径请求
    在任意挂载前缀下都能解析到正确的地址。root_path="" 时挂域根。"""
    html = INDEX_TEMPLATE_PATH.read_text(encoding="utf-8")
    base_href = f"{root_path}/" if root_path else "/"
    return html.replace("<!--BASE_HREF-->", f'<base href="{base_href}">')


def create_app(*, db_path: str, gateway_factory: Callable, root_path: str = "") -> FastAPI:
    app = FastAPI(title="卓品智能招聘助手 · Demo")
    app.add_middleware(AuthMiddleware)

    conn = get_connection(db_path)
    init_schema(conn)
    channel = WebChannel(conn)
    router = APIRouter()

    def _run_turn(job_id: str, message: str) -> dict:
        gateway = gateway_factory()
        graph = build_intake_graph(db_path, gateway=gateway, conn=conn, channel=channel)

        history_row = conn.execute(
            "SELECT profile_json FROM job_profile WHERE job_id=? ORDER BY version DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        accumulated = json.loads(history_row[0]) if history_row else {}
        round_count = conn.execute(
            "SELECT COUNT(*) FROM job_profile WHERE job_id=?", (job_id,)
        ).fetchone()[0]

        state = {
            "job_id": job_id,
            "history": [{"role": "user", "content": message}],
            "round_count": round_count,
            "profile_patch_accumulated": accumulated,
        }
        graph.invoke(state, config={"configurable": {"thread_id": job_id}})

        latest = channel.latest(job_id)
        return {"type": latest.type, "payload": latest.payload}

    @router.post("/api/jobs")
    def create_job(req: CreateJobRequest):
        job_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO job (id, title, status) VALUES (?, '待确定', 'drafting')", (job_id,)
        )
        conn.commit()
        message = _run_turn(job_id, req.message)
        return {"job_id": job_id, "message": message}

    @router.post("/api/jobs/{job_id}/reply")
    def reply(job_id: str, req: ReplyRequest):
        job = conn.execute("SELECT id FROM job WHERE id=?", (job_id,)).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        message = _run_turn(job_id, req.message)
        return {"job_id": job_id, "message": message}

    @router.post("/api/jobs/{job_id}/confirm")
    def confirm(job_id: str):
        row = conn.execute(
            "SELECT profile_json, status FROM job_profile WHERE job_id=? ORDER BY version DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no profile draft yet")

        latest_message = channel.latest(job_id)
        if latest_message is None or latest_message.type != "confirmation_prompt":
            raise HTTPException(status_code=409, detail="画像还在追问中，未到可确认状态")

        profile_dict = json.loads(row[0])
        version = conn.execute(
            "SELECT MAX(version) FROM job_profile WHERE job_id=?", (job_id,)
        ).fetchone()[0]

        effect_confirm_profile(
            conn, thread_id=job_id, business_key=str(version), profile_dict=profile_dict
        )

        gateway = gateway_factory()
        profile = JobProfile.model_validate(
            {
                "job_title": profile_dict.get("job_title", "未命名岗位"),
                "department": profile_dict.get("department", "未指定"),
                "headcount": profile_dict.get("headcount", 1),
                "education_requirement": profile_dict.get("education_requirement", "未指定"),
                "experience_years": profile_dict.get("experience_years", "未指定"),
                **{
                    k: v
                    for k, v in profile_dict.items()
                    if k
                    not in {
                        "job_title",
                        "department",
                        "headcount",
                        "education_requirement",
                        "experience_years",
                    }
                },
            }
        )
        jd_result = generate_jd(gateway, profile)

        conn.execute(
            "UPDATE job_profile SET profile_json = ? "
            "WHERE job_id = ? AND version = ?",
            (json.dumps({**profile_dict, "_jd_text": jd_result.text}, ensure_ascii=False), job_id, version),
        )
        conn.commit()

        return {
            "job_id": job_id,
            "jd_text": jd_result.text,
            "needs_manual": jd_result.needs_manual,
        }

    @router.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = conn.execute(
            "SELECT id, title, status FROM job WHERE id=?", (job_id,)
        ).fetchone()
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        latest = channel.latest(job_id)
        return {
            "job_id": job[0],
            "status": job[2],
            "message": {"type": latest.type, "payload": latest.payload} if latest else None,
        }

    @router.get("/")
    def index() -> HTMLResponse:
        return HTMLResponse(_render_index(root_path))

    app.include_router(router, prefix=root_path)
    app.mount(
        f"{root_path}/static" if root_path else "/static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="static",
    )

    return app
