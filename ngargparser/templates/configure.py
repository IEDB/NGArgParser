#! /usr/bin/env python3

import os
import importlib.util
import re

CONFIG_PATH = "paths.py"
DOT_ENV_PATH = ".env"

def load_config(path):
    if not os.path.exists(path):
        print(f"❌ Config file '{path}' not found.")
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

def write_env_info(config, output_path):
    with open(output_path, "w") as f:
        for key, value in config.items():
            if value is None:
                continue

            if isinstance(value, str):
                value = value.strip("'").strip('"')
            
            f.write(f"{key.upper()}={value}\n")

    print(f"* .env file created")

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
        print(f"❌ Shell script for '{tool_prefix}' not created: required path is None or empty")
        return

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

def main():
    config = load_config(CONFIG_PATH)
    if not config:
        return
    
    write_env_info(config, DOT_ENV_PATH)

    # Dynamically detect all dependency tools from paths.py
    detected_tools = detect_dependency_tools(config)
    
    if not detected_tools:
        print("* No dependency tools detected in paths.py")
        return
    
    print(f"* Detected {len(detected_tools)} dependency tools: {', '.join(detected_tools.keys())}")
    
    # Create shell scripts for each detected tool
    for tool_prefix in detected_tools.keys():
        create_shell_script(config, tool_prefix, output_path=f'setup_{tool_prefix}_env.sh')

if __name__ == "__main__":
    main()