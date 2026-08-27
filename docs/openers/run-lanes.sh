#!/usr/bin/env bash
# run-lanes.sh —— CC 端泳道看护执行器（v1，2026-08-26）
# ===========================================================================
# 设计来自「工具-opener批处理执行v2.ps1」（企业AI转型侧，2026-08-25）。
# 按本项目的做法在本仓库自建实现，不跨仓库引用。搬过来的四样机制：
#
#   ① 哨兵双指标 —— 退出码 0 只说明进程正常结束，不说明任务完成。要求每个 session
#      顶格输出 OPENER_DONE / OPENER_PARTIAL，两个指标都对才算 OK。
#      ⚠️ 哨兵扫全文，不扫 tail：原版实测哨兵落在第 2 行，日志一长就误判 NO-SENTINEL
#      并误停整条泳道。
#   ② 无头引导头 —— 统一注入硬规则，不靠每份 opener 各自手写。
#   ③ 泳道 —— 泳道内严格串行，泳道间并行。判据：同泳道=触碰区重叠，跨泳道=实测零重叠。
#   ④ 错峰启动 + 失败只停本泳道 + 打印续跑命令。
#
# 用法：
#   bash docs/openers/run-lanes.sh --dry-run          # 只打印编排，不执行
#   bash docs/openers/run-lanes.sh                    # 默认 acceptEdits 权限，问一次再跑
#   bash docs/openers/run-lanes.sh --full-auto --yes  # 全自动无人值守
#   bash docs/openers/run-lanes.sh --only OP-0826-B   # 只跑指定几条（逗号分隔）
#
# 参数：
#   --dry-run          只解析与打印
#   --yes              跳过开跑确认
#   --full-auto        用 --dangerously-skip-permissions（默认是 --permission-mode acceptEdits）
#   --only  A,B,C      只跑这几条
#   --model NAME       透传 --model
#   --max-parallel N   同时最多几条泳道（默认 3）
#   --stagger N        泳道错峰启动间隔秒（默认 90，降编辑锁碰撞）
#   --budget N         每条 session 的上限（默认 25.00）
#   --chain            链式接续：一轮收敛后重扫编排，还有待执行条目就自动接着跑
#   --max-rounds N     链式的轮次硬上限（默认 5），防标注没摘干净导致无限重跑
#
# 关于 --budget 的语义（2026-08-27 查证 code.claude.com/docs/en/costs）：
#   Max/Pro 订阅下用量**包含在订阅里**，那个美元数是 Claude Code 按标准价目**本地折算**
#   的估值，不是账单。所以它是「跑飞保险丝」不是钱闸，提高它不产生额外收费。
#   真花钱的只有 usage credits（claude.ai → Settings → Usage 里单独开的开关）。
#   真正的天花板是订阅的用量窗口（5 小时滚动 + 每周）——并行两条 run-build 用量翻倍，
#   开跑前看一眼 /usage 的窗口余量，比看这个美元数有意义。
#
# ⛔ 不进本脚本、也不要往里加的：
#   .51 整机重启 —— 会中断 8 个服务（含门户网关本体），CLAUDE.md 列为不可代项
#   贴进已开着 session 的补丁 —— 脚本新起的 session 没有那边的 TDD 台账
#   Win 笔记本上的手工项
# ===========================================================================
set -uo pipefail

REPO="/Users/paulshao/Projects/HumanResource"
PLAN="$REPO/docs/openers/OP-0820-全量编排.md"

DRY_RUN=0; ASSUME_YES=0; FULL_AUTO=0; MODEL=""; ONLY=""
MAX_PARALLEL=3; STAGGER=90
CHAIN=0; ROUND=0; ROUND_CAP=5
# 单条 session 的美元上限。⚠️ 别调回 8.00：2026-08-26 批次 lanes-20260826-231550 的
# OP-0826-E（出一份 6 Task 的 plan）撞了 $8 上限被判 FAIL(1)，而**活其实干完了**
# ——计划文件完整落盘，只是没来得及输出哨兵。撞上限的表现和真失败一模一样，
# 唯一区别是日志里那行 `Error: Exceeded USD budget`。run-build 比出 plan 还贵。
BUDGET="25.00"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)      DRY_RUN=1; shift ;;
    --yes|-y)       ASSUME_YES=1; shift ;;
    --full-auto)    FULL_AUTO=1; shift ;;
    --only)         ONLY="$2"; shift 2 ;;
    --model)        MODEL="$2"; shift 2 ;;
    --max-parallel) MAX_PARALLEL="$2"; shift 2 ;;
    --stagger)      STAGGER="$2"; shift 2 ;;
    --budget)       BUDGET="$2"; shift 2 ;;
    --chain)        CHAIN=1; shift ;;
    --round)        ROUND="$2"; shift 2 ;;
    --max-rounds)   ROUND_CAP="$2"; shift 2 ;;
    --plan)         PLAN="$2"; shift 2 ;;
    *) echo "未知参数：$1" >&2; exit 64 ;;
  esac
done

command -v claude >/dev/null || { echo "✗ 找不到 claude CLI"; exit 10; }
[[ -f "$PLAN" ]] || { echo "✗ 计划文件不存在：$PLAN"; exit 11; }

STAMP="$(date +%Y%m%d-%H%M%S)"
# dry-run 把 manifest 落到临时目录，**不在仓库里留任何痕迹**。
# 早先的做法是照建 .claude/handoff/lanes-<stamp>/ 再在退出前删掉，那有两个毛病：
#   ① 依赖删除权限——挂载目录下 rmdir 可能被挡，于是空目录还是留下了
#   ② 留下的空目录只有 manifest.tsv、没有 results.tsv 和 log，肉眼一扫像"跑了没结果"，
#      是个纯粹的误导源（2026-08-27 实际误判过一次）
# 不建，就不用删。
if [[ $DRY_RUN -eq 1 ]]; then
  LOGDIR="$(mktemp -d 2>/dev/null || echo "/tmp/lanes-dryrun-$STAMP")"
else
  LOGDIR="$REPO/.claude/handoff/lanes-$STAMP"
fi
mkdir -p "$LOGDIR"

# ---------------------------------------------------------------------------
# 无头引导头：注入到每份 opener 正文前面。
# 这几条 CLAUDE.md 里都有，但无头 session 靠自觉容易自我豁免，所以每次显式注入。
# ---------------------------------------------------------------------------
read -r -d '' HEADER <<'EOF' || true
【无头执行引导】本 session 由 run-lanes.sh 无头启动，没有人在旁边。五条硬规则：

① 无人在场，禁止提问。凡需 Shao Peishen 拍板的点——合规红线七条的任何变更或单次例外、
   候选人淘汰规则例外、候选人对外通道开关、真实简历处理范围变更、生产服务器 .51 的发版
   决定、预算与外部采购——**登记后停在该点**，绝不默认生效、绝不替他拍。
   其余歧义按 opener 里写明的预案处置；opener 没写预案的，取"保守方向"（宁可留一条待办，
   不要把没做的事标成做完了）并登记，继续往下跑。⛔ 不要输出问句等人回答，没人会回答。

② 并发协议。本仓库此刻可能有别的泳道在并行跑：
   - 只 git add 本 opener 明确列出的路径。⛔ 禁止 git add -A / git add . / git commit -a
   - git status 里出现别人的改动是正常的，不要停下、不要问、不要顺手提交
   - commit 前先 git pull --rebase origin main；push 被拒就再 pull --rebase 重试，最多 3 次
   - 报 .git/index.lock 已存在，等 5 秒重试最多 5 次，⛔ 绝不删除该锁（另一个 session 正在用）

③ 环境不可达时"留步"，不许假装闭合。凡需 .51 服务器访问、需要低峰窗口、或依赖尚未就绪的
   前置的步骤：代码与单测照做，该步骤如实登记「⏸ 留步：<原因>」后继续或收工。
   ⛔ 不得假装完成，也⛔ 不得因此判整件失败。

④ 收工必做：列出本次新增/修改文件清单 + 实际 commit hash。写完之后**反查一次**——
   git log --oneline -5 与 git status，确认你以为提交的东西真的在 main 上。

⑤ 哨兵（脚本靠它判成败，务必照做）：
   全部完成 → 最后顶格输出一行：OPENER_DONE
   有留步/未尽项 → 最后顶格输出一行：OPENER_PARTIAL: <一句话原因>
   两者都必须单独成行、顶格、不加任何前后缀。

────────────────── 以下为 opener 正文 ──────────────────
EOF

# ---------------------------------------------------------------------------
# 解析：编排文件里，每个 opener 代码块前面有一行
#   > 泳道：<泳道名>
# 代码块首行是 【OP-XXXX-X】...，末行是 fence。
# ---------------------------------------------------------------------------
MANIFEST="$LOGDIR/manifest.tsv"   # lane <TAB> id <TAB> title
: > "$MANIFEST"

awk '
  /^>[[:space:]]*泳道：/ {
    lane = $0
    sub(/^>[[:space:]]*泳道：[[:space:]]*/, "", lane)
    sub(/[[:space:]].*$/, "", lane)
    pending = lane
    next
  }
  /^```/ { if (pending != "") { inblk = 1; next } }
  inblk {
    # ⚠️ 这里绝不能用 match() + substr() 做偏移算术。
    # macOS 自带 BSD awk (20200816) 的 match() 返回的 RSTART/RLENGTH 是**字节**偏移，
    # 不是字符偏移。【 和 】各占 3 字节（UTF-8 E3 80 90 / E3 80 91），
    # RSTART+1 / RLENGTH-2 只各削掉 1 个字节，抽出的 id 两头各挂半个括号残骸，
    # 后面 extract() 拼出 【【OP-XXXX-X】】 永远匹配不上 → 每条都 NO-BODY。
    # 实证：2026-08-26 批次 lanes-20260826-230115 整批 0 条执行，全死在这一行。
    # 加 LC_ALL=en_US.UTF-8 无效——BSD awk 的 match() 就是按字节算的，环境变量救不了。
    # sub() 按正则替换，不涉及偏移算术，多字节安全。
    # 现行格式：[Mac]0827A-<主题短名>
    # `-` 是 id 与主题的唯一分隔符，id 内不含 `-`。这样主题以 ASCII 开头时
    # （如 `audit U1计划`）也不会与 id 粘连读岔——这正是弃用空格分隔的原因。
    if ($0 ~ /^\[Mac\][0-9A-Za-z]+-/) {
      id = $0;    sub(/^\[Mac\]/, "", id);      sub(/-.*$/, "", id)
      title = $0; sub(/^\[Mac\][^-]*-/, "", title)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", title)
      print pending "\t" id "\t" title
    }
    # 历史格式一：[Mac] 0820-10 <主题>（空格分隔，数字序号压不进 MMDDX，不追改）
    else if ($0 ~ /^\[Mac\][[:space:]]+[0-9A-Za-z]+-[0-9A-Za-z]+[[:space:]]/) {
      id = $0;    sub(/^\[Mac\][[:space:]]+/, "", id);    sub(/[[:space:]].*$/, "", id)
      title = $0; sub(/^\[Mac\][[:space:]]+[^[:space:]]+[[:space:]]+/, "", title)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", title)
      print pending "\t" id "\t" title
    }
    # 历史格式二：【OP-0826-A】...
    else if ($0 ~ /^【OP-[0-9A-Za-z]+-[0-9A-Za-z]+】/) {
      id = $0;    sub(/^【/, "", id);       sub(/】.*$/, "", id)
      title = $0; sub(/^【[^】]*】/, "", title)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", title)
      print pending "\t" id "\t" title
    }
    inblk = 0; pending = ""
  }
' "$PLAN" > "$MANIFEST"

[[ -s "$MANIFEST" ]] || {
  echo "✗ 没解析到任何带泳道标注的 opener。"
  echo "  编排文件里每个 opener 的代码块**前一行**要有：  > 泳道：<泳道名>"
  exit 12
}

# --only 过滤
if [[ -n "$ONLY" ]]; then
  IFS=',' read -ra keep <<< "$ONLY"
  tmp="$LOGDIR/manifest.filtered"; : > "$tmp"
  while IFS=$'\t' read -r lane id title; do
    for k in "${keep[@]}"; do
      [[ "$id" == "${k// /}" ]] && printf '%s\t%s\t%s\n' "$lane" "$id" "$title" >> "$tmp"
    done
  done < "$MANIFEST"
  mv "$tmp" "$MANIFEST"
  [[ -s "$MANIFEST" ]] || { echo "✗ --only 过滤后为空"; exit 12; }
fi

LANES=()
while IFS= read -r l; do LANES+=("$l"); done < <(cut -f1 "$MANIFEST" | awk '!seen[$0]++')

# ---------------------------------------------------------------------------
# 抽取某条 opener 的正文。
# 定义提前到 dry-run 之前 —— 见下方「抽取预检」为什么必须这样。
# ---------------------------------------------------------------------------
extract() {
  # 三种抬头都认：现行 `[Mac]<id>-<题>`，历史 `[Mac] <id> <题>`，更早的 `【OP-<id>】`
  awk -v cur="[Mac]$1-" -v old1="[Mac] $1 " -v old2="【$1】" '
    !inblk && (index($0, cur) == 1 || index($0, old1) == 1 || index($0, old2) == 1) { inblk = 1 }
    inblk && /^```[[:space:]]*$/  { exit }
    inblk                          { print }
  ' "$PLAN"
}

# ---------------------------------------------------------------------------
# 打印编排 ＋ 抽取预检
#
# ⚠️ 为什么 dry-run 必须真的调用 extract()：
# 解析 manifest 与抽取正文是**两条不同的代码路径**。2026-08-26 那次
# （批次 lanes-20260826-230115）manifest 解析出的泳道条数、顺序、编号数字**全对**，
# dry-run 与人工自检都判通过——但 id 里混进了半个括号的字节残骸，
# 真正的 extract() 要到实跑才被调用，于是三条泳道全部 NO-BODY，整批 0 条执行。
# 结论：**dry-run 通过 ≠ 抽取能成功**。所以这里对每条真跑一次 extract 并数行数。
# ---------------------------------------------------------------------------
if [[ $FULL_AUTO -eq 1 ]]; then PERM_DESC="dangerously-skip-permissions（全自动）"
else PERM_DESC="acceptEdits（写文件免问，Bash/push 仍会问——无人值守请加 --full-auto）"; fi

echo "计划文件：$PLAN"
echo "泳道 ${#LANES[@]} 条（并行上限 $MAX_PARALLEL，错峰 ${STAGGER}s，单条预算上限 \$$BUDGET）："

PRECHECK_BAD=0
for ln in "${LANES[@]}"; do
  echo "  ◆ $ln （泳道内串行）"
  while IFS=$'\t' read -r _l id title; do
    n="$(extract "$id" | wc -l | tr -d ' ')"
    if [[ "$n" -eq 0 ]]; then
      echo "      ✗ $id  抽取 0 行 —— 正文取不到，实跑必定 NO-BODY"
      PRECHECK_BAD=1
    else
      printf '      ✓ %-14s 正文 %s 行   %s\n' "$id" "$n" "$title"
    fi
  done < <(awk -F'\t' -v L="$ln" '$1==L' "$MANIFEST")
done

echo "权限模式：$PERM_DESC"
echo "日志目录：$LOGDIR"

if [[ $PRECHECK_BAD -eq 1 ]]; then
  echo
  echo "✗ 抽取预检不通过：上面标 ✗ 的条目取不到正文，⛔ 拒绝开跑。"
  echo "  常见原因：编排文件里的标题行格式变了，或 manifest 解析出的 id 被污染。"
  echo "  自查：cat $MANIFEST | od -c | head   ——看 id 两侧有没有多余字节"
  exit 13
fi

# ---------------------------------------------------------------------------
# index.lock 预检
#
# ⚠️ 为什么必须开跑前查一次：孤儿锁不会自己消失。每条 opener 的并发协议是
# 「等 5 秒重试 5 次、绝不删锁」——对活锁是对的，对孤儿锁则意味着**全灭**。
# 实证：2026-08-26 批次 lanes-20260826-231550，5 条泳道全部败在同一把孤儿锁上，
# 三份计划文件都写完了却一个 commit 都没落地。事前查一次，成本几乎为零。
#
# 判据取自 CLAUDE.md「多指令并行的硬规则」第 4 条，三项须同时成立才算孤儿：
#   ① 文件 0 字节  ② mtime 距今 > 10 分钟  ③ 查无 git 进程持有它
# 少一项都按「别人正在用」处理——⛔ 本脚本只报告不删，删锁的判断留给人。
# ---------------------------------------------------------------------------
LOCK="$REPO/.git/index.lock"
if [[ -e "$LOCK" ]]; then
  echo
  echo "⚠️  发现 $LOCK"
  sz=$(wc -c < "$LOCK" | tr -d ' ')
  age=$(( ( $(date +%s) - $(stat -f %m "$LOCK" 2>/dev/null || stat -c %Y "$LOCK") ) / 60 ))
  gitproc=$(pgrep -x git | wc -l | tr -d ' ')   # ⛔ 不用 -f，DigitalGoodsApi 之类会误报
  echo "    大小=${sz}B  age=${age}分钟  pgrep -x git=${gitproc} 个进程"
  if [[ "$sz" -eq 0 && "$age" -gt 10 && "$gitproc" -eq 0 ]]; then
    echo "    → 三项判据全中，**像是孤儿锁**。"
    echo "      ⚠️ 但 pgrep 对 VM 里的 git 是盲的：若持锁方是 Cowork／虚拟机，"
    echo "         还要确认那个 VM 进程已失去监管（PPID=1）。见"
    echo "         docs/findings/2026-08-26-index-lock-孤儿锁判据.md"
    echo "      确认无误后手工删除再重跑：rm '$LOCK'"
  else
    echo "    → 判据未全中，按「别人正在用」处理。⛔ 不要删。"
  fi
  echo
  echo "✗ 带着 index.lock 开跑会让所有泳道全灭（实证见脚本注释）。拒绝开跑。"
  exit 14
fi

if [[ $DRY_RUN -eq 1 ]]; then
  echo; echo "（--dry-run，未执行。抽取预检 + index.lock 预检均已通过。manifest 落在临时目录，仓库零留痕）"
  exit 0
fi

if [[ $ASSUME_YES -ne 1 ]]; then
  read -r -p "开跑？(y/N) " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || exit 0
fi

# ---------------------------------------------------------------------------
# 摘掉某条 opener 的泳道标注，使它不再被后续轮次抓到。
#
# 这是链式执行能收敛的**唯一**机制：编排文件是累积的，跑完的条目若还带
# `> 泳道：`，下一轮会把它连同新条目一起重跑。原来靠人记得摘，现在由脚本摘。
# 定位方式：从该 opener 的标题行往上找最近的一行 `> 泳道：`，只改那一行。
# ---------------------------------------------------------------------------
mark_done() {
  local id="$1" lane="$2" status="$3"
  python3 - "$PLAN" "$id" "$lane" "$status" <<'PY'
import io, re, sys
plan, oid, lane, status = sys.argv[1:5]
lines = io.open(plan, encoding='utf-8').read().split('\n')
# 找标题行（三种抬头都认）
hdr = None
for i, l in enumerate(lines):
    if l.startswith(f'[Mac]{oid}-') or l.startswith(f'[Mac] {oid} ') or l.startswith(f'【{oid}】'):
        hdr = i
        break
if hdr is None:
    sys.exit(0)
# 往上找最近的泳道标注行
for j in range(hdr, max(-1, hdr - 12), -1):
    if re.match(r'^>\s*泳道：', lines[j]):
        from datetime import datetime, timezone, timedelta
        today = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
        lines[j] = f'> ✅ 已完成 {today}（{status}）· 原泳道 {lane}（脚本自动摘，不再进 run-lanes）'
        io.open(plan, 'w', encoding='utf-8').write('\n'.join(lines))
        print(f'    ↳ 已摘标注: {oid}')
        break
PY
}

# ---------------------------------------------------------------------------
# 一条泳道：内部严格串行，遇 FAIL / NO-SENTINEL 停本泳道，不影响别的泳道
# ---------------------------------------------------------------------------
run_lane() {
  local lane="$1"
  local id title body log t0 t1 code mins status

  while IFS=$'\t' read -r _lane id title; do
    log="$LOGDIR/${lane}-${id}.log"
    body="$(extract "$id")"

    if [[ -z "$body" ]]; then
      printf '%s\t%s\t%s\t%s\t%s\n' "$lane" "$id" "NO-BODY" "0" "$log" >> "$LOGDIR/results.tsv"
      echo "  ✗ [$lane/$id] 抽不到正文，停本泳道"
      return 1
    fi

    t0=$(date +%s)
    echo "[lane:$lane] $id $title | start=$(date -Iseconds)" > "$log"

    # -n 给会话起带编号的名字。⚠️ 这一条不是锦上添花：
    # 侧边栏拿不到显式名字就只能用首句猜，编号一断，跨会话对账时"这是哪件任务"就查不回来。
    # 实证：2026-08-26 侧边栏里凡是从 opener 整块复制的会话都带【OP-XXXX-X】，
    # 凡是即兴敲一句话起的、以及本脚本起的，全部丢号。
    local args=(-p -n "[Mac]$id-$title" --output-format text --max-budget-usd "$BUDGET")
    if [[ $FULL_AUTO -eq 1 ]]; then args+=(--dangerously-skip-permissions)
    else args+=(--permission-mode acceptEdits); fi
    [[ -n "$MODEL" ]] && args+=(--model "$MODEL")

    ( cd "$REPO" && printf '%s\n%s\n' "$HEADER" "$body" | claude "${args[@]}" ) >> "$log" 2>&1
    code=$?
    t1=$(date +%s); mins=$(( (t1 - t0) / 60 ))

    # 哨兵扫全文，不扫 tail —— 原版实测哨兵落在第 2 行，扫 tail 会误判
    if   grep -qE '^OPENER_DONE[[:space:]]*$' "$log"; then sentinel=DONE
    elif grep -qE '^OPENER_PARTIAL'           "$log"; then sentinel=PARTIAL
    else sentinel=NONE; fi

    # 撞预算上限单独成一档：它和真失败的退出码一样，但**活可能已经干完**，
    # 只是没来得及输出哨兵。混在 FAIL 里会让人以为要重跑，实际该做的是查产出物。
    if grep -q 'Exceeded USD budget' "$log"; then    status="BUDGET-HIT"
    elif [[ $code -ne 0 ]];                     then status="FAIL($code)"
    elif [[ $sentinel == DONE ]];               then status="OK"
    elif [[ $sentinel == PARTIAL ]];            then status="PARTIAL"
    else                                             status="NO-SENTINEL"; fi

    printf '%s\t%s\t%s\t%s\t%s\n' "$lane" "$id" "$status" "$mins" "$log" >> "$LOGDIR/results.tsv"
    echo "  • [$lane/$id] $status (${mins}m)"

    # 活干完的条目自动摘掉泳道标注 —— 让「跑完即摘」成为机制，不靠人记得。
    # 只摘 OK / PARTIAL（活都干完了，PARTIAL 只是有留步项另行处理）。
    # ⛔ BUDGET-HIT / FAIL / NO-SENTINEL 不摘：前者要人查产出物，后两者待重试。
    if [[ "$status" == "OK" || "$status" == "PARTIAL" ]]; then
      mark_done "$id" "$lane" "$status"
    fi

    # PARTIAL 继续跑本泳道后续（留步是预期内的）；FAIL / NO-SENTINEL 停
    if [[ "$status" == FAIL* || "$status" == "NO-SENTINEL" ]]; then
      echo "  ⏹ 泳道「$lane」在 $id 停下（$status），其余泳道不受影响"
      return 1
    fi
  done < <(awk -F'\t' -v L="$lane" '$1==L' "$MANIFEST")
  return 0
}

# ---------------------------------------------------------------------------
# 调度：并发上限 + 错峰
# ---------------------------------------------------------------------------
: > "$LOGDIR/results.tsv"
declare -a PIDS=()
started=0

for ln in "${LANES[@]}"; do
  while [[ "$(jobs -rp | wc -l | tr -d ' ')" -ge "$MAX_PARALLEL" ]]; do sleep 5; done
  if [[ $started -gt 0 && $STAGGER -gt 0 ]]; then
    echo "  … 错峰等待 ${STAGGER}s"
    sleep "$STAGGER"
  fi
  echo "━━ 泳道「$ln」启动 $(date +%H:%M:%S)"
  run_lane "$ln" &
  PIDS+=("$!")
  started=$((started+1))
done

for p in "${PIDS[@]}"; do wait "$p" || true; done

# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
echo
echo "━━━━━━ 泳道执行汇总 ━━━━━━"
{
  printf '%-12s %-14s %-14s %s\n' 泳道 编号 状态 分钟
  sort "$LOGDIR/results.tsv" | while IFS=$'\t' read -r lane id status mins _; do
    printf '%-12s %-14s %-14s %s\n' "$lane" "$id" "$status" "$mins"
  done
} | tee "$LOGDIR/summary.txt"

echo
echo "日志目录：$LOGDIR"

failed="$(awk -F'\t' '$3 ~ /^FAIL/ || $3=="NO-SENTINEL" || $3=="NO-BODY" {printf "%s,", $2}' "$LOGDIR/results.tsv" | sed 's/,$//')"
partial="$(awk -F'\t' '$3=="PARTIAL" {printf "%s ", $2}' "$LOGDIR/results.tsv")"

[[ -n "$partial" ]] && {
  echo
  echo "⏸ 有留步项（PARTIAL）：$partial"
  echo "   看日志里的「⏸ 留步」登记，多半是等 .51、等窗口、等你拍板。这不算失败。"
}

echo
echo "⚠️ 别只信这张表。跑完自己核一次真身："
echo "   cd $REPO && git log --oneline -12 && git status --short"
echo "   git worktree list"
echo "   for b in \$(git branch --format='%(refname:short)' | grep '^claude/'); do"
echo "     echo \"\$b: \$(git cherry -v main \$b | grep -c '^+') 条真未合\"; done"

# ---------------------------------------------------------------------------
# 链式接续：本轮收敛后重扫编排，还有带泳道标注的待执行条目就接着跑下一轮。
#
# 收敛靠两道保险，缺一会变成无限重跑：
#   ① 跑成的条目由 mark_done() 自动摘标注 → 下轮扫不到它
#   ② ROUND_CAP 硬上限 → 就算标注没摘干净（FAIL 的不摘）也不会无限循环
# 「摘标注即停」这个手动闸仍然成立：你随时可以去编排文件里把标注摘掉喊停。
# ---------------------------------------------------------------------------
if [[ $CHAIN -eq 1 ]]; then
  ROUND=$((ROUND + 1))
  remain="$(awk '/^>[[:space:]]*泳道：/{c++} END{print c+0}' "$PLAN")"

  if [[ -n "$failed" ]]; then
    echo
    echo "⏹ 链式接续中止：本轮有 FAIL/NO-SENTINEL（$failed）。"
    echo "   这类条目不会自动摘标注，接着跑只会原地重复失败。先看日志。"
  elif [[ "$remain" -eq 0 ]]; then
    echo
    echo "✅ 链式接续结束：编排文件里已无带泳道标注的待执行条目。"
  elif [[ "$ROUND" -ge "$ROUND_CAP" ]]; then
    echo
    echo "⏹ 链式接续停在第 $ROUND 轮（上限 $ROUND_CAP）。仍剩 $remain 条带标注。"
    echo "   若确属正常进度，加 --max-rounds N 再起一次。"
  else
    echo
    echo "🔗 链式接续：仍有 $remain 条待执行，进入第 $((ROUND + 1)) 轮"
    ( cd "$REPO" && git pull --rebase origin main ) || \
      echo "   ⚠️ pull --rebase 失败，下一轮各条目自己还会再 pull 一次，继续"
    sleep 10
    exec "$0" --chain --round "$ROUND" --max-rounds "$ROUND_CAP" \
      $([[ $FULL_AUTO -eq 1 ]] && echo --full-auto) --yes \
      --budget "$BUDGET" --max-parallel "$MAX_PARALLEL" --stagger "$STAGGER" \
      ${MODEL:+--model "$MODEL"} --plan "$PLAN"
  fi
fi

if [[ -n "$failed" ]]; then
  echo
  echo "✗ 失败/无哨兵：$failed（只停了各自所在泳道）"
  echo "  续跑：bash docs/openers/run-lanes.sh --only $failed  （记得带上其泳道内的后续编号）"
  exit 1
fi
exit 0
