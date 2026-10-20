# core_validators.py
# Core validation logic - DO NOT MODIFY
# This file contains system-level validators that should not be changed by users

import argparse
import os
import re
import stat
from pathlib import Path

# Relative: core.app_env in a tool, ngargparser.app_env in the package's own tests.
from .app_env import PROJECT_ROOT, load_app_env

# Load this tool's .env (its own values win over inherited ones; see app_env)
load_app_env()

# Get the project root directory and app name from environment variables
APP_ROOT = os.getenv("APP_ROOT")
APP_NAME = os.getenv("APP_NAME")


def get_dependencies_from_paths(file_path="paths.py"):
    """
    Read paths.py file and return a list of dependency tool names that have
    their required path fields filled (not None or empty string).

    Args:
        file_path (str): Path to the paths.py file

    Returns:
        list: List of dependency tool names that have valid paths. A missing
        file is treated the same as an existing, empty file (returns an
        empty list) rather than raising.
    """
    try:
        with open(file_path, "r") as file:
            content = file.read()
    except FileNotFoundError:
        # A missing paths.py is the scaffold's default state right after
        # `cli startapp` (before any `cli deps add`) — treat it the same as
        # an existing-but-empty file: zero dependencies declared.
        return []

    dependencies = []

    # Pattern to match tool sections and their path variables
    # Looking for comments like ''' [ Tool Name ] ''' followed by tool_path=value
    tool_sections = re.findall(r"'''\s*\[\s*([^\]]+)\s*\]\s*'''(.*?)(?='''\s*\[|$)", content, re.DOTALL)

    for tool_name, section_content in tool_sections:
        tool_name = tool_name.strip()

        # Look for the main path variable (ends with _path=)
        path_matches = re.findall(r"(\w+_path)\s*=\s*([^#\n]+)", section_content)

        for var_name, value in path_matches:
            # Clean the value - remove quotes, whitespace, and check if it's not None or empty
            cleaned_value = value.strip().strip("'\"")

            # Debug print to see what we're parsing
            # print(f"Debug: Tool='{tool_name}', Var='{var_name}', Value='{cleaned_value}'")

            if cleaned_value and cleaned_value.lower() != "none":
                dependencies.append(tool_name)
                break  # Only need one valid path per tool

    return dependencies


def create_directory_structure_for_dependencies(output_path, paths_file_path=None, errors=None):
    """
    Read paths.py file, identify dependencies, and create the required directory
    structure for each dependency tool under the specified output directory.
    If no dependencies are found, create a default directory structure.

    NEW LOGIC:
    - If there are dependencies: Create structure inside each dependency folder + main tool folder
    - If no dependencies: Create structure directly in output directory

    Args:
        output_path (str or Path): The output directory where tool structures will be created
        paths_file_path (str or Path, optional): Path to the paths.py file. If None,
                                               looks for paths.py in the same directory as this script
        errors (list, optional): When given, each directory that could not be created
                                 is appended as a message (it is printed either way), so
                                 the caller can stop instead of carrying on.

    Returns:
        dict: Dictionary with tool names as keys and their created directories
        as values. A missing paths.py file is treated the same as an
        existing, empty file: the default directory structure is created.
    """
    # Handle paths.py file location
    if paths_file_path is None:
        # Look for paths.py in the project root: APP_ROOT, or, when .env has not set
        # it, the project this file sits in (2 levels up from core_validators.py).
        paths_file = Path(APP_ROOT or PROJECT_ROOT) / "paths.py"
    else:
        paths_file = Path(paths_file_path)

    # Output directory location
    output_dir = Path(output_path)

    def report_failure(directory, e):
        message = f"Error creating directory {directory}: {e}"
        print(message)
        if errors is not None:
            errors.append(message)

    try:
        with open(paths_file, "r") as file:
            content = file.read()
    except FileNotFoundError:
        # Missing file == empty file: no tool sections parsed below, so
        # has_dependencies stays False and the default structure is built.
        content = ""

    created_structures = {}

    # More robust pattern to match tool sections
    # Split by triple quotes and process each section
    sections = re.split(r"'''\s*\[\s*([^\]]+)\s*\]\s*'''", content)[1:]  # Skip first empty part

    # Check if we have any dependencies
    has_dependencies = False
    dependency_tools = []

    # Process sections in pairs (name, content)
    for i in range(0, len(sections), 2):
        if i + 1 < len(sections):
            tool_name = sections[i].strip()
            section_content = sections[i + 1]

            # Look for the main path variable (ends with _path=)
            path_matches = re.findall(r"(\w+_path)\s*=\s*([^#\n]+)", section_content)

            for var_name, value in path_matches:
                # Clean the value - remove quotes, whitespace, and check if it's not None or empty
                cleaned_value = value.strip().strip("'\"")

                if cleaned_value and cleaned_value.lower() != "none":
                    has_dependencies = True
                    dependency_tools.append(tool_name)
                    break  # Only need one valid path per tool

    # Get the current app name - prefer APP_NAME from environment (.env),
    # then fall back to existing detection methods.
    curr_app_name = APP_NAME

    # Method 1: Try to get from current working directory
    if not curr_app_name:
        try:
            cwd = Path.cwd()
            if "test" in cwd.parts:
                test_index = cwd.parts.index("test")
                if test_index + 1 < len(cwd.parts):
                    curr_app_name = cwd.parts[test_index + 1]
        except Exception:
            pass

    # Method 2: Fallback to the old method if Method 1 fails
    if not curr_app_name:
        curr_app_name = Path(__file__).resolve().parents[1].name

    # Method 3: If still not found, try to extract from the paths.py file location
    if (not curr_app_name or curr_app_name == "src") and paths_file_path:
        try:
            paths_file = Path(paths_file_path)
            if "test" in paths_file.parts:
                test_index = paths_file.parts.index("test")
                if test_index + 1 < len(paths_file.parts):
                    curr_app_name = paths_file.parts[test_index + 1]
        except Exception:
            pass

    # Final fallback
    if not curr_app_name or curr_app_name == "src":
        curr_app_name = "app"  # Generic fallback

    if has_dependencies:
        # NEW LOGIC: Create structure inside each dependency folder + main tool folder
        print(f"Found {len(dependency_tools)} dependencies: {', '.join(dependency_tools)}")

        # Create structure for each dependency tool
        for tool_name in dependency_tools:
            # Convert tool name to lowercase and replace spaces with underscores for directory name
            tool_dir_name = tool_name.lower().replace(" ", "_")

            # Defining alias for tool names
            if tool_dir_name == "t_cell_class_i":
                tool_dir_name = "mhci"
            elif tool_dir_name == "t_cell_class_ii":
                tool_dir_name = "mhcii"

            tool_path = output_dir / tool_dir_name

            # Create the dependency tool directory
            try:
                tool_path.mkdir(parents=True, exist_ok=True)

                # Create the standard structure inside each dependency folder
                dep_directories = [
                    tool_path / "predict-inputs" / "data",
                    tool_path / "predict-inputs" / "params",
                    tool_path / "predict-outputs",
                    tool_path / "aggregate",
                ]

                created_dirs = []
                for directory in dep_directories:
                    try:
                        directory.mkdir(parents=True, exist_ok=True)
                        created_dirs.append(str(directory))
                    except Exception as e:
                        report_failure(directory, e)

                created_structures[tool_name] = created_dirs

            except Exception as e:
                report_failure(tool_path, e)

        # Create structure for the main tool (phbr in your example)
        main_tool_directories = [
            output_dir / curr_app_name / "predict-inputs" / "data",
            output_dir / curr_app_name / "predict-inputs" / "params",
            output_dir / curr_app_name / "predict-outputs",
            output_dir / curr_app_name / "aggregate",
            output_dir / curr_app_name / "results",  # Added results folder for main tool
        ]

        created_dirs = []
        for directory in main_tool_directories:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                created_dirs.append(str(directory))
            except Exception as e:
                report_failure(directory, e)

        created_structures[curr_app_name] = created_dirs

    else:
        # If no dependencies were found, create default directory structure directly in output directory
        print("No dependencies found, creating default structure")

        directories = [
            output_dir / "predict-inputs" / "data",
            output_dir / "predict-inputs" / "params",
            output_dir / "predict-outputs",
            output_dir / "aggregate",
            output_dir / "results",  # Added results folder for no-dependencies case
        ]

        # Create the directories
        created_dirs = []
        for directory in directories:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                created_dirs.append(str(directory))
            except Exception as e:
                report_failure(directory, e)

        created_structures["default"] = created_dirs

    return created_structures


def _missing(path_str, path):
    """What a path that does not exist is: a link to nothing, or nothing."""
    if os.path.islink(path):
        return f"'{path_str}' is a broken link: its target '{os.readlink(path)}' does not exist."
    return f"'{path_str}' does not exist."


def _refuse_empty(path_str, wanted):
    """Raise ArgumentTypeError for an empty path: Path('') is the current folder,
    so postprocess -i '' (an unset shell variable) merged whatever was there, and
    -j '' said "'' is a folder". `wanted` names what to give instead."""
    if isinstance(path_str, (str, bytes)) and not path_str:
        raise argparse.ArgumentTypeError(f"the path is empty; give {wanted}.")


def _unreadable(path_str):
    """ArgumentTypeError for a path this user may not read."""
    return argparse.ArgumentTypeError(f"'{path_str}' cannot be read (permission denied).")


def _mode(path_str, path):
    """The st_mode of path (links followed), or None when nothing is there.

    A path in a folder this user may not enter raises ArgumentTypeError (cannot
    be read). Path.is_file() and Path.exists() raised PermissionError for it
    before Python 3.14 and return False from 3.14 on, which said "does not
    exist"; os.stat() raises PermissionError on every Python. Any other OSError
    (no such file, a file used as a folder, a link loop, a name too long) or a
    ValueError (a NUL in the path) is nothing there."""
    try:
        return os.stat(path).st_mode
    except PermissionError:
        raise _unreadable(path_str) from None
    except (OSError, ValueError):
        return None


def validate_file(path_str):
    """Validate that the given path is a file (a folder is refused) this user can read.

    The message says what the path is instead: empty, a folder, missing, a broken
    link, another kind of file such as a pipe, or unreadable (mode 000, or in a
    folder this user may not enter), which ended the run in a PermissionError
    traceback. Each used to say "is not a valid file"."""
    _refuse_empty(path_str, "a file")
    path = Path(path_str)
    mode = _mode(path_str, path)
    if mode is None:
        raise argparse.ArgumentTypeError(_missing(path_str, path))
    if stat.S_ISDIR(mode):
        raise argparse.ArgumentTypeError(f"'{path_str}' is a folder, not a file.")
    if not stat.S_ISREG(mode):
        raise argparse.ArgumentTypeError(f"'{path_str}' is not a regular file.")
    if not os.access(path, os.R_OK):
        raise _unreadable(path_str)

    validate_directory_given_filename(path_str)
    return path


def validate_directory(path_str):
    """Validate that the given path is a directory this user can read and enter.

    The message says what the path is instead: empty, a file, missing, a broken
    link, another kind of file, or unreadable (os.access R_OK and X_OK), whose
    files the run could not list or open. Each used to say "is not a valid directory"."""
    _refuse_empty(path_str, "a folder")
    path = Path(path_str)
    mode = _mode(path_str, path)
    if mode is None:
        raise argparse.ArgumentTypeError(_missing(path_str, path))
    if stat.S_ISREG(mode):
        raise argparse.ArgumentTypeError(f"'{path_str}' is a file, not a folder.")
    if not stat.S_ISDIR(mode):
        raise argparse.ArgumentTypeError(f"'{path_str}' is not a folder.")
    if not os.access(path, os.R_OK | os.X_OK):
        raise _unreadable(path_str)
    return path


def validate_directory_given_filename(path_str):
    """Validate that the parent directory of the given path exists, and that the
    path is no folder (it names a file, which may not exist yet). An empty path
    is refused, and so is a path in a folder this user may not enter (cannot be
    read), which raised PermissionError before Python 3.14 and passed from 3.14 on."""
    _refuse_empty(path_str, "a file path")
    path = Path(path_str)
    parent_dir = path.parent

    mode = _mode(path_str, path)
    if mode is not None and stat.S_ISDIR(mode):
        raise argparse.ArgumentTypeError(f"'{path_str}' is a folder, not a file.")
    parent_mode = _mode(path_str, parent_dir)
    if parent_mode is None:
        raise argparse.ArgumentTypeError(f"'{path_str}': folder '{parent_dir}' does not exist.")
    if not stat.S_ISDIR(parent_mode):
        raise argparse.ArgumentTypeError(f"'{path_str}': '{parent_dir}' is not a folder.")
    return path


def validate_preprocess_dir(path_str):
    """Validate preprocessing directory and create necessary structure.

    Legacy: the framework no longer uses it (preprocess checks -o with a pure
    check and creates the folders in parse_args, once the whole command line is
    valid), and a new tool's src/validators.py no longer imports it. It stays so
    an older tool's validators.py, which imports it by name, still loads. Not a
    validator to pass as type=: it creates the preprocess folders and prints them
    while argparse is still reading the command line.
    """
    path = validate_directory(path_str)

    # paths.py file
    # paths_file = Path(__file__).resolve().parent / "paths.py"
    paths_file = path.resolve().parent / "paths.py"

    try:
        # Get list of dependencies
        deps = get_dependencies_from_paths(paths_file)
        print(f"Found {len(deps)} dependencies:")
        if deps:
            for dep in deps:
                print(f"- {dep}")
        else:
            print("- No dependencies found, creating default structure")

        print(f"\nCreating directory structures under: {path}")
        # Create directory structures for all dependencies under output directory
        created = create_directory_structure_for_dependencies(path, paths_file)

        print(f"\nSuccessfully created directory structures for {len(created)} tools:")
        for tool_name, dirs in created.items():
            print(f"\n{tool_name}:")
            for dir_path in dirs:
                print(f"  - {dir_path}")

    except FileNotFoundError as e:
        print(f"Error: {e}")

    return path
