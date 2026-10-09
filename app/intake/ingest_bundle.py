"""ZIP 导出包接收的副作用执行单元（channel-resume-intake U1 tasks 1.4）。

effect_ingest_bundle 是独占的副作用执行单元：幂等键
{job_id}:effect_ingest_bundle:{bundle_sha256}，重跑不产生第二批简历。本函数
自己的业务写只有 bundle_ingest_result 一行（与 effect_log 同事务提交，工程
铁律 1）；逐文件简历写入委托给调用方注入的 ingest_one（既有单文件接收，其
内部按文件哈希去重并由 effect_persist_parse 承担解析写入的幂等）。
"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Callable

from app.intake.bundle import FileEntry
from app.intake.source import detect_source, first_page_text
from app.storage.idempotency import idempotent_effect


class _BytesUpload:
    """给既有单文件接收函数的最小 UploadFile 适配器（只需 .filename 与 .file）。"""
    def __init__(self, filename: str, data: bytes) -> None:
        self.filename = filename
        self.file = io.BytesIO(data)


@idempotent_effect("effect_ingest_bundle")
def effect_ingest_bundle(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    business_key: str,
    job_id: str,
    sample_class: str,
    uploaded_by: str,
    entries: list[FileEntry],
    default_source: str | None,
    ingest_one: Callable[..., dict],
) -> dict:
    results: list[dict] = []
    seen_hashes: set[str] = set()
    for entry in entries:
        results.append(_ingest_entry(
            entry=entry,
            seen_hashes=seen_hashes,
            job_id=job_id,
            sample_class=sample_class,
            uploaded_by=uploaded_by,
            default_source=default_source,
            ingest_one=ingest_one,
        ))
    result_json = json.dumps({"results": results}, ensure_ascii=False)
    conn.execute(
        "INSERT INTO bundle_ingest_result (id, job_id, bundle_sha256, status, result_json) "
        "VALUES (?, ?, ?, 'completed', ?)",
        (str(uuid.uuid4()), job_id, business_key, result_json),
    )
    return {"results": results}


def _ingest_entry(*, entry, seen_hashes, job_id, sample_class, uploaded_by, default_source, ingest_one) -> dict:
    if entry.kind != "ok":
        return {"file_name": entry.filename, "status": "rejected", "reason": entry.reason}

    content_hash = hashlib.sha256(entry.data).hexdigest()
    if content_hash in seen_hashes:
        return {"file_name": entry.filename, "status": "intra_bundle_duplicate"}
    seen_hashes.add(content_hash)

    suffix = Path(entry.filename).suffix.lower()
    source, source_origin = _resolve_source(entry.filename, entry.data, suffix, default_source)
    result = ingest_one(
        job_id=job_id,
        sample_class=sample_class,
        uploaded_by=uploaded_by,
        upload=_BytesUpload(entry.filename, entry.data),
        source=source,
        source_origin=source_origin,
    )
    if result.get("status") == "accepted":
        result["source"] = source
        result["source_origin"] = source_origin
    return result


def _resolve_source(filename: str, data: bytes, suffix: str, default_source: str | None) -> tuple[str, str | None]:
    detected = detect_source(filename, first_page_text(data, suffix))
    if detected is not None:
        return detected.value, "detected"
    if default_source is not None:
        return default_source, "default"
    return "unknown", None
