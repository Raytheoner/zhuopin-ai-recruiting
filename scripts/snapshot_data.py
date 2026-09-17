"""Q-14 每日数据快照（`.51` 计划任务 `ZhuopinDailySnapshot` 的执行体）。

用法（Windows 计划任务与 macOS 本地同一命令）：
    python -m scripts.snapshot_data [--data-dir data] [--backup-root C:\\apps\\backups] [--keep 14]

做什么：
- 对 `--data-dir` 顶层每个 `*.db` 走 SQLite 在线备份 API（`Connection.backup`），源库以只读 URI 打开。
  ⛔ 不整目录 Copy-Item：服务在线时 `demo.db-shm` 被占用，Copy-Item 会 IOException 中断（09-08、09-09 两次实证）；
  backup API 在 WAL 模式下与在线写事务并存，拷出的是最近一次已提交状态。
- 顺带拷 `audit/` 目录与 `candidate_outbound.switch`（普通文件，直接复制）。
- 每份备份 `PRAGMA integrity_check` 必须为 ok，否则整次失败。
- 输出目录 `<backup-root>/daily-<yyyyMMdd-HHMM>`；同一分钟重跑追加 `-2`、`-3`，⛔ 不覆盖已有快照。
- 保留最近 `--keep` 份 `daily-*`，多的删；⛔ 只删名字匹配 daily-<8位>-<4位>[-n] 的目录，
  发版前手工快照（如 `20260903-1003`）永不触碰。
- 任一步失败 ⇒ 退出码非零；stdout 逐行打印进展供 schtasks 日志与人工核对。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_BACKUP_ROOT = r"C:\apps\backups" if sys.platform == "win32" else "backups"
DEFAULT_KEEP = 14
SIDE_FILES = ("candidate_outbound.switch",)
SIDE_DIRS = ("audit",)
_DAILY_RE = re.compile(r"^daily-\d{8}-\d{4}(-\d+)?$")


class SnapshotError(RuntimeError):
    pass


def _say(msg: str) -> None:
    print(f"[snapshot] {msg}", flush=True)


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M")


def _new_snapshot_dir(root: Path) -> Path:
    """建 daily-<stamp>[-n] 目录。root 不可写会在这里抛 OSError。"""
    root.mkdir(parents=True, exist_ok=True)
    base = root / f"daily-{_stamp()}"
    for n in range(1, 1000):
        cand = base if n == 1 else base.with_name(f"{base.name}-{n}")
        try:
            cand.mkdir()
            return cand
        except FileExistsError:
            continue
    raise SnapshotError(f"同一分钟内快照目录已达上限：{base}")


def backup_db(src: Path, dst: Path) -> int:
    """在线备份一个 SQLite 库并校验，返回页数。源库只读打开，⛔ 不写、不 checkpoint。"""
    src_conn = sqlite3.connect(f"{src.resolve().as_uri()}?mode=ro", uri=True)
    try:
        dst_conn = sqlite3.connect(dst)
        try:
            src_conn.backup(dst_conn)
            ok = dst_conn.execute("PRAGMA integrity_check").fetchone()[0]
            pages = dst_conn.execute("PRAGMA page_count").fetchone()[0]
        finally:
            dst_conn.close()
    finally:
        src_conn.close()
    if ok != "ok":
        raise SnapshotError(f"{dst.name} integrity_check={ok!r}")
    return int(pages)


def snapshot(data_dir: Path, snap_dir: Path) -> dict:
    dbs = sorted(p for p in data_dir.glob("*.db") if p.is_file())
    if not dbs:
        raise SnapshotError(f"{data_dir} 下没有 *.db，拒绝生成空快照（多半是 --data-dir 指错了）")
    manifest: dict = {"created": datetime.now().isoformat(timespec="seconds"), "data_dir": str(data_dir), "db": {}, "side": []}
    for db in dbs:
        pages = backup_db(db, snap_dir / db.name)
        manifest["db"][db.name] = {"pages": pages, "bytes": (snap_dir / db.name).stat().st_size}
        _say(f"备份 {db.name} → {snap_dir / db.name}（{pages} 页，integrity ok）")
    for name in SIDE_DIRS:
        d = data_dir / name
        if d.is_dir():
            shutil.copytree(d, snap_dir / name)
            manifest["side"].append(name + "/")
            _say(f"复制目录 {name}/")
    for name in SIDE_FILES:
        f = data_dir / name
        if f.is_file():
            shutil.copy2(f, snap_dir / name)
            manifest["side"].append(name)
            _say(f"复制文件 {name}")
    (snap_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def prune(root: Path, keep: int) -> list[Path]:
    """按名字排序（名字即时间戳）删最旧的，只认 daily-* 目录。返回删掉的路径。"""
    daily = sorted(p for p in root.iterdir() if p.is_dir() and _DAILY_RE.match(p.name))
    victims = daily[: max(0, len(daily) - keep)]
    for v in victims:
        shutil.rmtree(v)
        _say(f"清旧 {v.name}")
    return victims


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", default="data", help="服务的数据目录（默认 data，相对当前工作目录）")
    ap.add_argument("--backup-root", default=DEFAULT_BACKUP_ROOT, help=f"快照根目录（默认 {DEFAULT_BACKUP_ROOT}）")
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP, help=f"保留最近几份 daily-* 快照（默认 {DEFAULT_KEEP}，≥1）")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    data_dir = Path(args.data_dir)
    root = Path(args.backup_root)
    if args.keep < 1:
        _say(f"失败：--keep 必须 ≥ 1（给的是 {args.keep}）")
        return 2
    if not data_dir.is_dir():
        _say(f"失败：数据目录不存在 {data_dir.resolve()}")
        return 2

    snap_dir: Path | None = None
    complete = False
    try:
        snap_dir = _new_snapshot_dir(root)
        _say(f"快照目录 {snap_dir}")
        snapshot(data_dir, snap_dir)
        complete = True
        prune(root, args.keep)
    except (OSError, sqlite3.Error, SnapshotError) as e:
        _say(f"失败：{type(e).__name__}: {e}")
        # 只删没做完的快照；备份已完整、只是清旧失败时，新快照必须留下（它是今天唯一的备份）
        if snap_dir is not None and not complete and snap_dir.is_dir():
            shutil.rmtree(snap_dir, ignore_errors=True)
            _say(f"已删除不完整快照 {snap_dir.name}")
        return 1
    _say("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
