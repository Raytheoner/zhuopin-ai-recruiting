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
