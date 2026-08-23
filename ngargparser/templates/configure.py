#! /usr/bin/env python3

import argparse
import os
import sys
import importlib.util
import re
import glob
from dotenv import load_dotenv

CONFIG_PATH = "paths.py"
DOT_ENV_PATH = ".env"
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

def resolve_tool_venvs(config, detected_tools):
    """Fill in a tool's virtualenv from a .venv inside its own directory,
    when the user hasn't set one explicitly and a usable one is there.

    Resolved in memory only -- paths.py keeps its None, so the same file
    stays portable across hosts (dev laptop, dev server, SDSC), each
    resolving its own local .venv at configure time. A tool whose venv
    lives elsewhere (pyenv, conda) simply isn't matched here; that's what
    the explicit <tool>_venv setting is for.
    """
    for tool_prefix in detected_tools:
        if config.get(f"{tool_prefix}_venv"):
            continue  # an explicit setting always wins
        tool_path = config.get(f"{tool_prefix}_path")
        if not tool_path:
            continue
        candidate = os.path.join(tool_path, ".venv")
        # Check the file that actually gets sourced, not just the directory:
        # an empty or half-deleted .venv/ would otherwise yield a broken script.
        if os.path.isfile(os.path.join(candidate, "bin", "activate")):
            config[f"{tool_prefix}_venv"] = candidate
            print(f"* {tool_prefix}: using virtualenv found at {candidate}")

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
    if not config or (set(config.keys()) <= {'APP_ROOT', 'APP_NAME'}):
        return f"APP_ROOT={app_root}\nAPP_NAME={config['APP_NAME']}\n"

    lines = []
    for key, value in config.items():
        if value is None:
            continue

        if isinstance(value, str):
            value = value.strip("'").strip('"')

        lines.append(f"{key.upper()}={value}\n")
    return "".join(lines)

def create_shell_script(config, tool_prefix, output_path):
    """
    Create shell script for a dependency tool.
    
    Args:
        config: Configuration dictionary
        tool_prefix: The prefix used for this tool's variables (e.g., 'phbr', 'pepx', 'mhci')
        output_path: Path where to write the shell script
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
    resolve_tool_venvs(config, detected_tools)

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

    if not config or (set(config.keys()) <= {'APP_ROOT', 'APP_NAME'}):
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
        ok = create_shell_script(config, tool_prefix, output_path=f'setup_{tool_prefix}_env.sh')
        if not ok:
            unfilled.append(tool_prefix)

    if unfilled:
        sys.exit(1)

if __name__ == "__main__":
    main()
