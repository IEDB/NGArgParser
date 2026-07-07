from ngargparser import cli


class TestFormatProjectName:
    def test_dashes_become_underscores(self):
        assert cli.format_project_name("aa-counter") == "aa_counter"

    def test_capitalize_produces_capwords(self):
        assert cli.format_project_name("aa-counter", capitalize=True) == "AaCounter"

    def test_plain_name_unchanged(self):
        assert cli.format_project_name("demo") == "demo"


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
