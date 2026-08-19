import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_sync_whitelist_never_ships_logs_to_or_from_the_server():
    """日志目录若被误加进同步白名单，会把生产日志反向拉回开发机——
    那是未授权的个人信息转移，属合规事件，不是运维小事。"""
    script = (REPO / "sync-to-server.sh").read_text(encoding="utf-8")

    sync_block = re.search(r"SYNC_PATHS=\((.*?)\)", script, re.S).group(1)
    assert "logs" not in re.findall(r'"([^"]+)"', sync_block)

    exclude_block = re.search(r"EXCLUDE_NAMES=\((.*?)\)", script, re.S).group(1)
    assert "logs" in re.findall(r'"([^"]+)"', exclude_block)


def test_deploy_script_creates_and_verifies_writable_log_dir():
    script = (REPO / "deploy-server.ps1").read_text(encoding="utf-8")
    assert 'Join-Path $AppDir "logs"' in script
    assert "New-Item -ItemType Directory -Path $logDir" in script
    assert "FileSystemAccessRule" in script and "SYSTEM" in script
    assert "deploy-write-probe" in script, "只创建目录不验证可写，等于把降级留到运行时"


def test_deploy_script_has_no_backslash_escaped_quotes():
    """PowerShell 双引号字符串里反斜杠不是转义字符（转义符是反引号 `" 或双写
    引号 ""）。写成 \\" 不会转义任何东西——反斜杠原样留下，紧跟着的裸引号会
    提前把字符串截断，解析器在下一个引号处重新进入字符串模式，中间的内容靠
    「空白相邻」被拼接回命令行，不会报错，只会在运行时打印出带杂散反斜杠、
    缺失引号的乱码提示。这类 bug 在 macOS 上跑不出来（不能执行 PowerShell），
    只能在这里用文本断言钉死，防止同类写法再次混入。"""
    script = (REPO / "deploy-server.ps1").read_text(encoding="utf-8")
    assert '\\"' not in script

