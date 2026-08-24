#!/usr/bin/env bash
# OP-0820 批量执行器
# ---------------------------------------------------------------------------
# 从 docs/openers/OP-0820-全量编排.md 抽取 opener 正文并交给 claude 非交互执行。
# 单一真源：opener 正文只存在于编排文件里，本脚本不复制一份，改编排文件即生效。
#
# 用法：
#   bash docs/openers/run-batch.sh            # 全量：先 10，再 11∥12∥13
#   bash docs/openers/run-batch.sh 11 12      # 只跑指定几条（自行保证前置已满足）
#   DRY_RUN=1 bash docs/openers/run-batch.sh  # 只打印会执行什么，不真跑
#
# ⛔ 本脚本**不包含**这三条，且不要往里加：
#   OP-0820-9R  .51 整机重启 —— 会中断 7 个服务（含门户网关本体），CLAUDE.md 列为
#               不可代项，必须 Shao Peishen 本人选窗口、知会到位后手动执行
#   OP-0820-5S  单元 B 收口补丁 —— 要贴进已经开着的那个 run-build session，
#               脚本新起的 session 没有它的 TDD 台账，起了等于从头再来
#   OP-0820-W   门户挂载 —— 在 Win 笔记本上，不在本仓库
# ---------------------------------------------------------------------------
set -uo pipefail

REPO="/Users/paulshao/Projects/HumanResource"
PLAN="$REPO/docs/openers/OP-0820-全量编排.md"
LOGDIR="$REPO/.claude/handoff/batch-$(date +%Y%m%d-%H%M%S)"

# 每条 session 的美元上限。任一条烧到这个数会停，避免脚本跑飞。
BUDGET="${BUDGET:-8.00}"

# 无人值守必须免交互。这等价于 --permission-mode bypassPermissions：
# 会写文件、跑 bash、git commit/push 而不再询问。风险自负，见文件末尾说明。
PERM="--dangerously-skip-permissions"

# ---------------------------------------------------------------------------

command -v claude >/dev/null || { echo "❌ 找不到 claude CLI，先装或把它加进 PATH"; exit 1; }
[[ -f "$PLAN" ]] || { echo "❌ 找不到编排文件：$PLAN"; exit 1; }
mkdir -p "$LOGDIR"

# 从编排文件里抽取某条 opener 的正文：
# 从含【OP-XXXX】的那一行开始，到下一个单独成行的 ``` 为止。
extract() {
  awk -v tag="【$1】" '
    !inblk && index($0, tag) == 1 { inblk = 1 }
    inblk && /^```[[:space:]]*$/  { exit }
    inblk                          { print }
  ' "$PLAN"
}

run_one() {
  local id="$1"
  local prompt log
  prompt="$(extract "$id")"

  if [[ -z "$prompt" ]]; then
    echo "❌ [$id] 在编排文件里抽不到正文——检查标题行是否仍是【$id】开头" >&2
    return 1
  fi

  log="$LOGDIR/$id.log"
  echo "▶ [$id] 启动，日志：$log"

  if [[ -n "${DRY_RUN:-}" ]]; then
    echo "--- DRY RUN [$id] 前 12 行 ---"
    echo "$prompt" | head -12
    echo "--- 共 $(echo "$prompt" | wc -l | tr -d ' ') 行 ---"
    return 0
  fi

  (
    cd "$REPO" || exit 1
    claude -p "$prompt" \
      $PERM \
      --max-budget-usd "$BUDGET" \
      --output-format text \
      > "$log" 2>&1
    echo "$?" > "$LOGDIR/$id.exit"
  )
}

wave() {
  local pids=() id rc=0
  for id in "$@"; do
    run_one "$id" &
    pids+=("$!")
  done
  for p in "${pids[@]}"; do wait "$p" || rc=1; done
  return $rc
}

# ---------------------------------------------------------------------------

TARGETS=("$@")

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  # 全量模式：两波。10 先跑完，11/12/13 再并行。
  # 依赖理由：10 重写 m1-job-profile-intake 的 tasks 并修 07 文档，
  # 后三条出 plan 时会读这些文档；且 Shao Peishen 定了「先清失真再并行全开」。
  echo "═══ 波 1：OP-0820-10（清文档失真，独占）"
  wave "OP-0820-10"
  rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "❌ 波 1 失败，停在这里不继续。看 $LOGDIR/OP-0820-10.log"
    exit 1
  fi
  # 后三条要读 10 刚推上去的东西，先同步一次
  ( cd "$REPO" && git pull --rebase origin main ) || {
    echo "⚠️ pull --rebase 失败，波 2 的三条各自还会再 pull 一次，继续"; }

  echo
  echo "═══ 波 2：OP-0820-11 ∥ -12 ∥ -13（三条并行，触碰区零重叠）"
  wave "OP-0820-11" "OP-0820-12" "OP-0820-13"
else
  echo "═══ 指定模式：${TARGETS[*]}（前置由你自己保证）"
  # 统一加前缀，允许只写数字
  norm=()
  for t in "${TARGETS[@]}"; do
    [[ "$t" == OP-* ]] && norm+=("$t") || norm+=("OP-0820-$t")
  done
  wave "${norm[@]}"
fi

# ---------------------------------------------------------------------------

echo
echo "═══ 结果"
for f in "$LOGDIR"/*.exit; do
  [[ -e "$f" ]] || continue
  id="$(basename "$f" .exit)"
  code="$(cat "$f")"
  if [[ "$code" == "0" ]]; then
    echo "  ✅ $id"
  else
    echo "  ❌ $id  exit=$code  →  $LOGDIR/$id.log"
  fi
done

echo
echo "日志目录：$LOGDIR"
echo
echo "跑完后请自己核一次真身，不要只信日志说的："
echo "  cd $REPO && git log --oneline -10"
echo "  git status --short"
echo "  ls -lt docs/superpowers/plans/ | head -4"
echo "  grep -c '^### Task ' docs/superpowers/plans/<新出的两份计划>"
