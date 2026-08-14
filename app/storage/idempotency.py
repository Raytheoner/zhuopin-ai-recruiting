import functools
import sqlite3
from typing import Callable, TypeVar

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
                # caller needs to see and act on.
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise

            conn.execute(
                "INSERT INTO effect_log (effect_key, thread_id, node_name, business_key, applied_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                (effect_key, thread_id, node_name, business_key),
            )
            conn.commit()
            return result

        return wrapper

    return decorator
