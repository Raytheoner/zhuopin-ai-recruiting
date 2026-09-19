"""语音主机 FastAPI 服务端点（voice-structured-interview U4 tasks 5.2，
design D12："`.51` 与语音主机之间只有两类交互，全部由 `.51` 主动发起"——
本文件是被动接收方）。

签名校验用 voice_host/_vendor/signing.py（部署期从 `.51` 仓库拷贝的独立
副本，见 Task 9 Step 1/2）。`on_session_opened` 回调在真实部署时接上
`voice_host/worker.py::run_session`（Task 11），本文件自己不知道 ASR/TTS/
LiveKit 是什么，只负责"收请求、验签名、读写本地队列、回响应"。
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request, Response

from voice_host import queue_store
from voice_host._vendor import signing


def create_app(*, queue_db_path: str, shared_secret: str, on_session_opened=None) -> FastAPI:
    app = FastAPI()
    conn = queue_store.get_connection(queue_db_path)
    queue_store.init_schema(conn)

    async def _verify(request: Request) -> bytes:
        body = await request.body()
        timestamp = request.headers.get(signing.HEADER_TIMESTAMP)
        signature = request.headers.get(signing.HEADER_SIGNATURE)
        if timestamp is None or signature is None:
            raise HTTPException(status_code=401, detail="缺少签名头")
        try:
            signing.verify(
                secret=shared_secret, method=request.method, path=request.url.path,
                timestamp=timestamp, body=body, signature=signature,
            )
        except signing.SignatureInvalidError:
            raise HTTPException(status_code=401, detail="签名无效")
        return body

    @app.post("/sessions")
    async def post_sessions(request: Request):
        body = await _verify(request)
        import json
        payload = json.loads(body)
        session_id = payload["session_id"]
        queue_store.open_session(conn, session_id=session_id, bundle_json=body.decode("utf-8"))
        if on_session_opened is not None:
            on_session_opened(session_id, body.decode("utf-8"))
        return {"accepted": True}

    @app.get("/sessions/{session_id}/events")
    async def get_events(session_id: str, since: int, request: Request):
        await _verify(request)
        status = queue_store.get_session_status(conn, session_id=session_id)
        if status is None:
            raise HTTPException(status_code=404, detail="场次不存在")
        events = queue_store.events_since(conn, session_id=session_id, since_seq=since)
        return {"events": events, "session_status": status}

    @app.get("/sessions/{session_id}/recording")
    async def get_recording(session_id: str, request: Request):
        await _verify(request)
        info = queue_store.get_recording_file(conn, session_id=session_id)
        if info is None:
            raise HTTPException(status_code=404, detail="录音尚未就绪")
        with open(info["path"], "rb") as fh:
            content = fh.read()
        return Response(content=content, headers={"X-Recording-SHA256": info["sha256"]})

    @app.delete("/sessions/{session_id}/artifacts", status_code=204)
    async def delete_artifacts(session_id: str, request: Request):
        await _verify(request)
        info = queue_store.get_recording_file(conn, session_id=session_id)
        if info is not None:
            from pathlib import Path
            Path(info["path"]).unlink(missing_ok=True)
            queue_store.mark_recording_transferred(conn, session_id=session_id)
        return Response(status_code=204)

    @app.post("/sessions/{session_id}/switch-to-text")
    async def switch_to_text(session_id: str, request: Request):
        body = await _verify(request)
        import json
        payload = json.loads(body) if body else {}
        trigger = payload.get("trigger", "manual")
        event_type = "mode_switched_after_prompt" if trigger == "after_network_prompt" else "mode_switched_manual"
        queue_store.set_current_answer_mode(conn, session_id=session_id, mode="text")
        queue_store.record_voice_host_event(conn, session_id=session_id, event_type=event_type)
        return {"ok": True}

    @app.post("/sessions/{session_id}/text-answer")
    async def post_text_answer(session_id: str, request: Request):
        body = await _verify(request)
        import json
        payload = json.loads(body)
        queue_store.submit_text_answer(conn, session_id=session_id, text=payload["text"])
        return {"ok": True}

    return app
