from scripts.smoke_m2_deps import DEPS, probe, probe_all, render_markdown


def test_probe_installed_package_reports_version_and_size():
    result = probe("pytest", "pytest")
    assert result["ok"] is True
    assert result["version"]
    assert result["size_mb"] is None or result["size_mb"] >= 0


def test_probe_missing_package_reports_error_without_raising():
    result = probe("no-such-dist-0917af", "no_such_module_0917af")
    assert result["ok"] is False
    assert "no_such_module_0917af" in result["error"]


def test_deps_cover_design_choices():
    dists = {d for d, _ in DEPS}
    assert {"paddleocr", "paddlepaddle", "FlagEmbedding", "pypdf", "python-docx", "numpy"} <= dists


def test_render_markdown_has_one_row_per_probe():
    report = probe_all([("pytest", "pytest"), ("no-such-dist-0917af", "no_such_module_0917af")])
    md = render_markdown(report)
    assert "| pytest |" in md
    assert "| no-such-dist-0917af |" in md
    assert report["python"] and report["platform"]
