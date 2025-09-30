# core_validators.py
# Core validation logic - DO NOT MODIFY
# This file contains system-level validators that should not be changed by users

import argparse
import re
import os
import sys
import dotenv
from pathlib import Path

# Load environment variables from .env file
dotenv.load_dotenv()

# Get the project root directory from environment variable
APP_ROOT = os.getenv('APP_ROOT')


def get_dependencies_from_paths(file_path='paths.py'):
    """
    Read paths.py file and return a list of dependency tool names that have
    their required path fields filled (not None or empty string).
    
    Args:
        file_path (str): Path to the paths.py file
        
    Returns:
        list: List of dependency tool names that have valid paths
    """
    try:
        with open(file_path, 'r') as file:
            content = file.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"Could not find {file_path}")
    
    dependencies = []
    
    # Pattern to match tool sections and their path variables
    # Looking for comments like ''' [ Tool Name ] ''' followed by tool_path=value
    tool_sections = re.findall(r"'''\s*\[\s*([^\]]+)\s*\]\s*'''(.*?)(?='''\s*\[|$)", content, re.DOTALL)
    
    for tool_name, section_content in tool_sections:
        tool_name = tool_name.strip()
        
        # Look for the main path variable (ends with _path=)
        path_matches = re.findall(r'(\w+_path)\s*=\s*([^#\n]+)', section_content)
        
        for var_name, value in path_matches:
            # Clean the value - remove quotes, whitespace, and check if it's not None or empty
            cleaned_value = value.strip().strip("'\"")
            
            # Debug print to see what we're parsing
            # print(f"Debug: Tool='{tool_name}', Var='{var_name}', Value='{cleaned_value}'")
            
            if cleaned_value and cleaned_value.lower() != 'none':
                dependencies.append(tool_name)
                break  # Only need one valid path per tool
    
    return dependencies

def create_directory_structure_for_dependencies(output_path, paths_file_path=None):
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
        
    Returns:
        dict: Dictionary with tool names as keys and their created directories as values
    """
    # Handle paths.py file location
    if paths_file_path is None:
        # Look for paths.py in the project root (2 levels up from core_validators.py)
        paths_file = Path(APP_ROOT) / "paths.py"
    else:
        paths_file = Path(paths_file_path)
    
    # Output directory location
    output_dir = Path(output_path)
    
    try:
        with open(paths_file, 'r') as file:
            content = file.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"Could not find {paths_file}")
    
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
            path_matches = re.findall(r'(\w+_path)\s*=\s*([^#\n]+)', section_content)
            
            for var_name, value in path_matches:
                # Clean the value - remove quotes, whitespace, and check if it's not None or empty
                cleaned_value = value.strip().strip("'\"")
                
                if cleaned_value and cleaned_value.lower() != 'none':
                    has_dependencies = True
                    dependency_tools.append(tool_name)
                    break  # Only need one valid path per tool
    
    # Get the current app name - try to find it from the current working directory
    # or from the project structure
    curr_app_name = None
    
    # Method 1: Try to get from current working directory (most reliable)
    try:
        cwd = Path.cwd()
        # Look for the app name in the current working directory path
        # This should be something like /path/to/test/cd4ep
        if 'test' in cwd.parts:
            test_index = cwd.parts.index('test')
            if test_index + 1 < len(cwd.parts):
                curr_app_name = cwd.parts[test_index + 1]
    except Exception:
        pass
    
    # Method 2: Fallback to the old method if Method 1 fails
    if not curr_app_name:
        curr_app_name = Path(__file__).resolve().parents[1].name
    
    # Method 3: If still not found, try to extract from the paths.py file location
    if not curr_app_name or curr_app_name == 'src':
        try:
            # Look for the app name in the paths.py file path
            if paths_file_path:
                paths_file = Path(paths_file_path)
                # If paths.py is in test/appname/paths.py, extract appname
                if 'test' in paths_file.parts:
                    test_index = paths_file.parts.index('test')
                    if test_index + 1 < len(paths_file.parts):
                        curr_app_name = paths_file.parts[test_index + 1]
        except Exception:
            pass
    
    # Final fallback
    if not curr_app_name or curr_app_name == 'src':
        curr_app_name = 'app'  # Generic fallback
    
    if has_dependencies:
        # NEW LOGIC: Create structure inside each dependency folder + main tool folder
        print(f"Found {len(dependency_tools)} dependencies: {', '.join(dependency_tools)}")
        
        # Create structure for each dependency tool
        for tool_name in dependency_tools:
            # Convert tool name to lowercase and replace spaces with underscores for directory name
            tool_dir_name = tool_name.lower().replace(' ', '_')

            # Defining alias for tool names
            if tool_dir_name == 't_cell_class_i':
                tool_dir_name = 'mhci'
            elif tool_dir_name == 't_cell_class_ii':
                tool_dir_name = 'mhcii'
            
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
                        print(f"Error creating directory {directory}: {e}")
                
                created_structures[tool_name] = created_dirs
                
            except Exception as e:
                print(f"Error creating directory {tool_path}: {e}")
        
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
                print(f"Error creating directory {directory}: {e}")
        
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
                print(f"Error creating directory {directory}: {e}")
        
        created_structures["default"] = created_dirs
    
    return created_structures

def validate_file(path_str):
    """Validate that the given path is a file."""
    path = Path(path_str)
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"'{path_str}' is not a valid file.")

    validate_directory_given_filename(path_str)
    return path

def validate_directory(path_str):
    """Validate that the given path is a directory."""
    path = Path(path_str)
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"'{path_str}' is not a valid directory.")
    return path

def validate_directory_given_filename(path_str):
    """Validate that the parent directory of the given path exists."""
    path = Path(path_str)
    parent_dir = path.parent
    
    if not parent_dir.is_dir():
        raise argparse.ArgumentTypeError(f"'{path_str}' is not a valid directory.")
    return path

def validate_preprocess_dir(path_str):
    """Validate preprocessing directory and create necessary structure."""
    path = Path(path_str)
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"'{path_str}' is not a valid directory.")

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