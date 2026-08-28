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
.\venv\Scripts\python.exe -c "from pathlib import Path; from app.audit.sinks import JsonlChainSink; from app.config import get_settings; p = Path(get_settings().audit_jsonl_path); print('path:', p.resolve(), '| exists:', p.exists()); print(JsonlChainSink(p).verify_chain())"
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
.\venv\Scripts\python.exe -c "from app.config import is_candidate_outbound_enabled; print(is_candidate_outbound_enabled())"
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
2. **2.2 链校验**：`.51` 上的首次实跑输出未取得，本页只给了命令未给输出。
3. **4.x 编码约束**：本页写的是约束本身（已在本机用字节级实测验证），但**尚未在
   `.51` 上按 4.1 实际创建过开关文件**。U5 接线前需要有人上机执行一次 4.1 + 3.3
   的验证命令，确认这条链路在真机上通。

---

## 关联

- 实现：`app/config.py`（开关取值）、`app/audit/sinks.py`（`JsonlChainSink.verify_chain`）
- 设计：`openspec/changes/ai-audit-trail-and-outbound-gate/design.md` D1 / D3 / 迁移计划
- 技术债：`docs/tech-debt.md` TD-6（`operator_id` 不可信）、TD-7（JSONL 仅进程内锁）
- 发版：`05-发布运行手册.md`、`docs/deploy-51-server.md`
- 决策代理与不可代项：`CLAUDE.md`
