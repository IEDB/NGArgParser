#! /usr/bin/env python3

import argparse
import os
import sys
import platform
from configparser import ConfigParser


# Interactive input for tool paths
def get_tool_path(tool_name, non_interactive, default_path="None"):
    """Prompt user for a tool path or use default in non-interactive mode."""
    if non_interactive:
        print(f"⚙️  Using default path for {tool_name}: {default_path}")
        return default_path

    try:
        path = input(f"Enter the path to {tool_name} (press Enter to skip): ").strip()
        return path if path else default_path
    except KeyboardInterrupt:
        print("\n⚠️  Configuration aborted by user. Exiting gracefully...")
        sys.exit(0)

def create_config_file(args, dependent_tools):
    """Create a default configuration file if it doesn't exist."""
    config_file = 'config.ini'
    
    if not os.path.exists(config_file):
        print(f"Creating default configuration file: {config_file}")
    else:
        print('Overriding exising configuration file.')

    # Create a ConfigParser object
    config_object = ConfigParser()

    # Add general information about this package
    config_object["GENERAL"] = {
        "name": "PROJECT_NAME"
    }

    # Add Dependency Tools
    if dependent_tools:
        print("\nThis tool depends on:")
        for tool in dependent_tools:
            print(f"🔹 {tool[0]} (e.g., /usr/local/bin/{tool[1]})")
        print("\n")

        config_object["DEPENDENCY_TOOL_PATHS"] = {}
        for tool in dependent_tools:
            config_object["DEPENDENCY_TOOL_PATHS"][tool[0]] = get_tool_path(tool[1], args.no_interactive)

    # Add Python envrionment
    config_object["ENV"] = {
        "python_version": platform.python_version(),
        "path": sys.prefix,
        "python_executable": sys.executable,
    }
    

    with open(config_file, 'w') as conf: 
        config_object.write(conf)

    print(f"\n✅ Configuration saved to {config_file}")


def main():
    # Argument parser for non-interactive mode
    parser = argparse.ArgumentParser(description="Configuration Tool")
    parser.add_argument("--no-interactive", "-i", dest="no_interactive", action="store_true", help="Run in non-interactive mode")
    args = parser.parse_args()

    # ADD app-specific configuration
    # -----------------------------------------------------
    
    
    # MODIFY to include other IEDB tools
    # -----------------------------------------------------
    dependent_tools = [
        # General name, Package name
        ('mhci', 'tcell_mhci'),
        ('mhcii', 'tcell_mhcii'),
    ]

    # Creates the .ini file
    create_config_file(args, dependent_tools)


if __name__=='__main__':
    main()