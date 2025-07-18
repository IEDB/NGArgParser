import argparse
import textwrap
import os
import shutil
import re
from pathlib import Path

# Get the absolute path of the followings
CURR_FILE_PATH = Path(__file__).resolve()
NGPARSER_DIR = CURR_FILE_PATH.parent
TEMPLATE_DIR = NGPARSER_DIR / 'templates'
EXAMPLE_DIR = TEMPLATE_DIR / 'example-app'
# print(CURR_FILE_PATH, type(CURR_FILE_PATH))
# print(NGPARSER_DIR, type(NGPARSER_DIR))
# print(TEMPLATE_DIR, type(TEMPLATE_DIR))
# print(EXAMPLE_DIR, type(EXAMPLE_DIR))


def format_project_name(name, capitalize=False):
    name = name.replace('-', '_')

    if capitalize:
        name = name.split('_')
        name = [_.capitalize() for _ in name]
        name = ''.join(name)

    return name

def create_example_structure():
    try:
        project_name = 'aa-counter'

        # Create directory structure
        os.makedirs(project_name)
        os.makedirs(os.path.join(project_name, 'src'))
        # os.makedirs(os.path.join(project_name, 'output-directory'))
        # os.makedirs(os.path.join(project_name, 'output-directory', 'predict-inputs', 'data'))
        # os.makedirs(os.path.join(project_name, 'output-directory', 'predict-inputs', 'params'))
        # os.makedirs(os.path.join(project_name, 'output-directory', 'predict-outputs'))
        

        # Create necessary files
        parser_file = 'AACounterArgumentParser.py'
        update_and_place_readme(f'{EXAMPLE_DIR}/README', project_name, is_example=True)
        # shutil.copy('./misc/README', f'{project_name}/README')
        shutil.copy(f'{EXAMPLE_DIR}/example.json', f'{project_name}/src/example.json')
        shutil.copy(f'{EXAMPLE_DIR}/example.tsv', f'{project_name}/src/example.tsv')
        shutil.copy(f'{EXAMPLE_DIR}/run_aa_counter.py', f'{project_name}/src/run_aa_counter.py')
        shutil.copy(f'{EXAMPLE_DIR}/{parser_file}', f'{project_name}/src/{parser_file}')
        shutil.copy(f'{EXAMPLE_DIR}/preprocess.py', f'{project_name}/src/preprocess.py')
        shutil.copy(f'{EXAMPLE_DIR}/postprocess.py', f'{project_name}/src/postprocess.py')
        shutil.copy(f'{TEMPLATE_DIR}/configure.py', f'{project_name}/src/configure.py')     
        # Make configure.py executable
        os.chmod(f'{project_name}/src/configure.py', 0o755)
        shutil.copy(f'{NGPARSER_DIR}/NGArgumentParser.py', f'{project_name}/src/NGArgumentParser.py')
        shutil.copy(f'{NGPARSER_DIR}/validators.py', f'{project_name}/src/validators.py')
        shutil.copy(f'{NGPARSER_DIR}/core_validators.py', f'{project_name}/src/core_validators.py')

        # Copy build.sh, Makefile, and do-not-distribute.txt to project root
        shutil.copy(f'{TEMPLATE_DIR}/build.sh', f'{project_name}/build.sh')
        shutil.copy(f'{TEMPLATE_DIR}/Makefile', f'{project_name}/Makefile')
        shutil.copy(f'{TEMPLATE_DIR}/do-not-distribute.txt', f'{project_name}/do-not-distribute.txt')
        # Make build.sh executable
        os.chmod(f'{project_name}/build.sh', 0o755)
        # Replace TOOL_NAME in build.sh for example app
        replace_text_in_place(f'{project_name}/build.sh', 'TOOL_NAME=ng_appname', 'TOOL_NAME=ng_aa-counter')

        # Create configure executable file
        configure_file = f'{project_name}/configure'

        # Create and write the line into the configure file
        with open(configure_file, 'w') as f:
            f.write(f'./src/configure.py')
        
        # Make the file executable
        os.chmod(configure_file, 0o755)

        print(f"Created '{project_name}' project structure successfully.")
    except Exception as e:
        print(f"Error: {e}")


def create_project_structure(project_name):
    try:
        # Create directory structure
        os.makedirs(project_name)
        os.makedirs(os.path.join(project_name, 'src'))

        
        # Create necessary files
        exec_file = f'run_{format_project_name(project_name)}.py'
        parser_file = f'{format_project_name(project_name, capitalize=True)}ArgumentParser.py'
        parser_name = f'{format_project_name(project_name, capitalize=True)}ArgumentParser'
        update_and_place_readme(f'{TEMPLATE_DIR}/README', project_name)
        shutil.copy(f'{TEMPLATE_DIR}/run_app.py', f'{project_name}/src/{exec_file}')
        shutil.copy(f'{NGPARSER_DIR}/NGChildArgumentParser.py', f'{project_name}/src/{parser_file}')
        shutil.copy(f'{TEMPLATE_DIR}/preprocess.py', f'{project_name}/src/preprocess.py')
        shutil.copy(f'{TEMPLATE_DIR}/postprocess.py', f'{project_name}/src/postprocess.py')
        shutil.copy(f'{TEMPLATE_DIR}/configure.py', f'{project_name}/src/configure.py')     
        # Make configure.py executable
        os.chmod(f'{project_name}/src/configure.py', 0o755)
        shutil.copy(f'{NGPARSER_DIR}/NGArgumentParser.py', f'{project_name}/src/NGArgumentParser.py')
        shutil.copy(f'{NGPARSER_DIR}/validators.py', f'{project_name}/src/validators.py')
        shutil.copy(f'{NGPARSER_DIR}/core_validators.py', f'{project_name}/src/core_validators.py')
        
        # Copy build.sh, Makefile, and do-not-distribute.txt to project root
        shutil.copy(f'{TEMPLATE_DIR}/build.sh', f'{project_name}/build.sh')
        shutil.copy(f'{TEMPLATE_DIR}/Makefile', f'{project_name}/Makefile')
        shutil.copy(f'{TEMPLATE_DIR}/do-not-distribute.txt', f'{project_name}/do-not-distribute.txt')
        # Make build.sh executable
        os.chmod(f'{project_name}/build.sh', 0o755)
        # Replace TOOL_NAME in build.sh
        replace_text_in_place(f'{project_name}/build.sh', 'TOOL_NAME=ng_appname', f'TOOL_NAME=ng_{project_name}')

        # Try to copy license file, but don't fail if it's not available
        license_source = f'{NGPARSER_DIR}/license-LJI.txt'
        if os.path.exists(license_source):
            shutil.copy(license_source, f'{project_name}/license-LJI.txt')
        else:
            print(f"Warning: License file not found at {license_source}")
            print("Skipping license file copy.")

        # Add default content to all the files
        replace_text_in_place(f'{project_name}/src/{exec_file}', 'CHILDPARSER', parser_name)
        replace_text_in_place(f'{project_name}/src/{parser_file}', 'ChildArgumentParser', parser_name)        
        replace_text_in_place(f'{project_name}/src/configure.py', 'PROJECT_NAME', project_name)

        # Create configure executable file
        configure_file = f'{project_name}/configure'

        # Create and write the line into the configure file
        with open(configure_file, 'w') as f:
            f.write(f'./src/configure.py')
        
        # Make the file executable
        os.chmod(configure_file, 0o755)

        print(f"Created '{project_name}' project structure successfully.")
    except Exception as e:
        print(f"Error: {e}")


def update_and_place_readme(file_path, app_name, is_example=False):
    # Copy over the README blueprint
    app_readme_path = f'{app_name}/README'
    shutil.copy(file_path, app_readme_path)

    with open(app_readme_path, 'r') as file:
        content = file.read()

    # Replace variables in README
    content = content.replace("{TOOL_NAME}", app_name)
    content = content.replace("{TOOL_EXEC_NAME}", format_project_name(app_name))
    if is_example:
        updated_content = content.replace("{TOOL_NAME_CAP}", 'AACounter')
    else:
        updated_content = content.replace("{TOOL_NAME_CAP}", format_project_name(app_name, capitalize=True))

    # Write the updated content back to the file
    with open(app_readme_path, 'w') as file:
        file.write(updated_content)
    

def replace_text_in_place(file_path, old_text, new_text):
    """
    Replaces all occurrences of old_text with new_text in the specified file.

    :param file_path: Path to the file where the replacement should occur.
    :param old_text: Text to be replaced.
    :param new_text: Text to replace with.
    """
    try:
        # Read the content from the file
        with open(file_path, 'r') as file:
            content = file.read()
        
        # Replace all occurrences of old_text with new_text
        modified_content = content.replace(old_text, new_text)
        
        # Write the modified content back to the same file
        with open(file_path, 'w') as file:
            file.write(modified_content)
        
    except Exception as e:
        print(f"An error occurred: {e}")


def normalize_content_ending(content):
    """
    Normalize the content to ensure it ends with exactly one newline.
    
    Args:
        content (str): The content to normalize
        
    Returns:
        str: Content with exactly one trailing newline
    """
    return content.strip() + '\n'


def normalize_name(name):
    """
    Normalize a dependency name to a valid Python variable name.
    
    Args:
        name (str): The original dependency name
        
    Returns:
        str: Normalized variable name
    """
    # Convert to lowercase and replace spaces/special chars with underscores
    normalized = re.sub(r'[^a-zA-Z0-9_]', '_', name.lower())
    # Remove multiple consecutive underscores
    normalized = re.sub(r'_+', '_', normalized)
    # Remove leading/trailing underscores
    normalized = normalized.strip('_')
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
    words = re.split(r'[_\s-]+', name)
    formatted_words = []
    
    for word in words:
        if word:  # Skip empty strings
            # Check if word is a Roman numeral (i, ii, iii, iv, v, etc.)
            if word.lower() in ['i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x']:
                formatted_words.append(word.upper())
            else:
                formatted_words.append(word.capitalize())
    
    return ' '.join(formatted_words)


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
    if project_name_or_path.endswith('.py'):
        # Direct file path
        paths_file_path = project_name_or_path
    else:
        # Project name - create in project/src/ structure
        if project_name_or_path == 'example':
            project_name_or_path = 'aa-counter'

        paths_file_path = f'{project_name_or_path}/paths.py'
    
    try:
        # Check if paths.py already exists
        if Path(paths_file_path).exists():
            print("\n⚠️  'paths.py' already exists!")
            while True:
                response = input("Do you want to overwrite it? (y/n): ").lower().strip()
                if response in ['y', 'yes']:
                    break
                elif response in ['n', 'no']:
                    print("Skipping paths.py creation. Existing file was not modified.")
                    return
                else:
                    print("Please enter 'y' for yes or 'n' for no.")
        
        # Get dependencies from user
        dependencies = get_dependencies()
        
        if not dependencies:
            print("No dependencies specified. Creating empty paths.py file.")
            # Create empty file
            with open(paths_file_path, 'w', encoding='utf-8') as f:
                f.write('')
            print(f"\n✓ Created empty '{paths_file_path}'.")
            return
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
        with open(paths_file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        print(f"\n✓ Created '{paths_file_path}' with {len(dependencies)} dependencies.")
        print(f"You can now edit '{paths_file_path}' to set the actual paths for your dependencies.")
        
    except KeyboardInterrupt:
        print("\n\nPaths.py configuration cancelled.")
    except Exception as e:
        print(f"\nError creating paths.py: {e}")



def parse_existing_paths_file(file_path):
    """
    Parse an existing paths.py file to extract current dependencies.
    
    Args:
        file_path (str): Path to the existing paths file
        
    Returns:
        tuple: (file_content, existing_dependencies_dict)
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Extract existing dependencies by looking for the ''' [ Name ] ''' pattern
        import re
        existing_deps = {}
        
        # Find all dependency sections
        pattern = r"'''\s*\[\s*([^\]]+)\s*\]\s*'''"
        matches = re.findall(pattern, content)
        
        for match in matches:
            display_name = match.strip()
            var_name = normalize_name(display_name)
            existing_deps[var_name] = display_name
        
        return content, existing_deps
        
    except Exception as e:
        print(f"Error reading existing file: {e}")
        return None, {}


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
        return
    
    # Check for duplicates and prepare new sections
    new_sections = []
    skipped = []
    
    for dep in new_dependencies:
        var_name = normalize_name(dep)
        if var_name in existing_deps:
            skipped.append(dep)
        else:
            new_sections.append(generate_dependency_section(dep))
    
    if skipped:
        print(f"\nSkipped {len(skipped)} dependencies (already exist):")
        for dep in skipped:
            print(f"  - {dep}")
    
    if not new_sections:
        print("No new dependencies to add after checking for duplicates.")
        return
    
    # Append new sections to existing content
    # Remove any trailing whitespace from existing content first
    existing_content = existing_content.rstrip()
    
    # If this is the first dependency being added, ensure no empty lines at the top
    if not existing_deps:
        # For the first dependency, remove all leading newlines
        first_section = new_sections[0].lstrip('\n')  # Remove leading newlines
        remaining_sections = new_sections[1:] if len(new_sections) > 1 else []
        
        # If existing content is empty or just whitespace, don't add any newline
        if not existing_content.strip():
            updated_content = first_section + ''.join(remaining_sections)
        else:
            # If there's existing content, add one newline to separate
            updated_content = existing_content + '\n' + first_section + ''.join(remaining_sections)
    else:
        # For subsequent dependencies, use the normal format
        updated_content = existing_content + ''.join(new_sections)
    
    # Normalize content ending to ensure exactly one newline
    updated_content = normalize_content_ending(updated_content)
    
    # Write updated content
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(updated_content)
        
        print(f"\n✓ Updated '{file_path}' with {len(new_sections)} new dependencies.")
        print(f"You can now edit '{file_path}' to set the actual paths for your dependencies.")
        
    except Exception as e:
        print(f"Error updating file: {e}")


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
        return
    
    print(f"\nCurrent dependencies:")
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
            return
        
        try:
            # Parse selection (support both comma-separated and ranges)
            indices_to_remove = set()
            
            for part in selection.split(','):
                part = part.strip()
                if '-' in part:
                    # Handle range (e.g., 1-3)
                    start, end = map(int, part.split('-'))
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
    print(f"\nDependencies to remove:")
    for var_name, display_name in deps_to_remove:
        print(f"  - {display_name}")
    
    while True:
        confirm = input(f"\nRemove {len(deps_to_remove)} dependencies? (y/n): ").lower().strip()
        if confirm in ['y', 'yes']:
            break
        elif confirm in ['n', 'no']:
            print("Removal cancelled.")
            return
        else:
            print("Please enter 'y' for yes or 'n' for no.")
    
    # Remove dependencies from content
    updated_content = existing_content
    
    for var_name, display_name in deps_to_remove:
        # Create pattern to match the entire dependency section
        # This matches from ''' [ Name ] ''' to the end of the last variable
        pattern = rf"'''\s*\[\s*{re.escape(display_name)}\s*\]\s*'''.*?{re.escape(var_name)}_lib_path\s*=\s*[^\n]*"
        updated_content = re.sub(pattern, '', updated_content, flags=re.DOTALL)
    
    # Normalize content ending to ensure exactly one newline
    updated_content = normalize_content_ending(updated_content)
    
    # Write updated content
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(updated_content)
        
        print(f"\n✓ Removed {len(deps_to_remove)} dependencies from '{file_path}'.")
        
    except Exception as e:
        print(f"Error updating file: {e}")


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
            print(f"Could not read existing file. Creating new file instead.")
            create_paths_file(file_path)
            return
        
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
            if choice == '1':
                update_paths_file(file_path, existing_content, existing_deps)
                break
            elif choice == '2':
                remove_dependencies_from_file(file_path, existing_content, existing_deps)
                break
            elif choice == '3':
                print("Operation cancelled.")
                break
            else:
                print("Please enter 1, 2, or 3.")
    else:
        # File doesn't exist - create new one
        create_paths_file(file_path)




def startapp_command(args):
    if args.project_name == 'example':
        create_example_structure()
    else:
        create_project_structure(args.project_name)

    # Create paths.py file
    create_paths_file(args.project_name)


def setup_paths_command(args):
    setup_paths_file(args.paths_file)


def main():
    parser = argparse.ArgumentParser(description='NG Argument Parser Framework')
    subparsers = parser.add_subparsers(dest='command')

    # Create 'startapp' sub-command
    startapp_parser = subparsers.add_parser('generate',  aliases=["g"], allow_abbrev=True, help='Create a new custom app project structure')
    startapp_parser.add_argument('project_name', type=str, help='Name of the project to create')

    # Create 'setup-paths' sub-command
    setup_paths_parser = subparsers.add_parser('setup-paths', help='Setup or update paths.py with tool dependencies')
    setup_paths_parser.add_argument('paths_file', type=str, help='Path to the paths.py file to create or update')

    args = parser.parse_args()

    if args.command == 'generate' or args.command == 'g':
        startapp_command(args)
    elif args.command == 'setup-paths':
        setup_paths_command(args)
    else:
        parser.print_help()  # Print help message if 'startapp' command is not specified

if __name__ == '__main__':
    main()