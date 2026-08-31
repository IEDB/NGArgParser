#! /usr/bin/env python3

import argparse
import os
import sys
import importlib.util
import re
import glob
from dotenv import load_dotenv, dotenv_values

CONFIG_PATH = "paths.py"
DOT_ENV_PATH = ".env"
# Keys that describe the app itself rather than one of its dependencies.
APP_KEYS = {'APP_ROOT', 'APP_NAME', 'APP_VENV'}
# Conventional virtualenv directory names, in the order they're tried.
VENV_CANDIDATE_NAMES = (".venv", "venv", "env", ".virtualenv")
load_dotenv()

def load_config(path):
    if not os.path.exists(path):
        # Missing paths.py is a valid steady state for tools with no external
        # IEDB-tool dependencies (e.g. standalone scorers like pepsysco).
        print(f"\033[2mℹ  No '{path}' found — treating as a tool with no external dependencies.\033[0m")
        return {}
    
    # Treat 'path' as a module and load everything into 'config'
    spec = importlib.util.spec_from_file_location("config", path)
    config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config)

    return {
        k: v for k, v in config.__dict__.items()
        if not k.startswith("__")
    }

def detect_dependency_tools(config):
    """
    Detect all dependency tools from the config by finding variables ending with '_path'.
    Only detects main tool paths, ignoring optional paths like _lib_path.
    Returns a dictionary mapping tool names to their variable prefixes.
    """
    tools = {}
    
    for key in config.keys():
        if key.endswith('_path'):
            # Extract the tool prefix (everything before '_path')
            tool_prefix = key[:-5]  # Remove '_path' suffix
            
            # Skip optional path variables (lib_path, venv, module are not main tools)
            if tool_prefix.endswith('_lib') or tool_prefix.endswith('_venv') or tool_prefix.endswith('_module'):
                continue
            
            # Check if this tool has the required configuration structure
            # (at minimum, it should have a _path variable)
            if f"{tool_prefix}_path" in config:
                tools[tool_prefix] = tool_prefix

    return tools

def is_usable_venv(path):
    """True when `path` really is a virtualenv.

    Both markers are required: pyvenv.cfg identifies a virtualenv (PEP 405),
    and bin/activate is the file the generated setup script sources. An empty
    or half-deleted directory fails on one or the other.
    """
    return os.path.isfile(os.path.join(path, "pyvenv.cfg")) and os.path.isfile(
        os.path.join(path, "bin", "activate")
    )

def find_bundled_venvs(root):
    """Return the conventional virtualenvs bundled inside `root`.

    Convention, not discovery: only well-known directory names are tried, so
    a scan can never bind a tool to some unrelated directory that happens to
    contain an 'activate' file. Every match is returned, because two matches
    is a question for the user rather than something to guess at.
    """
    return [
        os.path.join(root, name)
        for name in VENV_CANDIDATE_NAMES
        if is_usable_venv(os.path.join(root, name))
    ]

def declared_venv(tool_path):
    """Read a dependency's own APP_VENV out of its .env, if it publishes one.

    Parsed as key/value text, never executed. The tool's paths.py is a Python
    module, so reading that instead would run another project's code inside
    this configure run. A tool with no .env, or one that predates APP_VENV,
    simply declares nothing.
    """
    env_path = os.path.join(tool_path, DOT_ENV_PATH)
    if not os.path.isfile(env_path):
        return None
    try:
        return dotenv_values(env_path).get("APP_VENV") or None
    except OSError:
        return None

def resolve_tool_venvs(config, detected_tools):
    """Fill in a tool's virtualenv when the user hasn't set one explicitly.

    Two sources, in order: what the tool publishes about itself in its own
    .env, then a conventional virtualenv directory bundled inside the tool.

    Resolved in memory only -- paths.py keeps its None, so the same file
    stays portable across hosts (dev laptop, dev server, SDSC), each
    resolving locally at configure time. A tool whose venv lives elsewhere
    (pyenv, conda) and that publishes nothing simply isn't matched here;
    that's what the explicit <tool>_venv setting is for.

    Returns the tools left unresolved because their bundled virtualenv was
    ambiguous, so the caller can skip the generic "no virtualenv" warning for
    them: "has more than one" already said it, more precisely.
    """
    ambiguous = set()
    for tool_prefix in detected_tools:
        if config.get(f"{tool_prefix}_venv"):
            continue  # an explicit setting always wins
        tool_path = config.get(f"{tool_prefix}_path")
        if not tool_path:
            continue

        declared = declared_venv(tool_path)
        if declared:
            # Adopted even when it looks wrong, for the same reason a missing
            # path doesn't block: it may be correct on the deploy target.
            config[f"{tool_prefix}_venv"] = declared
            print(f"* {tool_prefix}: using virtualenv declared in its .env: {declared}")
            if os.path.isdir(declared) and not is_usable_venv(declared):
                print(
                    f"\033[93m⚠\033[0m  \033[1m{tool_prefix}\033[0m declares "
                    f"'{declared}', which has no pyvenv.cfg. Using it anyway."
                )
            continue

        candidates = find_bundled_venvs(tool_path)
        if len(candidates) == 1:
            config[f"{tool_prefix}_venv"] = candidates[0]
            print(f"* {tool_prefix}: using virtualenv found at {candidates[0]}")
        elif candidates:
            ambiguous.add(tool_prefix)
            flag_base = tool_prefix.replace("_", "-")
            names = ", ".join(os.path.basename(c) for c in candidates)
            print(
                f"\033[93m⚠\033[0m  \033[1m{tool_prefix}\033[0m has more than one "
                f"virtualenv in '{tool_path}' ({names}). Not guessing: pick one with "
                f"'\033[1m./configure --{flag_base}-venv=<path>\033[0m'."
            )

    return ambiguous

def build_arg_parser(config, detected_tools):
    """
    Build an argparse parser with one --<tool>-path/-venv/-module/-lib-path
    flag per field of every tool already declared in paths.py. Flags let you
    set a dependency's fields without hand-editing paths.py; a flag only
    exists for a tool `cli deps add` already declared.
    """
    parser = argparse.ArgumentParser(
        prog="./configure",
        description="Regenerate .env and per-tool setup scripts from paths.py. "
                     "Optionally set a dependency's fields without hand-editing paths.py.",
    )
    if not detected_tools:
        parser.epilog = "No dependencies declared in paths.py yet — run `cli deps add <tool>` first."
        return parser

    for tool_prefix in detected_tools:
        flag_base = tool_prefix.replace("_", "-")
        for suffix in ("path", "venv", "module", "lib_path"):
            var_name = f"{tool_prefix}_{suffix}"
            current = config.get(var_name)
            parser.add_argument(
                f"--{flag_base}-{suffix.replace('_', '-')}",
                default=None,
                help=f"Set {var_name} (currently: {current!r}).",
            )
    return parser

def apply_paths_overrides(config, args, config_path):
    """
    Apply any --<tool>-<field> flags to `config` and persist them back into
    `config_path` (paths.py). Returns the set of variable names changed.
    """
    overrides = {
        var_name: value
        for var_name, value in vars(args).items()
        if value is not None and config.get(var_name) != value
    }
    if not overrides:
        return overrides

    with open(config_path, "r") as f:
        content = f.read()

    for var_name, value in overrides.items():
        content = re.sub(
            rf"(?m)^{re.escape(var_name)}\s*=.*$",
            f"{var_name}={value!r}",
            content,
        )
        config[var_name] = value
        print(f"* Set {var_name} = {value!r} in paths.py")

    with open(config_path, "w") as f:
        f.write(content)

    return overrides

def render_env_content(config, app_root):
    """Build the .env content for `config`. Returns a string; callers
    decide whether it differs from what's on disk before writing."""
    if not config or (set(config.keys()) <= APP_KEYS):
        lines = [f"APP_ROOT={app_root}\n", f"APP_NAME={config['APP_NAME']}\n"]
        if config.get('APP_VENV'):
            lines.append(f"APP_VENV={config['APP_VENV']}\n")
        return "".join(lines)

    lines = []
    for key, value in config.items():
        if value is None:
            continue

        if isinstance(value, str):
            value = value.strip("'").strip('"')

        lines.append(f"{key.upper()}={value}\n")
    return "".join(lines)

def create_shell_script(config, tool_prefix, output_path, warn_no_venv=True):
    """
    Create shell script for a dependency tool.

    Args:
        config: Configuration dictionary
        tool_prefix: The prefix used for this tool's variables (e.g., 'phbr', 'pepx', 'mhci')
        output_path: Path where to write the shell script
        warn_no_venv: Whether to warn when the tool has no virtualenv. Off for a
            tool whose bundled virtualenvs were ambiguous, which already warned.
    """
    # Get values from config using prefix
    module = config.get(f"{tool_prefix}_module")
    venv = config.get(f"{tool_prefix}_venv")
    lib_path = config.get(f"{tool_prefix}_lib_path")
    tool_path = config.get(f"{tool_prefix}_path")
    env_var = f"{tool_prefix.upper()}_PATH"

    # Check if required path is None or empty
    if tool_path is None or (isinstance(tool_path, str) and tool_path.strip() == ""):
        flag_base = tool_prefix.replace("_", "-")
        print(
            f"\033[91m✗\033[0m \033[1m{tool_prefix}_path\033[0m is None in paths.py. "
            f"Set it with '\033[1m./configure --{flag_base}-path=<path>\033[0m', "
            f"or run '\033[1mcli deps remove {tool_prefix}\033[0m' to drop this dependency."
        )
        return False

    # Advisory only: a missing directory doesn't block .env/script generation,
    # it just warns the tool may not work until the path is corrected.
    for field, value in (("path", tool_path), ("venv", venv), ("lib_path", lib_path)):
        if value and not os.path.isdir(value):
            print(
                f"\033[93m⚠\033[0m  \033[1m{tool_prefix}_{field}\033[0m = '{value}' does not exist. "
                f"'{tool_prefix}' may fail until this is corrected."
            )

    # Nothing set, nothing published, nothing bundled: the tool inherits
    # whatever interpreter happens to be active, which is worth saying out
    # loud rather than leaving to be discovered at runtime.
    if not venv and warn_no_venv:
        flag_base = tool_prefix.replace("_", "-")
        print(
            f"\033[93m⚠\033[0m  \033[1m{tool_prefix}\033[0m has no virtualenv. It will run "
            f"under whatever Python is active. Set one with "
            f"'\033[1m./configure --{flag_base}-venv=<path>\033[0m' if it needs its own."
        )

    lines = ["#!/bin/bash\n"]

    lines.append(f"# ---- Setup for {tool_prefix.upper()} ----")

    # Optional: Load module
    if module:
        lines.append("module purge")
        lines.append(f"module load {module}")

    # Optional: Activate virtualenv
    if venv:
        lines.append(f"source {venv}/bin/activate")

    # Optional: Set LD_LIBRARY_PATH
    if lib_path:
        lines.append(f"export LD_LIBRARY_PATH={lib_path}:$LD_LIBRARY_PATH")

    # Required: Export tool path
    lines.append(f"export {env_var}={tool_path}")

    with open(output_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    os.chmod(output_path, 0o755)
    print(f"* Shell script for '{tool_prefix}' created at '{output_path}'")
    return True

def cleanup_old_shell_scripts(current_tools):
    """
    Remove shell scripts for tools that are no longer in paths.py
    
    Args:
        current_tools: Set of current tool prefixes from paths.py
    """
    # Find all existing setup_*_env.sh files
    existing_scripts = glob.glob("setup_*_env.sh")
    removed = 0

    for script_path in existing_scripts:
        # Extract tool prefix from filename (setup_TOOL_env.sh -> TOOL)
        filename = os.path.basename(script_path)
        if filename.startswith("setup_") and filename.endswith("_env.sh"):
            tool_prefix = filename[6:-7]  # Remove "setup_" and "_env.sh"

            # If this tool is no longer in paths.py, remove the script
            if tool_prefix not in current_tools:
                os.remove(script_path)
                removed += 1
                print(f"* Removed shell script for '{tool_prefix}' (no longer in paths.py)")

    return removed

def main():
    config = load_config(CONFIG_PATH)
    config_present = os.path.exists(CONFIG_PATH)

    # Dynamically detect all dependency tools from paths.py, build --<tool>-*
    # flags from them, and apply any the user supplied before doing anything
    # else, so the rest of this run sees the updated values.
    detected_tools = detect_dependency_tools(config)
    args = build_arg_parser(config, detected_tools).parse_args()
    apply_paths_overrides(config, args, CONFIG_PATH)

    # Fall back to a tool's own bundled .venv for any tool without an
    # explicit virtualenv, so paths.py doesn't need a host-specific path.
    ambiguous_venvs = resolve_tool_venvs(config, detected_tools)

    # Always ensure APP_ROOT is present in config
    app_root = os.path.abspath(".")
    if 'APP_ROOT' not in config:
        config['APP_ROOT'] = app_root

    # Ensure APP_NAME is set. Prefer persisted APP_NAME from .env (created by 'cli g').
    # If not present, derive from build dir name pattern 'ng_<name>-local' or fall back to directory name.
    env_app_name = os.getenv('APP_NAME')
    if env_app_name:
        config['APP_NAME'] = env_app_name
    elif 'APP_NAME' not in config:
        base_name = os.path.basename(app_root)
        match = re.match(r'^ng[_-]([A-Za-z0-9_]+?)-local$', base_name)
        config['APP_NAME'] = match.group(1) if match else base_name

    # Publish this project's own virtualenv so a tool that depends on it can
    # read it from .env instead of hunting for it. Re-derived every run, so a
    # deleted virtualenv stops being advertised. Two candidates publishes
    # nothing: the ambiguity belongs to whoever set the project up.
    if 'APP_VENV' not in config:
        own_venvs = find_bundled_venvs(app_root)
        if len(own_venvs) == 1:
            config['APP_VENV'] = own_venvs[0]

    # Regenerate .env file based on current paths.py content (this ensures
    # removed dependencies are cleaned up), but skip the write entirely when
    # nothing would actually change.
    env_content = render_env_content(config, app_root)
    existing_content = None
    if os.path.exists(DOT_ENV_PATH):
        with open(DOT_ENV_PATH, "r") as f:
            existing_content = f.read()

    if existing_content == env_content:
        action = "unchanged"
    else:
        with open(DOT_ENV_PATH, "w") as f:
            f.write(env_content)
        action = "updated" if existing_content is not None else "created"

    if not config or (set(config.keys()) <= APP_KEYS):
        print(f"* Minimal .env file {action} (no external dependencies declared).")
    else:
        print(f"* .env file {action}")

    # Clean up shell scripts for removed dependencies
    removed = cleanup_old_shell_scripts(set(detected_tools.keys()))

    if not detected_tools:
        # Only surface this line when there's something to report — either paths.py
        # exists (so the user might expect deps) or we cleaned up stale scripts.
        # When paths.py is absent and nothing was cleaned, load_config's info line
        # already covers the state.
        if config_present or removed:
            print("* No dependency tools declared in paths.py")
        return

    print(f"* Detected {len(detected_tools)} dependency tools: {', '.join(detected_tools.keys())}")

    # Create shell scripts for each detected tool
    unfilled = []
    for tool_prefix in detected_tools.keys():
        ok = create_shell_script(
            config,
            tool_prefix,
            output_path=f'setup_{tool_prefix}_env.sh',
            warn_no_venv=tool_prefix not in ambiguous_venvs,
        )
        if not ok:
            unfilled.append(tool_prefix)

    if unfilled:
        sys.exit(1)

if __name__ == "__main__":
    main()
