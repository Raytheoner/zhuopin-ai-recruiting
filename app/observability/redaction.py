from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from typing import Any, Mapping

REDACTED = "<redacted>"

# 白名单：只有这些键是「非内容」字段，可以原样进日志。白名单之外一律不输出
# ——对应 spec「新增字段的默认归属」：未声明即受控。
NON_CONTENT_KEYS = frozenset({"job_id", "thread_id", "version", "round_count", "status"})

# 兜底 Filter 的高危键名。这层是探测性的、不是主防线：正则永远追不上业务
# 字段的增长速度。它的价值是「当有人绕过 loggable_summary 时留下痕迹」。
RISKY_KEYS = (
    "job_title",
    "department",
    "responsibilities",
    "requirements",
    "must_have_skills",
    "nice_to_have_skills",
    "profile_json",
    "profile_patch",
    "history_json",
    "resume_text",
    "candidate_name",
    "content",
    "message",
    "_jd_text",
    # 以下补齐 app/schemas/job_profile.py::JobProfile 的真实自由文本/结构化内容
    # 字段（Task 4 review finding 5：上面这批键名是按预想业务对象猜的，跟真实
    # schema 对不上，等于形同虚设）。保留原有猜测键不删——它们不花代价，且
    # 可能命中未来或相邻对象。
    "education_requirement",
    "experience_years",
    "core_skills",
    "project_experience_requirement",
    "soft_skill_keywords",
    "autosar_experience",
    "mcu_family",
    "diag_stack",
    "sop_projects",
    "toolchain",
    "vehicle_model",  # SopProject 的嵌套字段
    "role",  # SopProject 的嵌套字段
    # 真实的 graph state 键（app/graph/nodes.py / app/graph/state.py）是
    # profile_patch_accumulated，而不是 profile_patch；单独列一项覆盖它，
    # 而不是把 "profile_patch" 改成子串匹配（子串匹配会误伤不相关的键名）。
    "profile_patch_accumulated",
)

_KEY_ALTERNATION = "|".join(re.escape(k) for k in RISKY_KEYS)
# 匹配三种键值渲染形态：
#   1) dict/JSON 形态： "键": "值" / '键': '值'
#   2) pydantic 模型 repr 形态： 键=值（无引号键名 + 等号，例如
#      `JobProfile(job_title='...', department='...')`）——finding 1：这个
#      形态之前完全漏网，%r 打印一个真实模型会让全部内容字段明文落盘且不
#      触发兜底告警，是「静默泄漏」。
#   3) pydantic ValidationError 回显形态： input_value=值（`str(ValidationError)`
#      把被拒绝的原始输入原样回显在这个键后面，键名本身不在 RISKY_KEYS 里
#      也必须兜——它天然就是「刚被拒绝、可能是任意自由文本」的值）。
# 值本身有四种形态，按顺序尝试（互不重叠，第一个字符不同即可判定分支，不
# 存在回溯歧义）：单引号串、双引号串、列表 [^\]]*（只吃到第一个 ]，不处理
# 嵌套列表/字典）、裸值兜底（数字/None/True/枚举 repr 等未加引号的值，吃到
# 下一个 , ] ) 或换行为止）。四个分支都是线性扫描的字符类，不含嵌套量词，
# 不会带来灾难性回溯。只吃掉值部分，键名保留在日志里（键名不是内容，值才
# 是）。前缀始终是唯一的捕获组 1，值不单独捕获——调用方用 \1 + REDACTED
# 拼接替换整个匹配，不依赖值的具体内容，因此三种前缀分支可以共用同一套值
# 分支而不改变现有调用方的替换逻辑。
_RISKY_VALUE_RE = re.compile(
    r"("
    r"['\"](?:" + _KEY_ALTERNATION + r")['\"]\s*:\s*"
    r"|\b(?:" + _KEY_ALTERNATION + r")\s*=\s*"
    r"|\binput_value\s*=\s*"
    r")"
    r"(?:"
    r"'(?:[^'\\]|\\.)*'"
    r"|\"(?:[^\"\\]|\\.)*\""
    r"|\[[^\]]*\]"
    r"|[^,\]\)\n]*"
    r")"
)

_local = threading.local()


def content_digest(value: Mapping[str, Any]) -> str:
    """短哈希变更探测器，仅用于判断「同一段内容是否变过」——**不是隐私边界**。

    保证的是：同一 value 的规范化 JSON 表示总映射到同一个 16 位十六进制串，
    可用来对比两次记录是否指向同一份内容。**不保证不可逆**——这是无盐的
    确定性 SHA-256 截断，对手机号/身份证号/姓名这类低熵单值，攻击者可以在
    可接受时间内穷举撞回原文（已验证：11 位手机号可在 1 秒内暴力还原）。

    因此这不是「调用方自觉遵守的约定」，而是**运行时强制的不变式**：只接受
    `Mapping`，且至少要有 2 个条目，否则拒绝执行并抛异常。**严禁**对手机号、
    身份证号、姓名等单个低熵字段单独调用——那等价于把这类字段明文换个编码
    方式写进日志，在 PIPL 意义上仍然是个人信息，不是脱敏。

    零配置是本函数的设计前提：拒绝低熵输入不能靠加盐/加 HMAC key 解决（那
    需要引入新的配置项，而生产 `.env` 与代码分开维护，要求两边同步变更正是
    本次改动要消除的失败模式）；能做且必须做的是在类型层面堵死误用——把
    「不要对单值调用」从文档警告升级成强制校验。

    当前唯一调用点 `loggable_summary` 在内容字段不足 2 个时**不会**把异常
    传播出日志调用——见该函数文档：跳过摘要、留空更安全，而不是让一次记录
    动作因为业务对象恰好只有 0/1 个内容字段而抛异常中断日志记录本身。

    Raises:
        TypeError: value 不是 Mapping。
        ValueError: value 是 Mapping 但条目数 < 2（单值，不足以抵抗穷举）。
    """
    if not isinstance(value, Mapping):
        raise TypeError(
            f"content_digest 只接受 Mapping（完整内容字典），收到 {type(value).__name__}；"
            "对单个标量字段调用等价于把它明文换个编码方式写进日志。"
        )
    if len(value) < 2:
        raise ValueError(
            f"content_digest 拒绝对少于 2 个条目的 Mapping 计算摘要（收到 {len(value)} 个）；"
            "单值输入熵太低，无盐 SHA-256 可在可接受时间内被暴力穷举还原。"
        )
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def loggable_summary(obj: Mapping[str, Any], *, known_fields: frozenset[str] | None = None) -> dict:
    """主防线：把业务对象压成只含非内容字段的摘要。

    白名单外的键**只贡献名字与统计量，不贡献取值**；且键名本身也要过一遍
    known_fields（画像 patch 是 LLM 自由生成的裸 dict，键名理论上也可能是
    模型幻觉出来的自由文本）。

    `content_digest` 现在会对少于 2 个内容字段的输入抛 ValueError（见其
    docstring）——这里刻意不让那个异常穿透到调用方：`loggable_summary` 是
    日志路径的一部分，一次记录动作不应该因为业务对象恰好只有 0/1 个内容
    字段就抛异常、打断日志记录本身（脱敏助手不该把一行日志变成一次崩溃）。
    内容字段不足 2 个时直接把 `content_digest` 置 None——单值本来就不该被
    摘要（那等价于明文换个编码方式），跳过是唯一安全的选择，字段仍然保留
    在摘要里以维持下游消费者读取的 schema 稳定。
    """
    summary: dict[str, Any] = {k: obj[k] for k in NON_CONTENT_KEYS if k in obj}
    content_keys = [k for k in obj if k not in NON_CONTENT_KEYS]
    summary["field_count"] = len(obj)
    if known_fields is not None:
        summary["field_names"] = sorted(k for k in content_keys if k in known_fields)
        summary["unknown_field_count"] = sum(1 for k in content_keys if k not in known_fields)
    else:
        summary["unknown_field_count"] = len(content_keys)
    summary["content_chars"] = sum(len(str(obj[k])) for k in content_keys)
    summary["content_digest"] = (
        content_digest({k: obj[k] for k in content_keys}) if len(content_keys) >= 2 else None
    )
    return summary


class RedactionFilter(logging.Filter):
    """兜底层：扫描最终 record，命中高危键名时把值替换成非还原形式。

    命中即额外记一条 WARNING，使「主防线被绕过」这件事可见而不是被悄悄修正。
    _local.busy 是重入护栏：那条 WARNING 自己也会流经本 Filter。

    同一条 record 可能被挂在同一 logger 树上的多个 handler 各自 filter 一遍
    （例如 console + file）；用 record.redacted_fields 做已处理标记，防止
    第二个 handler 把第一个 handler 已经替换成 '<redacted>' 的值当成"新的
    命中"重新替换一遍、重新告警一遍——那会让一次绕过被计成两次事故，并且
    拖累 50MB 轮转预算下的日志量。
    """

    def __init__(self, name: str = "") -> None:
        super().__init__(name)
        self.hits = 0

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "redacted_fields", 0):
            # 本 record 已经被（同一条流水线上的）另一个 handler 处理过。
            return True

        try:
            rendered = record.getMessage()
        except Exception as exc:
            # record.getMessage() 本身可能因为 %-占位符与 args 数量不匹配而
            # 抛异常。绝不能在这里把未脱敏的 record 原样放行——它会在
            # emit() 里再次触发同样的异常，落进 stdlib Handler.handleError()，
            # 后者会把 record.msg / record.args 原文写进 sys.stderr，绕开
            # Filter 和 Formatter 两层防线。这里直接就地中和：丢弃原始
            # msg/args，换成一句不带负载的诊断信息。
            record.msg = (
                "[redaction] 日志格式化失败，原始 msg/args 已丢弃以避免明文外泄："
                f"logger={record.name} 位置={record.pathname}:{record.lineno} "
                f"错误类型={type(exc).__name__}"
            )
            record.args = ()
            record.redacted_fields = 1
            return True

        redacted, count = _RISKY_VALUE_RE.subn(r"\1'" + REDACTED + "'", rendered)
        if count == 0:
            return True

        record.msg = redacted
        record.args = ()
        record.redacted_fields = count
        self.hits += count

        if not getattr(_local, "busy", False):
            _local.busy = True
            try:
                logging.getLogger("app.observability.redaction").warning(
                    "脱敏兜底命中 %d 处：logger=%s 位置=%s:%s。"
                    "主防线被绕过了——业务对象不应整体进日志，请改用 loggable_summary()",
                    count,
                    record.name,
                    record.pathname,
                    record.lineno,
                )
            finally:
                _local.busy = False
        return True


class RedactingFormatter(logging.Formatter):
    """异常堆栈不经过 record.getMessage()，Filter 看不到它。

    堆栈里会出现局部变量的 repr（例如 `profile_dict = {...}` 的那一帧），
    以及 `ValidationError` 把原始输入回显进 str(exc) 的情况——所以格式化
    之后再扫一遍最终文本，是 Filter 之外必须补的一刀。

    注意：`super().format(record)` 会把原始（未脱敏）的 traceback 文本写进
    `record.exc_text` 缓存下来，本方法只对**返回值**做替换，不回写
    record.exc_text。今天这是安全的，因为本项目所有 handler 都配置了
    RedactingFormatter，没有第二个 formatter 会读到这个未脱敏缓存；但它不是
    对 record 的纯粹只读——调用方如果换成裸 logging.Formatter 读同一个
    record，会读到未脱敏的 exc_text。轮转检查路径下的重复调用是安全的
    （两次 format() 输出一致，不依赖调用次数），但这个缓存写回不是。
    """

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        return _RISKY_VALUE_RE.sub(r"\1'" + REDACTED + "'", text)
