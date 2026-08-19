import asyncio
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability.context import REQUEST_ID_HEADER, current_request_id
from app.observability.logging_config import setup_logging
from app.observability.middleware import RequestIdMiddleware

LINE_RE = re.compile(r"\[([0-9a-f]{16})\]")


def _probe_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    log = logging.getLogger("probe")

    @app.get("/sync/{job_id}")
    def sync_route(job_id: str):
        log.info("sync-enter job=%s", job_id)
        time.sleep(0.02)
        log.info("sync-exit job=%s", job_id)
        return {"request_id": current_request_id(), "job_id": job_id}

    @app.get("/async/{job_id}")
    async def async_route(job_id: str):
        log.info("async-enter job=%s", job_id)
        await asyncio.sleep(0.02)
        log.info("async-exit job=%s", job_id)
        return {"request_id": current_request_id(), "job_id": job_id}

    return app


@pytest.fixture
def log_file(tmp_path):
    path = tmp_path / "logs"
    setup_logging(log_dir=str(path), level="INFO", retention_days=30)
    yield path / "app.log"
    logging.shutdown()


def test_all_lines_of_one_request_share_the_id_and_it_is_returned(log_file):
    client = TestClient(_probe_app())
    resp = client.get("/sync/J1")

    assert resp.status_code == 200
    header_id = resp.headers[REQUEST_ID_HEADER]
    assert resp.json()["request_id"] == header_id

    logging.shutdown()
    lines = [
        line for line in log_file.read_text(encoding="utf-8").splitlines() if "job=J1" in line
    ]
    assert len(lines) == 2, f"期望 enter/exit 两行，实得 {lines}"
    assert {LINE_RE.search(line).group(1) for line in lines} == {header_id}


@pytest.mark.parametrize("route", ["sync", "async"])
def test_overlapping_requests_never_cross_talk(log_file, route):
    """同步路由（线程池）与异步路由（事件循环）两条执行路径都要覆盖——
    thread-local 会在异步路径上串号，这条测试是那个错误实现的报警器。"""
    app = _probe_app()
    jobs = [f"J{i}" for i in range(12)]

    if route == "sync":
        client = TestClient(app)
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            responses = list(pool.map(lambda j: client.get(f"/sync/{j}"), jobs))
        pairs = {r.json()["job_id"]: r.headers[REQUEST_ID_HEADER] for r in responses}
    else:

        async def drive():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
                return await asyncio.gather(*(ac.get(f"/async/{j}") for j in jobs))

        responses = asyncio.run(drive())
        pairs = {r.json()["job_id"]: r.headers[REQUEST_ID_HEADER] for r in responses}

    assert len(set(pairs.values())) == len(jobs), "请求标识重复了"

    logging.shutdown()
    text = log_file.read_text(encoding="utf-8")
    for job, request_id in pairs.items():
        for phase in ("enter", "exit"):
            matching = [
                line
                for line in text.splitlines()
                if line.endswith(f"{route}-{phase} job={job}")
            ]
            assert len(matching) == 1, f"{job} 的 {phase} 行数异常：{matching}"
            got = LINE_RE.search(matching[0]).group(1)
            assert got == request_id, (
                f"{job} 的 {phase} 行串号了：日志里是 {got}，该请求实际是 {request_id}"
            )
