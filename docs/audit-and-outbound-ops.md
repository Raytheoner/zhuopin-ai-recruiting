# 留痕与外发门禁：运维口径

对应变更包 `ai-audit-trail-and-outbound-gate` 的 7.3。读者是在 `.51` 上做运维的人，
不是写这个模块的人——所以这里只写"在机器上怎么做、做错了什么症状"，不解释实现。

⚠️ **本页所有路径与行为都是 2026-08-28 从 `app/config.py` / `app/audit/sinks.py`
源码核过的真值**，不是从设计文档抄的。改了那两个文件请回头改本页。

**一句话优先级**：本页第四节（开关文件的编码约束）是本页存在的首要理由。
`.51` 是 Windows，用错写法会让总开关**静默不生效且不报错**——出事时你以为放行了，
其实全拦着。U5 接线前必须确认这一节已被执行本机操作的人读过。

---

## 一、审计 JSONL 的路径与备份

### 1.1 路径真值

| 项 | 值 | 出处 |
|---|---|---|
| 配置字段 | `Settings.audit_jsonl_path` | `app/config.py` |
| 默认值 | `data/audit/decisions.jsonl` | 同上 |
| 解析方式 | **相对进程工作目录**，与 `db_path` 同一约定 | 同上 |
| 环境变量覆盖 | `AUDIT_JSONL_PATH`（`.env` 或进程环境） | pydantic-settings 按字段名大写取 |
| `.51` 上的实际绝对路径 | `C:\apps\zhuopin-recruit-agent\data\audit\decisions.jsonl` | 计划任务 `-WorkingDirectory $AppDir`（`deploy-server.ps1:95`），`$AppDir` 默认 `C:\apps\zhuopin-recruit-agent`（`deploy-server.ps1:15`） |

⚠️ **"相对进程工作目录"是这里唯一的坑**：手工从别的目录敲命令拉起服务，JSONL 就会
落到那个目录下的 `data/audit/`，而不是你以为的那个文件。链不会报错——它只是开了一条
新链。排查"记录不见了"时，第一件事是确认进程的工作目录，不是去翻文件内容。

### 1.2 发版不会覆盖它

`sync-to-server.sh` 用**白名单**推送（`SYNC_PATHS`：`app` `scripts` `requirements.txt`
`pyproject.toml` `.env.example` `deploy-server.ps1` `sync-to-server.sh`），且
`EXCLUDE_NAMES` 里显式列了 `data`。所以：

- ✅ 发版**不会**覆盖、清空或回滚 `data/audit/decisions.jsonl`
- ✅ 开发机上的 JSONL 也**不会**被推上去污染生产链
- ⛔ 反过来也成立：**生产的 JSONL 永远不会被同步回开发机**（生产日志含个人信息，
  反向同步构成一次未经授权的个人信息转移）。要看生产的链，去 `.51` 上跑校验，
  ⛔ 不要把文件拉回本地。

### 1.3 备份口径

**当前状态：⏸ 留步——`.51` 上是否已有覆盖 `C:\apps\zhuopin-recruit-agent\data\`
的既有备份任务，本页无法确认（需要 `.51` 访问，本轮不连服务器）。**

design.md 的 Open Questions 把"是否纳入 `.51` 备份范围"留给了实现阶段按运维便利定。
在有人上机确认之前，按下面这条保守口径执行：

- **备份的对象是 `data/` 整个目录**，不是单独一个 JSONL 文件。SQLite 是可查询真身、
  JSONL 是防篡改镜像，**两者互为独立证据**，只备份其中一个等于把双证据降级成单证据。
- **备份必须是复制，不能是移动/轮转/截断。** JSONL 是 append-only 的哈希链，任何
  "归档旧行、只留最近 N 行"的处理都会让链从截断点起永久断裂，且**写入侧不报错**。
  ⛔ 不要给它配日志轮转（`log_retention_days` 是给 `logs/` 的，不管 `data/audit/`）。
- 备份之后**在副本上跑一次链校验**（第二节），确认拷贝没坏。

**待办（需 `.51` 上机确认后回填本节）**：① 现有备份任务是否已覆盖 `data/`；
② 若无，加一个把 `data/` 拷到备份盘的计划任务；③ 留存年限——这条要等法务口径
（已登记在 `06-企业AI转型资产借鉴清单.md` §7.1，是 M2 门槛项，不是本变更范围）。

---

## 二、链校验 `verify_chain()` 怎么手动跑

### 2.1 开发机（Mac / Linux）

在仓库根目录，整条复制：

```bash
./venv/bin/python -c "
from pathlib import Path
from app.audit.sinks import JsonlChainSink
from app.config import get_settings
p = Path(get_settings().audit_jsonl_path)
print('path:', p.resolve(), '| exists:', p.exists())
print(JsonlChainSink(p).verify_chain())
"
```

实跑输出（2026-08-28，开发机上该文件尚未生成）：

```
path: /Users/paulshao/Projects/HumanResource/data/audit/decisions.jsonl | exists: False
ChainVerification(ok=True, total=0, broken_at=None, error=None, tail_hash=None)
```

### 2.2 `.51`（Windows）

同一段代码，换解释器路径，**且必须先 `cd` 到 `C:\apps\zhuopin-recruit-agent`**
（否则相对路径解析到别处，见 1.1）：

```powershell
cd C:\apps\zhuopin-recruit-agent
.venv\Scripts\python.exe -c "from pathlib import Path; from app.audit.sinks import JsonlChainSink; from app.config import get_settings; p = Path(get_settings().audit_jsonl_path); print('path:', p.resolve(), '| exists:', p.exists()); print(JsonlChainSink(p).verify_chain())"
```

⏸ 留步：这条 `.51` 上的实跑输出本轮无法提供（不连服务器）。首次上机跑通后把输出
贴回本节。

### 2.3 怎么读返回值

`ChainVerification(ok, total, broken_at, error, tail_hash)`：

| 输出 | 含义 | 该做什么 |
|---|---|---|
| `ok=True, total=N>0` | N 行全部对得上 | 记下 `tail_hash`，见 2.4 |
| **`ok=True, total=0`** | **文件不存在或是空的** | ⚠️ **这不是"验证通过"**。是"没有东西可验"。生产上出现这个，先查 1.1 的工作目录问题，再查服务是否真的写过留痕 |
| `ok=False` + `broken_at=K` | 第 K 行与上一行的落盘字节对不上 | 见下 |
| `ok=False` + `error` 含"不是合法的 UTF-8 JSON" / "不是 JSON 对象" | 有人往文件里 append 了垃圾，或文件被文本编辑器改坏 | 同下 |

实跑演示（在临时文件上写 3 行、再改中间那行的内容）：

```
--- 正常链 ---
ChainVerification(ok=True, total=3, broken_at=None, error=None, tail_hash='ce0be95f138dea7652cd6390e221202d3c73b7bb48966a52160af2832399b25a')
--- 篡改第 2 行后 ---
ChainVerification(ok=False, total=3, broken_at=3, error='第 3 行的 prev_hash 与上一行落盘字节的 SHA-256 不一致：期望 0c8de2ce…81bde2，实得 408441b7…815042', tail_hash=None)
```

⚠️ **`broken_at` 指的是"第一个对不上的行"，被改的通常是它的上一行**——上例改的是
第 2 行，报的是第 3 行。哈希链只能由后继来暴露前驱。

### 2.4 校验报断链之后：⛔ 不要"修好"它

- ⛔ **禁止编辑 JSONL 让它重新通过校验。** 有写权限的人从断点往后重算全部
  `prev_hash` 就能让链恢复"通过"——那等于亲手销毁这份证据的全部价值。
- ✅ 正确处置：**原样保留文件**，另存一份副本，去 SQLite 侧（`analysis_run` /
  `criterion_score`）比对同期记录，把差异登记成事件。两套介质同时被改才能无痕，
  所以对账才是断链之后唯一有意义的动作。
- ✅ 缺行的补齐方式是**在链尾 append 一条 `type=backfill` 的补录事件**指向缺失的
  `analysis_run.id`（design D1）。⛔ 不允许把缺的行插回原位——插回必断链。

### 2.5 已知边界（不是 bug）

- **检不出最后一行被改**：它没有后继来暴露它。这是哈希链的固有性质。`tail_hash`
  返回出来就是给外部锚定用的（定期把 `tail_hash` 记到别处），本层不做锚定。
- **第 1 行的 `prev_hash` 取值不校验**：它没有前驱。第 2 行起缺 `prev_hash` 直接
  判断链（否则删光全文件的 `prev_hash` 字段就能让每行都被当成合法起点）。
- **只有进程内互斥**，假设单进程部署。多进程会断链，已登记 `docs/tech-debt.md` TD-7。

---

## 三、`CANDIDATE_OUTBOUND_ENABLED` 的开关流程

### 3.1 取值优先级（前者存在即短路）

1. **开关文件** `Settings.candidate_outbound_switch_file`，默认 `data/candidate_outbound.switch`
   （相对工作目录 → `.51` 上是 `C:\apps\zhuopin-recruit-agent\data\candidate_outbound.switch`）
2. **环境变量** `CANDIDATE_OUTBOUND_ENABLED`
3. **基线值** `Settings.candidate_outbound_enabled`，默认 `False`

只有 `1` / `true` / `yes` / `on`（大小写不敏感，按第一行非空内容判定）算"开"。
**其余一切——拼错、空串、空文件、读不到、文件坏——一律算关。未知即拦截。**

热改文件的理由：`.51` 是 Windows 计划任务拉起的单进程，改环境变量要重启服务，而
那台机器上还跑着另外 7 个服务。出事要能立刻全拦，改一个文件就够，不重启生效
（Shao Peishen 2026-08-26 拍板）。

### 3.2 全拦（出事时的第一动作）

```powershell
[System.IO.File]::WriteAllText('C:\apps\zhuopin-recruit-agent\data\candidate_outbound.switch', 'false')
```

立刻生效，不用重启。**⛔ 不要用"删掉开关文件"来关**——删掉是降级去看环境变量和
基线值，那两层现在是关的、但将来可能被改，删文件等于把结果交给别人。写 `false`
是显式的、优先级最高的关。

### 3.3 放行（需要有人对这次放行负责）

```powershell
[System.IO.File]::WriteAllText('C:\apps\zhuopin-recruit-agent\data\candidate_outbound.switch', 'true')
```

**放行属于 CLAUDE.md 决策代理表的不可代项**（"候选人对外通道的开关：一次性邀请
链接发放、拒信/邀约对外发送"）。执行前必须有 Shao Peishen 本人的明确指示，代理人
不能代批，也没有"紧急例外"口子。执行后当次留痕：谁、什么时候、依据哪一条指示。

**改完必须验证**——不验证等于没改，见第四节：

```powershell
cd C:\apps\zhuopin-recruit-agent
.venv\Scripts\python.exe -c "from app.config import is_candidate_outbound_enabled; print(is_candidate_outbound_enabled())"
```

打印 `True` 才算开成功。打印 `False` 而你以为写的是 `true` → 100% 是编码写错了。

### 3.4 为什么不提供"一键放行全部"

`design.md`「迁移计划 · 回滚策略」逐字：

> 门禁的回滚要注意：关闭 `CANDIDATE_OUTBOUND_ENABLED` 是"更安全"的方向（全拦），
> 真要恢复无门禁投递必须显式移除门禁节点——**不提供"一键放行全部"的配置项**，
> 避免它成为红线的旁路。

拆开说：

- 这个开关是**不对称**的。关 = 全拦，是安全方向，任何人任何时候都可以关。
  开 = 让待审批队列里的东西可以往外走，**仍然要逐条过人工确认**，开关不替代确认。
- 一个"跳过全部待审批、直接放行"的配置项，等于给合规红线开了一个配置级旁路：
  「AI 只做排序推荐，不做自动淘汰，淘汰必须有人工确认节点并留痕」这条红线，
  会变成"取决于某个配置项当前的值"。红线不能是可配置的。
- 所以真要回到无门禁投递，唯一的路径是**显式移除门禁节点**——一次代码改动、
  一次 review、一条 commit，有作者有时间有理由。这个成本是刻意的：它让"绕过门禁"
  从一个运维动作变成一次需要署名的工程决定。

---

## 四、🔴 开关文件的编码约束（本页首要理由）

**2026-08-27 Shao Peishen 拍板取方案 (b)：不改代码，把约束写进文档。**
改代码去剥 BOM / 认 UTF-16 属于"在合规开关上放松"，是不可代项，他明确选择不改。

### 4.1 唯一允许的写法

```powershell
[System.IO.File]::WriteAllText('C:\apps\zhuopin-recruit-agent\data\candidate_outbound.switch', 'true')
```

`.NET` 的 `File.WriteAllText(path, contents)` 默认写 **UTF-8 无 BOM**，这是唯一能被
`_read_switch_file()` 正确读到的编码。

⚠️ **路径必须写绝对路径。** `[System.IO.File]::` 是 .NET 调用，相对路径按 .NET 的
当前目录解析，**不是** PowerShell 的 `$PWD`——在 PowerShell 里 `cd` 过去再写相对
路径，文件会落到别的地方，且不报错。

### 4.2 ⛔ 禁止的写法

| 写法 | 实际落盘 | 结果 |
|---|---|---|
| `'true' \| Out-File $path` | UTF-16LE + BOM | ❌ 静默失效 |
| `'true' > $path` | UTF-16LE + BOM（`>` 是 `Out-File` 的别名） | ❌ 静默失效 |
| `'true' >> $path` | 同上 | ❌ 静默失效 |
| 记事本 → 另存为 → 编码选"UTF-8" | UTF-8 **带 BOM**（Win10 旧版记事本的"UTF-8"就是带 BOM 的） | ❌ 静默失效 |
| `[System.IO.File]::WriteAllText($path,'true')` | UTF-8 无 BOM | ✅ |

> PowerShell 7+ 的 `Out-File` 默认已改成 UTF-8 无 BOM，PowerShell 5.1（Windows
> Server 自带的那个）仍是 UTF-16LE。⛔ **不要依赖"这台机器上的 PS 版本可能是新的"**
> 来赌 `Out-File` 能用——赌错的症状是静默失效，而你没有任何提示。只用 4.1 那一种。

### 4.3 症状：静默不生效，且不报错

`_read_switch_file()` 用 `read_text(encoding="utf-8")`，**不剥 BOM、不认 UTF-16**：

- **UTF-8 带 BOM** → 解出来是 `"\ufefftrue"`。`str.strip()` 不剥 `\ufeff`（它不是
  Python 认的空白字符），所以比对 `_TRUTHY` 不中 → **判定为关**。
- **UTF-16LE** → 头两字节 `\xff\xfe` 不是合法 UTF-8 → `UnicodeDecodeError` →
  内部转成 `_SwitchFileBroken` → **判定为关**（且不降级去看环境变量：一个坏掉的
  开关文件不该让 `.env` 说开就开）。
- 两种情况都**不抛异常、不打日志、不返回错误**——`is_candidate_outbound_enabled()`
  契约上就不允许抛异常，配置崩了一律返回 `False`。

**所以方向是 fail-closed：拦住了，但打不开。** 这是设计上正确的方向，代价是"打不开"
这件事没有任何症状可看——外发就是不走，而开关文件躺在那里，内容用记事本打开
看起来明明白白写着 `true`。

**这就是 3.3 那条"改完必须跑一次 `print(is_candidate_outbound_enabled())`"存在的
全部理由。** 眼睛看文件内容是验证不了的。

### 4.4 实测证据（2026-08-28，本机 Python 直接喂字节）

```
True   <- UTF-8 无 BOM (WriteAllText)          bytes=b'true'
False  <- UTF-8 带 BOM (记事本另存)             bytes=b'\xef\xbb\xbftrue'
False  <- UTF-16LE 带 BOM (Out-File 默认)       bytes=b'\xff\xfet\x00r\x00u\x00e\x00'
True   <- UTF-8 无 BOM + CRLF                   bytes=b'true\r\n'
```

最后一行是好消息：**行尾 CRLF 不影响**（按行 `strip()` 后比对），Windows 的换行
习惯不会额外坑一次。坑只在 BOM 和 UTF-16 两处。

### 4.5 怎么当场确认文件编码对不对

```powershell
Format-Hex 'C:\apps\zhuopin-recruit-agent\data\candidate_outbound.switch' -Count 8
```

头几个字节应当直接是 `74 72 75 65`（`true`）。看到 `EF BB BF` 是 UTF-8 BOM，
看到 `FF FE` 是 UTF-16LE——两种都要按 4.1 重写一遍。

---

## 五、⏸ 留步清单（需要 `.51` 访问才能闭合）

本轮 opener 明确不连服务器，以下三项如实登记，**不得当作已完成**：

1. **1.3 备份**：`.51` 上是否已有覆盖 `C:\apps\zhuopin-recruit-agent\data\` 的
   备份任务，未确认；若无，需新增。
2. **2.2 链校验**：✅ 已闭合（2026-09-03 三次实跑，见下）——`.51` 完成重新部署后
   命令正常执行，返回 `ok=True, total=0`（`decisions.jsonl` 尚不存在，属"还没有
   东西可验"，非"验证失败"，读法见 §2.3）。此前两轮的 `ModuleNotFoundError` /
   `CommandNotFoundException` 均已随部署缺口一并解决。
3. **4.x 编码约束 + 3.3 应用层校验**：✅ 已闭合（2026-09-03 三次实跑，见下）——
   四步全部打出预期布尔值，"文件 → 应用层"链路首次被真正跑通，产线开关确认
   处于关闭态（安全）。

   🔴 **验证必须走「开→验→关→再验」四步，⛔ 不是跑完 3.3 就完事**（2026-08-31 补）：

   | 步 | 动作 | 期望 |
   |---|---|---|
   | 1 | 按 4.1 写 `'true'` | —— |
   | 2 | 跑 3.3 的验证命令 | 打印 **True**。打印 False ＝ 编码写错了 |
   | 3 | 按 3.2 写回 `'false'` | —— |
   | 4 | **再跑一次**验证命令 | 打印 **False**，确认已恢复安全态 |

   **为什么非得经过 `true` 态**：基线值本身就是 `False`（§3.1 第 3 层），
   写 `'false'` 时编码坏了也照样打印 `False`——**分辨不出链路通没通**。
   只有 `true` 能证伪。这就是本条无法"安全地绕过去"的原因。

   ⚠️ **第 1–3 步之间，生产上的候选人对外通道是真开着的。** 当前 U5 尚未接线、
   `TD-8` 已记明生产无 outbound 调用方，所以窗口内实际发不出东西；
   但**这不改变它属于 `CLAUDE.md` 不可代项的性质**——须 Shao Peishen 本人明确指示后执行，
   当次留痕「谁、什么时候、依据哪一条指示、第 4 步的实际输出」。
   ⛔ 第 4 步没跑出 `False` 之前，这件事不算完。

   **2026-08-31 实跑记录 ｜ ⏸ 仍未闭合（环境缺口，非编码问题）**

   执行人：`[Mac]0831A` session ｜ 依据：Shao Peishen 2026-08-31 明确指示

   | 步 | 期望 | 实际 |
   |---|---|---|
   | 1（写 `true`） | —— | 静默成功 |
   | 2（验证） | 打印 `True` | ❌ 未打印布尔值，`CommandNotFoundException`（见下） |
   | 3（写回 `false`） | —— | 静默成功，已在步骤 2 报错后**立即**执行 |
   | 4（再验证） | 打印 `False` | ❌ 与步骤 2 同一报错 |

   步骤 2 / 4 报错原文（两次完全一致，节选非乱码部分）：

   ```
   .\venv\Scripts\python.exe : ObjectNotFound: (.\venv\Scripts\python.exe:String) []
   CommandNotFoundException
   ```

   **根因已定位，与第四节的编码约束无关**：`Test-Path 'C:\apps\zhuopin-recruit-agent'`
   → `True`（目录存在），但 `Test-Path '...\venv\Scripts\python.exe'` → `False`——
   验证脚本本身跑不起来，不是开关文件被 BOM/UTF-16 坑了。

   🔴 **2026-09-03 订正：上面那句「`.51` 该路径下没有 venv」是错的，venv 一直在。**
   **真因是本页命令少写一个点** —— 目录名是 **`.venv`**（带点），本页原来写成 `.\venv\`。
   `0831A` 的 `Test-Path` 诊断动作对，但跟着错的路径查，于是得出"没有 venv"。

   三条独立证据：

   1. `deploy-server.ps1:31`（建 venv 的真源）：`$venvPath = Join-Path $AppDir ".venv"`
   2. `docs/findings/2026-08-20-51整机重启验证-重启前采集.md` —— 从 `.51` **实机**取的计划任务
      `Execute` = `C:\apps\zhuopin-recruit-agent\.venv\Scripts\uvicorn.exe`
   3. `.51:8095` 服务一直活着 ⇒ 必然有个可用的 Python 在跑

   ⚠️ **同一个错在本页出现两处**，`§2.2 链校验`（本页第二节）的命令也是 `.\venv\`——
   这很可能就是留步清单第 2 项「链校验实跑输出一直取不到」的同一个根因。
   两处已于 2026-09-03 一并改为 `.venv\Scripts\python.exe`。

   📌 **教训**：`0831A` 报回来的是 `CommandNotFoundException`，一个**环境缺口形态**的报错，
   于是诊断方向整个偏到"服务器上缺东西"，没人回头核一遍命令字符串本身。
   ⇒ 报错说"找不到 X"时，**先核 X 的拼写与真源是否一致**，再去查环境。
   本例的真源（`deploy-server.ps1`）就在本仓库里，一条 grep 的事。

   **当前安全态已用另一种方式确认**：直接 `Get-Content` 开关文件（不经过应用层
   `is_candidate_outbound_enabled()`）读出原始内容是 `false`——**产线开关目前确实
   处于关闭态**。但这条确认绕开了应用层解析，不能替代 3.3 要求的校验，所以本项
   **不算闭合**：四步验证要证明的是"文件 → 应用层"这条链路通不通，而这条链路
   本身没被跑通过。

   **U5 接线前还差**（2026-09-03 订正后）：venv 一直在，不需要创建或迁移——
   只差**用改对的路径补跑一次本节四步**，取得真正来自 `is_candidate_outbound_enabled()`
   的 True/False。顺带把 §2.2 链校验也用 `.venv\` 重跑一次，多半能一并闭合留步第 2 项。

   **2026-09-03 实跑记录（路径订正后重跑）｜ ⏸ 仍未闭合（部署缺口，非路径/编码问题）**

   执行人：`[Mac]0903A` session ｜ 依据：Shao Peishen 2026-09-03 明确指示

   先证伪：`Test-Path '...\.venv\Scripts\python.exe'` → `True`（09-03 的路径订正确认有效）。

   | 步 | 期望 | 实际 |
   |---|---|---|
   | 1（写 `true`） | —— | 静默成功 |
   | 2（验证） | 打印 `True` | ❌ `ImportError: cannot import name 'is_candidate_outbound_enabled' from 'app.config'` |
   | 3（写回 `false`） | —— | 静默成功，已在步骤 2 报错后**立即**执行 |
   | 4（再验证，同步骤 2 命令） | 打印 `False` | ❌ 与步骤 2 报同一个 `ImportError`（同一根因，非偶发） |

   **真实安全态已独立确认**（绕开报错的应用层入口，直接读开关文件原始内容）：

   ```powershell
   Get-Content 'C:\apps\zhuopin-recruit-agent\data\candidate_outbound.switch' -Raw
   ```

   输出：`false`。**产线开关当前处于关闭态。**

   **根因已定位，与路径拼写、编码均无关**：`.51` 上部署的 `app/config.py`
   **压根不存在 `candidate_outbound` 相关的任何字段或函数**——不是命令跑错，
   是**代码本身没有部署过去**：

   | 检查 | 结果 |
   |---|---|
   | `.51` 上 `app\config.py` 最后修改时间 | `2026-08-19 21:24:39` |
   | `.51` 上 `app\config.py` 行数 | 34 行（本仓库当前 186 行） |
   | `.51` 上 `app\config.py` 是否含 `candidate_outbound` / `audit_jsonl_path` | 否，一处都没有 |
   | `.51` 上 `app\audit\sinks.py` 是否存在 | `Test-Path` → `False` |
   | 引入该功能的提交 | `4890fab`（加齐审计 JSONL 路径与外发总开关）／`eb338b0`／`49c5e4c`，均 **2026-08-27** |

   即 `.51` 自 08-19 起未再同步过代码，而整个 `ai-audit-trail-and-outbound-gate`
   功能（含开关文件读取逻辑、审计 JSONL 落盘）是 08-27 才落进仓库的——**`.51` 跑的
   是这个功能诞生前八天的旧代码**，本节四步验证在这份部署上无论怎么改开关文件、
   无论路径写不写对，都不可能通过。

   §2.2 链校验顺带实跑（三层引号嵌套在 shell 层直接报语法错，改用
   `-EncodedCommand` 绕开后取得的干净输出）：

   ```
   ModuleNotFoundError: No module named 'app.audit'
   ```

   与上面 `Test-Path` 的结论一致：`app/audit/` 整个包都不在 `.51` 上。

   📌 **两轮误判的教训**：`0831A` 把"环境缺口形态的报错"诊断成"缺 venv"；
   09-03 订正后仍把"命令跑不起来"归因为"文档路径拼写"——两次都对了"报错说
   `.venv` 不存在" / "报错说 import 不到"这个表层现象，但都没有先问一句
   "**目标机器上到底跑着哪个版本的代码**"。`Test-Path` 和 `Select-String`
   两条命令就能把范围从"猜编码/猜路径"缩小到"看部署时间戳"，本可以更早查到。

   **U5 接线前还差**（09-03 二次订正）：`.venv` 路径没问题，**真正缺的是一次
   `.51` 完整重新部署**（把 08-27 至今的代码同步过去），使 `app/config.py` 与
   `app/audit/sinks.py` 落地后，本节四步才有可能被真正跑通。
   🔴 **`.51` 的发版决定是 `CLAUDE.md` 决策代理表的不可代项**，需 Shao Peishen
   本人决定是否、以及何时执行这次重新部署——本 opener 的授权范围仅到"重跑验证"，
   不含发版，故到此为止，不代为发起部署。

   **2026-09-03 三次实跑记录（`.51` 完成重新部署后）｜ ✅ 已闭合**

   执行人：`[Mac]0903D` session ｜ 时间：2026-09-03 10:03–10:10 左右（发版完成、
   冒烟通过后即接续跑）｜ 依据：Shao Peishen 2026-09-03 09:55 在 Cowork 会话
   `HR业务线-接力0903B` 回「发」（同一条指示覆盖本次 `.51` 发版与本项四步验证
   两个不可代项；`docs/session接力.md` 【二】② 与号池台账 0903D 行独立记有
   同一条指示，互为印证——本 session 无法直接读取 Cowork 会话原文，以上两份
   本仓库既有记录是交叉印证的依据）。

   前置：`.51` 已发版至 `d104249`（`sync-to-server.sh`，健康检查 `HTTP 200`），
   `Test-Path app\config.py` / `app\audit\sinks.py` 均 `True`，`app.log` 无
   `Traceback` / `OperationalError`。

   | 步 | 动作 | 期望 | 实际 |
   |---|---|---|---|
   | 1 | 按 4.1 写 `'true'` | —— | 静默成功 |
   | 2 | 跑 3.3 验证命令 | 打印 `True` | ✅ `True` |
   | 3 | 按 3.2 写回 `'false'` | —— | 静默成功，步骤 2 后立即执行 |
   | 4 | 再跑一次验证命令 | 打印 `False` | ✅ `False` |

   第 4 步实际输出（留痕四要素之一，逐字）：`False`。与前两轮（08-31 环境缺口、
   09-03 首轮部署缺口）不同，本轮路径、编码、部署全部到位，"文件 → 应用层"
   链路首次完整跑通，产线开关验证后确认处于**关闭态（安全）**。

   §2.2 链校验同轮实跑（三层引号嵌套在 shell 层报语法错，改用 `-EncodedCommand`
   绕开后取得的干净输出）：

   ```
   path: C:\apps\zhuopin-recruit-agent\data\audit\decisions.jsonl | exists: False
   ChainVerification(ok=True, total=0, broken_at=None, error=None, tail_hash=None)
   ```

   按 §2.3 读法：`ok=True, total=0` = 文件不存在，"没有东西可验"，不是"验证
   通过"也不是失败——`decisions.jsonl` 至今没有产生过审计记录（本环境是内网
   Demo，尚未有候选人走过完整流程），与部署是否到位无关，链路本身已确认可用。

   **本轮起两项标 ✅ 已闭合**：第 2 项（2.2 链校验）、第 3 项（4.x 编码约束 +
   3.3 应用层校验）。**第 1 项（备份任务）仍 ⏸**——本轮发版前只做了一次性快照
   （`C:\apps\backups\20260903-1003`），不构成第 1 项要求的常态化备份任务，
   需另行确认或新增。

---

## 关联

- 实现：`app/config.py`（开关取值）、`app/audit/sinks.py`（`JsonlChainSink.verify_chain`）
- 设计：`openspec/changes/ai-audit-trail-and-outbound-gate/design.md` D1 / D3 / 迁移计划
- 技术债：`docs/tech-debt.md` TD-6（`operator_id` 不可信）、TD-7（JSONL 仅进程内锁）
- 发版：`05-发布运行手册.md`、`docs/deploy-51-server.md`
- 决策代理与不可代项：`CLAUDE.md`
