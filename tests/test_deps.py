from ngargparser import cli


def test_add_creates_file_with_blocks(in_tmp_dir):
    added = cli.add_deps_to_paths("paths.py", ["mhci", "mhcii"])
    assert added == 2
    content = (in_tmp_dir / "paths.py").read_text()
    assert "''' [ Mhci ] '''" in content
    assert "''' [ Mhcii ] '''" in content


def test_add_skips_duplicate_names(in_tmp_dir):
    cli.add_deps_to_paths("paths.py", ["mhci"])
    added = cli.add_deps_to_paths("paths.py", ["mhci"])
    assert added == 0


def test_remove_deletes_targeted_block_only(in_tmp_dir):
    cli.add_deps_to_paths("paths.py", ["mhci", "mhcii"])
    removed = cli.remove_deps_from_paths("paths.py", ["mhci"])
    assert removed == 1
    content = (in_tmp_dir / "paths.py").read_text()
    assert "''' [ Mhci ] '''" not in content
    assert "''' [ Mhcii ] '''" in content


def test_remove_missing_name_is_noop(in_tmp_dir):
    cli.add_deps_to_paths("paths.py", ["mhci"])
    removed = cli.remove_deps_from_paths("paths.py", ["nonexistent"])
    assert removed == 0
    assert "''' [ Mhci ] '''" in (in_tmp_dir / "paths.py").read_text()


def test_list_reports_declared_count(in_tmp_dir, capsys):
    cli.add_deps_to_paths("paths.py", ["mhci", "mhcii"])
    capsys.readouterr()  # clear add output
    count = cli.list_deps_in_paths("paths.py")
    assert count == 2
    out = capsys.readouterr().out
    assert "Mhci" in out and "Mhcii" in out


def test_add_then_remove_round_trip(in_tmp_dir):
    cli.add_deps_to_paths("paths.py", ["mhci", "mhcii"])
    cli.remove_deps_from_paths("paths.py", ["mhci", "mhcii"])
    assert cli.list_deps_in_paths("paths.py") == 0


def test_remove_backs_up_before_rewrite(in_tmp_dir):
    cli.add_deps_to_paths("paths.py", ["mhci", "mhcii"])
    before = (in_tmp_dir / "paths.py").read_text()

    cli.remove_deps_from_paths("paths.py", ["mhci"])

    backup = in_tmp_dir / "paths.py.bak"
    assert backup.exists()
    assert backup.read_text() == before  # pre-removal content is recoverable
