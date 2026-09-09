"""测试期网络闸门：任何指向**非回环地址**的连接一律当场炸。

**为什么是 `sitecustomize.py`**：TD-36 要防的那条路径跑在**子进程**里
（`test_liaison_credentials.py` 用 `sys.executable -m tools.liaison` 起真进程），
进程内的 monkeypatch 够不着它。`site` 模块在解释器启动时会自动 import 名为
`sitecustomize` 的顶层模块——把本目录塞进子进程的 `PYTHONPATH`，闸门就在
**用户代码跑起来之前**装好了，SDK、`websockets`、`asyncio` 全都罩在里面。

⛔ 本文件不是"测试辅助的一种写法"，它是 TD-36 验收判据的执行体：
「`tools/liaison/tests` 里不存在任何会向 `qyapi.weixin.qq.com` 发起连接的路径」
靠它变成一条**机器判据**，而不是"我看了一遍觉得没有"。

⛔ 不许加"跳过闸门"的环境变量。要真连企微是 8.6 的事，由 Shao Peishen 亲自跑
`python -m tools.liaison`（不经 pytest、不经本目录），与本闸门无关。

⚠️ 回环放行是刻意的：`test_queue_concurrency.py` 之类的用例可能起本地 socket。
判据按**地址**放行，⛔ 不按"哪个用例"放行——按用例开口子等于没有闸门。
"""

from __future__ import annotations

import os
import socket
import urllib.request

#: 🔴 **代理必须一并断掉，⛔ 不是"顺手做的清理"**。
#: 2026-09-09 实测（本机 `HTTPS_PROXY=http://127.0.0.1:<port>`）：`websockets` 17.1
#: 默认走代理，于是 SDK 的建连**打到 127.0.0.1**——回环，闸门放行——再由代理转发到
#: 企微，一次真实认证请求就这么出去了（企微回 errcode=853000）。闸门看起来一切正常，
#: 实际上完全没拦住。⛔ 这就是"闸门还在、但看闸门的人被喂了假绿灯"，比闸门直接失效
#: 更危险。
#: ⚠️ **只清环境变量不够**：`websockets` 用的是 `urllib.request.getproxies()`，
#: 它在 macOS 上还会去读**系统代理设置**（`_scproxy`），环境变量清了照样能拿到代理
#: ——实测清完 8 个变量后依然从 127.0.0.1:<port> 出去了。所以下面还要把
#: `getproxies` / `proxy_bypass` 一起摁成"没有代理"。
#: ⛔ 不许改成"把 127.0.0.1 也拉黑"——本地 socket 用例要用回环；根因是代理，不是回环。
_PROXY_ENV_NAMES = (
    "http_proxy", "https_proxy", "all_proxy", "ws_proxy", "wss_proxy",
    "socks_proxy", "ftp_proxy", "no_proxy",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "WS_PROXY", "WSS_PROXY",
    "SOCKS_PROXY", "FTP_PROXY", "NO_PROXY",
)

_LOOPBACK_HOSTS = frozenset({"localhost", "localhost.", "127.0.0.1", "::1", "", None})


class NetworkAccessInTestError(RuntimeError):
    """用例（或它起的子进程）试图连一个外部地址。

    ⛔ 看到这个错误不要往放行名单里加东西——先问「这条用例为什么需要联网」。
    TD-36 的原始形态就是两条本地用例带着假凭据去连企微生产端点，而且没有任何
    报错会告诉你。
    """


def _host_of(address) -> object:
    if isinstance(address, (tuple, list)) and address:
        return address[0]
    return address


def _is_loopback(host) -> bool:
    if host in _LOOPBACK_HOSTS:
        return True
    if not isinstance(host, str):
        # AF_UNIX 的 address 是路径（str）；bytes / int 之类交给下面的兜底判断。
        return False
    return host.startswith("127.") or host == "::1"


def install() -> None:
    """装上闸门。重复调用是安全的（只包一次）。"""
    if getattr(socket, "_liaison_netguard_installed", False):
        return

    # ⛔ 先断代理，再装闸门：顺序反了没有实际差别，但读的人应当先看见这一步。
    removed_proxies = {
        name: os.environ.pop(name) for name in _PROXY_ENV_NAMES if name in os.environ
    }
    real_getproxies = urllib.request.getproxies
    real_proxy_bypass = urllib.request.proxy_bypass
    urllib.request.getproxies = lambda: {}
    urllib.request.proxy_bypass = lambda host: False

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def _check(host) -> None:
        if not _is_loopback(host):
            raise NetworkAccessInTestError(
                f"用例试图连接外部地址 {host!r}。tools/liaison 的测试 ⛔ 不许触网——"
                "真实建连是 8.6 由 Shao Peishen 亲自跑的事（TD-19/TD-36）。"
                "⛔ 不要把它加进放行名单，先查这条路径为什么会走到建连。"
            )

    # AF_UNIX（本机 socket 文件）不涉及网络。Windows 上没有这个常量，
    # ⛔ 不许写成裸 `socket.AF_UNIX`——那会让闸门在 Windows 上 import 就炸。
    af_unix = getattr(socket, "AF_UNIX", None)

    def guarded_connect(self, address):
        if af_unix is None or self.family != af_unix:
            _check(_host_of(address))
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        if af_unix is None or self.family != af_unix:
            _check(_host_of(address))
        return real_connect_ex(self, address)

    def guarded_getaddrinfo(host, port, *args, **kwargs):
        # ⚠️ asyncio 建连前先解析：拦在这里比拦 connect 更早，报错也指得更准。
        _check(host)
        return real_getaddrinfo(host, port, *args, **kwargs)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.getaddrinfo = guarded_getaddrinfo
    socket._liaison_netguard_originals = (real_connect, real_connect_ex, real_getaddrinfo)
    socket._liaison_netguard_removed_proxies = removed_proxies
    socket._liaison_netguard_real_proxy_funcs = (real_getproxies, real_proxy_bypass)
    socket._liaison_netguard_installed = True


def uninstall() -> None:
    """还原。给进程内的 fixture 收尾用——⛔ 子进程里不该调它。

    ⚠️ 存在的理由是**爆炸半径**：`socket` 的补丁是进程全局的，而
    `tools/liaison/tests/conftest.py` 所在的这次 pytest 运行里还跑着根 `tests/`。
    装了不摘 = 把一条只为本目录立的规矩强加给整个套件。
    """
    if not getattr(socket, "_liaison_netguard_installed", False):
        return
    real_connect, real_connect_ex, real_getaddrinfo = socket._liaison_netguard_originals
    socket.socket.connect = real_connect
    socket.socket.connect_ex = real_connect_ex
    socket.getaddrinfo = real_getaddrinfo
    os.environ.update(socket._liaison_netguard_removed_proxies)
    urllib.request.getproxies, urllib.request.proxy_bypass = (
        socket._liaison_netguard_real_proxy_funcs
    )
    del socket._liaison_netguard_real_proxy_funcs
    del socket._liaison_netguard_removed_proxies
    del socket._liaison_netguard_originals
    socket._liaison_netguard_installed = False


install()
