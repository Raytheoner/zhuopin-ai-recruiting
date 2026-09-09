"""8.7·`send-followup` 子命令：把一封跟进信发进值守群，并回填台账。

**一次真网络都不发**：transport 一律注入 fake，地址用 `https://example.invalid/...`
（`.invalid` 是 RFC 2606 保留顶级域，误发也一定解析失败）。真发是 Shao Peishen 本人
在 CC 里跑 `--send` 那一条——对外发送属"不可代"项。

🔴 本文件里最重要的不是"发得出去"，是 **`--dry-run` 真的什么都不做**：那道闸是
他在真发前唯一能看清"到底会发出去什么"的地方，闸漏了他就是在盲发。
"""

from __future__ import annotations

import datetime
import pathlib
import sys

import pytest

from tools.liaison import followup
from tools.liaison.notify import transport, webhook

FAKE_WEBHOOK = "https://example.invalid/cgi-bin/webhook/send?key=fake-key-for-tests"
FIXED_DAY = datetime.date(2026, 9, 9)

LETTER = """---
title: 人事部 AI 招聘值守机制启用与配合方式
created: 2026-09-09
status: ⏳ 待你审
---

# 人事部#1 · AI 招聘值守机制启用与配合方式

汤丽萍，你好：

正文第一段。
"""

LEDGER = """# 人事部 · 跟进信清单

| 编号 | 日期 | 收信人 | 主要事项 | 交期要点 | 发送状态 |
|---|---|---|---|---|---|
| `人事部#1（待发，暂不占号）` | 2026-09-09 | 汤丽萍 | 值守机制启用 | 不催 | `🆕 待发` |
| `人事部#2（待你审，暂不占号）` | 2026-09-09 | 陈忱 | 另一件事 | 不催 | `⏳ 待你审` |
"""


class FakeTransport:
    """记账用的假体。`json_calls` 的**顺序**就是消息发出去的顺序。"""

    def __init__(self, *, json_errcodes=None, upload_ok=True):
        self.json_errcodes = list(json_errcodes or [])
        self.json_calls: list[dict] = []
        self.multipart_calls: list[dict] = []
        self.upload_ok = upload_ok

    def post_json(self, url, payload, *, timeout=transport.WEBHOOK_TIMEOUT_SECONDS):
        self.json_calls.append({"url": url, "payload": payload})
        errcode = self.json_errcodes.pop(0) if self.json_errcodes else 0
        return transport.WebhookResponse(
            errcode=errcode, errmsg="scripted", payload={"errcode": errcode}
        )

    def post_multipart(self, url, *, filename, content, timeout=transport.WEBHOOK_TIMEOUT_SECONDS):
        self.multipart_calls.append({"filename": filename, "content": content})
        if not self.upload_ok:
            return transport.WebhookResponse(
                errcode=40058, errmsg="bad media", payload={"errcode": 40058}
            )
        return transport.WebhookResponse(
            errcode=0, errmsg="ok", payload={"errcode": 0, "media_id": "MEDIA-1"}
        )

    def msgtypes(self) -> list[str]:
        return [c["payload"]["msgtype"] for c in self.json_calls]

    def markdown_bodies(self) -> list[str]:
        return [
            c["payload"]["markdown"]["content"]
            for c in self.json_calls
            if c["payload"]["msgtype"] == "markdown"
        ]


@pytest.fixture
def letters(tmp_path):
    """一封信 + 一份 docx + 台账，三件同目录（台账按信件所在目录定位）。"""
    folder = tmp_path / "跟进信"
    folder.mkdir()
    md = folder / "人事部-汤丽萍-跟进-2026-09-09-值守机制.md"
    md.write_text(LETTER, encoding="utf-8")
    docx = folder / "人事部-汤丽萍-跟进-2026-09-09-值守机制.docx"
    docx.write_bytes(b"PK\x03\x04\x89\xff\x00fake-docx-bytes")
    ledger = folder / followup.LEDGER_FILENAME
    ledger.write_text(LEDGER, encoding="utf-8")
    return {"md": md, "docx": docx, "ledger": ledger}


def run(argv, *, fake=None, env=None, today=FIXED_DAY):
    return followup.send_followup_main(
        argv,
        transport=fake if fake is not None else FakeTransport(),
        env={"HR_LIAISON_GROUP_WEBHOOK": FAKE_WEBHOOK} if env is None else env,
        today=today,
    )


# ── 第一组：--dry-run 真的什么都不做 ──────────────────────────────────────


def test_dry_run_sends_nothing_and_writes_nothing(letters, capsys):
    fake = FakeTransport()
    before = letters["ledger"].read_text(encoding="utf-8")
    code = run(
        ["--md", str(letters["md"]), "--docx", str(letters["docx"]), "--dry-run"], fake=fake
    )
    assert code == 0
    assert fake.json_calls == [], "--dry-run ⇒ ⛔ 一条消息都不许发"
    assert fake.multipart_calls == [], "--dry-run ⇒ ⛔ 一个附件都不许传"
    assert letters["ledger"].read_text(encoding="utf-8") == before, "--dry-run ⇒ ⛔ 不碰台账"


def test_dry_run_is_the_default_when_no_flag_is_given(letters):
    """默认即 dry-run：真发必须**显式**传 `--send`。

    ⛔ 不许把默认改成真发——这条 CLI 的唯一危险动作就是外发，而"手滑漏了一个
    flag"必须落在安全的那一侧。
    """
    fake = FakeTransport()
    assert run(["--md", str(letters["md"])], fake=fake) == 0
    assert fake.json_calls == []


def test_dry_run_prints_the_body_the_attachment_size_and_the_domain(letters, capsys):
    """他在真发前唯一的窗口：正文、附件名与字节数、目标域名。"""
    run(["--md", str(letters["md"]), "--docx", str(letters["docx"])])
    out = capsys.readouterr().out
    assert "正文第一段。" in out
    assert letters["docx"].name in out
    assert str(letters["docx"].stat().st_size) in out
    assert "example.invalid" in out


def test_dry_run_never_prints_the_webhook_key(letters, capsys):
    """🔴 `?key=` 那一段**本身就是凭据**，而 dry-run 的输出正是要被贴进聊天的东西。"""
    run(["--md", str(letters["md"]), "--docx", str(letters["docx"])])
    captured = capsys.readouterr()
    assert "fake-key-for-tests" not in captured.out + captured.err
    assert "key=" not in captured.out + captured.err


# ── 第二组：正文的形状 ────────────────────────────────────────────────────


def test_frontmatter_never_reaches_the_group_message(letters):
    """⛔ frontmatter 绝不进群消息：`status: ⏳ 待你审` 发进群里是把内部状态泄给收信人。"""
    fake = FakeTransport()
    run(["--md", str(letters["md"]), "--send"], fake=fake)
    body = fake.markdown_bodies()[0]
    assert "status:" not in body
    assert "待你审" not in body
    assert "---" not in body.splitlines()[0]
    assert body.startswith("# 人事部#1 ·"), body[:40]
    assert "正文第一段。" in body


# ── 第三组：--send 的顺序与幂等 ───────────────────────────────────────────


def test_send_posts_the_markdown_first_then_the_file(letters):
    """顺序钉死为「先正文、后附件」。

    ⚠️ 与 `DegradedDelivery` 的「先附件、后提要」**刻意相反**，理由不同：那边的
    提要声称"完整正文见附件"，附件没上去就成了一句谎；这边发出去的 markdown
    **本身就是完整正文**，附件只是同一内容的 Word 版。附件失败留下的是一封
    收得到、读得懂的信，不是谎。
    """
    fake = FakeTransport()
    assert run(["--md", str(letters["md"]), "--docx", str(letters["docx"]), "--send"], fake=fake) == 0
    assert fake.msgtypes() == ["markdown", "file"]
    assert len(fake.multipart_calls) == 1


def test_docx_bytes_reach_the_transport_unmangled(letters):
    """docx 是 zip：任何 decode 往返都会毁掉它，而毁掉的 zip 只换来一个数字错误码。"""
    fake = FakeTransport()
    run(["--md", str(letters["md"]), "--docx", str(letters["docx"]), "--send"], fake=fake)
    assert fake.multipart_calls[0]["content"] == letters["docx"].read_bytes()
    assert fake.multipart_calls[0]["filename"] == letters["docx"].name


def test_send_without_docx_posts_only_the_markdown(letters):
    fake = FakeTransport()
    assert run(["--md", str(letters["md"]), "--send"], fake=fake) == 0
    assert fake.msgtypes() == ["markdown"]
    assert fake.multipart_calls == []


def test_send_backfills_the_ledger_row_by_number(letters):
    """按**编号**定位那一行，改状态 + 去掉编号列的括注。"""
    run(["--md", str(letters["md"]), "--send"], fake=FakeTransport())
    text = letters["ledger"].read_text(encoding="utf-8")
    assert "`人事部#1`" in text
    assert "人事部#1（待发，暂不占号）" not in text
    assert "✅ 已推送 2026-09-09" in text
    assert "🆕 待发" not in text


def test_backfill_never_touches_another_persons_row(letters):
    """并行泳道的同族要求：只改自己那一行，⛔ 别人的行一个字节都不动。"""
    run(["--md", str(letters["md"]), "--send"], fake=FakeTransport())
    text = letters["ledger"].read_text(encoding="utf-8")
    assert "`人事部#2（待你审，暂不占号）`" in text
    assert "⏳ 待你审" in text


def test_second_run_is_a_no_op_and_does_not_resend(letters, capsys):
    """幂等：已是 `✅ 已推送` 的行再跑一次 ⇒ 不发、不改日期、退 0。

    🔴 判定必须发生在**发送之前**——放在发送之后就成了"每跑一次多发一条"，
    而群里那条消息撤不回来。
    """
    run(["--md", str(letters["md"]), "--send"], fake=FakeTransport())
    first = letters["ledger"].read_text(encoding="utf-8")

    fake = FakeTransport()
    code = run(
        ["--md", str(letters["md"]), "--send"],
        fake=fake,
        today=datetime.date(2026, 12, 25),  # 换一天：日期被改动就会露出来
    )
    assert code == 0
    assert fake.json_calls == [], "已是终态 ⇒ ⛔ 不重复发消息"
    assert fake.multipart_calls == []
    assert letters["ledger"].read_text(encoding="utf-8") == first, "⛔ 不改日期"
    assert "已是终态，不重复回填" in capsys.readouterr().out


# ── 第四组：拒发的四条出口 ────────────────────────────────────────────────


def test_missing_credential_exits_3_and_sends_nothing(letters, capsys):
    fake = FakeTransport()
    code = run(["--md", str(letters["md"]), "--send"], fake=fake, env={})
    assert code == followup.EXIT_MISSING_CREDENTIALS
    assert fake.json_calls == [] and fake.multipart_calls == []
    assert "HR_LIAISON_GROUP_WEBHOOK" in capsys.readouterr().err


def test_oversized_attachment_is_refused_before_the_markdown_goes_out(letters, monkeypatch):
    """超限 ⇒ **一条消息都不发**。

    ⛔ 不许先把正文发出去再发现附件太大：那会留下一封发了一半的信，而"补发附件"
    没有幂等路径——台账那一行已经不是终态，重跑会把正文再发一遍。
    """
    monkeypatch.setattr(webhook, "MAX_ATTACHMENT_BYTES", 4)
    fake = FakeTransport()
    code = run(
        ["--md", str(letters["md"]), "--docx", str(letters["docx"]), "--send"], fake=fake
    )
    assert code == followup.EXIT_BAD_ARGS
    assert fake.json_calls == [], "附件超限 ⇒ ⛔ 正文也不许发"
    assert fake.multipart_calls == []


def test_oversized_attachment_is_also_refused_in_dry_run(letters, monkeypatch, capsys):
    """dry-run 要能**提前**报出来，否则这道闸只在真发那一刻才起作用。"""
    monkeypatch.setattr(webhook, "MAX_ATTACHMENT_BYTES", 4)
    assert run(["--md", str(letters["md"]), "--docx", str(letters["docx"])]) == followup.EXIT_BAD_ARGS


def test_send_failure_exits_4_and_leaves_the_ledger_alone(letters, capsys):
    """发送失败 ⇒ 退 4，台账**不动**——台账写着"已推送"而群里没有，比没发更糟。"""
    before = letters["ledger"].read_text(encoding="utf-8")
    fake = FakeTransport(json_errcodes=[93000])
    code = run(["--md", str(letters["md"]), "--send"], fake=fake)
    assert code == followup.EXIT_SEND_FAILED
    assert letters["ledger"].read_text(encoding="utf-8") == before
    assert "93000" in capsys.readouterr().err, "错误码必须进日志，⛔ 不吞"


def test_upload_failure_exits_4(letters):
    fake = FakeTransport(upload_ok=False)
    code = run(
        ["--md", str(letters["md"]), "--docx", str(letters["docx"]), "--send"], fake=fake
    )
    assert code == followup.EXIT_SEND_FAILED


def test_missing_md_file_exits_2(tmp_path):
    assert run(["--md", str(tmp_path / "没有这封信.md"), "--send"]) == followup.EXIT_BAD_ARGS


def test_letter_without_a_number_heading_exits_2_without_sending(letters):
    """抬头里没有编号 ⇒ 台账无从定位 ⇒ 拒发，⛔ 不"先发了再说"。"""
    letters["md"].write_text("---\ntitle: x\n---\n\n# 没有编号的抬头\n\n正文\n", encoding="utf-8")
    fake = FakeTransport()
    assert run(["--md", str(letters["md"]), "--send"], fake=fake) == followup.EXIT_BAD_ARGS
    assert fake.json_calls == []


def test_number_absent_from_the_ledger_exits_2_without_sending(letters):
    """台账里没有这一行 ⇒ 拒发。发出去却无处回填 ＝ 台账当场失真。"""
    letters["ledger"].write_text(LEDGER.replace("人事部#1", "人事部#9"), encoding="utf-8")
    fake = FakeTransport()
    assert run(["--md", str(letters["md"]), "--send"], fake=fake) == followup.EXIT_BAD_ARGS
    assert fake.json_calls == []


def test_the_cli_takes_no_webhook_argument(letters):
    """6.7 的同族要求：凭据只从环境读，⛔ 不给命令行传地址的口子。

    命令行参数会进 shell 历史、进 `ps` 的输出、进被贴出来的报错——那三处都是
    凭据最不该出现的地方。
    """
    fake = FakeTransport()
    with pytest.raises(SystemExit):
        followup.build_parser().parse_args(["--md", "x", "--webhook", FAKE_WEBHOOK])


# ── 第五组：子命令真的接进了 `python -m tools.liaison` ────────────────────


def test_send_followup_is_reachable_as_a_subcommand(letters):
    """真起一个进程跑 `python -m tools.liaison send-followup`。

    ⛔ 不能只测 `send_followup_main`：接线断了那个函数照样绿，而他在 CC 里敲的是
    模块入口那一行。走 **dry-run**，所以这个子进程一个字节都不发。
    """
    import os
    import subprocess

    repo_root = pathlib.Path(__file__).resolve().parents[3]
    env = dict(os.environ)
    env["HR_LIAISON_GROUP_WEBHOOK"] = FAKE_WEBHOOK
    env["PYTHONPATH"] = str(repo_root)
    result = subprocess.run(
        [sys.executable, "-m", "tools.liaison", "send-followup",
         "--md", str(letters["md"]), "--docx", str(letters["docx"]), "--dry-run"],
        cwd=repo_root, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout
    assert "fake-key-for-tests" not in result.stdout + result.stderr


def test_send_followup_branch_short_circuits_before_credentials_are_required():
    """结构断言：这条分支必须排在 `main()` 之前。

    `main()` 首先做的是 `load_credentials()`（BOT_ID 等企微凭据）。发跟进信 ⛔ 不
    需要那套——它不建长连接。分支排到后面去，会让一台还没配 BOT_ID 的机器
    永远发不了跟进信，而报错说的是"缺 BOT_ID"，与真实原因毫无关系。
    """
    source = (pathlib.Path(followup.__file__).parent / "__main__.py").read_text(encoding="utf-8")
    lines = source.splitlines()
    # ⛔ 按**行**定位，不用 str.index 找子串：cleanup 那段注释的正文里就写着
    # 「`raise SystemExit(main())` 不会先跑掉」，子串匹配会命中那句散文，
    # 于是这条断言测的是注释的位置，不是代码的位置。
    branch = next(i for i, ln in enumerate(lines) if 'sys.argv[1] == "send-followup"' in ln)
    entry = next(i for i, ln in enumerate(lines) if ln.strip() == "raise SystemExit(main())")
    assert branch < entry, "send-followup 分支必须排在 `raise SystemExit(main())` 之前"


def test_the_subcommand_reads_the_webhook_out_of_dotenv(letters, tmp_path):
    """凭据在 `.env` 里就要认。

    🔴 他机器上的 `HR_LIAISON_GROUP_WEBHOOK` 就在仓库根的 `.env`（守护进程那条路
    靠 `main()` 里的 `load_dotenv_into_environ` 读它）。这条子命令短路在 `main()`
    之前，⇒ 不自己读一次 `.env`，他在配置完全正确的机器上跑也会得到"缺凭据"，
    而那个报错指向的原因是错的。
    """
    import os
    import subprocess

    repo_root = pathlib.Path(__file__).resolve().parents[3]
    dotenv = tmp_path / "fake.env"
    dotenv.write_text(f"HR_LIAISON_GROUP_WEBHOOK={FAKE_WEBHOOK}\n", encoding="utf-8")

    env = dict(os.environ)
    env.pop("HR_LIAISON_GROUP_WEBHOOK", None)  # 只能从 .env 拿到，⛔ 不许走进程环境
    env["HR_LIAISON_DOTENV_PATH"] = str(dotenv)
    env["PYTHONPATH"] = str(repo_root)
    result = subprocess.run(
        [sys.executable, "-m", "tools.liaison", "send-followup",
         "--md", str(letters["md"]), "--dry-run"],
        cwd=repo_root, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"stdout={result.stdout} stderr={result.stderr}"
    assert "example.invalid" in result.stdout
    assert "fake-key-for-tests" not in result.stdout + result.stderr
