# Add any validation logic here. These files can be used along with argument parser.
# For example, all or most arguments should have a validation function defined here.
# Then, these functions can be called from add_argument() by setting the 'type'.
# ex) self.parser_preprocess.add_argument(
#           "--inputs-dir",
#           dest="preprocess_inputs_dir",
#           type=validators.validate_directory)
import argparse
import re
from pathlib import Path
import sys

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
    
    Args:
        output_path (str or Path): The output directory where tool structures will be created
        paths_file_path (str or Path, optional): Path to the paths.py file. If None, 
                                               looks for paths.py in the same directory as this script
        
    Returns:
        dict: Dictionary with tool names as keys and their created directories as values
    """
    # Handle paths.py file location
    if paths_file_path is None:
        paths_file = Path(__file__).resolve().parent / "paths.py"
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
    
    # Process sections in pairs (name, content)
    for i in range(0, len(sections), 2):
        if i + 1 < len(sections):
            tool_name = sections[i].strip()
            section_content = sections[i + 1]
            
            # print(f"Processing tool: {tool_name}")
            
            # Look for the main path variable (ends with _path=)
            path_matches = re.findall(r'(\w+_path)\s*=\s*([^#\n]+)', section_content)
            
            for var_name, value in path_matches:
                # Clean the value - remove quotes, whitespace, and check if it's not None or empty
                cleaned_value = value.strip().strip("'\"")
                
                # Debug print to see what we're parsing
                # print(f"Debug: Tool='{tool_name}', Var='{var_name}', Value='{cleaned_value}'")
                
                if cleaned_value and cleaned_value.lower() != 'none':
                    # Create tool directory under output path
                    # Convert tool name to lowercase and replace spaces with underscores for directory name
                    tool_dir_name = tool_name.lower().replace(' ', '_')
                    if tool_dir_name == 't_cell_class_i':
                        tool_dir_name = 'mhci'
                    elif tool_dir_name == 't_cell_class_ii':
                        tool_dir_name = 'mhcii'
                    elif tool_dir_name == 'pepx':
                        tool_dir_name = 'pepx'
                    
                    tool_path = output_dir / tool_dir_name
                    
                    # Define the directory structure
                    directories = [
                        tool_path / "aggregate",
                        tool_path / "predict-inputs" / "data",
                        tool_path / "predict-inputs" / "params",
                        tool_path / "predict-outputs",
                        tool_path / "results"
                    ]
                    
                    # Create the directories
                    created_dirs = []
                    for directory in directories:
                        try:
                            directory.mkdir(parents=True, exist_ok=True)
                            created_dirs.append(str(directory))
                            # print(f"Created directory: {directory}")
                        except Exception as e:
                            print(f"Error creating directory {directory}: {e}")
                    
                    created_structures[tool_name] = created_dirs
                    break  # Only need one valid path per tool
    
    return created_structures

def validate_file(path_str):
    path = Path(path_str)
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"'{path_str}' is not a valid file.")
    return path

def validate_directory(path_str):
    path = Path(path_str)
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"'{path_str}' is not a valid directory.")
    return path

def validate_directory_given_filename(path_str):
    path = Path(path_str)
    parent_dir = path.parent
    
    if not parent_dir.is_dir():
        raise argparse.ArgumentTypeError(f"'{path_str}' is not a valid directory.")
    return path

def validate_preprocess_dir(path_str):
    path = Path(path_str)
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"'{path_str}' is not a valid directory.")

    # paths.py file
    paths_file = Path(__file__).resolve().parent / "paths.py"

    try:
        # Get list of dependencies
        deps = get_dependencies_from_paths(paths_file)
        print(f"Found {len(deps)} dependencies:")
        for dep in deps:
            print(f"- {dep}")

        
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

# ADD ADDITIONAL VALIDATION LOGIC HERE
# ------------------------------------