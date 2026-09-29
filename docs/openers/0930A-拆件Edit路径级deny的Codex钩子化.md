[Mac]0930A-拆件Edit路径级deny的Codex钩子化
【设置】执行环境: Codex ｜ Session: 新开（run-lanes 无头起）｜ 分支: lane-0930a-codex-edit-deny ｜ worktree: ✅ 勾（写代码与配置）｜ 工作区: .claude/worktrees/lane-0930a-codex-edit-deny ｜ 派发: Codex·泳道看护（automation hr）

## 零、为什么

`AGENTS.md` §4「已知缺口」：拆件受限会话（`tools/liaison/unpack/dispatch.py` 起的 codex 会话）目前只有
workspace-write 沙箱 ＋ 章程正文 ＋ 网络沙箱三条约束，缺 Claude 侧那套「Edit(path) 白名单／deny」的
机器级路径闸。本条把它补成 Codex 原生 PreToolUse hook。

## 一、授权与边界（Shao Peishen 2026-09-30 答 1a 授权的**专门 opener**）

⛔ 只许改本文件「三、交付物」列出的文件。⛔ 不改 `.claude/skills/**`、`AGENTS.md` §1–§3 与 §5、
`docs/openers/run-lanes.sh`、`scripts/dispatcher_event.sh`。并行同伴：0930B 改
`scripts/dispatcher_answers.py`＋`pyproject.toml`＋其测试；0930C 改 `docs/roadmap/任务台账.yaml`＋新建核真文档。
⛔ 不碰它们的文件。

## 二、前置

1. `codex --version` 可用；worktree 里有 `AGENTS.md`（缺 ⇒ 登记「⏸ 留步：执行环境不可达」并停）。
2. 读官方手册 Hooks 节，确认三件事的**实测形状**（⛔ 不靠记忆）：用 openai-docs 技能拉手册
   `node ~/.codex/skills/.system/openai-docs/scripts/fetch-codex-manual.mjs`，读返回手册的「Hooks」章：
   - PreToolUse 事件 JSON 的工具名字段与参数键（如 `tool_name`／`tool_input.file_path`／`tool_input.command`）
   - 拦截输出协议（手册实测：stdout JSON `{"permissionDecision":"deny","permissionDecisionReason":"…"}`
     或 legacy `{"decision":"block","reason":"…"}`；也可 exit 2 + stderr）
   - 项目级 hooks 的加载位置（`.codex/hooks.json` 或 `.codex/config.toml` 内联），以及无头免信任旗标
     `--dangerously-bypass-hook-trust`（help 实测存在）
3. 读 `tools/liaison/unpack/dispatch.py`（`CHARTER_WRITABLE_PATHS`、`build_headless_argv_codex`、
   `_CHILD_ENV_ALLOWLIST`）与章程 §三白名单真源。

## 三、交付物

1. **`scripts/hooks/codex_unpack_guard.py`（新）**：读 stdin 的 PreToolUse 事件 JSON。
   - 未置位 `HR_LIAISON_UNPACK=1` ⇒ 静默 exit 0（放行）——⛔ 不影响任何正常会话。
   - 置位时：编辑类工具（Edit／Write／apply_patch，工具名与参数键按手册实测；apply_patch 从 patch 文本
     解析 `*** Add File:`／`*** Update File:` 的目标路径）⇒ 目标路径必须落在
     `dispatch.CHARTER_WRITABLE_PATHS`（**import 真源**，⛔ 不抄第二份）内，否则 deny；
     Bash ⇒ 命令含 `git add -A`／`git add .`／`git commit -a`／`git stash`／`git push` 之一 ⇒ deny。
   - **拦截契约（本 opener 钉死）**：deny 时把拦截 JSON 打到 **stdout** 并 exit 0；放行时 stdout 无输出、exit 0。
     理由里带命中的路径／命令与白名单出处。
2. **`.codex/hooks.json`（新）**：项目层 `PreToolUse` 两组 matcher（文件编辑类／Bash）挂该脚本，带 `timeout`；
   合法 JSON；脚本命令用绝对 git 根解析（照手册示例 `$(git rev-parse --show-toplevel)` 写法）。
3. **`tools/liaison/unpack/dispatch.py`**：codex argv 追加 `--dangerously-bypass-hook-trust`（无头无法走
   `/hooks` 信任流程，属自动化已自审 hook 源）；`_CHILD_ENV_ALLOWLIST` 加 `HR_LIAISON_UNPACK`，且
   `dispatch_headless_unpack` 起活时显式把该键置 `1`。
4. **测试**：新增 `tests/test_unpack_guard_hook.py`（白名单内放行／外 deny／未置位放行／`git push` deny／
   apply_patch 路径解析，样本事件用实测形状）；更新 `tools/liaison/tests/test_unpack_dispatch.py`
   （argv 含 bypass 旗标；child env 含 `HR_LIAISON_UNPACK=1`）。
5. **`AGENTS.md` §4**：把「已知缺口」那一条改为「已实现」并指向 hook 与测试（⛔ 只动这一条）。

## 四、机器判据

```bash
cd /Users/paulshao/Projects/HumanResource
set -e
./venv/bin/python -m pytest tests/test_unpack_guard_hook.py -q -p no:cacheprovider
./venv/bin/python -m pytest tools/liaison/tests/test_unpack_dispatch.py tools/liaison/tests/test_unpack_path_guard.py -q -p no:cacheprovider
./venv/bin/python -c "import json,pathlib;json.loads(pathlib.Path('.codex/hooks.json').read_text())"
printf '%s' '{"tool_name":"Edit","tool_input":{"file_path":"app/main.py"}}' | env HR_LIAISON_UNPACK=1 ./venv/bin/python scripts/hooks/codex_unpack_guard.py | grep -q '"deny"'
out="$(printf '%s' '{"tool_name":"Edit","tool_input":{"file_path":"docs/跟进信/回件/x.md"}}' | env HR_LIAISON_UNPACK=1 ./venv/bin/python scripts/hooks/codex_unpack_guard.py)"; [ -z "$out" ]
printf '%s' '{"tool_name":"Bash","tool_input":{"command":"git push origin main"}}' | env HR_LIAISON_UNPACK=1 ./venv/bin/python scripts/hooks/codex_unpack_guard.py | grep -q '"deny"'
out="$(printf '%s' '{"tool_name":"Edit","tool_input":{"file_path":"app/main.py"}}' | ./venv/bin/python scripts/hooks/codex_unpack_guard.py)"; [ -z "$out" ]
grep -q -- '--dangerously-bypass-hook-trust' tools/liaison/unpack/dispatch.py
grep -q 'HR_LIAISON_UNPACK' tools/liaison/unpack/dispatch.py
```

## 五、并发协议（逐字照做）

1. 只 `git add` 本 opener 明确列出的路径。⛔ 禁止 `git add -A` / `git add .` / `git commit -a` / `git stash`。
2. `git status` 里出现别人的改动是正常的，不要停下、不要问、不要顺手提交。
3. ⛔ 不要在 commit 前 pull；push 被拒才 `git pull --rebase --autostash origin main` 重试，最多 3 次。
4. 报 `.git/index.lock` 已存在 → 等 5 秒重试最多 5 次，⛔ 绝不删除该锁。

## 六、红线

- ⛔ 不改章程 §三白名单的内容（只引用），不改 `CHARTER_WRITABLE_PATHS` 的值。
- ⛔ 不让 hook 在 `HR_LIAISON_UNPACK` 未置位时产生任何输出或拦截。
- ⛔ 不用「卸载/覆盖」手段绕过 hooks 信任问题；无头会话用 `--dangerously-bypass-hook-trust` 是唯一允许路径。
- ⛔ 不写提问句（无头会话没人回答）；遇不确定 ⇒ 保守方向 + 登记留步。

## 七、收口

1. 测试全绿后在分支上 commit（只列本 opener 的路径），提交信息带 `(0930A)`。
2. 合回 main：`git -C /Users/paulshao/Projects/HumanResource merge --ff-only <分支>`；失败改
   `git merge <分支> --no-edit`（无冲突才继续）；然后 `git push origin main`（被拒按并发协议重试 ≤3 次）。
3. 🔴 反查真身：`git cherry -v main <分支>` 不允许出现 `+` 行；`git log --oneline -3` 确认提交在 main 上。
   ⛔ 确认内容真在 main 上之前不许输出 `OPENER_DONE`。
4. 顶格输出 `OPENER_DONE`；有留步 ⇒ `OPENER_PARTIAL: <一句话原因>`。
