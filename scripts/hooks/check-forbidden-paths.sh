#!/usr/bin/env bash
#
# 拦截「绝不入库」类别的文件进入暂存区。
#
# 为什么这跟 .gitignore 不重复：
#   - .gitignore 只挡「未跟踪文件被通配符捎带进来」
#   - `git add -f` 能直接绕过 .gitignore
#   - 已被跟踪的文件根本不受 .gitignore 管
# 这个钩子拦的就是上面后两条路。构造上防住优于检查防住，但两道都要有。
#
# 由 .pre-commit-config.yaml 以 language: script 调用，参数是暂存的文件列表。
#
set -uo pipefail
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8

# 凭据 / 候选人个人信息 / 评测集
FORBIDDEN_REGEX='^(\.env($|\.)|data/|evalset/|secrets/|credentials\.json$|.*\.pem$|.*\.key$|.*\.resume\.pdf$)'
# 唯一放行项：示例配置，不含真实凭据
ALLOW_REGEX='^\.env\.example$'

bad=()
for f in "$@"; do
  if [[ "$f" =~ $ALLOW_REGEX ]]; then
    continue
  fi
  if [[ "$f" =~ $FORBIDDEN_REGEX ]]; then
    bad+=("$f")
  fi
done

if [ ${#bad[@]} -eq 0 ]; then
  exit 0
fi

echo "✋ 以下文件属于「绝不入库」类别，已拦下："
for f in "${bad[@]}"; do
  echo "     $f"
done
cat <<'EOF'

  凭据 / 候选人个人信息 / 200 份真实简历评测集，任意一类进了 git 历史都是永久的
  —— 改 .gitignore 删不掉，只能 filter-repo 重写历史 + 强推，代价远大于现在停一下。

  确认是误报 → 改 scripts/hooks/check-forbidden-paths.sh 里的 ALLOW_REGEX
  不要用 git commit --no-verify 绕过（CI 里还会再拦一次，绕不过去）
EOF
exit 1
