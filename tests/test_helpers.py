import subprocess

import pytest

from ngargparser import cli

# The managed block as releases before the ./configure rules wrote it.
OLD_MANAGED_BLOCK = (
    "# --- ngargparser (managed) ---\n"
    "# Runtime artifacts written by `cli sync` / `cli deps` — keep out of git.\n"
    ".ngargparser/\n"
    "*.bak\n"
)
CONFIGURE_COMMENT = cli.GITIGNORE_MANAGED_RULES[1][0]
# The old block once sync has updated it: `.ngargparser/` becomes `.ngargparser/*`, so the
# hash record below it can be re-included (git cannot re-include a file under an ignored folder).
MIGRATED_BLOCK = OLD_MANAGED_BLOCK.replace(".ngargparser/\n", ".ngargparser/*\n")
HASH_RULE_LINES = (
    "# The framework file hashes stay in git, so cli sync spots local edits in a fresh clone.\n"
    "!.ngargparser/framework-hashes.json\n"
)


def git_ignores(repo, name):
    if not (repo / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return subprocess.run(["git", "check-ignore", "-q", name], cwd=repo).returncode == 0


class TestFormatProjectName:
    def test_dashes_become_underscores(self):
        assert cli.format_project_name("aa-counter") == "aa_counter"

    def test_capitalize_produces_capwords(self):
        assert cli.format_project_name("aa-counter", capitalize=True) == "AaCounter"

    def test_plain_name_unchanged(self):
        assert cli.format_project_name("demo") == "demo"

    def test_capitalize_keeps_the_capitals_already_in_each_part(self):
        assert cli.format_project_name("MyTool", capitalize=True) == "MyTool"
        assert cli.format_project_name("my-tool", capitalize=True) == "MyTool"
        assert cli.format_project_name("my_app", capitalize=True) == "MyApp"
        assert cli.format_project_name("NetMHC-pan", capitalize=True) == "NetMHCPan"
        # The file-name form is unchanged: only '-' becomes '_'.
        assert cli.format_project_name("MyTool") == "MyTool"
        assert cli.format_project_name("my-tool") == "my_tool"


class TestNormalizeName:
    def test_spaces_and_special_chars(self):
        assert cli.normalize_name("MHC Class I") == "mhc_class_i"

    def test_collapses_repeated_separators(self):
        assert cli.normalize_name("net--mhc  pan") == "net_mhc_pan"

    def test_strips_leading_trailing_underscores(self):
        assert cli.normalize_name("_tool_") == "tool"


class TestFormatDisplayName:
    def test_capitalizes_words(self):
        assert cli.format_display_name("net_mhc_pan") == "Net Mhc Pan"

    def test_roman_numerals_uppercased(self):
        assert cli.format_display_name("t_cell_class_ii") == "T Cell Class II"


class TestGenerateDependencySection:
    def test_contains_all_stub_variables(self):
        section = cli.generate_dependency_section("mhc class i")
        assert "''' [ Mhc Class I ] '''" in section
        for suffix in ("_path", "_venv", "_module", "_lib_path"):
            assert f"mhc_class_i{suffix}=None" in section


class TestParseSemver:
    def test_plain_and_v_prefixed(self):
        assert cli._parse_semver("1.2.3") == (1, 2, 3)
        assert cli._parse_semver("v0.3.1") == (0, 3, 1)

    def test_prerelease_suffix_ignored_for_ordering(self):
        assert cli._parse_semver("1.2.3-rc1") == (1, 2, 3)

    def test_invalid_returns_none(self):
        assert cli._parse_semver("not-a-version") is None
        assert cli._parse_semver("") is None
        assert cli._parse_semver(None) is None


class TestWriteScaffoldVersion:
    # Without a TOML parser (Python 3.9 or 3.10 without tomli), an inline-table or
    # dotted [tool.ngargparser] used to get a second table appended: invalid TOML.
    @pytest.mark.parametrize(
        "content",
        [
            '[project]\nname = "demo"\n\n[tool]\nngargparser = { scaffold_version = "0.4.0" }\n',
            '[project]\nname = "demo"\n\n[tool]\nngargparser.scaffold_version = "0.4.0"\n',
            '[project]\nname = "demo"\ntool.ngargparser.app_name = "demo"\n',
            "[tool.ngargparser.extra]\nx = 1\n",
        ],
        ids=["inline table", "dotted under tool", "dotted at top", "sub-table"],
    )
    def test_no_second_table_without_a_toml_parser(self, tmp_path, monkeypatch, content):
        monkeypatch.setattr(cli, "_toml_reader", lambda: None)
        target = tmp_path / "pyproject.toml"
        target.write_text(content)

        with pytest.raises(ValueError):
            cli.write_scaffold_version(str(target), "1.0.0")
        assert target.read_text() == content

    def test_a_file_without_the_table_still_gets_one_without_a_toml_parser(self, tmp_path, monkeypatch):
        # Control: nothing named ngargparser yet, so the table is added as before.
        monkeypatch.setattr(cli, "_toml_reader", lambda: None)
        target = tmp_path / "pyproject.toml"
        target.write_text('[project]\nname = "demo"\n')

        assert cli.write_scaffold_version(str(target), "1.0.0") is None
        assert target.read_text().count("[tool.ngargparser]") == 1

    def test_missing_file_returns_none_and_creates_nothing(self, tmp_path):
        target = tmp_path / "pyproject.toml"
        assert cli.write_scaffold_version(str(target), "1.0.0") is None
        assert not target.exists()

    def test_inserts_stamp_when_absent(self, tmp_path):
        target = tmp_path / "pyproject.toml"
        target.write_text('[project]\nname = "demo"\n')
        previous = cli.write_scaffold_version(str(target), "1.0.0")
        assert previous is None
        content = target.read_text()
        assert "[tool.ngargparser]" in content
        assert 'scaffold_version = "1.0.0"' in content

    def test_updates_existing_stamp_and_returns_previous(self, tmp_path):
        target = tmp_path / "pyproject.toml"
        target.write_text('[tool.ngargparser]\nscaffold_version = "0.9.0"\n')
        previous = cli.write_scaffold_version(str(target), "1.0.0")
        assert previous == "0.9.0"
        assert 'scaffold_version = "1.0.0"' in target.read_text()

    def test_same_version_is_a_noop(self, tmp_path):
        target = tmp_path / "pyproject.toml"
        original = '[tool.ngargparser]\nscaffold_version = "1.0.0"\n'
        target.write_text(original)
        assert cli.write_scaffold_version(str(target), "1.0.0") == "1.0.0"
        assert target.read_text() == original

    # A "[" between the header and the stamp (a comment) used to hide the stamp, so a
    # second [tool.ngargparser] table was appended and uv refused the file.

    def test_bracket_in_a_comment_before_the_stamp(self, tmp_path):
        target = tmp_path / "pyproject.toml"
        target.write_text(
            '[project]\nname = "demo"\n\n[tool.ngargparser]\n# see [release notes]\nscaffold_version = "0.3.0"\n'
        )

        assert cli.write_scaffold_version(str(target), "1.0.0") == "0.3.0"
        assert target.read_text() == (
            '[project]\nname = "demo"\n\n[tool.ngargparser]\n# see [release notes]\nscaffold_version = "1.0.0"\n'
        )

    def test_table_without_a_stamp_gets_it_inside_not_a_second_table(self, tmp_path):
        target = tmp_path / "pyproject.toml"
        target.write_text('[tool.ngargparser]\nnote = "kept"\n\n[tool.other]\nx = 1\n')

        assert cli.write_scaffold_version(str(target), "1.0.0") is None
        content = target.read_text()
        assert content.count("[tool.ngargparser]") == 1
        assert content == '[tool.ngargparser]\nscaffold_version = "1.0.0"\nnote = "kept"\n\n[tool.other]\nx = 1\n'

    def test_header_with_a_trailing_comment_is_found(self, tmp_path):
        target = tmp_path / "pyproject.toml"
        target.write_text('[tool.ngargparser]  # stamped by cli sync\nscaffold_version = "0.9.0"\n')

        assert cli.write_scaffold_version(str(target), "1.0.0") == "0.9.0"
        assert target.read_text().count("[tool.ngargparser]") == 1

    def test_stamp_in_another_table_is_not_read_as_ours(self, tmp_path):
        target = tmp_path / "pyproject.toml"
        target.write_text('[tool.other]\nscaffold_version = "9.9.9"\n')

        assert cli.write_scaffold_version(str(target), "1.0.0") is None
        assert '[tool.ngargparser]\nscaffold_version = "1.0.0"' in target.read_text()
        assert 'scaffold_version = "9.9.9"' in target.read_text()  # control: theirs untouched

    def test_project_stamp_is_read_past_a_bracket_comment(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[tool.ngargparser]\n# see [notes]\nscaffold_version = "0.3.0"\n')

        assert cli._project_scaffold_version() == "0.3.0"

    # The same writer stamps app_name, and each key leaves the other alone.

    def test_app_name_goes_into_the_table_and_keeps_the_version(self, tmp_path):
        target = tmp_path / "pyproject.toml"
        target.write_text('[tool.ngargparser]\n# see [notes]\nscaffold_version = "1.0.0"\n\n[tool.other]\nx = 1\n')

        assert cli.write_ngargparser_key(str(target), "app_name", "demo") is None
        assert target.read_text() == (
            '[tool.ngargparser]\napp_name = "demo"\n# see [notes]\nscaffold_version = "1.0.0"\n\n[tool.other]\nx = 1\n'
        )
        assert cli.write_scaffold_version(str(target), "2.0.0") == "1.0.0"
        assert 'app_name = "demo"\n' in target.read_text()

    def test_project_app_name_is_read_from_our_table_only(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_text('[tool.other]\napp_name = "theirs"\n')
        assert cli._project_ngargparser_key("app_name") is None

        (tmp_path / "pyproject.toml").write_text('[tool.ngargparser]\napp_name = "ours"\n')
        assert cli._project_ngargparser_key("app_name") == "ours"

    def test_project_key_in_a_file_that_is_not_utf8_is_none(self, tmp_path, monkeypatch):
        # The cli upgrade reminder reads it from any folder; it must not crash there.
        monkeypatch.chdir(tmp_path)
        (tmp_path / "pyproject.toml").write_bytes(b'[tool.ngargparser]\napp_name = "\xff"\n')
        assert cli._project_ngargparser_key("app_name") is None


class TestUpsertReadmeBadge:
    def test_missing_file_returns_false(self, tmp_path):
        assert cli.upsert_readme_badge(str(tmp_path / "README.md"), "1.0.0") is False

    def test_inserts_badge_below_title(self, tmp_path):
        readme = tmp_path / "README.md"
        readme.write_text("My Tool\n=======\n\nBody text.\n")
        assert cli.upsert_readme_badge(str(readme), "1.0.0") is True
        assert "img.shields.io/badge/ngargparser-1.0.0-green.svg" in readme.read_text()

    def test_replaces_existing_badge(self, tmp_path):
        readme = tmp_path / "README.md"
        readme.write_text("My Tool\n=======\n\nBody text.\n")
        cli.upsert_readme_badge(str(readme), "1.0.0")
        assert cli.upsert_readme_badge(str(readme), "2.0.0") is True
        content = readme.read_text()
        assert "ngargparser-2.0.0" in content
        assert "ngargparser-1.0.0" not in content

    def test_identical_badge_is_a_noop(self, tmp_path):
        readme = tmp_path / "README.md"
        readme.write_text("My Tool\n=======\n\nBody text.\n")
        cli.upsert_readme_badge(str(readme), "1.0.0")
        assert cli.upsert_readme_badge(str(readme), "1.0.0") is False


class TestReplaceTextInPlace:
    def test_replaces_all_occurrences(self, tmp_path):
        target = tmp_path / "file.txt"
        target.write_text("NAME says hello to NAME")
        cli.replace_text_in_place(str(target), "NAME", "demo")
        assert target.read_text() == "demo says hello to demo"


class TestEnsureGitignore:
    def test_creates_when_missing(self, tmp_path):
        gi = tmp_path / ".gitignore"
        assert cli.ensure_gitignore(str(gi)) == "created"
        content = gi.read_text()
        assert ".ngargparser/" in content
        assert "*.bak" in content

    def test_appends_block_preserving_user_entries(self, tmp_path):
        gi = tmp_path / ".gitignore"
        gi.write_text("node_modules/\n")
        assert cli.ensure_gitignore(str(gi)) == "updated"
        content = gi.read_text()
        assert "node_modules/" in content  # user entry kept
        assert cli.GITIGNORE_MANAGED_MARKER in content

    def test_noop_when_marker_present(self, tmp_path):
        gi = tmp_path / ".gitignore"
        cli.ensure_gitignore(str(gi))
        before = gi.read_text()
        assert cli.ensure_gitignore(str(gi)) == "unchanged"
        assert gi.read_text() == before

    def test_dry_run_writes_nothing(self, tmp_path):
        gi = tmp_path / ".gitignore"
        assert cli.ensure_gitignore(str(gi), dry_run=True) == "created"
        assert not gi.exists()

    def test_created_file_keeps_configure_output_out_of_git(self, tmp_path):
        cli.ensure_gitignore(str(tmp_path / ".gitignore"))
        assert git_ignores(tmp_path, ".env")
        assert git_ignores(tmp_path, "setup_tc1_env.sh")
        assert not git_ignores(tmp_path, "paths.py")  # control

    def test_old_block_gets_only_the_new_rules(self, tmp_path):
        gi = tmp_path / ".gitignore"
        before = "build/\n\n" + OLD_MANAGED_BLOCK + "\n# mine, after the block\nnotes.txt\n"
        gi.write_text(before)

        assert cli.ensure_gitignore(str(gi)) == "updated"
        added = f"{CONFIGURE_COMMENT}\n.env\nsetup_*_env.sh\n" + HASH_RULE_LINES
        assert gi.read_text() == before.replace(OLD_MANAGED_BLOCK, MIGRATED_BLOCK + added)
        assert cli.ensure_gitignore(str(gi)) == "unchanged"
        assert git_ignores(tmp_path, ".env") and git_ignores(tmp_path, "setup_tc1_env.sh")

    def test_old_block_at_end_of_file_without_newline(self, tmp_path):
        gi = tmp_path / ".gitignore"
        gi.write_text(OLD_MANAGED_BLOCK.rstrip("\n"))

        assert cli.ensure_gitignore(str(gi)) == "updated"
        assert gi.read_text() == cli.GITIGNORE_MANAGED_BLOCK

    def test_rules_the_user_already_has_are_not_repeated(self, tmp_path):
        # axelf: its own .env and setup_*_env.sh lines above the old block.
        gi = tmp_path / ".gitignore"
        before = "__pycache__/\n.env\nsetup_*_env.sh\n\n" + OLD_MANAGED_BLOCK
        gi.write_text(before)

        assert cli.ensure_gitignore(str(gi)) == "updated"
        assert gi.read_text() == "__pycache__/\n.env\nsetup_*_env.sh\n\n" + MIGRATED_BLOCK + HASH_RULE_LINES
        assert cli.ensure_gitignore(str(gi)) == "unchanged"

    def test_block_appended_without_rules_the_user_already_has(self, tmp_path):
        # pepsysco: its own .env and setup_*_env.sh lines, no managed block.
        gi = tmp_path / ".gitignore"
        gi.write_text(".env\nsetup_*_env.sh\n")

        assert cli.ensure_gitignore(str(gi)) == "updated"
        content = gi.read_text()
        assert content == ".env\nsetup_*_env.sh\n\n" + MIGRATED_BLOCK + HASH_RULE_LINES
        assert content.count(".env\n") == 1 and content.count("setup_*_env.sh") == 1
        assert CONFIGURE_COMMENT not in content

    def test_anchored_or_any_depth_rule_counts_as_present(self, tmp_path):
        # bcell-sequence-input: **/.env above the old block.
        gi = tmp_path / ".gitignore"
        before = "**/.env\n\n" + OLD_MANAGED_BLOCK
        gi.write_text(before)

        assert cli.ensure_gitignore(str(gi)) == "updated"
        assert (
            gi.read_text()
            == "**/.env\n\n" + MIGRATED_BLOCK + f"{CONFIGURE_COMMENT}\nsetup_*_env.sh\n" + HASH_RULE_LINES
        )

    def test_rule_the_user_re_includes_is_not_added(self, tmp_path):
        gi = tmp_path / ".gitignore"
        gi.write_text(OLD_MANAGED_BLOCK + "\n!.env\n")

        assert cli.ensure_gitignore(str(gi)) == "updated"
        assert "\n.env\n" not in gi.read_text()
        assert not git_ignores(tmp_path, ".env")

    def test_dry_run_on_old_block_writes_nothing(self, tmp_path):
        gi = tmp_path / ".gitignore"
        gi.write_text(OLD_MANAGED_BLOCK)

        assert cli.ensure_gitignore(str(gi), dry_run=True) == "updated"
        assert gi.read_text() == OLD_MANAGED_BLOCK

    def test_old_ngargparser_rule_is_replaced_so_git_tracks_the_hash_record(self, tmp_path):
        # A file under an ignored folder cannot be re-included: `.ngargparser/` has to become `.ngargparser/*`.
        gi = tmp_path / ".gitignore"
        before = "notes/\n\n" + cli.GITIGNORE_MANAGED_BLOCK.replace(".ngargparser/*\n", ".ngargparser/\n").replace(
            HASH_RULE_LINES, ""
        )
        gi.write_text(before)
        assert git_ignores(tmp_path, ".ngargparser/framework-hashes.json")  # control: the old block ignores it

        assert cli.ensure_gitignore(str(gi), dry_run=True) == "updated"
        assert gi.read_text() == before
        assert cli.ensure_gitignore(str(gi)) == "updated"

        assert gi.read_text() == before.replace(".ngargparser/\n", ".ngargparser/*\n") + HASH_RULE_LINES
        assert not git_ignores(tmp_path, ".ngargparser/framework-hashes.json")
        assert git_ignores(tmp_path, ".ngargparser/sync-backups/x/src/core/app_env.py")
        assert git_ignores(tmp_path, ".ngargparser/libs-stamp")
        assert cli.ensure_gitignore(str(gi)) == "unchanged"

    def test_the_users_own_ngargparser_rule_is_left_alone(self, tmp_path):
        # Only the managed section is rewritten; a rule the developer wrote above it stays as written.
        gi = tmp_path / ".gitignore"
        gi.write_text(".ngargparser/\n\n" + OLD_MANAGED_BLOCK)

        assert cli.ensure_gitignore(str(gi)) == "updated"

        content = gi.read_text()
        assert content.startswith(".ngargparser/\n\n" + MIGRATED_BLOCK)
        assert content.count(".ngargparser/\n") == 1 and HASH_RULE_LINES in content
