"""P0–P3 共用的「拆件」子包：回件桥、信号、并发起活、章程、口径点台账。

⛔ 本包任何模块都不碰值守线程独占的 sqlite 连接，除了显式经 `storage/effects.py`
的 `effect_*` 节点（P0 task 1.5）——文件操作与库操作分得越干净，并发守卫与信号
文件就越容易在不持锁的情况下保证安全。
"""

from __future__ import annotations
