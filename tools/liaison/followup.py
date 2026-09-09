"""8.7·`send-followup`：把一封跟进信发进值守群，并回填台账那一行。

**为什么是一条本机 CLI**：跟进信原先只能手工发。Windows 侧的对等物是
`push_followup_letter.py`——发信的是**跑在本机原生环境的脚本**，AI 会话只是调它。
Mac 侧的对等物是 CC 的 Bash（macOS 原生、有网）；⛔ **不是** Cowork 的 shell
（隔离 VM，`qyapi.weixin.qq.com` 无 DNS 解析，2026-09-09 两次实测；云容器经代理
亦 403，不在 egress 白名单）。所以这条 CLI 只能由 CC 或本机 Terminal 跑。

🔴 **默认 `--dry-run`，真发必须显式 `--send`。** 这条 CLI 唯一的危险动作就是外发，
而外发撤不回来——"手滑漏了一个 flag"必须落在安全的那一侧。对外发送属"不可代"项：
真发那一次由 Shao Peishen 本人跑，他跑＝授权留痕。

⛔ **凭据只从 `HR_LIAISON_GROUP_WEBHOOK` 读，不接受命令行传地址**：命令行参数会进
shell 历史、进 `ps` 的输出、进被贴出来的报错，那三处都是凭据最不该出现的地方。
dry-run 只打**域名**，⛔ 不打 `?key=` 那一段——它本身就是凭据，而 dry-run 的输出
正是要被贴进聊天的东西。

⛔ 本模块不写 `with X:`（第 2 章事务扫描器），也 ⛔ 不调 `setup_logging()`
（日志接线唯一性由 `__main__.main()` 那一处承担）。
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
import urllib.parse
from pathlib import Path

from tools.liaison.errors import MissingCredentialsError
from tools.liaison.config import load_group_webhook
from tools.liaison.notify import webhook as notify_webhook
from tools.liaison.notify.transport import UrllibTransport, WebhookTransportError
from tools.liaison.notify.webhook import GroupWebhookSender

#: 台账真身的文件名。**按信件所在目录定位**，⛔ 不写死仓库路径——写死之后这条
#: CLI 就只能在一个目录里用，而单测连一份可控的台账都造不出来。
LEDGER_FILENAME = "README-跟进信清单.md"

#: 退出码。⚠️ 与 `__main__.py` 那套（守护进程的启动期码）**刻意不共用**：那边
#: 2＝缺凭据，这边 2＝参数错。两个命令的失败面完全不同，硬凑成一套只会让排障的人
#: 按错的表查。
EXIT_OK = 0
EXIT_BAD_ARGS = 2
EXIT_MISSING_CREDENTIALS = 3
EXIT_SEND_FAILED = 4

#: 信件抬头里的编号，如 `# 人事部#1 · 主题`。编号是台账的**唯一定位键**——
#: ⛔ 不用标题模糊匹配：标题会改字、会有全半角差异，而认错行的后果是把**别人的**
#: 那一行标成已推送。
_NUMBER_IN_HEADING = re.compile(r"^#\s+(?P<number>[^\s#]+#\d+)\s*(?:·|$)", re.MULTILINE)

#: 编号列里那对"暂不占号"括注。发出后要去掉（编号规则：未发出的信不占号）。
_PARENTHETICAL = re.compile(r"（[^）]*暂不占号[^）]*）")

#: 台账里的终态标记。命中即幂等短路。
_ALREADY_SENT = "✅ 已推送"

_FRONTMATTER = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)


def strip_frontmatter(text: str) -> str:
    """去掉 YAML frontmatter。**纯函数**。

    ⛔ frontmatter 绝不进群消息：`status: ⏳ 待你审` 之类是**我方内部状态**，
    发进群里等于把审批过程摊给收信人看。
    """
    return _FRONTMATTER.sub("", text, count=1).strip()


def extract_number(body: str) -> str | None:
    """从信件抬头取编号（`# 人事部#1 · …` → `人事部#1`）。**纯函数**。"""
    match = _NUMBER_IN_HEADING.search(body)
    return match.group("number") if match else None


def compute_backfilled_ledger(
    text: str, *, number: str, today: datetime.date
) -> tuple[str, bool]:
    """把编号为 `number` 的那一行改成已推送。**纯函数**。

    返回 `(新文本, 是否改动了)`；已是终态时原样返回并给 `False`，由调用方短路。
    ⚠️ 只重写命中的那**一行**，⛔ 别人的行一个字节都不动——并行泳道的同族要求。
    """
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|") or f"`{number}" not in line:
            continue
        if _ALREADY_SENT in line:
            return text, False
        cells = line.split("|")
        # 首尾是表格前后的空串，⛔ 不参与改写。
        cells[1] = _PARENTHETICAL.sub("", cells[1])
        cells[-2] = f" `{_ALREADY_SENT} {today.isoformat()}` "
        lines[index] = "|".join(cells)
        return "".join(lines), True
    raise LookupError(number)


def compute_dry_run_report(
    *, number: str, body: str, webhook_url: str, attachment: Path | None, size: int | None
) -> str:
    """dry-run 的输出。**纯函数**，因此可以逐字断言。

    🔴 只打**域名**，⛔ 不打 `?key=`。
    """
    domain = urllib.parse.urlsplit(webhook_url).netloc
    lines = [
        "── DRY RUN·以下内容【没有】发出去 ──────────────────────────────",
        f"编号     ：{number}",
        f"目标群   ：{domain}（域名，key 不予回显）",
        f"附件     ：{attachment.name}（{size} 字节）" if attachment else "附件     ：无",
        "",
        "── 将要发送的 markdown 正文（逐字）─────────────────────────────",
        body,
        "── 正文结束 ────────────────────────────────────────────────────",
        "真发请把 --dry-run 换成 --send。",
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.liaison send-followup",
        description="把一封跟进信发进值守群并回填台账。默认 dry-run，真发要显式 --send。",
    )
    parser.add_argument("--md", required=True, help="跟进信正文（frontmatter 会被剥掉）")
    parser.add_argument("--docx", help="同名 Word 附件，可省")
    mode = parser.add_mutually_exclusive_group()
    # ⛔ 两个 flag 都不给时落在 dry-run，见模块 docstring。
    mode.add_argument("--dry-run", action="store_true", help="只打印，什么都不发（默认）")
    mode.add_argument("--send", action="store_true", help="真发。⚠️ 撤不回来")
    return parser


def send_followup_main(
    argv: list[str],
    *,
    transport=None,
    env=None,
    today: datetime.date | None = None,
) -> int:
    """子命令入口。三个关键字参数是**测试注入缝**，⛔ 不是配置项。

    顺序是刻意的，每一步都在为"别留下发了一半的信"服务：
    ① 读信、取编号 → ② 台账定位（终态即短路，**发送之前**）→ ③ 附件尺寸预检
    → ④ 发正文 → ⑤ 发附件 → ⑥ 回填台账。
    ③ 必须排在 ④ 前面：先发正文再发现附件超限，会留下一封发了一半的信，而补发
    没有幂等路径——台账那一行还不是终态，重跑会把正文再发一遍。
    """
    args = build_parser().parse_args(argv)
    today = today or datetime.date.today()

    md_path = Path(args.md)
    if not md_path.is_file():
        print(f"找不到跟进信正文：{md_path}", file=sys.stderr)
        return EXIT_BAD_ARGS

    body = strip_frontmatter(md_path.read_text(encoding="utf-8"))
    number = extract_number(body)
    if number is None:
        print(
            f"{md_path.name} 的抬头里没有编号（形如 `# 人事部#1 · 主题`），"
            "台账无从定位 ⇒ ⛔ 拒发",
            file=sys.stderr,
        )
        return EXIT_BAD_ARGS

    ledger_path = md_path.parent / LEDGER_FILENAME
    if not ledger_path.is_file():
        print(f"找不到台账 {ledger_path} ⇒ ⛔ 拒发", file=sys.stderr)
        return EXIT_BAD_ARGS
    ledger_text = ledger_path.read_text(encoding="utf-8")
    try:
        backfilled, changed = compute_backfilled_ledger(
            ledger_text, number=number, today=today
        )
    except LookupError:
        print(f"台账里没有 {number} 这一行 ⇒ ⛔ 拒发（发出去却无处回填＝台账当场失真）",
              file=sys.stderr)
        return EXIT_BAD_ARGS
    if not changed:
        print(f"{number} 已是终态，不重复回填")
        return EXIT_OK

    attachment = Path(args.docx) if args.docx else None
    blob: bytes | None = None
    if attachment is not None:
        if not attachment.is_file():
            print(f"找不到附件：{attachment}", file=sys.stderr)
            return EXIT_BAD_ARGS
        blob = attachment.read_bytes()
        # ⛛ 按属性取、不 `from … import` 成本地名：上限只能有**一个**活口径。
        # 复制成本地常量之后，`webhook.py` 那边改了这边不知道，而症状是
        # 「有时候挡有时候不挡」——比不挡更难查。
        if len(blob) > notify_webhook.MAX_ATTACHMENT_BYTES:
            print(
                f"附件 {attachment.name} 有 {len(blob)} 字节，超过企微上限 "
                f"{notify_webhook.MAX_ATTACHMENT_BYTES} 字节 ⇒ ⛔ 提前拒绝，正文也不发",
                file=sys.stderr,
            )
            return EXIT_BAD_ARGS

    try:
        webhook_url = load_group_webhook(env)
    except MissingCredentialsError as exc:
        # 只打变量名，⛔ 不打取值。
        print(str(exc), file=sys.stderr)
        return EXIT_MISSING_CREDENTIALS

    if not args.send:
        print(
            compute_dry_run_report(
                number=number,
                body=body,
                webhook_url=webhook_url,
                attachment=attachment,
                size=len(blob) if blob is not None else None,
            )
        )
        return EXIT_OK

    sender = GroupWebhookSender(
        webhook_url=webhook_url,
        transport=transport if transport is not None else UrllibTransport(),
    )
    try:
        response = sender.send_markdown(body)
        if not response.ok:
            print(
                f"正文发送失败：errcode={response.errcode} {response.errmsg}",
                file=sys.stderr,
            )
            return EXIT_SEND_FAILED
        if blob is not None and attachment is not None:
            media_id = sender.publish_attachment(filename=attachment.name, content=blob)
            response = sender.send_file(media_id)
            if not response.ok:
                print(
                    f"附件发送失败：errcode={response.errcode} {response.errmsg}",
                    file=sys.stderr,
                )
                return EXIT_SEND_FAILED
    except WebhookTransportError as exc:
        # ⛔ 不吞：`transport.py` 已保证异常文本里没有 URL。
        print(f"发送失败：{exc}", file=sys.stderr)
        return EXIT_SEND_FAILED

    # 台账只在**真的发出去之后**才改：写着"已推送"而群里没有，比没发更糟——
    # 那会让串行闸放行下一封，而上一封根本没到收信人手里。
    ledger_path.write_text(backfilled, encoding="utf-8")
    print(f"已发送并回填台账：{number} → {_ALREADY_SENT} {today.isoformat()}")
    return EXIT_OK
