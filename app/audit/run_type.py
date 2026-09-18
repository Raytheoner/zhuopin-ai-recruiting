"""analysis_run.run_type 语义约定：不加列，用 prompt_version 前缀区分。

design.md 决策：「analysis_run 增加 run_type 语义约定（parse/rank，可空列已存在
的用 prompt_version 前缀区分，⛔ 不加列）」；voice-structured-interview
design.md tasks 2.6 追加 interview 取值——prep 出题/追问选择/post 评分三处
AI 调用统一归为 interview 语义类别，具体调用共用一个 "interview-" 前缀
（如 "interview-prep-v1"/"interview-score-v1"），与 parse-/rank- 同一机制。
⛔ 不要给这个模块加数据库读写——它是纯函数，只做字符串前缀判定，不 import
app.storage。
"""
from __future__ import annotations

RUN_TYPE_PARSE = "parse"
RUN_TYPE_RANK = "rank"
RUN_TYPE_INTERVIEW = "interview"

# 前缀 → run_type。design.md 与 tasks.md 里已出现的具体值：
# "parse-v1"（resume-parsing spec，U2 tasks 3.5）、"rank-v1"（candidate-ranking
# spec，U4 tasks 5.4）、"interview-*"（voice-structured-interview，M3 U1
# tasks 2.6，覆盖 prep/追问/评分三处调用）。新增运行类型时在这里加一行，
# ⛔ 不要在别处再判一次前缀——散成两处会出现"一处判 parse 一处判 rank"的分叉。
_PREFIX_TO_RUN_TYPE: dict[str, str] = {
    "parse-": RUN_TYPE_PARSE,
    "rank-": RUN_TYPE_RANK,
    "interview-": RUN_TYPE_INTERVIEW,
}


def run_type_of(prompt_version: str) -> str:
    for prefix, run_type in _PREFIX_TO_RUN_TYPE.items():
        if prompt_version.startswith(prefix):
            return run_type
    raise ValueError(
        f"未登记的 prompt_version 前缀: {prompt_version!r}；"
        f"已登记前缀: {sorted(_PREFIX_TO_RUN_TYPE)}"
    )
