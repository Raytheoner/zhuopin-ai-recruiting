"""HR 值守通道服务的测试。

本目录被接进根 `pyproject.toml` 的 `testpaths`，全量 `pytest` 会跑到它。
`__init__.py` 是必需的：根 `tests/` 没有 `__init__.py`，靠 basename 命名模块，
本目录若也不带包标记，两边一旦出现同名文件就会直接冲突。
"""
