from ngargparser import cli

CORE_FILE = "src/core/NGArgumentParser.py"


def test_fresh_project_reports_no_updates(scaffolded_project, sync_args, strip_ansi, capsys):
    rc = cli.sync_command(sync_args())
    assert rc == 0
    out = strip_ansi(capsys.readouterr().out)
    assert "already up to date" in out


def test_drifted_core_file_is_restored(scaffolded_project, sync_args):
    target = scaffolded_project / CORE_FILE
    pristine = target.read_text()
    target.write_text("# corrupted by user\n")

    rc = cli.sync_command(sync_args())
    assert rc == 0
    assert target.read_text() == pristine


def test_stale_stamp_is_restamped(scaffolded_project, sync_args):
    pyproject = scaffolded_project / "pyproject.toml"
    cli.write_scaffold_version(str(pyproject), "0.0.1")

    cli.sync_command(sync_args())
    assert f'scaffold_version = "{cli.__version__}"' in pyproject.read_text()
