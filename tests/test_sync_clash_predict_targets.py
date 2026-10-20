"""`cli sync`'s known-clash check looks only at calls on the predict subparser.

It matched any call whose object had "predict" in its name, so an unrelated
`predictor_cfg.add_argument("-o", ...)` made `cli sync -n` report a clash and exit 1,
while the real run, whose help screens pass, exited 0. It now follows the predict
subparser itself: self.parser_predict, self.predict_input_group, a local name assigned
from them or from add_predict_subparser(...), and their argument groups.
"""

import sys

import pytest

from ngargparser import cli

PARSER_FILE = "src/DemoArgumentParser.py"


@pytest.fixture(autouse=True)
def _release_version_stamps(monkeypatch):
    # The tests run from a git checkout; stamp the plain version, as a release install does.
    monkeypatch.setattr(cli, "_local_version_label", lambda: None, raising=False)


@pytest.fixture
def runnable_project(scaffolded_project, monkeypatch):
    monkeypatch.setattr(cli, "_project_python", lambda: sys.executable)
    return scaffolded_project


def append_to_init(project, code):
    """Add `code` (lines indented for DemoArgumentParser.__init__) at the end of the parser file."""
    parser = project / PARSER_FILE
    parser.write_text(parser.read_text() + "\n" + code)
    return len(parser.read_text().splitlines())


def help_error(project):
    passed, error = cli._smoke_check()["results"]["src/run_demo.py --help"]
    return None if passed else error


def test_an_option_on_another_parser_named_like_predict_is_not_a_clash(runnable_project, sync_args, strip_ansi, capsys):
    append_to_init(
        runnable_project,
        "        predictor_cfg = argparse.ArgumentParser(add_help=False)\n"
        '        predictor_cfg.add_argument("-o", dest="cfg_out")\n',
    )
    assert help_error(runnable_project) is None  # the tool starts: there is no clash to report

    assert cli._clash_predict_output_options() == []
    preview_rc = cli.sync_command(sync_args(dry_run=True))
    preview = strip_ansi(capsys.readouterr().out)
    real_rc = cli.sync_command(sync_args())
    real = strip_ansi(capsys.readouterr().out)

    assert (preview_rc, real_rc) == (0, 0)  # the preview says what the real run does
    assert "Sync would stop" not in preview and "known clash" not in preview
    assert "Sync stopped" not in real


def test_the_check_follows_the_predict_subparser_through_names_and_groups(runnable_project):
    alias = append_to_init(
        runnable_project,
        '        p = self.parser_predict\n        p.add_argument("-o", dest="mine")\n',
    )
    assert [(f["first"], f["what"]) for f in cli._clash_predict_output_options()] == [(alias, "declares -o on predict")]
    assert "-o belongs to the framework (--output-prefix)" in help_error(runnable_project)


def test_an_argument_group_of_predict_input_options_is_on_predict(runnable_project):
    line = append_to_init(
        runnable_project,
        "        extra = self.predict_input_group.add_mutually_exclusive_group()\n"
        '        extra.add_argument("--format-name", "-f", dest="format_name")\n',
    )
    assert [(f["first"], f["what"]) for f in cli._clash_predict_output_options()] == [(line, "declares -f on predict")]
    assert "-f belongs to the framework (--output-format)" in help_error(runnable_project)


def test_a_removal_on_another_parser_does_not_free_the_predict_option(scaffolded_project):
    # remove_argument("-o") on predict frees -o there (test_sync_safety.py); on another object it does not.
    line = append_to_init(
        scaffolded_project,
        '        predictor_cfg.remove_argument("-o")\n        self.parser_predict.add_argument("-o", dest="mine")\n',
    )
    assert [(f["first"], f["what"]) for f in cli._clash_predict_output_options()] == [(line, "declares -o on predict")]
