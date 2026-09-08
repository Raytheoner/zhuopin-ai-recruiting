import functools
import logging
import sqlite3
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class EffectAlreadyApplied(Exception):
    """
    预留异常：本任务的 idempotent_effect 装饰器命中重复 effect_key 时静默跳过
    并返回 None，不主动抛出此异常。保留给后续需要“必须是首次执行”语义的
    调用方（例如要求重复调用视为错误而非静默跳过）显式使用。
    """


def idempotent_effect(node_name: str) -> Callable[[Callable[..., T]], Callable[..., T | None]]:
    """
    装饰一个 effect_* 节点函数。被装饰函数必须接受
    (conn: sqlite3.Connection, thread_id: str, business_key: str, **kwargs) 签名。

    幂等键 = f"{thread_id}:{node_name}:{business_key}"，命中 effect_log 则跳过、返回 None。
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T | None]:
        @functools.wraps(fn)
        def wrapper(
            conn: sqlite3.Connection, *, thread_id: str, business_key: str, **kwargs
        ) -> T | None:
            effect_key = f"{thread_id}:{node_name}:{business_key}"
            existing = conn.execute(
                "SELECT 1 FROM effect_log WHERE effect_key = ?", (effect_key,)
            ).fetchone()
            if existing is not None:
                return None

            try:
                result = fn(conn, thread_id=thread_id, business_key=business_key, **kwargs)
            except Exception:
                # conn is a single connection shared across the whole app
                # (see db.get_connection); if fn wrote rows before raising,
                # those writes sit in SQLite's implicit open transaction and
                # would otherwise be durably committed the next time ANY
                # unrelated effect calls conn.commit(). Roll back so a failed
                # effect leaves no trace for a later, unrelated commit to pick up.
                #
                # The rollback itself can fail (e.g. the transaction was
                # already ended by another owner before we got here) — that
                # failure must never replace the original exception the
                # caller needs to see and act on. But it must not be silent
                # either: if the rollback fails, the partial write it was
                # supposed to undo is left sitting in the still-open
                # transaction and will be durably committed by the next,
                # unrelated effect's conn.commit() — log it at ERROR so this
                # failure mode leaves a trace somewhere.
                try:
                    conn.rollback()
                except Exception as rollback_exc:
                    logger.error(
                        "rollback failed while cleaning up after effect_key=%s "
                        "raised; the effect's partial write was NOT undone and "
                        "may be silently committed by a later, unrelated effect",
                        effect_key,
                        exc_info=rollback_exc,
                    )
                raise

            try:
                conn.execute(
                    "INSERT INTO effect_log (effect_key, thread_id, node_name, business_key, applied_at) "
                    "VALUES (?, ?, ?, ?, datetime('now'))",
                    (effect_key, thread_id, node_name, business_key),
                )
            except sqlite3.IntegrityError as exc:
                # 上面第一行的 SELECT 预检**永远不可能充分**：它和这条 INSERT 之间
                # 隔着被装饰函数的整个函数体，另一条路径（双击、客户端超时重发、
                # 反向代理重试）可以在这段窗口里完整应用同一个 effect_key。
                # 唯一索引才是唯一权威，预检只是省一次无谓执行的优化。
                #
                # 撞上唯一键 = 这件事已经被别人做完了 = 幂等命中，是**正常路径**，
                # ⛔ 不是异常。抛出去用户就看到 500（现网 2026-09-08 13:18 实发一次）。
                #
                # 但短路之前必须先回滚：fn 刚写的业务行还在这个未提交的事务里，
                # 而 conn 是全应用共享的单连接（见 db.get_connection）。不回滚，
                # 这些行会被之后任何一次*不相关*的 effect 的 conn.commit() 悄悄
                # 落盘 —— 业务表多一行、effect_log 仍只有一行，工程铁律1 的恒等式
                # 当场破掉，而且没有任何症状。
                # ⛔ 同理不能用 INSERT OR IGNORE 之后照常 commit：那等于主动把
                # 重复的业务行提交下去。
                try:
                    conn.rollback()
                except Exception as rollback_exc:
                    # 回滚失败 ⇒ 那批业务写仍留在未提交事务里，随时会被下一次
                    # 不相关的 commit 带下去。此时**不能**假装幂等成功返回 None
                    # （那会让调用方以为一切正常，而恒等式正悬在破掉的边缘），
                    # 只能记 ERROR 并把原始异常抛给调用方。
                    logger.error(
                        "rollback failed while short-circuiting duplicate effect_key=%s; "
                        "this call's business write was NOT undone and may be silently "
                        "committed by a later, unrelated effect",
                        effect_key,
                        exc_info=rollback_exc,
                    )
                    raise exc

                # 语义判据，⛔ 不匹配错误文案（SQLite 的消息措辞不是契约）：
                # 回滚后这把键确实在库里 ⇒ 是幂等命中；不在 ⇒ 坏的是别的完整性
                # 约束（例如 thread_id 为 None 触发 NOT NULL），那是真 bug，原样上抛。
                already_applied = conn.execute(
                    "SELECT 1 FROM effect_log WHERE effect_key = ?", (effect_key,)
                ).fetchone()
                if already_applied is None:
                    raise
                logger.warning(
                    "effect_key=%s was applied by another path between this call's "
                    "pre-check and its effect_log insert; treating as already applied "
                    "and attempting to roll back this call's business write. The "
                    "rollback is connection-wide (conn is shared across the app) and "
                    "may be a no-op if a peer thread already committed on it",
                    effect_key,
                )
                return None

            conn.commit()
            return result

        return wrapper

    return decorator
