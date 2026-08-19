"""运行时可观测性：日志落盘与轮转、请求标识串联、错误证据、个人信息脱敏。

刻意不做 re-export：各模块按全路径导入（app.observability.logging_config 等），
避免 __init__ 变成一个所有子模块都要回头改一笔的公共依赖点。
"""
