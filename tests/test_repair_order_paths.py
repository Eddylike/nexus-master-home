from nexus.master import repair_order as ro


def test_valid_game_path_is_inside_and_writable():
    path, error = ro.resolve("nexus/games/haus5t/rules.py")
    assert error is None
    assert path == (ro.ROOT / "nexus" / "games" / "haus5t" / "rules.py").resolve()
    assert ro.write_allowed("nexus/games/haus5t/rules.py")
    assert ro.write_allowed("workspace/example.py")
    assert ro.write_allowed("nexus/integrations/example.py")
    assert not ro.write_allowed("nexus/bridge/executors.py")


def test_windows_style_relative_path_is_normalized():
    path, error = ro.resolve(r"nexus\games\haus5t\rules.py")
    assert error is None and path.is_file()


def test_outside_and_traversal_are_refused():
    assert ro.resolve("C:/Windows/win.ini") == (None, "OUTSIDE_PROJECT")
    assert ro.resolve("../../etc/passwd") == (None, "PATH_TRAVERSAL")


def test_run_does_not_mislabel_inside_project_as_outside():
    refused = ro.run(None, "Repariere nexus/bridge/executors.py")
    assert refused["error"] == "REPAIR_PATH_NOT_WRITABLE"
    assert "ausserhalb" not in refused["reply"].lower()
    outside = ro.run(None, "Repariere C:/Windows/win.ini")
    assert outside["error"] == "REPAIR_OUTSIDE_PROJECT"
