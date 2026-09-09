# `tools/liaison/.venv` 建立后四条测试转红——实测记录与待裁决

**产出 session**：`[Mac]0909AA`（凭据验证与 liaison-venv）
**状态**：🔴 **未修复，待 Shao Peishen 裁决**。本 session 按 opener 明令「⛔ 不改测试去迁就实现、
⛔ 不改 `session_client.py` 的常量来让它过」，只做取证，未改动任何测试或实现代码。

## 现象

在新建的 `tools/liaison/.venv`（Python 3.14.6，已装 `wecom-aibot-python-sdk==1.0.2`）里首次真跑：

```bash
PYTHONPATH=. tools/liaison/.venv/bin/python -m pytest -q tools/liaison/tests
# 4 failed, 737 passed in 10.50s
```

四条失败：

| # | 用例 | 文件 |
|---|---|---|
| 1 | `test_entrypoint_succeeds_when_credentials_present` | `test_liaison_credentials.py:139` |
| 2 | `test_entrypoint_reads_dotenv_when_process_env_is_absent` | `test_liaison_credentials.py:168` |
| 3 | `test_no_second_transaction_manager_in_source` | `test_liaison_effects.py` |
| 4 | `test_setup_logging_is_wired_exactly_once_in_the_package` | `test_liaison_logsetup.py:313` |

## 根因：两类，都是「venv 落在 `tools/liaison/` 之内」的环境效应，不是实现回归

两类根因都由失败消息**自证**，无需另做对照实验。

### 甲类（#1 #2）：测试硬编码假设 `sys.executable` 是**没装 aibot 的根 venv**

两条用例的 docstring 写明其"成功"标准是 `returncode == EXIT_SDK_UNAVAILABLE`(3)，理由是
「design D10 的依赖隔离决定了 aibot 只装在 `tools/liaison/.venv`，根 venv 里必然装不上」。

但它们用 `sys.executable` 起子进程——**在 tools venv 里跑 pytest 时，`sys.executable` 就是
tools venv 的解释器**，aibot 装得上。于是进程越过「SDK 不可用」这一关，止步于下一关：

```
E   assert 4 == 3
E   CompletedProcess(args=['/Users/paulshao/Projects/HumanResource/tools/liaison/.venv/bin/python', '-m', 'tools.liaison']...)
```

实际 stderr（exit 4）：

> HR 值守通道拒绝启动：SDK 的 connect 是协程函数（async def），不满足 run_forever 期望的
> 「阻塞到断开为止」同步调用契约：同步调用它只会返回一个协程对象，不执行任何网络操作。
> 真正的阻塞入口是 client.run()。本模块拒绝把未经验证的 async→同步适配硬接上去，
> 详见 docs/findings/2026-09-09-aibot-wsclient-表面实测.md「遗留发现」一节。

🔵 **这条 exit 4 是本轮最有价值的正面实证**：`SdkSurfaceUnverifiedError` 这道护栏在**装了真实
SDK 的环境里**确实拦住了启动，而不是只在 Fake 下成立。TD-19 描述的 async/同步接线缺口，
第一次由真实 SDK 而非源码阅读证实。

### 乙类（#3 #4）：源码扫描测试的扫描根 `LIAISON_ROOT` 现在包含 `.venv/site-packages`

两条都用 `LIAISON_ROOT.rglob("*.py")`，排除列表里只有 `tests`，**没有 `.venv`**。
建 venv 后，`tools/liaison/.venv/lib/python3.14/site-packages/**` 里的第三方代码被当成本项目
源码扫描：

- #3 报出的"白名单之外的事务管理者"全部来自 `_pytest`：
  `.venv/lib/python3.14/site-packages/_pytest/_py/path.py::read_binary` 用 `with self.open('rb'):`
  被判为"隐式提交"，等等
- #4 报 `setup_logging 的调用点不止一处：['base_command.py', '_cmd.py', '__main__.py']`——
  前两个是 **pip 的 vendored 代码**：
  `tools/liaison/.venv/lib/python3.14/site-packages/pip/_internal/cli/base_command.py`
  `tools/liaison/.venv/lib/python3.14/site-packages/pip/_vendor/cachecontrol/_cmd.py`

⚠️ 乙类的危害不止于"测试变红"：这两条断言分别是**事务归属**（工程铁律 1）与**日志接线唯一性**
的静态闸门。扫描根被污染后，它们从「守住本项目源码」退化为「对第三方库报一堆噪声」——
**闸门还在，但看闸门的人会开始习惯性忽略它的报警**，这比直接失效更危险。

## ⛔ 本 session 明确没有做的事

- 没有给 `#3 #4` 的扫描逻辑加 `.venv` 排除（改的是测试，opener 禁止）
- 没有给 `#1 #2` 改断言或加 skip 条件（同上）
- 没有动 `session_client.py` 的 `REQUIRED_CLIENT_ATTRS` / `EVENT_*` 常量
- 没有把 venv 挪到 `tools/liaison/` 之外（会与 design D10、`requirements.txt` 注释、
  既有 findings 里写死的 `tools/liaison/.venv` 路径全部冲突，属口径变更）

## 待裁决

甲乙两类的修法方向不同，建议分开裁：

- **乙类**：性质更接近"扫描根定义漏了一个目录"的缺陷，修法明确（扫描排除 `.venv`，与已有的
  排除 `tests` 同一形状）。风险低。
- **甲类**：涉及"这两条用例到底要验什么"——若目标是"凭据这关过得去"，则断言不该锁死在
  下一关的具体 exit code；若目标是"根 venv 下的完整链路"，则该用例本就不该在 tools venv 里跑。
  这是判据问题，需要拍。

## 复现命令

```bash
PYTHONPATH=. tools/liaison/.venv/bin/python -m pytest -q tools/liaison/tests
```

对照：此前该套件在**未装 SDK 的解释器**下全绿，且 `test_liaison_sdk_smoke.py` 一直处于 skip。
