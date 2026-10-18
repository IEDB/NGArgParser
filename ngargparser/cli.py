import argparse
import filecmp
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

# Get the absolute path of the followings
CURR_FILE_PATH = Path(__file__).resolve()
NGPARSER_DIR = CURR_FILE_PATH.parent
TEMPLATE_DIR = NGPARSER_DIR / "templates"
EXAMPLE_DIR = TEMPLATE_DIR / "example-app"


def get_version():
    try:
        import importlib.metadata

        return importlib.metadata.version("ngargparser")
    except Exception:
        return "unknown"


__version__ = get_version()

# A project name becomes a directory, a Python module (run_<name>.py), and a
# class prefix, so it must be a single safe path segment — no separators,
# parent refs, absolute paths, or leading dash (which argparse-style tools and
# shells treat as a flag).
PROJECT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def validate_project_name(name):
    """Return an error message if `name` is unsafe to scaffold, else None.

    Guards against path traversal (``../foo``), absolute paths, nested paths
    (``a/b``), and names that aren't a valid leading directory/module segment.
    """
    if not name or not name.strip():
        return "Project name must not be empty."
    if os.path.isabs(name) or name.startswith("~"):
        return f"Project name must be a plain name, not a path: '{name}'."
    seps = {os.sep} | ({os.altsep} if os.altsep else set())
    if any(s in name for s in seps):
        return f"Project name must not contain path separators: '{name}'."
    if not PROJECT_NAME_RE.match(name):
        return (
            f"Invalid project name '{name}'. Use a letter followed by letters, "
            "digits, underscores, or hyphens (e.g. 'aa-counter')."
        )
    # Belt-and-suspenders: the resolved target must live directly in the CWD.
    if (Path.cwd() / name).resolve().parent != Path.cwd().resolve():
        return f"Project name would resolve outside the current directory: '{name}'."
    return None


def _backup_file(path):
    """Copy `path` to `path`.bak before a destructive rewrite; return the backup
    path (or None if the source didn't exist)."""
    if not os.path.exists(path):
        return None
    backup_path = f"{path}.bak"
    shutil.copy2(path, backup_path)
    return backup_path


GITIGNORE_MANAGED_MARKER = "# --- ngargparser (managed) ---"
# Each group is a comment and the rules it explains. A group's comment is
# written only together with at least one of its rules.
GITIGNORE_MANAGED_RULES = (
    (
        "# Runtime artifacts written by `cli sync` / `cli deps` — keep out of git.",
        (".ngargparser/", "*.bak"),
    ),
    (
        "# Written by ./configure with this machine's paths; rebuilt on each host.",
        (".env", "setup_*_env.sh"),
    ),
)
GITIGNORE_MANAGED_BLOCK = "".join(
    f"{line}\n"
    for line in [GITIGNORE_MANAGED_MARKER]
    + [line for comment, rules in GITIGNORE_MANAGED_RULES for line in (comment, *rules)]
)
GITIGNORE_BASE = """# Python
__pycache__/
*.py[cod]
*.egg-info/
.eggs/
build/
dist/

# Virtual environments
.venv/
venv/

# OS / editor cruft
.DS_Store
"""


def _gitignore_has_rule(lines, rule):
    """True when `lines` already ignore `rule` (as `rule`, `/rule` or `**/rule`),
    or re-include it with `!`, which means the project chose to track it."""
    forms = {rule, f"/{rule}", f"**/{rule}"}
    return any(line in forms or (line.startswith("!") and line[1:] in forms) for line in lines)


def _missing_managed_lines(content):
    """The managed rules `content` doesn't cover yet, each group under its comment."""
    lines = [line.strip() for line in content.splitlines()]
    missing = []
    for comment, rules in GITIGNORE_MANAGED_RULES:
        absent = [rule for rule in rules if not _gitignore_has_rule(lines, rule)]
        if absent:
            missing += [comment, *absent]
    return missing


def ensure_gitignore(path=".gitignore", *, dry_run=False):
    """Ensure `path` exists and covers every ngargparser-managed ignore rule.

    Adds only, and is idempotent: creates the file (base ignores + managed
    block) when missing, appends the managed block when the file exists
    without it, and adds rules a newer release introduced to the end of an
    existing managed section. A rule the file already covers, or re-includes
    with `!`, is never added, and a user's own entries are never rewritten.
    Returns 'created' | 'updated' | 'unchanged'. With `dry_run`, reports the
    action without writing.
    """
    if not os.path.exists(path):
        if not dry_run:
            with open(path, "w", encoding="utf-8") as f:
                f.write(GITIGNORE_BASE.rstrip() + "\n\n" + GITIGNORE_MANAGED_BLOCK)
        return "created"

    with open(path, encoding="utf-8") as f:
        content = f.read()
    missing = _missing_managed_lines(content)
    if not missing:
        return "unchanged"

    if GITIGNORE_MANAGED_MARKER not in content:
        if not dry_run:
            separator = "" if content.endswith("\n") else "\n"
            block = "".join(f"{line}\n" for line in [GITIGNORE_MANAGED_MARKER, *missing])
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{separator}\n{block}")
        return "updated"

    # The managed section runs from the marker to the first blank line.
    parts = content.splitlines(keepends=True)
    start = next((i for i, line in enumerate(parts) if line.strip() == GITIGNORE_MANAGED_MARKER), len(parts))
    end = start + 1
    while end < len(parts) and parts[end].strip():
        end += 1
    end = min(end, len(parts))
    if end and not parts[end - 1].endswith("\n"):
        parts[end - 1] += "\n"
    parts[end:end] = [f"{line}\n" for line in missing]
    if not dry_run:
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(parts))
    return "updated"


def _sync_file(src, dst, *, executable=False, dry_run=False, backup_root=None, changes=None):
    """Copy `src` to `dst` only when their contents differ (or `dst` is missing).

    Records ``('created'|'updated'|'unchanged', dst)`` in `changes`. When an
    existing file would be overwritten and `backup_root` is set, its current
    content is first copied under `backup_root` (preserving `dst`'s relative
    path) — unless `dry_run` is set, in which case nothing is written. Returns
    the action string.
    """
    exists = os.path.exists(dst)
    if exists and filecmp.cmp(src, dst, shallow=False):
        if changes is not None:
            changes.append(("unchanged", dst))
        return "unchanged"

    action = "updated" if exists else "created"
    if not dry_run:
        if exists and backup_root is not None:
            backup_path = os.path.join(backup_root, dst)
            os.makedirs(os.path.dirname(backup_path) or ".", exist_ok=True)
            shutil.copy2(dst, backup_path)
        parent = os.path.dirname(dst)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.copy(src, dst)
        if executable:
            os.chmod(dst, 0o755)
    if changes is not None:
        changes.append((action, dst))
    return action


def format_project_name(name, capitalize=False):
    name = name.replace("-", "_")

    if capitalize:
        name = name.split("_")
        name = [_.capitalize() for _ in name]
        name = "".join(name)

    return name


def write_pyproject_toml(project_dir, tool_name):
    """Render templates/pyproject.toml.tmpl into project_dir with {TOOL_NAME} substituted,
    then attempt to generate uv.lock via `uv lock` if uv is available."""
    import subprocess

    template_path = TEMPLATE_DIR / "pyproject.toml.tmpl"
    target_path = os.path.join(project_dir, "pyproject.toml")

    shutil.copy(template_path, target_path)
    replace_text_in_place(target_path, "{TOOL_NAME}", tool_name)
    replace_text_in_place(target_path, "{NGARGPARSER_VERSION}", __version__)

    if shutil.which("uv"):
        try:
            subprocess.run(["uv", "lock"], cwd=project_dir, check=True)
            print(f"\033[92m✓\033[0m Generated 'uv.lock' for '{project_dir}'.")
        except subprocess.CalledProcessError as e:
            print(f"\033[93m⚠\033[0m  'uv lock' failed (exit {e.returncode}); run it manually in '{project_dir}'.")
    else:
        print(
            "\033[93m⚠\033[0m  'uv' not found on PATH; skipping 'uv lock'. Run 'uv lock' inside the project once uv is installed."
        )


SCAFFOLD_STAMP_RE = re.compile(
    r'(\[tool\.ngargparser\][^\[]*?scaffold_version\s*=\s*")([^"]*)(")',
    re.DOTALL,
)


def write_scaffold_version(pyproject_path, version):
    """Write or update [tool.ngargparser] scaffold_version in pyproject.toml.
    Returns the previous version string, or None if no stamp existed before."""
    if not os.path.exists(pyproject_path):
        return None

    with open(pyproject_path, "r", encoding="utf-8") as f:
        content = f.read()

    match = SCAFFOLD_STAMP_RE.search(content)
    if match:
        previous = match.group(2)
        if previous == version:
            return previous
        new_content = content[: match.start(2)] + version + content[match.end(2) :]
    else:
        previous = None
        if not content.endswith("\n"):
            content += "\n"
        new_content = content + f'\n[tool.ngargparser]\nscaffold_version = "{version}"\n'

    with open(pyproject_path, "w", encoding="utf-8") as f:
        f.write(new_content)
    return previous


def create_example_structure():
    project_name = "aa-counter"

    # Fail fast if the target already exists, before writing anything.
    try:
        os.makedirs(project_name)
    except FileExistsError:
        print(f"\033[91m✗\033[0m '{project_name}' already exists; aborting.")
        return 1

    try:
        # Create directory structure
        os.makedirs(os.path.join(project_name, "src"))
        os.makedirs(os.path.join(project_name, "src", "core"))
        os.makedirs(os.path.join(project_name, "scripts"))
        os.makedirs(os.path.join(project_name, "scripts", "core"))

        # Create necessary files
        parser_file = "AACounterArgumentParser.py"
        update_and_place_readme(f"{EXAMPLE_DIR}/README", project_name, is_example=True)
        # shutil.copy('./misc/README', f'{project_name}/README')
        shutil.copy(f"{EXAMPLE_DIR}/example.json", f"{project_name}/src/example.json")
        shutil.copy(f"{EXAMPLE_DIR}/example.tsv", f"{project_name}/src/example.tsv")
        shutil.copy(f"{EXAMPLE_DIR}/run_aa_counter.py", f"{project_name}/src/run_aa_counter.py")
        shutil.copy(f"{EXAMPLE_DIR}/{parser_file}", f"{project_name}/src/{parser_file}")
        shutil.copy(f"{EXAMPLE_DIR}/preprocess.py", f"{project_name}/src/preprocess.py")
        shutil.copy(f"{EXAMPLE_DIR}/postprocess.py", f"{project_name}/src/postprocess.py")
        shutil.copy(f"{TEMPLATE_DIR}/configure.py", f"{project_name}/src/core/configure.py")
        # Make configure.py executable
        os.chmod(f"{project_name}/src/core/configure.py", 0o755)

        # Copy core files to protected core/ directory
        shutil.copy(f"{NGPARSER_DIR}/NGArgumentParser.py", f"{project_name}/src/core/NGArgumentParser.py")
        shutil.copy(f"{NGPARSER_DIR}/core_validators.py", f"{project_name}/src/core/core_validators.py")
        shutil.copy(f"{TEMPLATE_DIR}/set_pythonpath.py", f"{project_name}/src/core/set_pythonpath.py")
        shutil.copy(f"{NGPARSER_DIR}/result_writer.py", f"{project_name}/src/core/result_writer.py")
        shutil.copy(f"{NGPARSER_DIR}/app_env.py", f"{project_name}/src/core/app_env.py")

        # Create __init__.py for core package
        with open(f"{project_name}/src/core/__init__.py", "w") as f:
            f.write("")

        # Copy user-modifiable files to src/
        shutil.copy(f"{NGPARSER_DIR}/validators.py", f"{project_name}/src/validators.py")

        # Framework-owned: scripts/core/build.sh (cli sync overwrites this)
        shutil.copy(f"{TEMPLATE_DIR}/build.sh", f"{project_name}/scripts/core/build.sh")
        os.chmod(f"{project_name}/scripts/core/build.sh", 0o755)
        # Framework-owned: root Makefile (cli sync overwrites this)
        shutil.copy(f"{TEMPLATE_DIR}/Makefile", f"{project_name}/Makefile")
        # User-owned: scripts/core/build.conf, .distignore (root)
        shutil.copy(f"{TEMPLATE_DIR}/build.conf", f"{project_name}/scripts/core/build.conf")
        shutil.copy(f"{TEMPLATE_DIR}/distignore", f"{project_name}/.distignore")

        # User-owned: deploy/install.sh — invoked by nxg-tools-deployments at deploy time.
        os.makedirs(f"{project_name}/deploy", exist_ok=True)
        shutil.copy(f"{TEMPLATE_DIR}/deploy/install.sh", f"{project_name}/deploy/install.sh")
        replace_text_in_place(f"{project_name}/deploy/install.sh", "{TOOL_NAME}", project_name)

        # Copy the configure launcher (forwards args to src/core/configure.py)
        shutil.copy(f"{TEMPLATE_DIR}/configure", f"{project_name}/configure")
        os.chmod(f"{project_name}/configure", 0o755)

        # Render pyproject.toml from template (and lock with uv if available)
        write_pyproject_toml(project_name, project_name)

        print(f"Created '{project_name}' project structure successfully.")
        return 0
    except Exception as e:
        print(f"\033[91m✗ Error: {e}\033[0m")
        shutil.rmtree(project_name, ignore_errors=True)
        print(f"  └ Cleaned up partial project '{project_name}'.")
        return 1


def create_project_structure(project_name):
    # Fail fast if the target already exists, before writing anything.
    try:
        os.makedirs(project_name)
    except FileExistsError:
        print(f"\033[91m✗\033[0m '{project_name}' already exists; aborting.")
        return 1

    try:
        # Create directory structure
        os.makedirs(os.path.join(project_name, "src"))
        os.makedirs(os.path.join(project_name, "src", "core"))
        os.makedirs(os.path.join(project_name, "scripts"))
        os.makedirs(os.path.join(project_name, "scripts", "core"))

        # Create necessary files
        exec_file = f"run_{format_project_name(project_name)}.py"
        parser_file = f"{format_project_name(project_name, capitalize=True)}ArgumentParser.py"
        parser_name = f"{format_project_name(project_name, capitalize=True)}ArgumentParser"
        update_and_place_readme(f"{TEMPLATE_DIR}/README.md", project_name, plain_file_path=f"{TEMPLATE_DIR}/README")
        shutil.copy(f"{TEMPLATE_DIR}/run_app.py", f"{project_name}/src/{exec_file}")
        shutil.copy(f"{NGPARSER_DIR}/NGChildArgumentParser.py", f"{project_name}/src/{parser_file}")
        shutil.copy(f"{TEMPLATE_DIR}/preprocess.py", f"{project_name}/src/preprocess.py")
        shutil.copy(f"{TEMPLATE_DIR}/postprocess.py", f"{project_name}/src/postprocess.py")
        shutil.copy(f"{TEMPLATE_DIR}/configure.py", f"{project_name}/src/core/configure.py")
        # Make configure.py executable
        os.chmod(f"{project_name}/src/core/configure.py", 0o755)

        # Copy core files to protected core/ directory
        shutil.copy(f"{NGPARSER_DIR}/NGArgumentParser.py", f"{project_name}/src/core/NGArgumentParser.py")
        shutil.copy(f"{NGPARSER_DIR}/core_validators.py", f"{project_name}/src/core/core_validators.py")
        shutil.copy(f"{TEMPLATE_DIR}/set_pythonpath.py", f"{project_name}/src/core/set_pythonpath.py")
        shutil.copy(f"{NGPARSER_DIR}/result_writer.py", f"{project_name}/src/core/result_writer.py")
        shutil.copy(f"{NGPARSER_DIR}/app_env.py", f"{project_name}/src/core/app_env.py")

        # Create __init__.py for core package
        with open(f"{project_name}/src/core/__init__.py", "w") as f:
            f.write("")

        # Copy user-modifiable files to src/
        shutil.copy(f"{NGPARSER_DIR}/validators.py", f"{project_name}/src/validators.py")

        # Framework-owned: scripts/core/build.sh + root Makefile (cli sync overwrites these)
        shutil.copy(f"{TEMPLATE_DIR}/build.sh", f"{project_name}/scripts/core/build.sh")
        os.chmod(f"{project_name}/scripts/core/build.sh", 0o755)
        shutil.copy(f"{TEMPLATE_DIR}/Makefile", f"{project_name}/Makefile")
        # User-owned: scripts/core/build.conf, get_libs.sh, .distignore (root) (sync leaves alone).
        # scripts/hooks.sh is optional and not created; templates/hooks.sh is the starting point.
        shutil.copy(f"{TEMPLATE_DIR}/build.conf", f"{project_name}/scripts/core/build.conf")
        shutil.copy(f"{TEMPLATE_DIR}/distignore", f"{project_name}/.distignore")
        shutil.copy(f"{TEMPLATE_DIR}/get_libs.sh", f"{project_name}/scripts/get_libs.sh")
        os.chmod(f"{project_name}/scripts/get_libs.sh", 0o755)

        # User-owned: deploy/install.sh — invoked by nxg-tools-deployments at deploy time.
        os.makedirs(f"{project_name}/deploy", exist_ok=True)
        shutil.copy(f"{TEMPLATE_DIR}/deploy/install.sh", f"{project_name}/deploy/install.sh")
        replace_text_in_place(f"{project_name}/deploy/install.sh", "{TOOL_NAME}", project_name)

        # Try to copy license file, but don't fail if it's not available
        license_source = f"{NGPARSER_DIR}/license-LJI.txt"
        if os.path.exists(license_source):
            shutil.copy(license_source, f"{project_name}/license-LJI.txt")
        else:
            print(f"Warning: License file not found at {license_source}")
            print("Skipping license file copy.")

        # Add default content to all the files
        replace_text_in_place(f"{project_name}/src/{exec_file}", "CHILDPARSER", parser_name)
        replace_text_in_place(f"{project_name}/src/{parser_file}", "ChildArgumentParser", parser_name)
        replace_text_in_place(f"{project_name}/src/core/configure.py", "PROJECT_NAME", project_name)

        # Copy the configure launcher (forwards args to src/core/configure.py)
        shutil.copy(f"{TEMPLATE_DIR}/configure", f"{project_name}/configure")
        os.chmod(f"{project_name}/configure", 0o755)

        # Render pyproject.toml from template (and lock with uv if available)
        write_pyproject_toml(project_name, project_name)

        print(f"Created '{project_name}' project structure successfully.")
        return 0
    except Exception as e:
        print(f"\033[91m✗ Error: {e}\033[0m")
        shutil.rmtree(project_name, ignore_errors=True)
        print(f"  └ Cleaned up partial project '{project_name}'.")
        return 1


def update_and_place_readme(file_path, app_name, is_example=False, plain_file_path=None):
    # Copy over and generate README files (both README.md and README).
    # README.md comes from `file_path` and gets the version badge. README comes
    # from `plain_file_path` when given (a blank tool's end-user README, which
    # ships in the tarball), else from `file_path` too (the example walkthrough).
    readme_md_path = f"{app_name}/README.md"
    readme_plain_path = f"{app_name}/README"
    # Ensure we have a starting file to read from
    shutil.copy(file_path, readme_md_path)

    def replace_variables(content):
        content = content.replace("{TOOL_NAME}", app_name)
        content = content.replace("{TOOL_EXEC_NAME}", format_project_name(app_name))
        if is_example:
            return content.replace("{TOOL_NAME_CAP}", "AACounter")
        return content.replace("{TOOL_NAME_CAP}", format_project_name(app_name, capitalize=True))

    with open(readme_md_path, "r") as file:
        content = file.read()

    # Replace variables in README
    base_updated_content = replace_variables(content)

    # Insert ngargparser version badge just below the title if possible
    try:
        badge_md = f"[![ngargparser](https://img.shields.io/badge/ngargparser-{__version__}-blue.svg)](https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser)"
        lines = base_updated_content.splitlines(True)  # keep line endings
        if len(lines) >= 2:
            # Common template starts with title and an underline of dashes
            lines.insert(2, f"\n{badge_md}\n\n")
            content_with_badge = "".join(lines)
        else:
            content_with_badge = f"{badge_md}\n\n" + base_updated_content
    except Exception:
        # If anything goes wrong, proceed without badge insertion
        content_with_badge = base_updated_content

    plain_content = base_updated_content
    if plain_file_path:
        with open(plain_file_path, "r") as file:
            plain_content = replace_variables(file.read())

    # Write the updated content to both README.md and README
    with open(readme_md_path, "w") as f_md:
        f_md.write(content_with_badge)  # README.md includes badge
    with open(readme_plain_path, "w") as f_plain:
        f_plain.write(plain_content)  # README (no extension) has no badge


def replace_text_in_place(file_path, old_text, new_text):
    """
    Replaces all occurrences of old_text with new_text in the specified file.

    :param file_path: Path to the file where the replacement should occur.
    :param old_text: Text to be replaced.
    :param new_text: Text to replace with.
    """
    try:
        # Read the content from the file
        with open(file_path, "r") as file:
            content = file.read()

        # Replace all occurrences of old_text with new_text
        modified_content = content.replace(old_text, new_text)

        # Write the modified content back to the same file
        with open(file_path, "w") as file:
            file.write(modified_content)

    except Exception as e:
        print(f"\033[91m✗ An error occurred: {e}\033[0m")


def upsert_readme_badge(readme_path: str, version: str, color: str = "green") -> bool:
    """
    Update or insert the ngargparser version badge in the given README file.
    Returns True if the file was modified, False otherwise.
    """
    try:
        if not os.path.exists(readme_path):
            return False
        with open(readme_path, "r", encoding="utf-8") as f:
            content = f.read()
        new_badge = f"[![ngargparser](https://img.shields.io/badge/ngargparser-{version}-{color}.svg)](https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser)"
        import re

        pattern = r"\[!\[ngargparser\]\(https://img\.shields\.io/badge/ngargparser-[^-/\s)]+-[^)\s]+\.svg\)\]\([^)]+\)"
        if re.search(pattern, content):
            updated = re.sub(pattern, new_badge, content, count=1)
            if updated != content:
                with open(readme_path, "w", encoding="utf-8") as f:
                    f.write(updated)
                return True
            return False
        # If no existing badge, insert below title when possible, else prepend
        lines = content.splitlines(True)
        if len(lines) >= 2:
            lines.insert(2, f"\n{new_badge}\n\n")
            updated = "".join(lines)
        else:
            updated = f"{new_badge}\n\n" + content
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(updated)
        return True
    except Exception as e:
        print(f"\033[93m⚠\033[0m Skipped updating README badge for {readme_path}: {e}")
        return False


def normalize_content_ending(content):
    """
    Normalize the content to ensure it ends with exactly one newline.

    Args:
        content (str): The content to normalize

    Returns:
        str: Content with exactly one trailing newline
    """
    return content.strip() + "\n"


def normalize_name(name):
    """
    Normalize a dependency name to a valid Python variable name.

    Args:
        name (str): The original dependency name

    Returns:
        str: Normalized variable name
    """
    # Convert to lowercase and replace spaces/special chars with underscores
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", name.lower())
    # Remove multiple consecutive underscores
    normalized = re.sub(r"_+", "_", normalized)
    # Remove leading/trailing underscores
    normalized = normalized.strip("_")
    return normalized


def format_display_name(name):
    """
    Format the dependency name for display in comments.
    Handles Roman numerals (i, ii) specially.

    Args:
        name (str): The original dependency name

    Returns:
        str: Formatted display name
    """
    # Split by common separators and capitalize each word
    words = re.split(r"[_\s-]+", name)
    formatted_words = []

    for word in words:
        if word:  # Skip empty strings
            # Check if word is a Roman numeral (i, ii, iii, iv, v, etc.)
            if word.lower() in ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"]:
                formatted_words.append(word.upper())
            else:
                formatted_words.append(word.capitalize())

    return " ".join(formatted_words)


def generate_dependency_section(name):
    """
    Generate the configuration section for a dependency.

    Args:
        name (str): The dependency name

    Returns:
        str: The configuration section
    """
    display_name = format_display_name(name)
    var_name = normalize_name(name)

    section = f"""\n
''' [ {display_name} ] '''
# Path to the {display_name} tool (required)
{var_name}_path=None

# Path to the {display_name} virtual environment (optional)
{var_name}_venv=None

# Name of the environment module to be activated (optional).
# Most users can keep this as None
{var_name}_module=None

# If using a different Python environment or binary for this tool compared to others,
# you may need to update LD_LIBRARY_PATH to ensure the correct libraries are used.
# If you're using the same environment across all tools, this can usually be left empty.
# (optional)
{var_name}_lib_path=None"""
    return section


def get_dependencies():
    """
    Get the list of dependencies from user input.

    Returns:
        list: List of dependency names
    """
    dependencies = []

    print("\nEnter the names of all dependencies that the current app depends on.")
    print("Press Enter after each dependency name.")
    print("Press Enter on an empty line when you're done.\n")

    while True:
        dep = input("Dependency name (or press Enter to finish): ").strip()
        if not dep:
            break
        dependencies.append(dep)

    return dependencies


def create_paths_file(project_name_or_path):
    """
    Create the paths.py file based on user input for dependencies.
    Integrated configuration functionality from configure.py.
    """
    print("=== Setting up paths.py configuration ===")

    # Check if this is a project name (for startapp) or a direct file path (for setup-paths)
    if project_name_or_path.endswith(".py"):
        # Direct file path
        paths_file_path = project_name_or_path
    else:
        # Project name - create in project/src/ structure
        if project_name_or_path == "example":
            project_name_or_path = "aa-counter"

        paths_file_path = f"{project_name_or_path}/paths.py"

    try:
        # Check if paths.py already exists
        if Path(paths_file_path).exists():
            print("\n\033[93m⚠\033[0m  'paths.py' already exists!")
            while True:
                response = input("Do you want to overwrite it? (y/n): ").lower().strip()
                if response in ["y", "yes"]:
                    break
                elif response in ["n", "no"]:
                    print("Skipping paths.py creation. Existing file was not modified.")
                    return 0
                else:
                    print("Please enter 'y' for yes or 'n' for no.")

        # Get dependencies from user, refusing names ./configure couldn't see as a tool
        dependencies = []
        refused = False
        for dep in get_dependencies():
            reason = _invalid_dep_name(dep)
            if reason:
                print(f"\033[91m✗\033[0m Not added: {reason}")
                refused = True
            else:
                dependencies.append(dep)

        if not dependencies:
            print("No dependencies specified. Creating empty paths.py file.")
            # Create empty file
            with open(paths_file_path, "w", encoding="utf-8") as f:
                f.write("")
            print(f"\n\033[92m✓\033[0m Created empty '\033[92m{paths_file_path}\033[0m'.")
            return 1 if refused else 0
        else:
            print(f"Found {len(dependencies)} dependencies:")
            for i, dep in enumerate(dependencies, 1):
                print(f"  {i}. {dep}")

        # Create the content
        content = ""

        # Add dependency sections if any
        if dependencies:
            for dep in dependencies:
                content += generate_dependency_section(dep)

        # Normalize content ending to ensure exactly one newline
        content = normalize_content_ending(content)

        # Write to paths.py
        with open(paths_file_path, "w", encoding="utf-8") as f:
            f.write(content)

        print(
            f"\n\033[92m✓\033[0m Created '\033[92m{paths_file_path}\033[0m' with \033[92m{len(dependencies)}\033[0m dependencies."
        )
        print(f"You can now edit '{paths_file_path}' to set the actual paths for your dependencies.")
        return 1 if refused else 0

    except KeyboardInterrupt:
        print("\n\n\033[91m✗\033[0m Paths.py configuration cancelled.")
        return 1
    except Exception as e:
        print(f"\n\033[91m✗\033[0m Error creating paths.py: \033[91m{e}\033[0m")
        return 1


# What makes a dependency is ./configure's rule (templates/configure.py,
# detect_dependency_tools): a top-level `<name>_path = ...` whose <name> doesn't
# end in one of these. The ''' [ Title ] ''' line above a block is only a label.
DEP_NAME_FIELD_SUFFIXES = ("_lib", "_venv", "_module")
# The settings of one dependency, by variable suffix.
DEP_FIELDS = ("_path", "_venv", "_module", "_lib_path")
_DEP_TITLE_RE = re.compile(r"\s*\[\s*([^\]]+?)\s*\]\s*")


class PathsFileError(Exception):
    """paths.py can't be read or doesn't parse; nothing may be written to it."""


def _paths_dependencies(content):
    """Return the dependencies declared in paths.py `content`, in file order.

    {name: {"display", "path", "start", "end"}}: `display` is the title right
    above the block, or the name when there is none; `path` is the source text
    of `<name>_path`; `start`/`end` are the block's 1-based first and last lines
    (from the title, or the first setting, through the last setting).
    """
    import ast

    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        raise PathsFileError(f"paths.py doesn't parse (line {e.lineno}): {e.msg}") from e

    assigns = {}  # variable -> (index in the module body, node)
    for i, node in enumerate(tree.body):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            assigns[node.targets[0].id] = (i, node)

    deps = {}
    for var, (_, path_node) in assigns.items():
        if not var.endswith("_path"):
            continue
        name = var[: -len("_path")]
        if not name or name.endswith(DEP_NAME_FIELD_SUFFIXES):
            continue
        fields = [assigns[name + suffix] for suffix in DEP_FIELDS if name + suffix in assigns]
        first = min(i for i, _ in fields)
        start = min(node.lineno for _, node in fields)
        end = max(node.end_lineno for _, node in fields)
        display = name
        if first > 0:
            above = tree.body[first - 1]
            if (
                isinstance(above, ast.Expr)
                and isinstance(above.value, ast.Constant)
                and isinstance(above.value.value, str)
            ):
                match = _DEP_TITLE_RE.fullmatch(above.value.value)
                if match:
                    display = match.group(1)
                    start = above.lineno
        deps[name] = {
            "display": display,
            "path": ast.get_source_segment(content, path_node.value) or "None",
            "start": start,
            "end": end,
        }
    return deps


def _read_paths(file_path):
    """Return (content, dependencies) of an existing paths.py; raise PathsFileError."""
    try:
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        raise PathsFileError(f"can't read '{file_path}': {e}") from e
    return content, _paths_dependencies(content)


def _invalid_dep_name(name):
    """Why `name` can't be a dependency name, or None when it can."""
    var = normalize_name(name)
    if not var:
        return f"'{name}' has no letters or digits"
    for suffix in (*DEP_NAME_FIELD_SUFFIXES, "_path"):
        if var.endswith(suffix):
            return (
                f"'{name}' becomes '{var}', which ends in '{suffix}': ./configure would read "
                f"{var}_path as a setting of another tool, not as a tool. Pick a name that doesn't "
                f"end in _lib, _venv, _module or _path."
            )
    return None


def parse_existing_paths_file(file_path):
    """
    Parse an existing paths.py file to extract current dependencies.

    Uses ./configure's rule (see _paths_dependencies), so both see the same tools.

    Args:
        file_path (str): Path to the existing paths file

    Returns:
        tuple: (file_content, {name: display_name}), or (None, {}) when the file
        can't be read or doesn't parse (the reason is printed).
    """
    try:
        content, deps = _read_paths(file_path)
    except PathsFileError as e:
        print(f"\n\033[91m✗\033[0m {e}")
        return None, {}
    return content, {name: dep["display"] for name, dep in deps.items()}


def update_paths_file(file_path, existing_content, existing_deps):
    """
    Update an existing paths file with new dependencies.

    Args:
        file_path (str): Path to the paths file
        existing_content (str): Current file content
        existing_deps (dict): Dictionary of existing dependencies
    """
    print(f"=== Updating existing paths configuration at {file_path} ===")

    if existing_deps:
        print(f"\nFound {len(existing_deps)} existing dependencies:")
        for i, (var_name, display_name) in enumerate(existing_deps.items(), 1):
            print(f"  {i}. {display_name}")

    # Get new dependencies from user
    print("\nAdd new dependencies:")
    new_dependencies = get_dependencies()

    if not new_dependencies:
        print("No new dependencies to add.")
        return 0

    # Check for duplicates and prepare new sections
    new_sections = []
    skipped = []
    refused = False

    for dep in new_dependencies:
        var_name = normalize_name(dep)
        reason = _invalid_dep_name(dep)
        if reason:
            print(f"\033[91m✗\033[0m Not added: {reason}")
            refused = True
        elif var_name in existing_deps:
            skipped.append(dep)
        else:
            new_sections.append(generate_dependency_section(dep))

    if skipped:
        print(f"\nSkipped {len(skipped)} dependencies (already exist):")
        for dep in skipped:
            print(f"  - {dep}")

    if not new_sections:
        print("No new dependencies to add after checking for duplicates.")
        return 1 if refused else 0

    # Append new sections to existing content
    # Remove any trailing whitespace from existing content first
    existing_content = existing_content.rstrip()

    # If this is the first dependency being added, ensure no empty lines at the top
    if not existing_deps:
        # For the first dependency, remove all leading newlines
        first_section = new_sections[0].lstrip("\n")  # Remove leading newlines
        remaining_sections = new_sections[1:] if len(new_sections) > 1 else []

        # If existing content is empty or just whitespace, don't add any newline
        if not existing_content.strip():
            updated_content = first_section + "".join(remaining_sections)
        else:
            # If there's existing content, add one newline to separate
            updated_content = existing_content + "\n" + first_section + "".join(remaining_sections)
    else:
        # For subsequent dependencies, use the normal format
        updated_content = existing_content + "".join(new_sections)

    # Normalize content ending to ensure exactly one newline
    updated_content = normalize_content_ending(updated_content)

    # Write updated content
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(updated_content)

        print(
            f"\n\033[92m✓\033[0m Updated '\033[92m{file_path}\033[0m' with \033[92m{len(new_sections)}\033[0m new dependencies."
        )
        print(f"You can now edit '{file_path}' to set the actual paths for your dependencies.")
        return 1 if refused else 0

    except Exception as e:
        print(f"\n\033[91m✗\033[0m Error updating file: \033[91m{e}\033[0m")
        return 1


def remove_dependencies_from_file(file_path, existing_content, existing_deps):
    """
    Remove dependencies from an existing paths file.

    Args:
        file_path (str): Path to the paths file
        existing_content (str): Current file content
        existing_deps (dict): Dictionary of existing dependencies
    """
    print(f"=== Removing dependencies from {file_path} ===")

    if not existing_deps:
        print("No dependencies found to remove.")
        return 0

    print("\nCurrent dependencies:")
    dep_list = list(existing_deps.items())
    for i, (var_name, display_name) in enumerate(dep_list, 1):
        print(f"  {i}. {display_name}")

    # Get dependencies to remove
    print("\nEnter the numbers of dependencies to remove (e.g., 1,3,5 or 1-3):")
    print("Press Enter to cancel removal.")

    while True:
        selection = input("Dependencies to remove: ").strip()
        if not selection:
            print("Removal cancelled.")
            return 0

        try:
            # Parse selection (support both comma-separated and ranges)
            indices_to_remove = set()

            for part in selection.split(","):
                part = part.strip()
                if "-" in part:
                    # Handle range (e.g., 1-3)
                    start, end = map(int, part.split("-"))
                    indices_to_remove.update(range(start, end + 1))
                else:
                    # Handle single number
                    indices_to_remove.add(int(part))

            # Validate indices
            valid_indices = set(range(1, len(dep_list) + 1))
            invalid_indices = indices_to_remove - valid_indices

            if invalid_indices:
                print(f"Invalid indices: {sorted(invalid_indices)}. Please use numbers 1-{len(dep_list)}.")
                continue

            break

        except ValueError:
            print("Invalid format. Use numbers separated by commas (e.g., 1,3,5) or ranges (e.g., 1-3).")

    # Get dependencies to remove
    deps_to_remove = []
    for idx in sorted(indices_to_remove):
        var_name, display_name = dep_list[idx - 1]
        deps_to_remove.append((var_name, display_name))

    # Confirm removal
    print("\nDependencies to remove:")
    for var_name, display_name in deps_to_remove:
        print(f"  - {display_name}")

    while True:
        confirm = input(f"\nRemove {len(deps_to_remove)} dependencies? (y/n): ").lower().strip()
        if confirm in ["y", "yes"]:
            break
        elif confirm in ["n", "no"]:
            print("Removal cancelled.")
            return 0
        else:
            print("Please enter 'y' for yes or 'n' for no.")

    # Same removal as `cli deps remove`: exact block lines, a backup first, and
    # a re-read afterwards so only what is really gone is reported.
    result = _remove_deps(file_path, [var_name for var_name, _ in deps_to_remove])
    return 1 if result["not_found"] or result["refused"] or result["error"] else 0


def _add_deps(file_path, names):
    """Append a stub block per name to `file_path` (created if missing).

    Returns {"added", "skipped", "rejected", "error"}. A name that ./configure
    couldn't see as a tool is rejected; one already declared is skipped; a
    paths.py that can't be read or doesn't parse is left untouched (`error`).
    """
    result = {"added": [], "skipped": [], "rejected": [], "error": None}
    for name in names:
        reason = _invalid_dep_name(name)
        if reason:
            result["rejected"].append(name)
            print(f"\033[91m✗\033[0m Not added: {reason}")
    wanted = [n for n in names if n not in result["rejected"]]
    if not wanted:
        return result

    target = Path(file_path)
    existing_content, existing_names = "", set()
    if target.exists():
        try:
            existing_content, deps = _read_paths(file_path)
        except PathsFileError as e:
            result["error"] = str(e)
            print(f"\033[91m✗\033[0m {e} Fix it, then run cli deps again; nothing was changed.")
            return result
        existing_names = set(deps)

    had_existing_deps = bool(existing_names)
    new_sections = []
    for name in wanted:
        var_name = normalize_name(name)
        if var_name in existing_names:
            result["skipped"].append(name)
        else:
            new_sections.append(generate_dependency_section(name))
            existing_names.add(var_name)
            result["added"].append(name)

    if result["skipped"]:
        print(f"\033[93m⚠\033[0m  Skipped (already declared): {', '.join(result['skipped'])}")

    if not new_sections:
        return result

    existing_content = existing_content.rstrip()
    if not had_existing_deps:
        first_section = new_sections[0].lstrip("\n")
        remaining = "".join(new_sections[1:])
        if not existing_content.strip():
            updated_content = first_section + remaining
        else:
            updated_content = existing_content + "\n" + first_section + remaining
    else:
        updated_content = existing_content + "".join(new_sections)

    updated_content = normalize_content_ending(updated_content)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(updated_content)

    print(
        f"\033[92m✓\033[0m Added {len(new_sections)} dependency stub(s) to '{file_path}': {', '.join(result['added'])}"
    )
    print(f"   Edit '{file_path}' to fill in the actual paths.")
    return result


def add_deps_to_paths(file_path, names):
    """Non-interactive equivalent of `setup_paths_file`'s "add" path.
    Appends a stub block for each name in `names` to `file_path`, skipping
    any already declared and rejecting any ./configure couldn't see as a
    tool. Creates the file if missing. Returns the count of blocks added."""
    if not names:
        return 0
    return len(_add_deps(file_path, names)["added"])


def _remove_deps(file_path, names):
    """Delete the block of each named dependency (by name or by title).

    Returns {"removed", "not_found", "refused", "error"}. A block whose lines
    overlap another dependency's is refused rather than cut apart. The file is
    backed up first, and re-read afterwards: only what is really gone counts.
    """
    result = {"removed": [], "not_found": [], "refused": [], "error": None}
    if not Path(file_path).exists():
        result["error"] = f"'{file_path}' not found"
        print(f"\033[91m✗\033[0m '{file_path}' not found. Nothing to remove.")
        return result
    try:
        content, deps = _read_paths(file_path)
    except PathsFileError as e:
        result["error"] = str(e)
        print(f"\033[91m✗\033[0m {e} Fix it, then run cli deps again; nothing was changed.")
        return result

    targets = []
    for n in names:
        match = next(
            (
                name
                for name, dep in deps.items()
                if normalize_name(n) in (name, normalize_name(dep["display"])) or n == dep["display"]
            ),
            None,
        )
        if match is None:
            result["not_found"].append(n)
        elif match not in targets:
            targets.append(match)

    for name in list(targets):
        dep = deps[name]
        others = [
            other
            for other, o in deps.items()
            if other not in targets and o["start"] <= dep["end"] and dep["start"] <= o["end"]
        ]
        if others:
            targets.remove(name)
            result["refused"].append(name)
            print(
                f"\033[91m✗\033[0m Not removed: the lines of '{name}' are mixed with those of "
                f"{', '.join(others)}. Edit '{file_path}' by hand."
            )

    if result["not_found"]:
        print(f"\033[93m⚠\033[0m  Not in '{file_path}' (skipped): {', '.join(result['not_found'])}")
    if not targets:
        return result

    drop = set()
    for name in targets:
        drop.update(range(deps[name]["start"], deps[name]["end"] + 1))
    lines = content.splitlines(keepends=True)
    updated = "".join(line for number, line in enumerate(lines, 1) if number not in drop)
    updated = normalize_content_ending(re.sub(r"\n{3,}", "\n\n", updated).lstrip("\n"))

    try:
        remaining = _paths_dependencies(updated)
    except PathsFileError as e:
        result["error"] = str(e)
        print(f"\033[91m✗\033[0m Removing would break '{file_path}' ({e}); nothing was changed.")
        return result

    backup_path = _backup_file(file_path)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(updated)

    result["removed"] = [name for name in targets if name not in remaining]
    print(
        f"\033[92m✓\033[0m Removed {len(result['removed'])} dependency block(s) from '{file_path}': "
        f"{', '.join(deps[n]['display'] for n in result['removed'])}"
    )
    if backup_path:
        print(f"  └ Backup saved to '{backup_path}'.")
    return result


def remove_deps_from_paths(file_path, names):
    """Non-interactive equivalent of `setup_paths_file`'s "remove" path.
    Deletes the block of each dependency whose name or title matches an entry
    in `names`. Returns the count of blocks removed."""
    if not names:
        return 0
    return len(_remove_deps(file_path, names)["removed"])


def _list_deps(file_path):
    """Print the dependencies declared in `file_path`. Returns (count, error)."""
    if not Path(file_path).exists():
        print(f"\033[93m⚠\033[0m  '{file_path}' not found — no dependencies declared.")
        return 0, None
    try:
        _content, deps = _read_paths(file_path)
    except PathsFileError as e:
        print(f"\033[91m✗\033[0m {e}")
        return 0, str(e)
    if not deps:
        print(f"No dependencies declared in '{file_path}'.")
        return 0, None

    print(f"Dependencies declared in '{file_path}':")
    for name, dep in deps.items():
        label = dep["display"] if dep["display"] == name else f"{dep['display']} ({name})"
        path_value = dep["path"]
        status = "\033[92m●\033[0m" if path_value not in ("None", "''", '""') else "\033[93m○\033[0m"
        print(f"  {status} {label:<30} _path = {path_value}")
    print("\n\033[92m●\033[0m = path filled in   \033[93m○\033[0m = stub (still None)")
    return len(deps), None


def list_deps_in_paths(file_path):
    """Print the dependency blocks declared in `file_path`. Returns the count."""
    return _list_deps(file_path)[0]


def setup_paths_file(file_path):
    """
    Setup or update a paths file at the specified location.
    If file exists, provides options to add or remove dependencies.
    If not, create a new one.

    Args:
        file_path (str): Path where to create or update the paths file
    """
    target_path = Path(file_path)

    if target_path.exists():
        # File exists - offer options
        existing_content, existing_deps = parse_existing_paths_file(file_path)

        if existing_content is None:
            # Never overwrite a paths.py that can't be read or doesn't parse.
            print(f"Fix '{file_path}' first; nothing was changed.")
            return 1

        print(f"=== Paths file exists at {file_path} ===")

        if existing_deps:
            print(f"\nFound {len(existing_deps)} existing dependencies:")
            for i, (var_name, display_name) in enumerate(existing_deps.items(), 1):
                print(f"  {i}. {display_name}")
        else:
            print("\nNo existing dependencies found.")

        # Ask user what they want to do
        print("\nWhat would you like to do?")
        print("1. Add new dependencies")
        print("2. Remove existing dependencies")
        print("3. Cancel")

        while True:
            choice = input("Choose an option (1-3): ").strip()
            if choice == "1":
                return update_paths_file(file_path, existing_content, existing_deps)
            elif choice == "2":
                return remove_dependencies_from_file(file_path, existing_content, existing_deps)
            elif choice == "3":
                print("Operation cancelled.")
                return 0
            else:
                print("Please enter 1, 2, or 3.")
    else:
        # File doesn't exist - create new one
        return create_paths_file(file_path)


def startapp_command(args):
    if args.project_name == "example":
        rc = create_example_structure()
    else:
        error = validate_project_name(args.project_name)
        if error:
            print(f"\033[91m✗\033[0m {error}")
            return 1
        rc = create_project_structure(args.project_name)

    # Scaffolding failed (and already cleaned up after itself) — don't write
    # paths.py/.env into a missing or partial project.
    if rc:
        return rc

    project_dir_name = "aa-counter" if args.project_name == "example" else args.project_name

    # Seed a .gitignore so ngargparser runtime artifacts (and common cruft)
    # stay out of the user's git history from the first commit.
    ensure_gitignore(os.path.join(project_dir_name, ".gitignore"))

    # Write initial .env with APP_NAME and APP_ROOT in the new project root, plus
    # APP_ENTRY: the start-up script a tool that depends on this one runs.
    try:
        import glob

        project_root_abs = os.path.abspath(project_dir_name)
        env_file_path = os.path.join(project_dir_name, ".env")
        entry_scripts = sorted(glob.glob(os.path.join(project_dir_name, "src", "run_*.py")))
        with open(env_file_path, "w", encoding="utf-8") as f:
            f.write(f"APP_ROOT={project_root_abs}\n")
            f.write(f"APP_NAME={project_dir_name}\n")
            if len(entry_scripts) == 1:
                f.write(f"APP_ENTRY=src/{os.path.basename(entry_scripts[0])}\n")
        print(f"\033[92m✓\033[0m Created initial '.env' at '\033[92m{env_file_path}\033[0m'")
    except Exception as e:
        print(f"\033[91m✗\033[0m Error writing initial .env: \033[91m{e}\033[0m")

    # Closing hint: tell users where to go from here.
    print()
    print(f"Project '\033[1m{project_dir_name}\033[0m' is ready. Next steps:")
    print(f"  cd {project_dir_name}")
    print("  uv sync                                  # install Python deps")
    print("  cli deps add <tool> [<tool> ...]         # declare external tool deps (optional)")


def deps_command(args):
    action = getattr(args, "deps_action", None)
    # Exit 1 when something asked for didn't happen, so scripts and CI can tell.
    if action == "add":
        if args.names:
            result = _add_deps("paths.py", args.names)
            return 1 if result["rejected"] or result["error"] else 0
        # No names given → fall through to interactive add menu via setup_paths_file
        return _deps_menu("paths.py")
    if action == "remove":
        if args.names:
            result = _remove_deps("paths.py", args.names)
            return 1 if result["not_found"] or result["refused"] or result["error"] else 0
        return _deps_menu("paths.py")
    if action == "list":
        _count, error = _list_deps("paths.py")
        return 1 if error else 0
    # Bare `cli deps` → today's interactive flow
    return _deps_menu("paths.py")


def _deps_menu(file_path):
    """Run the interactive `cli deps` menu; return 1 when it changed nothing because of an error.

    The menu asks questions, so without a terminal (a script, CI) it stops at once
    instead of reading empty answers and reporting success.
    """
    import sys

    if not sys.stdin.isatty():
        print(
            "\033[91m✗\033[0m cli deps without names asks questions, so it needs a terminal. "
            "Use 'cli deps add <name> ...', 'cli deps remove <name> ...' or 'cli deps list'."
        )
        return 1
    try:
        return setup_paths_file(file_path)
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return 1


class _RemoteUnavailable(Exception):
    """Raised (only in strict mode) when `git ls-remote` can't reach the remote.

    Lets callers tell "couldn't check" apart from "checked, found no tags" —
    both of which otherwise collapse into a None return.
    """


_UNSET = object()  # sentinel: "argument not supplied" (distinct from None)


# A release tag: vX.Y.Z, the v optional, a +build suffix allowed. Pre-release tags
# (v0.4.2-test, v1.0.0-rc1) do not match.
_RELEASE_TAG_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:\+.*)?")


def _latest_release_tag(remote_url, timeout=10, *, strict=False, prompt=True):
    """Return the highest release tag from a git remote, or None.

    Uses `git ls-remote --tags --refs` (no clone), keeps tags matching
    `vX.Y.Z` (an optional `+build` suffix is allowed), and returns the
    highest one by numeric (major, minor, patch). Pre-release tags such as
    `v0.4.2-test` or `v1.0.0-rc1` are skipped, so a test tag never becomes the
    default upgrade or the version the update notice points to; install one
    on purpose with `--ref`. `timeout` bounds the network call (kept short
    for the update notifier).

    Returns None when the remote is reachable but has no release tags. On a
    transport failure (network/timeout/git error) it returns None by default,
    or raises `_RemoteUnavailable` when `strict=True` — so a network blip is
    not misreported as "no tags exist".

    `prompt=False` makes the lookup non-interactive: git may not ask for
    credentials on the terminal or through an askpass helper, so a remote that
    wants credentials fails fast instead of stalling until `timeout`. Stored
    credentials, credential helpers and SSH keys still apply.
    """
    import subprocess

    env, stdin = None, None
    if not prompt:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": ""}
        stdin = subprocess.DEVNULL
    try:
        out = subprocess.check_output(
            ["git", "ls-remote", "--tags", "--refs", remote_url],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            env=env,
            stdin=stdin,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        if strict:
            raise _RemoteUnavailable(str(e)) from e
        return None
    semver_tags = []
    for line in out.splitlines():
        ref = line.split("\t", 1)[-1] if "\t" in line else ""
        name = ref.rsplit("/", 1)[-1]
        m = _RELEASE_TAG_RE.fullmatch(name)
        if m:
            semver_tags.append((tuple(int(g) for g in m.groups()), name))
    return max(semver_tags)[1] if semver_tags else None


def _is_uv_tool_install():
    """True when this ngargparser runs from a uv-managed tool env.

    uv installs each tool under `<data-dir>/uv/tools/<name>/`, and those
    venvs ship without pip — so `python -m pip` can't self-upgrade them.
    Detect the layout from sys.prefix so sync can use `uv tool install`.
    """
    import sys

    return os.path.join("uv", "tools") in os.path.normpath(getattr(sys, "prefix", ""))


BASE_REPO_URL = "git+https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser.git"


def _local_source(ref):
    """If `ref` points at a local directory ('.' or a path), return its absolute
    path; else None. Lets contributors install their working tree via `--ref .`.

    Detection is "does it resolve to an existing directory" so real git refs like
    'master' or 'feature/x' are never mistaken for paths.
    """
    import os

    if not ref:
        return None
    p = os.path.abspath(os.path.expanduser(ref))
    return p if os.path.isdir(p) else None


def _is_ngargparser_checkout(path):
    """Return (ok, message). True iff `path` looks like the ngargparser package source."""
    import os

    pyproject = os.path.join(path, "pyproject.toml")
    if not os.path.isfile(pyproject):
        return False, f"'{path}' has no pyproject.toml — not an ngargparser checkout."
    try:
        with open(pyproject, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        return False, f"can't read '{pyproject}': {e}"
    if 'name = "ngargparser"' not in content:
        return False, f"'{path}' doesn't look like the ngargparser repo (pyproject name is not 'ngargparser')."
    return True, ""


def _local_checkout():
    """Return the checkout path when this ngargparser runs from a source checkout
    (an editable install of the repo), else None. An installed release lives in
    site-packages, whose parent is not an ngargparser checkout."""
    import os

    parent = os.path.dirname(str(NGPARSER_DIR))
    return parent if _is_ngargparser_checkout(parent)[0] else None


def _checkout_version(path):
    """Best-effort read of the [project] version from a checkout's pyproject.toml, or None.

    Used to tailor the post-upgrade sync reminder after a local-checkout install, where the
    installed semver isn't otherwise known in-process (no re-exec; `__version__` is still stale)."""
    import os
    import re

    try:
        with open(os.path.join(path, "pyproject.toml"), encoding="utf-8") as f:
            m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', f.read())
    except OSError:
        return None
    return m.group(1) if m else None


def _resolve_upgrade_url(ref="latest", dev=False, latest_tag=_UNSET):
    """Resolve (source, label) to upgrade ngargparser to.

    Honors NGARGPARSER_UPGRADE_URL (full override). `dev` forces 'master'.
    `ref='latest'` resolves the highest semver tag on the remote, falling back
    to 'master' when no tags exist. A `ref` that points at a local directory
    ('.' or a path) returns that absolute path with a 'local:' label, so the
    install comes from the working tree instead of the git remote.

    When `ref='latest'`, pass `latest_tag` to reuse an already-resolved tag
    (or None for "no tags") instead of querying the remote again; omit it to
    query here as before.
    """
    import os

    override = os.environ.get("NGARGPARSER_UPGRADE_URL")
    if override:
        return override, ref
    if dev:
        ref = "master"
    local = _local_source(ref)
    if local:
        return local, f"local:{local}"
    if ref == "latest":
        if latest_tag is _UNSET:
            latest_tag = _latest_release_tag(BASE_REPO_URL[len("git+") :])
        ref = latest_tag or "master"
    return f"{BASE_REPO_URL}@{ref}", ref


def _run_self_upgrade(url):
    """Install `url` into the current ngargparser env — whatever the install type.

    Picks an installer that works for the current environment so a developer only
    ever types `cli upgrade`:
      - uv-tool install     -> `uv tool install`        (uv-tool envs have no pip)
      - env that has pip     -> `python -m pip install`
      - pip-less venv + uv   -> `uv pip install --python <this interpreter>`
        (e.g. a uv-managed project .venv, which ships without pip)
    Returns 0 on success, else a non-zero exit code (after printing a diagnostic).
    """
    import importlib.util
    import shutil
    import subprocess
    import sys

    uv = shutil.which("uv")
    has_pip = importlib.util.find_spec("pip") is not None

    if _is_uv_tool_install() and uv:
        cmd, tool = ["uv", "tool", "install", "--force", "--reinstall", url], "uv tool"
    elif has_pip:
        cmd, tool = [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", "--quiet", url], "pip"
    elif uv:
        cmd, tool = ["uv", "pip", "install", "--python", sys.executable, "--reinstall", url], "uv pip"
    else:
        print("\033[91m✗\033[0m Can't upgrade: this environment has no pip and uv isn't on PATH.")
        print("  Install uv (https://astral.sh/uv) or run manually:")
        print(f"    uv tool install --force --reinstall '{url}'")
        return 1

    try:
        subprocess.check_call(cmd)
        return 0
    except subprocess.CalledProcessError as e:
        print(f"\033[91m✗\033[0m Upgrade failed ({tool} exit {e.returncode}).")
        print(f"  Try manually:  uv tool install --force --reinstall '{url}'")
        return e.returncode or 1


def _project_scaffold_version():
    """Return the [tool.ngargparser] scaffold_version stamp in CWD's pyproject.toml, or None
    (i.e. None when not run from inside a stamped scaffolded project)."""
    import os

    if not os.path.isfile("pyproject.toml"):
        return None
    try:
        with open("pyproject.toml", encoding="utf-8") as f:
            m = SCAFFOLD_STAMP_RE.search(f.read())
    except OSError:
        return None
    return m.group(2) if m else None


def _remind_sync(installed, did_install):
    """After a successful `cli upgrade`, remind the developer to run `cli sync` so a project's
    vendored framework files actually pick up the newly installed tool. Informational only — the
    project keeps working on its old vendored files until it's synced. Context-aware:

      - inside a stamped scaffolded project that's *behind* the installed tool → a specific nudge
        naming both versions;
      - inside a stamped project that's already current (or has an unparseable stamp) → stay silent;
      - anywhere else → a generic reminder, but only when we actually (re)installed (`did_install`),
        so the 'already up to date' path doesn't nag when nothing changed.
    """
    stamp = _project_scaffold_version()
    if stamp:
        proj, tool = _parse_semver(stamp), _parse_semver(installed)
        if proj and tool and proj < tool:
            lead = f"You upgraded the CLI to {installed}" if did_install else f"The CLI is on {installed}"
            print(f"\nℹ {lead}, but this project is still on {stamp}.")
            print(f"  Run `cli sync` here to update this project to {installed}.")
        return
    if did_install:
        ver = installed or "the new version"
        print("\nℹ `cli upgrade` updates the CLI, not your projects.")
        print(f"  Run `cli sync` inside a project to update it to {ver}.")


def upgrade_command(args):
    """Check GitLab for the latest tag and upgrade ngargparser in place.

    Works from any directory and never touches project files. `--check` only
    reports installed-vs-latest. Unlike `sync` it does not re-exec — the next
    `cli` invocation runs the upgraded code.
    """
    import os

    ref, dev = getattr(args, "ref", "latest"), getattr(args, "dev", False)
    check = getattr(args, "check", False)
    current = __version__

    # Plain-latest case: resolve the tag ourselves in strict mode so a network
    # failure is reported honestly instead of being misread as "no tags exist"
    # (and silently resolving to master). The resolved tag is handed to
    # _resolve_upgrade_url so it doesn't query the remote a second time.
    latest_tag = _UNSET
    plain_latest = (
        ref == "latest" and not dev and _local_source(ref) is None and not os.environ.get("NGARGPARSER_UPGRADE_URL")
    )
    if plain_latest:
        try:
            latest_tag = _latest_release_tag(BASE_REPO_URL[len("git+") :], strict=True)
        except _RemoteUnavailable:
            print("⚠ Couldn't reach the remote to check for the latest release (network/VPN, or git unavailable).")
            print(f"ℹ Installed: {current}    Latest: unknown")
            return 2

    url, resolved = _resolve_upgrade_url(ref, dev, latest_tag=latest_tag)
    target = resolved.lstrip("v") if resolved else resolved

    # Local checkout mode (`--ref .` or `--ref <path>`): install the working tree.
    # Skips the tag-resolution / up-to-date logic — you always want what's on disk.
    if resolved.startswith("local:"):
        path = url
        ok, msg = _is_ngargparser_checkout(path)
        if not ok:
            print(f"\033[91m✗\033[0m {msg}")
            return 1
        if check:
            print(f"ℹ Would install ngargparser from local checkout: {path}")
            return 0
        print(f"ℹ Installing ngargparser from local checkout: {path} …")
        rc = _run_self_upgrade(path)
        if rc:
            return rc
        print(f"\033[92m✓\033[0m Installed ngargparser from {path}. Run 'cli --version' to confirm.")
        _remind_sync(_checkout_version(path), did_install=True)
        return 0

    pinned = dev or ref != "latest" or os.environ.get("NGARGPARSER_UPGRADE_URL")
    if not pinned:
        if resolved == "master":
            print("⚠ No semver tags on remote; 'latest' resolves to master.")
        else:
            up_to_date = current == target
            print(
                f"ℹ Installed: {current}    Latest: {resolved}"
                + ("  (up to date)" if up_to_date else "  (update available)")
            )
            if check:
                return 0
            if up_to_date:
                print("\033[92m✓\033[0m Already on the latest tag; nothing to do.")
                _remind_sync(target, did_install=False)  # flag a stale project even when the tool is current
                return 0
    if check:  # --check with an explicit --ref / --dev / override
        print(f"ℹ Installed: {current}    Target: {resolved}")
        return 0

    print(f"ℹ Upgrading ngargparser ({url}) …")
    rc = _run_self_upgrade(url)
    if rc:
        return rc
    print(f"\033[92m✓\033[0m Upgraded ngargparser → {resolved}. Run 'cli --version' to confirm.")
    _remind_sync(target, did_install=True)
    return 0


# --- Passive "update available" notifier -------------------------------------
# Standard update-notifier pattern (like npm / gh / uv): when a developer runs
# `cli`, occasionally remind them a newer release exists. Throttled to one
# network check per day (cached on disk), printed to stderr, and suppressed in
# non-interactive / CI / opted-out contexts. Never blocks or breaks a command.

UPDATE_CHECK_TTL = 24 * 60 * 60  # seconds between remote checks


def _env_flag(name):
    """True when environment variable `name` is switched on. Unset, empty, and
    the usual "off" spellings (0, false, no, off; any case) count as off, so
    `NAME=0` undoes `NAME=1`."""
    return os.environ.get(name, "").strip().lower() not in ("", "0", "false", "no", "off")


def _parse_semver(v):
    """Parse 'vX.Y.Z' / 'X.Y.Z' (optional pre-release/build) into (X, Y, Z), else None."""
    import re

    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", (v or "").strip())
    return tuple(int(g) for g in m.groups()) if m else None


def _update_cache_file():
    import os

    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "ngargparser", "update-check.json")


def _read_update_cache():
    import json

    try:
        with open(_update_cache_file(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_update_cache(checked_at, latest):
    import json
    import os

    path = _update_cache_file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"checked_at": checked_at, "latest": latest}, f)
    except OSError:
        pass


def _maybe_notify_update(command):
    """Print a one-line 'update available' notice on stderr, if appropriate.

    Silent (and side-effect-free) unless: stderr is a TTY, not in CI, not opted
    out via NGARGPARSER_NO_UPDATE_CHECK, and not already running `cli upgrade`.
    Re-checks the remote at most once per UPDATE_CHECK_TTL; otherwise reads the
    cached result. Any failure is swallowed so a command is never affected.
    """
    import os
    import sys
    import time

    try:
        if (
            command in ("upgrade", "u")
            or _env_flag("NGARGPARSER_NO_UPDATE_CHECK")
            or os.environ.get("CI")
            or not sys.stderr.isatty()
        ):
            return

        cache = _read_update_cache()
        cached = cache.get("latest")
        latest = cached if cached and _RELEASE_TAG_RE.fullmatch(cached) else None
        # A pre-release in the cache came from an older cli (they share the file); look it up again.
        fresh = cached == latest and (time.time() - cache.get("checked_at", 0)) < UPDATE_CHECK_TTL
        if not fresh:
            # One bounded network check per TTL. Stamp the time even on failure
            # so we don't re-query (or hang) on every invocation when offline.
            latest = _latest_release_tag(BASE_REPO_URL[len("git+") :], timeout=4) or latest
            _write_update_cache(time.time(), latest)

        cur, new = _parse_semver(__version__), _parse_semver(latest)
        if not cur or not new or new <= cur:
            return

        _print_update_notice(latest)
    except Exception:
        pass  # an update check must never break or slow the real command


def _release_notes_url(tag):
    """Web URL of CHANGELOG.md as it stands at `tag`: the release notes for that tag."""
    from urllib.parse import quote

    return f"{BASE_REPO_URL[len('git+') : -len('.git')]}/-/blob/{quote(tag, safe='')}/CHANGELOG.md"


def _print_update_notice(latest, color=True, notes_url=None):
    """Print the 'update available' notice on stderr. `color=False` leaves out
    the ANSI codes, for output that isn't a terminal. `notes_url` adds a
    'release notes' line between the headline and the how-to-update line."""
    import sys

    yellow, reset = ("\033[33m", "\033[0m") if color else ("", "")
    notes = f"   release notes: {notes_url}\n" if notes_url else ""
    print(
        f"\n{yellow}⬆  A new ngargparser is available: {__version__} → {latest}{reset}\n"
        f"{notes}"
        f"   run `cli upgrade` to update   ·   silence: NGARGPARSER_NO_UPDATE_CHECK=1",
        file=sys.stderr,
    )


RELEASE_CHECK_TIMEOUT = 5  # seconds `cli --version` / `cli --help` wait for the remote before giving up


def _check_latest_release():
    """Look up the latest release tag right now and report on stderr how the
    installed version compares. Called by `cli --version` and `cli --help` on
    every run.

    Unlike the passive notifier above, this never reads or writes the update
    cache and is not limited to interactive terminals: asking for the version
    or the help is an explicit request, so the answer is always live. Only CI
    and the NGARGPARSER_NO_UPDATE_CHECK opt-out skip it. A remote that can't be
    reached is reported as such, so silence always means "up to date". Any
    failure is swallowed so the command itself never breaks.
    """
    import sys

    try:
        if _env_flag("NGARGPARSER_NO_UPDATE_CHECK") or os.environ.get("CI"):
            return

        try:
            latest = _latest_release_tag(
                BASE_REPO_URL[len("git+") :], timeout=RELEASE_CHECK_TIMEOUT, strict=True, prompt=False
            )
        except _RemoteUnavailable:
            print(
                "⚠ Couldn't check for a newer ngargparser "
                "(GitLab unreachable: network/VPN, credentials, or git missing).",
                file=sys.stderr,
            )
            return

        new = _parse_semver(latest)
        if not new:
            return  # remote reachable but has no release tags to compare against

        cur = _parse_semver(__version__)
        if not cur or new > cur:
            _print_update_notice(latest, color=sys.stderr.isatty(), notes_url=_release_notes_url(latest))
        elif cur > new:
            print(
                f"ℹ Installed ngargparser {__version__} is ahead of the latest release tag {latest}.",
                file=sys.stderr,
            )
    except Exception:
        pass  # a release check must never break `cli --version` or `cli --help`


_INVOKED_COMMAND = None  # canonical subcommand once argv is parsed; read by the atexit hook


def _notify_at_exit():
    """atexit hook so the notice also appears on early exits (a subcommand's
    `--help`, an argparse error), where argparse exits before the end of main().
    Top-level `--version` / `--help` cancel it and run a live check instead (see
    `_ReleaseCheckAction`)."""
    import sys

    command = _INVOKED_COMMAND
    if command is None and len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        command = sys.argv[1]  # parse_args() exited early; best-effort from raw argv
    _maybe_notify_update(command)


class _ReleaseCheckAction(argparse.Action):
    """Base for the top-level flags that print something and then check the
    latest release tag (see `_check_latest_release`). Subclasses say what to
    print in `show`.

    The passive atexit notifier is unregistered first, so these flags never add
    a second, cache-based notice.
    """

    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings=option_strings, dest=dest, default=default, nargs=0, help=help)

    def show(self, parser):
        raise NotImplementedError

    def __call__(self, parser, namespace, values, option_string=None):
        import atexit
        import sys

        atexit.unregister(_notify_at_exit)
        self.show(parser)
        sys.stdout.flush()  # show the output before the remote lookup starts
        _check_latest_release()
        parser.exit()


class _VersionAction(_ReleaseCheckAction):
    """`-v` / `--version`. Stands in for argparse's stock "version" action and
    prints the same `<prog> <version>` line on stdout."""

    def show(self, parser):
        print(f"{parser.prog} {__version__}")


class _HelpAction(_ReleaseCheckAction):
    """`-h` / `--help` on the top-level parser. Stands in for argparse's stock
    "help" action and prints the same help text. Subcommand parsers keep the
    stock action."""

    def show(self, parser):
        parser.print_help()


# --- Sync safety -------------------------------------------------------------
# `cli sync` overwrites framework-owned files inside someone's project. Three
# nets keep that from leaving a broken project behind:
#   1. known clashes: project code this framework version is known to break is
#      reported before anything is written;
#   2. a test drive: the project's own help screens are run before and after;
#   3. a snapshot: if a screen that worked before fails after (or sync itself
#      errors out), every touched file is put back exactly as it was.


def _sync_framework_files():
    """Framework-owned files as (source, destination, executable). `cli sync`
    overwrites each destination whose content differs from its source."""
    return [
        (f"{NGPARSER_DIR}/NGArgumentParser.py", "src/core/NGArgumentParser.py", False),
        (f"{NGPARSER_DIR}/core_validators.py", "src/core/core_validators.py", False),
        (f"{NGPARSER_DIR}/result_writer.py", "src/core/result_writer.py", False),
        (f"{NGPARSER_DIR}/app_env.py", "src/core/app_env.py", False),
        (f"{TEMPLATE_DIR}/set_pythonpath.py", "src/core/set_pythonpath.py", False),
        (f"{TEMPLATE_DIR}/configure.py", "src/core/configure.py", True),
        (f"{TEMPLATE_DIR}/configure", "configure", True),
        (f"{TEMPLATE_DIR}/build.sh", "scripts/core/build.sh", True),
        (f"{TEMPLATE_DIR}/Makefile", "Makefile", False),
    ]


# Legacy file names `cli sync` moves to their current location.
_SYNC_LEGACY_BUILD = "scripts/build.sh"
_SYNC_LEGACY_HOOKS = ("scripts/dependencies.sh", "scripts/build_hooks.sh")
_SYNC_LEGACY_BUILD_CONF = "scripts/build.conf"


def _sync_touched_paths(framework_files):
    """Every project file `cli sync` may create, overwrite, move or edit."""
    return [dst for _src, dst, _exe in framework_files] + [
        _SYNC_LEGACY_BUILD,
        *_SYNC_LEGACY_HOOKS,
        "scripts/hooks.sh",
        _SYNC_LEGACY_BUILD_CONF,
        "scripts/core/build.conf",
        "deploy/install.sh",
        ".gitignore",
        "pyproject.toml",
        "README.md",
    ]


def _framework_changes_pending(framework_files):
    """True when sync would change a framework file or move a legacy one, i.e.
    when there is something a test drive could be affected by."""
    if os.path.exists(_SYNC_LEGACY_BUILD) and not os.path.exists("scripts/core/build.sh"):
        return True
    if not os.path.exists("scripts/hooks.sh") and any(os.path.exists(p) for p in _SYNC_LEGACY_HOOKS):
        return True
    if os.path.exists(_SYNC_LEGACY_BUILD_CONF) and not os.path.exists("scripts/core/build.conf"):
        return True
    return any(
        not os.path.exists(dst) or not filecmp.cmp(src, dst, shallow=False) for src, dst, _exe in framework_files
    )


class _SyncSnapshot:
    """Pre-image of the files `cli sync` may touch, so a sync can be undone exactly.

    Copies each existing file into a temporary directory outside the project,
    under the same relative path, and remembers which paths (and which
    directories) did not exist. Sync writes *through* a symlink, so for a
    symlinked path the content of the file it points at is what gets saved,
    along with the link itself. `restore()` puts every file back, deletes files
    and empty directories the sync created, and removes that sync's backup
    directory. `discard()` drops the pre-image.
    """

    _DIRS = ("src/core", "scripts/core", "deploy", ".ngargparser", ".ngargparser/sync-backups")

    def __init__(self, paths):
        import tempfile

        self._store = tempfile.mkdtemp(prefix="ngargparser-sync-")
        self._absent_dirs = [d for d in self._DIRS if not os.path.isdir(d)]
        # project path -> (existed, link target or None, stored copy of its content or None)
        self._entries = {}
        try:
            for path in dict.fromkeys(paths):
                link = os.readlink(path) if os.path.islink(path) else None
                stored = None
                if os.path.isfile(path):  # follows a symlink to the file sync would write to
                    stored = os.path.join(self._store, path)
                    os.makedirs(os.path.dirname(stored), exist_ok=True)
                    shutil.copy2(path, stored)
                self._entries[path] = (os.path.lexists(path), link, stored)
        except BaseException:
            self.discard()  # don't leave a half-built pre-image behind
            raise

    @staticmethod
    def _put_back(stored, destination):
        """Copy `stored` over `destination` without a moment where it is missing."""
        os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)
        temporary = destination + ".ngargparser-restore"
        try:
            shutil.copy2(stored, temporary)
            os.replace(temporary, destination)
        finally:
            if os.path.lexists(temporary):
                os.remove(temporary)

    def restore(self, backup_root=None):
        for path, (existed, link, stored) in self._entries.items():
            if not existed:
                if os.path.lexists(path):
                    os.remove(path)  # the sync created it
            elif link is not None:
                if not os.path.islink(path) or os.readlink(path) != link:
                    if os.path.lexists(path):
                        os.remove(path)
                    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                    os.symlink(link, path)
                target = os.path.realpath(path)
                if stored is not None:
                    self._put_back(stored, target)  # the content sync overwrote through the link
                elif os.path.isfile(target):
                    os.remove(target)  # the link dangled before; the sync created its target
            elif stored is not None:
                if os.path.islink(path):
                    os.remove(path)
                self._put_back(stored, path)
            # else: a directory or special file sat at this path; the snapshot left it alone
        if backup_root and os.path.isdir(backup_root):
            shutil.rmtree(backup_root)
        for directory in sorted(self._absent_dirs, key=len, reverse=True):  # deepest first
            try:
                os.rmdir(directory)  # only removes it when the sync left it empty
            except OSError:
                pass

    def discard(self):
        if self._store:
            shutil.rmtree(self._store, ignore_errors=True)

    def keep(self):
        """Leave the pre-image on disk (used when a restore failed) and return where it is.
        Files sit under their project-relative paths, so they can be copied back by hand."""
        store, self._store = self._store, None
        return store


def _clash_predict_output_options(skipped=None):
    """Find `add_argument` calls on a project's predict parser that use an
    option string the base class owns there: `--output-prefix`, `-o`,
    `--output-format` or `-f`.

    The base class has added `--output-prefix/-o` and `--output-format/-f` to
    `predict` itself since 0.3.0, so a project that declares either again, or
    reuses one of the short letters for another option, fails with argparse's
    "conflicting option string(s)" as soon as its parser is built. A declaration
    that follows `remove_argument(<option>, "predict")` in the same file is not
    a clash: removing an option frees all its names (removing --output-format
    frees -f too), so the project can declare its own.

    The project's parser files are read as syntax trees, so the option names in
    a comment or a help string don't count. A file that does not parse is
    appended to `skipped` (when given) instead of being judged. Returns a list
    of {'path', 'first', 'last', 'what'}.
    """
    import ast
    import glob

    owned = ("--output-prefix", "-o", "--output-format", "-f")
    # Removing either name of a framework option frees both.
    pairs = {
        "--output-prefix": {"--output-prefix", "-o"},
        "-o": {"--output-prefix", "-o"},
        "--output-format": {"--output-format", "-f"},
        "-f": {"--output-format", "-f"},
    }
    findings = []
    for path in sorted(glob.glob(os.path.join("src", "*ArgumentParser.py"))):
        try:
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
        except (OSError, SyntaxError, ValueError):
            if skipped is not None:
                skipped.append(path)
            continue  # unreadable here; the test drive is the net for this file
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
        freed = []  # (line, names) for each remove_argument(<option>, "predict")
        for node in calls:
            if node.func.attr != "remove_argument":
                continue
            values = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            values += [
                k.value.value for k in node.keywords if k.arg == "subparser_name" and isinstance(k.value, ast.Constant)
            ]
            if len(values) >= 2 and values[1] == "predict":
                freed.append((node.lineno, pairs.get(values[0], {values[0]})))
        for node in calls:
            if node.func.attr != "add_argument" or "predict" not in ast.unparse(node.func.value):
                continue
            names = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            declared = [
                name
                for name in owned
                if name in names and not any(line < node.lineno and name in gone for line, gone in freed)
            ]
            if declared:
                findings.append(
                    {
                        "path": path,
                        "first": node.lineno,
                        "last": node.end_lineno,
                        "what": f"declares {declared[0]} on predict",
                    }
                )
    return sorted(findings, key=lambda f: (f["path"], f["first"]))


# Project code that a framework release is known to break. Add an entry whenever
# a release changes something project-owned code may rely on: `since` is the
# release that made the change, `detect` returns the findings in the project in
# the current directory, and `explain` tells the developer what to do about them.
KNOWN_CLASHES = [
    {
        "since": "0.3.0",
        "detect": _clash_predict_output_options,
        "explain": (
            "Since 0.3.0 the framework adds --output-prefix/-o and --output-format/-f to predict.",
            "Declaring either again, or reusing -o or -f for another option there, makes every",
            'command fail with "conflicting option string(s)".',
            "Delete the duplicate declaration, or give your own option another letter. To change",
            "the help text or default of the framework's option, use",
            "self.parser_predict.update_arguments(...); to replace it, call",
            'self.remove_argument("--output-format", "predict") before your add_argument(...).',
            "Then run `cli sync` again.",
        ),
    },
]


def _find_known_clashes(skipped=None):
    """Return [(entry, findings)] for each KNOWN_CLASHES entry found in the project.
    Files a detector could not read are appended to `skipped` when it is given."""
    found = []
    for entry in KNOWN_CLASHES:
        findings = entry["detect"](skipped)
        if findings:
            found.append((entry, findings))
    return found


def _report_known_clashes(clashes):
    print("\n\033[91m✗\033[0m Sync stopped: this project has code the current framework breaks.")
    for entry, findings in clashes:
        places = [f"{f['path']}:{f['first']}" + ("" if f["last"] == f["first"] else f"-{f['last']}") for f in findings]
        width = max(len(place) for place in places)
        print()
        for place, finding in zip(places, findings):
            print(f"  {place:<{width}}   {finding['what']}")
        print()
        for line in entry["explain"]:
            print(f"  {line}")
    print("\n  Nothing was changed. To sync without these checks: cli sync --no-verify")


def _undo_sync(snapshot, backup_root):
    """Put the project back as it was before the sync and say so. If that fails
    partway, keep the pre-image on disk and say where it is instead of raising."""
    try:
        snapshot.restore(backup_root)
        print("  Every file was restored to its state before the sync.")
    except OSError as e:
        print(f"  \033[91m✗\033[0m Could not restore every file ({e}).")
        print(f"    The files as they were before the sync are in: {snapshot.keep()}")


SMOKE_TIMEOUT = 60  # seconds each help screen of the project's own tool may take
_SMOKE_COMMANDS = (["--help"], ["predict", "--help"], ["preprocess", "--help"], ["postprocess", "--help"])


def _project_python():
    """The interpreter of the project's own virtualenv (`.venv`), or None."""
    for relative in (os.path.join(".venv", "bin", "python"), os.path.join(".venv", "Scripts", "python.exe")):
        if os.path.isfile(relative):
            return os.path.abspath(relative)
    return None


def _smoke_check():
    """Test drive: run the help screens of the project's entry script.

    Uses the project's own interpreter, with APP_ROOT pointed at the project so
    an unconfigured checkout can still start. Returns
    ``{'runnable': bool, 'reason': str, 'results': {command: (passed, error)}}``;
    `runnable` is False when there is no interpreter, or not exactly one
    `src/run_*.py`. With several, sync cannot tell which one is the entry
    script, and running the others with `--help` could execute whatever they do.
    """
    import glob
    import subprocess

    scripts = sorted(glob.glob(os.path.join("src", "run_*.py")))
    if not scripts:
        return {"runnable": False, "reason": "no src/run_*.py entry script was found", "results": {}}
    if len(scripts) > 1:
        reason = f"several src/run_*.py scripts were found ({', '.join(scripts)}), so none was run"
        return {"runnable": False, "reason": reason, "results": {}}
    python = _project_python()
    if not python:
        return {"runnable": False, "reason": "the project has no .venv interpreter (run `uv sync`)", "results": {}}

    script = scripts[0]
    env = {**os.environ, "APP_ROOT": os.getcwd(), "PYTHONDONTWRITEBYTECODE": "1"}
    results = {}
    for arguments in _SMOKE_COMMANDS:
        try:
            done = subprocess.run(
                [python, script, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=SMOKE_TIMEOUT,
                env=env,
            )
            lines = [line for line in done.stderr.splitlines() if line.strip()]
            passed, error = done.returncode == 0, (lines[-1].strip() if lines else f"exit code {done.returncode}")
        except subprocess.TimeoutExpired:
            passed, error = False, f"timed out after {SMOKE_TIMEOUT}s"
        except OSError as e:
            passed, error = False, str(e)
        results[" ".join([script, *arguments])] = (passed, "" if passed else error)
    return {"runnable": True, "reason": "", "results": results}


def _checkout_skips_upgrade(ref, dev):
    """The local checkout a plain `cli sync` syncs from instead of replacing it
    with the latest release, else None. Naming a version (--ref, --dev or
    NGARGPARSER_UPGRADE_URL) still upgrades, as does any released install."""
    import os

    plain_latest = (
        ref == "latest" and not dev and _local_source(ref) is None and not os.environ.get("NGARGPARSER_UPGRADE_URL")
    )
    return _local_checkout() if plain_latest else None


def _report_dry_run_upgrade(ref, dev):
    """--dry-run skips the self-upgrade a real sync starts with, so its preview
    shows the installed version's files. Say what a real run would install
    first, and warn when that replaces a local checkout. Reads the remote's
    tags (no install), resolving the target the way `upgrade_command` does."""
    import os

    checkout = _checkout_skips_upgrade(ref, dev)
    if checkout:
        print(
            f"ℹ A real run syncs from your local checkout at {checkout} (no upgrade), the same files as this preview."
        )
        return

    latest_tag = _UNSET
    plain_latest = (
        ref == "latest" and not dev and _local_source(ref) is None and not os.environ.get("NGARGPARSER_UPGRADE_URL")
    )
    if plain_latest:
        try:
            latest_tag = _latest_release_tag(
                BASE_REPO_URL[len("git+") :], timeout=RELEASE_CHECK_TIMEOUT, strict=True, prompt=False
            )
        except _RemoteUnavailable:
            print("⚠ Couldn't check which ngargparser a real run would install (GitLab unreachable).")
            return

    url, resolved = _resolve_upgrade_url(ref, dev, latest_tag=latest_tag)
    from_local = resolved.startswith("local:")
    target = f"from the local checkout {url}" if from_local else resolved
    print(
        f"ℹ A real run first installs ngargparser {target} (installed: {__version__}), then syncs from it. "
        "This preview uses the installed version's files."
    )
    checkout = _local_checkout()
    if checkout and not (from_local and os.path.realpath(url) == os.path.realpath(checkout)):
        print(
            f"⚠ The installed ngargparser is your local checkout at {checkout}; a real run replaces it with "
            f"{target}. To sync from the checkout, use --no-upgrade."
        )


def sync_command(args):
    """Synchronize framework files in existing projects to the latest version."""
    snapshot = None
    backup_root = None
    try:
        import os
        import shutil

        dry_run = getattr(args, "dry_run", False)
        do_backup = getattr(args, "backup", True) and not _env_flag("NGARGPARSER_SYNC_NO_BACKUP")
        verify = getattr(args, "verify", True) and not _env_flag("NGARGPARSER_SYNC_NO_VERIFY")

        # Check if we're in a project directory
        if not os.path.exists("src") or not os.path.exists("scripts"):
            print("\033[91m✗\033[0m Error: This doesn't appear to be a valid ngargparser project directory")
            print("Make sure you're in a project directory with src/ and scripts/ subdirectories.")
            return 1

        # Self-upgrade the installed ngargparser, then re-exec so the new code
        # (including __version__ used by the scaffold_version stamp below) takes
        # effect for the rest of this sync. The env-var sentinel breaks recursion.
        # --dry-run must be side-effect-free, and the re-exec drops flags, so it
        # implies --no-upgrade.
        self_upgrade = (
            getattr(args, "upgrade", True) and not dry_run and not os.environ.get("NGARGPARSER_NO_SELF_UPGRADE")
        )
        # A plain sync from a local checkout syncs from it: the latest release
        # would replace the working copy and copy older framework files in.
        checkout = (
            _checkout_skips_upgrade(getattr(args, "ref", "latest"), getattr(args, "dev", False))
            if self_upgrade
            else None
        )
        if checkout:
            self_upgrade = False
            print(
                f"ℹ Using your local checkout at {checkout} (no upgrade). "
                "To install the latest release first: cli upgrade, then cli sync."
            )
        if self_upgrade:
            import sys

            if getattr(args, "dev", False):
                print("ℹ Dev mode: pulling from master.")
            url, resolved = _resolve_upgrade_url(getattr(args, "ref", "latest"), getattr(args, "dev", False))
            if resolved.startswith("local:"):
                ok, msg = _is_ngargparser_checkout(url)
                if not ok:
                    print(f"\033[91m✗\033[0m {msg}")
                    return 1
                print(f"ℹ Installing ngargparser from local checkout: {url} …")
            else:
                if resolved and resolved != "master":
                    print(f"ℹ Latest release tag: {resolved}")
                print(f"ℹ Upgrading ngargparser ({url}) …")
            rc = _run_self_upgrade(url)
            if rc:
                print("  Then re-run:       cli s --no-upgrade")
                return rc

            # On re-exec, pass only the bare `s` subcommand. The sentinel env
            # var skips the upgrade block, and any --ref/--no-upgrade flags
            # would break compatibility with older ngargparser versions whose
            # sync subparser doesn't recognize them.
            env = {**os.environ, "NGARGPARSER_NO_SELF_UPGRADE": "1"}
            if not verify:  # the bare re-exec drops --no-verify, so carry it in the environment
                env["NGARGPARSER_SYNC_NO_VERIFY"] = "1"
            if not do_backup:  # ... and --no-backup the same way
                env["NGARGPARSER_SYNC_NO_BACKUP"] = "1"
            os.execvpe(sys.argv[0], [sys.argv[0], "s"], env)

        if dry_run:
            print("\033[93mDRY RUN\033[0m — showing what would change; no files will be written.")
            if getattr(args, "upgrade", True) and not os.environ.get("NGARGPARSER_NO_SELF_UPGRADE"):
                _report_dry_run_upgrade(getattr(args, "ref", "latest"), getattr(args, "dev", False))
        print("Synchronizing framework files to latest version...")

        # Get the project name from current directory
        project_name = os.path.basename(os.getcwd())
        print(f"Project: {project_name}")

        # Safety net 1: project code this framework version is known to break
        # stops the sync before anything is written (reported in dry-run too).
        if verify:
            unreadable = []
            clashes = _find_known_clashes(unreadable)
            if clashes:
                _report_known_clashes(clashes)
                return 1
            for path in dict.fromkeys(unreadable):
                print(
                    f"\033[93m⚠\033[0m Could not check {path} for known clashes (it does not parse with this Python)."
                )
        else:
            print("\033[93m⚠\033[0m Safety checks are off for this run (--no-verify or NGARGPARSER_SYNC_NO_VERIFY).")

        # Safety nets 2 and 3: note which of the project's help screens work now,
        # and keep a pre-image of every file about to be touched. The screens are
        # run again once the files are in place; see "Test drive" below.
        framework_files = _sync_framework_files()
        baseline = None
        if not dry_run:
            if verify and _framework_changes_pending(framework_files):
                baseline = _smoke_check()
            snapshot = _SyncSnapshot(_sync_touched_paths(framework_files))

        # Backups of any overwritten file go under a timestamped dir so an
        # accidental clobber of a hand-edited framework file stays recoverable.
        if do_backup and not dry_run:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_root = os.path.join(".ngargparser", "sync-backups", timestamp)

        changes = []

        def report(action, label):
            verbs = {
                "created": "Would create" if dry_run else "Created",
                "updated": "Would update" if dry_run else "Updated",
            }
            if action in verbs:
                print(f"  └ {verbs[action]} {label}")
            else:
                print(f"  └ {label} is already up to date")

        # Layout migrations run before the sync loop so build.sh/hooks.sh/build.conf sit at
        # their current paths first. They move user files, so they're skipped in
        # dry-run (which must not touch the tree).
        if not dry_run:
            if os.path.exists(_SYNC_LEGACY_BUILD) and not os.path.exists("scripts/core/build.sh"):
                os.makedirs("scripts/core", exist_ok=True)
                shutil.move(_SYNC_LEGACY_BUILD, "scripts/core/build.sh")
                print(
                    "  └ \033[93mMigrated\033[0m scripts/build.sh → scripts/core/build.sh "
                    "(framework-owned scripts now live under scripts/core/)"
                )
            # Legacy hook filenames → hooks.sh. Content is preserved exactly.
            for legacy in _SYNC_LEGACY_HOOKS:
                if os.path.exists(legacy) and not os.path.exists("scripts/hooks.sh"):
                    shutil.move(legacy, "scripts/hooks.sh")
                    print(f"  └ \033[93mMigrated\033[0m {legacy} → scripts/hooks.sh")
                    break
            # build.conf moved next to build.sh. Content is preserved exactly.
            if os.path.exists(_SYNC_LEGACY_BUILD_CONF) and not os.path.exists("scripts/core/build.conf"):
                os.makedirs("scripts/core", exist_ok=True)
                shutil.move(_SYNC_LEGACY_BUILD_CONF, "scripts/core/build.conf")
                print(f"  └ \033[93mMigrated\033[0m {_SYNC_LEGACY_BUILD_CONF} → scripts/core/build.conf")
            # A legacy file left behind because its new name already exists is never
            # run (a hook) or stops the build (build.conf); say so rather than leave it.
            for legacy, current, effect in (
                *((hook, "scripts/hooks.sh", "The build never runs it") for hook in _SYNC_LEGACY_HOOKS),
                (_SYNC_LEGACY_BUILD_CONF, "scripts/core/build.conf", "The build stops while it exists"),
            ):
                if os.path.exists(legacy):
                    print(
                        f"  └ \033[93m⚠\033[0m {legacy} was not moved: {current} already exists. "
                        f"{effect}; merge it into {current} and delete it."
                    )

        # Framework-owned files (see _sync_framework_files). _sync_file copies
        # only when content differs, backs up any overwrite, and records the
        # action so the summary can count what changed.
        print("\nUpdating framework files...")
        for src, dst, executable in framework_files:
            action = _sync_file(
                src, dst, executable=executable, dry_run=dry_run, backup_root=backup_root, changes=changes
            )
            report(action, dst)

        files_changed = sum(1 for action, _ in changes if action in ("created", "updated"))

        # deploy/install.sh — user-owned; created only when missing, never overwritten.
        if not os.path.exists("deploy/install.sh"):
            if not dry_run:
                os.makedirs("deploy", exist_ok=True)
                shutil.copy(f"{TEMPLATE_DIR}/deploy/install.sh", "deploy/install.sh")
                replace_text_in_place("deploy/install.sh", "{TOOL_NAME}", project_name)
            report("created", "deploy/install.sh (user-owned; created only when missing)")
            files_changed += 1

        # Ensure the project's .gitignore keeps ngargparser runtime artifacts
        # (.ngargparser/, *.bak) out of git. Append-only; older projects created
        # before scaffolds shipped a .gitignore get one here.
        gitignore_action = ensure_gitignore(".gitignore", dry_run=dry_run)
        if gitignore_action != "unchanged":
            report(gitignore_action, ".gitignore (ngargparser ignore rules)")
            files_changed += 1

        # Advisory: legacy projects without pyproject.toml
        if not os.path.exists("pyproject.toml") and os.path.exists("requirements.txt"):
            print(
                "\n\033[93m⚠\033[0m  This project still uses 'requirements.txt'. "
                "ngargparser scaffolds now generate 'pyproject.toml' + 'uv.lock'.\n"
                f"   To migrate: copy '{TEMPLATE_DIR}/pyproject.toml.tmpl' to './pyproject.toml', "
                "edit name/dependencies, then run 'uv lock'."
            )

        # Stamp the project with the framework version it was last synced against.
        # Future `cli` commands can read [tool.ngargparser] scaffold_version to
        # dispatch version-specific migrations.
        stamp_updated = False
        if os.path.exists("pyproject.toml"):
            current_stamp = _project_scaffold_version()
            if current_stamp != __version__:
                stamp_updated = True
                print("\nStamping framework version...")
                if current_stamp is None:
                    verb = "Would add" if dry_run else "Added"
                    print(f"  └ {verb} scaffold_version = \033[92m{__version__}\033[0m to [tool.ngargparser]")
                else:
                    print(f"  └ scaffold_version: \033[93m{current_stamp}\033[0m → \033[92m{__version__}\033[0m")
                if not dry_run:
                    write_scaffold_version("pyproject.toml", __version__)

        # README badge → current ngargparser version (green), only in README.md.
        print("\nUpdating README version badges...")
        current_badge = f"ngargparser-{__version__}-green.svg"
        if dry_run:
            readme_updated = os.path.exists("README.md") and current_badge not in Path("README.md").read_text(
                encoding="utf-8"
            )
        else:
            readme_updated = upsert_readme_badge("README.md", __version__, color="green")
        if readme_updated:
            print(f"  └ {'Would update' if dry_run else 'Updated'} README.md badge")
        else:
            print("  └ README.md badge is already current (or no README found)")

        anything_changed = files_changed > 0 or stamp_updated or readme_updated

        # Test drive: every help screen that worked before the sync must still
        # work now. If one doesn't, undo the whole sync and say why.
        verification = None
        if baseline is not None:
            worked_before = [command for command, result in baseline["results"].items() if result[0]]
            if not baseline["runnable"]:
                verification = ("unverified", baseline["reason"])
            elif not worked_before:
                first_error = next(iter(baseline["results"].values()))[1]
                verification = ("unverified", f"it did not start before the sync either ({first_error})")
            else:
                after = _smoke_check()["results"]
                broken = [command for command in worked_before if not after.get(command, (False, ""))[0]]
                if broken:
                    print(
                        "\n\033[91m✗\033[0m Sync rolled back: these commands worked before the sync and fail after it.\n"
                    )
                    for command in broken:
                        print(f"  {command}")
                        print(f"      {after.get(command, (False, 'did not run'))[1]}")
                    print()
                    _undo_sync(snapshot, backup_root)
                    print("  Fix the cause, or sync without these checks: cli sync --no-verify")
                    return 1
                verification = ("verified", f"{len(worked_before)} of {len(baseline['results'])}")

        # Summary
        print("\nSynchronization Summary:")
        print(f"  └ Framework files {'to change' if dry_run else 'changed'}: \033[92m{files_changed}\033[0m")
        if stamp_updated:
            print(f"  └ scaffold_version stamp: \033[92m{'would update' if dry_run else 'updated'}\033[0m")
        if readme_updated:
            print(f"  └ README badge: \033[92m{'would update' if dry_run else 'updated'}\033[0m")

        if dry_run:
            if anything_changed:
                print("\n\033[93mDRY RUN\033[0m — the above changes would be made. Re-run without --dry-run to apply.")
            else:
                print("\n\033[92m✓\033[0m DRY RUN — project is already up to date; nothing would change.")
        elif anything_changed:
            if backup_root and os.path.isdir(backup_root):
                print(f"  └ Backups of overwritten files: \033[92m{backup_root}\033[0m")
            print("\n\033[92m✓\033[0m Framework synchronization completed successfully!")
            print(f"Your {project_name} project now has the latest framework files.")
        else:
            print("\n\033[93m⚠\033[0m No files needed updating - your project is already up to date!")

        if verification and verification[0] == "verified":
            print(
                f"\033[92m✓\033[0m Verified: every help screen that worked before the sync still works ({verification[1]})."
            )
        elif verification:
            print(f"\033[93m⚠\033[0m Could not verify that the project still runs: {verification[1]}.")

        return 0

    except KeyboardInterrupt:
        print("\n\033[91m✗\033[0m Sync interrupted.")
        if snapshot is not None:
            _undo_sync(snapshot, backup_root)
        raise

    except Exception as e:
        print(f"\033[91m✗\033[0m Error during synchronization: {e}")
        if snapshot is not None:
            _undo_sync(snapshot, backup_root)
        return 1

    finally:
        if snapshot is not None:
            snapshot.discard()


def main():
    # add_help=False: the top-level -h/--help is registered by hand below so it
    # can run the release check after printing the (unchanged) help text.
    parser = argparse.ArgumentParser(description="NG Argument Parser Framework", add_help=False)
    parser.add_argument("-h", "--help", action=_HelpAction, help="show this help message and exit")

    # Add version argument
    parser.add_argument("-v", "--version", action=_VersionAction, help="show program's version number and exit")

    subparsers = parser.add_subparsers(dest="command")

    # Create 'generate' sub-command
    startapp_parser = subparsers.add_parser(
        "generate", aliases=["g"], allow_abbrev=True, help="Create a new custom app project structure"
    )
    startapp_parser.add_argument("project_name", type=str, help="Name of the project to create")

    # Create 'deps' sub-command (manages external tool deps in paths.py)
    deps_parser = subparsers.add_parser(
        "deps", aliases=["d"], allow_abbrev=True, help="Manage external tool dependencies declared in paths.py"
    )
    deps_subparsers = deps_parser.add_subparsers(dest="deps_action")
    deps_add = deps_subparsers.add_parser("add", help="Add one or more dependency stubs to paths.py")
    deps_add.add_argument("names", nargs="*", help="Dependency names to add (interactive if omitted)")
    deps_remove = deps_subparsers.add_parser(
        "remove", aliases=["rm"], help="Remove one or more dependency blocks from paths.py"
    )
    deps_remove.add_argument("names", nargs="*", help="Dependency names to remove (interactive if omitted)")
    deps_subparsers.add_parser("list", aliases=["ls"], help="List dependencies declared in paths.py")

    # Create 'sync' sub-command
    sync_parser = subparsers.add_parser(
        "sync",
        aliases=["s"],
        allow_abbrev=True,
        help="Synchronize framework files in existing projects to the latest version.",
    )
    sync_parser.add_argument(
        "--no-upgrade",
        dest="upgrade",
        action="store_false",
        default=True,
        help="Skip the self-upgrade step; only sync templates from the currently-installed ngargparser. "
        "A local checkout skips it already.",
    )
    sync_parser.add_argument(
        "-n",
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Show what would change without writing any files. Implies --no-upgrade.",
    )
    sync_parser.add_argument(
        "--no-backup",
        dest="backup",
        action="store_false",
        default=True,
        help="Don't back up framework files before overwriting them (backups are on by default).",
    )
    sync_parser.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        default=True,
        help="Skip the safety checks: the known-clash check and the before/after test drive of the project's help screens.",
    )
    sync_parser.add_argument(
        "--ref",
        default="latest",
        help="Git ref to upgrade ngargparser to. 'latest' (default) resolves to the highest semver tag on the remote, falling back to 'master' if no tags exist. Pass a branch/tag/sha (e.g., 'master', 'v0.2.2') to override, or a local directory path (e.g., a checkout of the ngargparser repo) to install from your working tree.",
    )
    sync_parser.add_argument(
        "--dev",
        action="store_true",
        help="Dev mode: pull the bleeding-edge tip of 'master' instead of the latest semver tag. Shortcut for --ref master; overrides --ref if both are given.",
    )

    # Create 'upgrade' sub-command (upgrades ngargparser itself; works anywhere)
    upgrade_parser = subparsers.add_parser(
        "upgrade",
        aliases=["u"],
        allow_abbrev=True,
        help="Upgrade ngargparser itself to the latest release tag on GitLab.",
    )
    upgrade_parser.add_argument(
        "--check",
        action="store_true",
        help="Report the installed version vs the latest remote tag without installing anything.",
    )
    upgrade_parser.add_argument(
        "--ref",
        default="latest",
        help="What to upgrade to. 'latest' (default) resolves to the highest semver tag on the remote, falling back to 'master' if no tags exist. Pass a branch/tag/sha (e.g., 'master', 'v0.2.2'), or a local directory ('.' or a path to an ngargparser checkout) to install from your working tree.",
    )
    upgrade_parser.add_argument(
        "--dev",
        action="store_true",
        help="Upgrade to the bleeding-edge tip of 'master' instead of the latest semver tag. Shortcut for --ref master.",
    )

    # Register the update notifier via atexit so it fires no matter how we exit,
    # including argparse's early exits (a subcommand's `--help`, a usage error), not
    # just after a full command. Registered before parse_args() so those early exits
    # are covered. Top-level `--version` / `--help` unregister it and check live.
    import atexit

    atexit.register(_notify_at_exit)

    args = parser.parse_args()
    global _INVOKED_COMMAND
    _INVOKED_COMMAND = args.command

    # Normalize 'rm' → 'remove' and 'ls' → 'list' for the dispatcher
    if getattr(args, "deps_action", None) == "rm":
        args.deps_action = "remove"
    elif getattr(args, "deps_action", None) == "ls":
        args.deps_action = "list"

    if args.command == "generate" or args.command == "g":
        rc = startapp_command(args) or 0
    elif args.command == "deps" or args.command == "d":
        rc = deps_command(args) or 0
    elif args.command == "sync" or args.command == "s":
        rc = sync_command(args) or 0
    elif args.command == "upgrade" or args.command == "u":
        rc = upgrade_command(args) or 0
    else:
        parser.print_help()  # Print help message if no command is specified
        rc = 0

    return rc  # the update notice fires via the atexit hook registered above


if __name__ == "__main__":
    import sys

    sys.exit(main() or 0)
