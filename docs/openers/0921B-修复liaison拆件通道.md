# [Mac]0921B 修复 liaison 拆件通道（venv 缺 python ＋ 陈旧会话锁）

## 零、为什么

`人事部#3` 的两条回件 2026-09-20 17:04 CST 已入归档并打标，回件桥起了拆件无头会话
（`data/liaison/unpack-session.lock`：`pid 12388`，`started_at 2026-09-20T09:04:09Z`），
但**至今一条都没拆**：`data/liaison/unpack-signal.json` 仍有 2 条 `pending`，
`docs/跟进信/README-跟进信清单.md` 里 `人事部#3` 仍停在第九态「📨 回件已到，待拆件」。

根因实测（`0921` Cowork 侧）：`tools/liaison/.venv/bin/` 下**只剩 `activate*` 脚本，没有 `python`**，
任何 `tools/liaison/.venv/bin/python -m tools.liaison …` 立刻 `No such file or directory`。
章程 §一.4 要求「命令缺失一律按有信号处理」，但会话连 `--probe` 都执行不了，于是卡死、锁也没释放。

🔴 **本条只修通道，⛔ 不拆件**——拆件受 `liaison-unpack` 章程约束（§〇② 明令拆件会话不得修改 `tools/`），
两件事必须分开，由下一条 `0921C` 按章程拆。

## 一、开工自核（任一不过即顶格 `OPENER_PARTIAL` 并停）

1. `ps -p 12388` **无该进程**。若进程还在 ⇒ 顶格 `OPENER_PARTIAL: 拆件会话 pid 12388 仍存活，未动` 并停，⛔ 不杀进程。
2. `ls tools/liaison/.venv/bin/ | grep -c '^python'` 为 `0`（确认确实缺）。若已为 1 ⇒ 说明别人修过，跳过动作①直接做③④。
3. `test -f tools/liaison/requirements.txt`。

## 二、动作

① 重建 venv（⛔ 不删目录，用 `--clear` 就地重建）：`python3 -m venv --clear tools/liaison/.venv`
② 装依赖：`tools/liaison/.venv/bin/pip install -q -r tools/liaison/requirements.txt`
③ 清陈旧锁（①②都过之后再做）：`rm -f data/liaison/unpack-session.lock`
④ 验证通道活了：`PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison unpack-signal --probe`
   预期输出含 `[SIGNAL]` 与 2 条 `人事部#3` pending。
   🔴 ⛔ **只 probe，不加 `--clear`**——清信号是拆件落档之后的动作，不属本条。

## 三、机器判据

```bash
set -e
cd "$(git rev-parse --show-toplevel)"
tools/liaison/.venv/bin/python -c 'import sys; sys.exit(0)'
test ! -f data/liaison/unpack-session.lock
PYTHONPATH=. tools/liaison/.venv/bin/python -m tools.liaison unpack-signal --probe | grep -q 'SIGNAL'
```

## 四、并发协议

本泳道「回件拆件」内串行：本条过了 `0921C` 才跑。⛔ 不与任何 `.51` 条目同批。

## 五、红线

⛔ 不拆件、不读归档正文、不碰 `data/liaison/unpack-signal.json`、不碰 `data/liaison/archive/`（只读都不必）；
⛔ 不改 `tools/liaison/` 下任何源码（只重建 `.venv`）；⛔ 不 `git add` 任何东西（venv 与锁都不进 git，
`git status` 有别人的改动也不管）；⛔ 不 push；⛔ 不碰 `.51`。

## 六、收口

三条机器判据全过 ⇒ **顶格** `OPENER_DONE`。任一不过 ⇒ **顶格** `OPENER_PARTIAL: <哪条没过、实测值>`。
