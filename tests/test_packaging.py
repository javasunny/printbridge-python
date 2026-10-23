from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent.parent


def test_bundle_contains_resources_in_runtime_locations():
    calls = {}

    def analysis(scripts, **kwargs):
        calls["analysis"] = kwargs
        assert scripts == [str(ROOT / "desktop_entry.py")]
        return SimpleNamespace(pure=[], scripts=scripts, binaries=[], datas=kwargs["datas"])

    def executable(*args, **kwargs):
        calls["exe"] = kwargs
        return object()

    def collect(*args, **kwargs):
        calls["collect"] = kwargs

    scope = {"SPECPATH": str(ROOT), "Analysis": analysis, "PYZ": lambda pure: pure,
             "EXE": executable, "COLLECT": collect}
    spec = ROOT / "PrintBridge.spec"
    exec(compile(spec.read_text(encoding="utf-8"), str(spec), "exec"), scope)

    destinations = dict((destination, Path(source)) for source, destination in calls["analysis"]["datas"])
    for directory, resource in (("static", "index.html"), ("templates", "admin.html"),
                                ("drivers", "PrintBridge.ppd")):
        assert (destinations[directory] / resource).is_file()
    assert calls["exe"]["exclude_binaries"] is True
    assert calls["exe"]["console"] is False
    assert calls["collect"]["name"] == "PrintBridge"
    assert "pystray._win32" in calls["analysis"]["hiddenimports"]
