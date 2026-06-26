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

def get_version():
    try:
        import importlib.metadata
        return importlib.metadata.version('ngargparser')
    except Exception:
        return 'unknown'

__version__ = get_version()


def format_project_name(name, capitalize=False):
    name = name.replace('-', '_')

    if capitalize:
        name = name.split('_')
        name = [_.capitalize() for _ in name]
        name = ''.join(name)

    return name


def write_pyproject_toml(project_dir, tool_name):
    """Render templates/pyproject.toml.tmpl into project_dir with {TOOL_NAME} substituted,
    then attempt to generate uv.lock via `uv lock` if uv is available."""
    import subprocess

    template_path = TEMPLATE_DIR / 'pyproject.toml.tmpl'
    target_path = os.path.join(project_dir, 'pyproject.toml')

    shutil.copy(template_path, target_path)
    replace_text_in_place(target_path, '{TOOL_NAME}', tool_name)
    replace_text_in_place(target_path, '{NGARGPARSER_VERSION}', __version__)

    if shutil.which('uv'):
        try:
            subprocess.run(['uv', 'lock'], cwd=project_dir, check=True)
            print(f"\033[92m✓\033[0m Generated 'uv.lock' for '{project_dir}'.")
        except subprocess.CalledProcessError as e:
            print(f"\033[93m⚠\033[0m  'uv lock' failed (exit {e.returncode}); run it manually in '{project_dir}'.")
    else:
        print("\033[93m⚠\033[0m  'uv' not found on PATH; skipping 'uv lock'. Run 'uv lock' inside the project once uv is installed.")


SCAFFOLD_STAMP_RE = re.compile(
    r'(\[tool\.ngargparser\][^\[]*?scaffold_version\s*=\s*")([^"]*)(")',
    re.DOTALL,
)

def write_scaffold_version(pyproject_path, version):
    """Write or update [tool.ngargparser] scaffold_version in pyproject.toml.
    Returns the previous version string, or None if no stamp existed before."""
    if not os.path.exists(pyproject_path):
        return None

    with open(pyproject_path, 'r', encoding='utf-8') as f:
        content = f.read()

    match = SCAFFOLD_STAMP_RE.search(content)
    if match:
        previous = match.group(2)
        if previous == version:
            return previous
        new_content = content[:match.start(2)] + version + content[match.end(2):]
    else:
        previous = None
        if not content.endswith('\n'):
            content += '\n'
        new_content = content + f'\n[tool.ngargparser]\nscaffold_version = "{version}"\n'

    with open(pyproject_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    return previous


def create_example_structure():
    try:
        project_name = 'aa-counter'

        # Create directory structure
        os.makedirs(project_name)
        os.makedirs(os.path.join(project_name, 'src'))
        os.makedirs(os.path.join(project_name, 'src', 'core'))
        os.makedirs(os.path.join(project_name, 'scripts'))
        os.makedirs(os.path.join(project_name, 'scripts', 'core'))

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
        shutil.copy(f'{TEMPLATE_DIR}/configure.py', f'{project_name}/src/core/configure.py')
        # Make configure.py executable
        os.chmod(f'{project_name}/src/core/configure.py', 0o755)

        # Copy core files to protected core/ directory
        shutil.copy(f'{EXAMPLE_DIR}/NGArgumentParser.py', f'{project_name}/src/core/NGArgumentParser.py')
        shutil.copy(f'{NGPARSER_DIR}/core_validators.py', f'{project_name}/src/core/core_validators.py')
        shutil.copy(f'{TEMPLATE_DIR}/set_pythonpath.py', f'{project_name}/src/core/set_pythonpath.py')

        # Create __init__.py for core package
        with open(f'{project_name}/src/core/__init__.py', 'w') as f:
            f.write('')

        # Copy user-modifiable files to src/
        shutil.copy(f'{NGPARSER_DIR}/validators.py', f'{project_name}/src/validators.py')

        # Framework-owned: scripts/core/build.sh (cli sync overwrites this)
        shutil.copy(f'{TEMPLATE_DIR}/build.sh', f'{project_name}/scripts/core/build.sh')
        os.chmod(f'{project_name}/scripts/core/build.sh', 0o755)
        # Framework-owned: root Makefile (cli sync overwrites this)
        shutil.copy(f'{TEMPLATE_DIR}/Makefile', f'{project_name}/Makefile')
        # User-owned: scripts/build.conf, scripts/do-not-distribute.txt
        shutil.copy(f'{TEMPLATE_DIR}/build.conf', f'{project_name}/scripts/build.conf')
        shutil.copy(f'{TEMPLATE_DIR}/do-not-distribute.txt', f'{project_name}/scripts/do-not-distribute.txt')

        # User-owned: deploy/install.sh — invoked by nxg-tools-deployments at deploy time.
        os.makedirs(f'{project_name}/deploy', exist_ok=True)
        shutil.copy(f'{TEMPLATE_DIR}/deploy/install.sh', f'{project_name}/deploy/install.sh')
        replace_text_in_place(f'{project_name}/deploy/install.sh', '{TOOL_NAME}', project_name)

        # Create configure executable file
        configure_file = f'{project_name}/configure'

        # Create and write the line into the configure file
        with open(configure_file, 'w') as f:
            f.write(f'./src/core/configure.py')
        
        # Make the file executable
        os.chmod(configure_file, 0o755)

        # Render pyproject.toml from template (and lock with uv if available)
        write_pyproject_toml(project_name, project_name)

        print(f"Created '{project_name}' project structure successfully.")
    except Exception as e:
        print(f"\033[91m✗ Error: {e}\033[0m")


def create_project_structure(project_name):
    try:
        # Create directory structure
        os.makedirs(project_name)
        os.makedirs(os.path.join(project_name, 'src'))
        os.makedirs(os.path.join(project_name, 'src', 'core'))
        os.makedirs(os.path.join(project_name, 'scripts'))
        os.makedirs(os.path.join(project_name, 'scripts', 'core'))

        # Create necessary files
        exec_file = f'run_{format_project_name(project_name)}.py'
        parser_file = f'{format_project_name(project_name, capitalize=True)}ArgumentParser.py'
        parser_name = f'{format_project_name(project_name, capitalize=True)}ArgumentParser'
        update_and_place_readme(f'{TEMPLATE_DIR}/README.md', project_name)
        shutil.copy(f'{TEMPLATE_DIR}/run_app.py', f'{project_name}/src/{exec_file}')
        shutil.copy(f'{NGPARSER_DIR}/NGChildArgumentParser.py', f'{project_name}/src/{parser_file}')
        shutil.copy(f'{TEMPLATE_DIR}/preprocess.py', f'{project_name}/src/preprocess.py')
        shutil.copy(f'{TEMPLATE_DIR}/postprocess.py', f'{project_name}/src/postprocess.py')
        shutil.copy(f'{TEMPLATE_DIR}/configure.py', f'{project_name}/src/core/configure.py')     
        # Make configure.py executable
        os.chmod(f'{project_name}/src/core/configure.py', 0o755)
        
        # Copy core files to protected core/ directory
        shutil.copy(f'{NGPARSER_DIR}/NGArgumentParser.py', f'{project_name}/src/core/NGArgumentParser.py')
        shutil.copy(f'{NGPARSER_DIR}/core_validators.py', f'{project_name}/src/core/core_validators.py')
        shutil.copy(f'{TEMPLATE_DIR}/set_pythonpath.py', f'{project_name}/src/core/set_pythonpath.py')
        
        # Create __init__.py for core package
        with open(f'{project_name}/src/core/__init__.py', 'w') as f:
            f.write('')
        
        # Copy user-modifiable files to src/
        shutil.copy(f'{NGPARSER_DIR}/validators.py', f'{project_name}/src/validators.py')
        
        # Framework-owned: scripts/core/build.sh + root Makefile (cli sync overwrites these)
        shutil.copy(f'{TEMPLATE_DIR}/build.sh', f'{project_name}/scripts/core/build.sh')
        os.chmod(f'{project_name}/scripts/core/build.sh', 0o755)
        shutil.copy(f'{TEMPLATE_DIR}/Makefile', f'{project_name}/Makefile')
        # User-owned: scripts/build.conf, hooks.sh, do-not-distribute.txt (sync leaves alone)
        shutil.copy(f'{TEMPLATE_DIR}/build.conf', f'{project_name}/scripts/build.conf')
        shutil.copy(f'{TEMPLATE_DIR}/do-not-distribute.txt', f'{project_name}/scripts/do-not-distribute.txt')
        shutil.copy(f'{TEMPLATE_DIR}/hooks.sh', f'{project_name}/scripts/hooks.sh')
        os.chmod(f'{project_name}/scripts/hooks.sh', 0o755)

        # User-owned: deploy/install.sh — invoked by nxg-tools-deployments at deploy time.
        os.makedirs(f'{project_name}/deploy', exist_ok=True)
        shutil.copy(f'{TEMPLATE_DIR}/deploy/install.sh', f'{project_name}/deploy/install.sh')
        replace_text_in_place(f'{project_name}/deploy/install.sh', '{TOOL_NAME}', project_name)

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
        replace_text_in_place(f'{project_name}/src/core/configure.py', 'PROJECT_NAME', project_name)

        # Create configure executable file
        configure_file = f'{project_name}/configure'

        # Create and write the line into the configure file
        with open(configure_file, 'w') as f:
            f.write(f'./src/core/configure.py')
        
        # Make the file executable
        os.chmod(configure_file, 0o755)

        # Render pyproject.toml from template (and lock with uv if available)
        write_pyproject_toml(project_name, project_name)

        print(f"Created '{project_name}' project structure successfully.")
    except Exception as e:
        print(f"\033[91m✗ Error: {e}\033[0m")


def update_and_place_readme(file_path, app_name, is_example=False):
    # Copy over and generate README files (both README.md and README)
    readme_md_path = f'{app_name}/README.md'
    readme_plain_path = f'{app_name}/README'
    # Ensure we have a starting file to read from
    shutil.copy(file_path, readme_md_path)

    with open(readme_md_path, 'r') as file:
        content = file.read()

    # Replace variables in README
    content = content.replace("{TOOL_NAME}", app_name)
    content = content.replace("{TOOL_EXEC_NAME}", format_project_name(app_name))
    if is_example:
        base_updated_content = content.replace("{TOOL_NAME_CAP}", 'AACounter')
    else:
        base_updated_content = content.replace("{TOOL_NAME_CAP}", format_project_name(app_name, capitalize=True))

    # Insert ngargparser version badge just below the title if possible
    try:
        badge_md = f"[![ngargparser](https://img.shields.io/badge/ngargparser-{__version__}-blue.svg)](https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser)"
        lines = base_updated_content.splitlines(True)  # keep line endings
        if len(lines) >= 2:
            # Common template starts with title and an underline of dashes
            lines.insert(2, f"\n{badge_md}\n\n")
            content_with_badge = ''.join(lines)
        else:
            content_with_badge = f"{badge_md}\n\n" + base_updated_content
    except Exception:
        # If anything goes wrong, proceed without badge insertion
        content_with_badge = base_updated_content

    # Write the updated content to both README.md and README
    with open(readme_md_path, 'w') as f_md:
        f_md.write(content_with_badge)  # README.md includes badge
    with open(readme_plain_path, 'w') as f_plain:
        f_plain.write(base_updated_content)  # README (no extension) has no badge
    

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
        print(f"\033[91m✗ An error occurred: {e}\033[0m")


def upsert_readme_badge(readme_path: str, version: str, color: str = "green") -> bool:
    """
    Update or insert the ngargparser version badge in the given README file.
    Returns True if the file was modified, False otherwise.
    """
    try:
        if not os.path.exists(readme_path):
            return False
        with open(readme_path, 'r', encoding='utf-8') as f:
            content = f.read()
        new_badge = f"[![ngargparser](https://img.shields.io/badge/ngargparser-{version}-{color}.svg)](https://gitlab.lji.org/iedb/tools/tools-redesign/global-dependencies/ngargparser)"
        import re
        pattern = r"\[!\[ngargparser\]\(https://img\.shields\.io/badge/ngargparser-[^-/\s)]+-[^)\s]+\.svg\)\]\([^)]+\)"
        if re.search(pattern, content):
            updated = re.sub(pattern, new_badge, content, count=1)
            if updated != content:
                with open(readme_path, 'w', encoding='utf-8') as f:
                    f.write(updated)
                return True
            return False
        # If no existing badge, insert below title when possible, else prepend
        lines = content.splitlines(True)
        if len(lines) >= 2:
            lines.insert(2, f"\n{new_badge}\n\n")
            updated = ''.join(lines)
        else:
            updated = f"{new_badge}\n\n" + content
        with open(readme_path, 'w', encoding='utf-8') as f:
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
            print("\n\033[93m⚠\033[0m  'paths.py' already exists!")
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
            print(f"\n\033[92m✓\033[0m Created empty '\033[92m{paths_file_path}\033[0m'.")
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
        
        print(f"\n\033[92m✓\033[0m Created '\033[92m{paths_file_path}\033[0m' with \033[92m{len(dependencies)}\033[0m dependencies.")
        print(f"You can now edit '{paths_file_path}' to set the actual paths for your dependencies.")
        
    except KeyboardInterrupt:
        print("\n\n\033[91m✗\033[0m Paths.py configuration cancelled.")
    except Exception as e:
        print(f"\n\033[91m✗\033[0m Error creating paths.py: \033[91m{e}\033[0m")



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
        print(f"\n\033[91m✗\033[0m Error reading existing file: \033[91m{e}\033[0m")
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
        
        print(f"\n\033[92m✓\033[0m Updated '\033[92m{file_path}\033[0m' with \033[92m{len(new_sections)}\033[0m new dependencies.")
        print(f"You can now edit '{file_path}' to set the actual paths for your dependencies.")
        
    except Exception as e:
        print(f"\n\033[91m✗\033[0m Error updating file: \033[91m{e}\033[0m")


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
        
        print(f"\n\033[92m✓\033[0m Removed \033[92m{len(deps_to_remove)}\033[0m dependencies from '\033[92m{file_path}\033[0m'.")
        
    except Exception as e:
        print(f"\n\033[91m✗\033[0m Error updating file: \033[91m{e}\033[0m")


def add_deps_to_paths(file_path, names):
    """Non-interactive equivalent of `setup_paths_file`'s "add" path.
    Appends a stub block for each name in `names` to `file_path`, skipping
    any whose normalized var name already exists. Creates the file if
    missing. Returns the count of blocks added."""
    if not names:
        return 0

    target = Path(file_path)
    if target.exists():
        existing_content, existing_deps = parse_existing_paths_file(file_path)
        if existing_content is None:
            existing_content = ''
            existing_deps = {}
    else:
        existing_content = ''
        existing_deps = {}

    had_existing_deps = bool(existing_deps)
    new_sections = []
    skipped = []
    for name in names:
        var_name = normalize_name(name)
        if var_name in existing_deps:
            skipped.append(name)
        else:
            new_sections.append(generate_dependency_section(name))
            existing_deps[var_name] = format_display_name(name)

    if skipped:
        print(f"\033[93m⚠\033[0m  Skipped (already declared): {', '.join(skipped)}")

    if not new_sections:
        return 0

    existing_content = existing_content.rstrip()
    if not had_existing_deps:
        first_section = new_sections[0].lstrip('\n')
        remaining = ''.join(new_sections[1:])
        if not existing_content.strip():
            updated_content = first_section + remaining
        else:
            updated_content = existing_content + '\n' + first_section + remaining
    else:
        updated_content = existing_content + ''.join(new_sections)

    updated_content = normalize_content_ending(updated_content)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(updated_content)

    added_names = [n for n in names if n not in skipped]
    print(f"\033[92m✓\033[0m Added {len(new_sections)} dependency stub(s) to '{file_path}': {', '.join(added_names)}")
    print(f"   Edit '{file_path}' to fill in the actual paths.")
    return len(new_sections)


def remove_deps_from_paths(file_path, names):
    """Non-interactive equivalent of `setup_paths_file`'s "remove" path.
    Deletes blocks whose display name OR var name matches any entry in `names`.
    Returns the count of blocks removed."""
    if not names:
        return 0

    if not Path(file_path).exists():
        print(f"\033[91m✗\033[0m '{file_path}' not found. Nothing to remove.")
        return 0

    existing_content, existing_deps = parse_existing_paths_file(file_path)
    if existing_content is None:
        return 0

    # Map display_name → var_name and var_name → var_name so users can pass either
    name_to_var = {}
    for var_name, display_name in existing_deps.items():
        name_to_var[var_name] = (var_name, display_name)
        name_to_var[display_name] = (var_name, display_name)
        name_to_var[normalize_name(display_name)] = (var_name, display_name)

    not_found = []
    to_remove = []  # list of (var_name, display_name)
    seen_vars = set()
    for n in names:
        match = name_to_var.get(n) or name_to_var.get(normalize_name(n))
        if match is None:
            not_found.append(n)
            continue
        var_name, display_name = match
        if var_name in seen_vars:
            continue
        seen_vars.add(var_name)
        to_remove.append((var_name, display_name))

    if not_found:
        print(f"\033[93m⚠\033[0m  Not in '{file_path}' (skipped): {', '.join(not_found)}")

    if not to_remove:
        return 0

    updated_content = existing_content
    for var_name, display_name in to_remove:
        pattern = rf"'''\s*\[\s*{re.escape(display_name)}\s*\]\s*'''.*?{re.escape(var_name)}_lib_path\s*=\s*[^\n]*"
        updated_content = re.sub(pattern, '', updated_content, flags=re.DOTALL)

    updated_content = normalize_content_ending(updated_content)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(updated_content)

    print(f"\033[92m✓\033[0m Removed {len(to_remove)} dependency block(s) from '{file_path}': {', '.join(d for _, d in to_remove)}")
    return len(to_remove)


def list_deps_in_paths(file_path):
    """Print the dependency blocks declared in `file_path`. Returns the count."""
    if not Path(file_path).exists():
        print(f"\033[93m⚠\033[0m  '{file_path}' not found — no dependencies declared.")
        return 0

    existing_content, existing_deps = parse_existing_paths_file(file_path)
    if not existing_deps:
        print(f"No dependencies declared in '{file_path}'.")
        return 0

    print(f"Dependencies declared in '{file_path}':")
    for var_name, display_name in existing_deps.items():
        # Try to surface whether _path has been filled in or is still None
        m = re.search(rf"^{re.escape(var_name)}_path\s*=\s*(.+?)$", existing_content, re.MULTILINE)
        path_value = m.group(1).strip() if m else 'None'
        status = '\033[92m●\033[0m' if path_value not in ('None', "''", '""') else '\033[93m○\033[0m'
        print(f"  {status} {display_name:<30} _path = {path_value}")
    print(f"\n\033[92m●\033[0m = path filled in   \033[93m○\033[0m = stub (still None)")
    return len(existing_deps)


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

    project_dir_name = 'aa-counter' if args.project_name == 'example' else args.project_name

    # Create an empty paths.py — users declare external tool deps later via `cli deps add`.
    paths_file_path = os.path.join(project_dir_name, 'paths.py')
    with open(paths_file_path, 'w', encoding='utf-8') as f:
        f.write('')
    print(f"\033[92m✓\033[0m Created empty '\033[92m{paths_file_path}\033[0m'.")

    # Write initial .env with APP_NAME and APP_ROOT in the new project root
    try:
        project_root_abs = os.path.abspath(project_dir_name)
        env_file_path = os.path.join(project_dir_name, '.env')
        with open(env_file_path, 'w', encoding='utf-8') as f:
            f.write(f"APP_ROOT={project_root_abs}\n")
            f.write(f"APP_NAME={project_dir_name}\n")
        print(f"\033[92m✓\033[0m Created initial '.env' at '\033[92m{env_file_path}\033[0m'")
    except Exception as e:
        print(f"\033[91m✗\033[0m Error writing initial .env: \033[91m{e}\033[0m")

    # Closing hint: tell users where to go from here.
    print()
    print(f"Project '\033[1m{project_dir_name}\033[0m' is ready. Next steps:")
    print(f"  cd {project_dir_name}")
    print(f"  uv sync                                  # install Python deps")
    print(f"  cli deps add <tool> [<tool> ...]         # declare external tool deps (optional)")


def config_paths_command(args):
    import sys
    print(
        "\033[93m⚠\033[0m  'cli config-paths' is deprecated; use 'cli deps' instead "
        "('cli deps add <name>', 'cli deps remove <name>', 'cli deps list').",
        file=sys.stderr,
    )
    setup_paths_file('paths.py')


def deps_command(args):
    action = getattr(args, 'deps_action', None)
    if action == 'add':
        if args.names:
            add_deps_to_paths('paths.py', args.names)
        else:
            # No names given → fall through to interactive add menu via setup_paths_file
            setup_paths_file('paths.py')
        return 0
    if action == 'remove':
        if args.names:
            remove_deps_from_paths('paths.py', args.names)
        else:
            setup_paths_file('paths.py')
        return 0
    if action == 'list':
        list_deps_in_paths('paths.py')
        return 0
    # Bare `cli deps` → today's interactive flow
    setup_paths_file('paths.py')
    return 0


def _latest_release_tag(remote_url):
    """Return the highest semver-like tag from a git remote, or None on failure.

    Uses `git ls-remote --tags --refs` (no clone), parses tags matching
    `vX.Y.Z` (with optional pre-release/build suffix), and returns the
    highest one by numeric (major, minor, patch). Pre-release suffixes are
    parsed but compared as None < anything-else, matching the conventional
    "pre-releases sort below releases" intent for picking a default upgrade.
    """
    import re, subprocess
    try:
        out = subprocess.check_output(
            ["git", "ls-remote", "--tags", "--refs", remote_url],
            stderr=subprocess.DEVNULL, text=True, timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    semver_tags = []
    for line in out.splitlines():
        ref = line.split("\t", 1)[-1] if "\t" in line else ""
        name = ref.rsplit("/", 1)[-1]
        m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?", name)
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


def _resolve_upgrade_url(ref="latest", dev=False):
    """Resolve (url, resolved_ref) to upgrade ngargparser to.

    Honors NGARGPARSER_UPGRADE_URL (full override). `dev` forces 'master'.
    `ref='latest'` resolves the highest semver tag on the remote, falling back
    to 'master' when no tags exist.
    """
    import os
    override = os.environ.get("NGARGPARSER_UPGRADE_URL")
    if override:
        return override, ref
    if dev:
        ref = "master"
    if ref == "latest":
        ref = _latest_release_tag(BASE_REPO_URL[len("git+"):]) or "master"
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
    import sys
    import shutil
    import subprocess
    import importlib.util

    uv = shutil.which("uv")
    has_pip = importlib.util.find_spec("pip") is not None

    if _is_uv_tool_install() and uv:
        cmd, tool = ["uv", "tool", "install", "--force", "--reinstall", url], "uv tool"
    elif has_pip:
        cmd, tool = [sys.executable, "-m", "pip", "install",
                     "--upgrade", "--force-reinstall", "--quiet", url], "pip"
    elif uv:
        cmd, tool = ["uv", "pip", "install", "--python", sys.executable,
                     "--reinstall", url], "uv pip"
    else:
        print("\033[91m✗\033[0m Can't upgrade: this environment has no pip and uv isn't on PATH.")
        print(f"  Install uv (https://astral.sh/uv) or run manually:")
        print(f"    uv tool install --force --reinstall '{url}'")
        return 1

    try:
        subprocess.check_call(cmd)
        return 0
    except subprocess.CalledProcessError as e:
        print(f"\033[91m✗\033[0m Upgrade failed ({tool} exit {e.returncode}).")
        print(f"  Try manually:  uv tool install --force --reinstall '{url}'")
        return e.returncode or 1


def upgrade_command(args):
    """Check GitLab for the latest tag and upgrade ngargparser in place.

    Works from any directory and never touches project files. `--check` only
    reports installed-vs-latest. Unlike `sync` it does not re-exec — the next
    `cli` invocation runs the upgraded code.
    """
    import os
    ref, dev = getattr(args, "ref", "latest"), getattr(args, "dev", False)
    url, resolved = _resolve_upgrade_url(ref, dev)
    current = __version__
    target = resolved.lstrip("v") if resolved else resolved
    check = getattr(args, "check", False)

    pinned = dev or ref != "latest" or os.environ.get("NGARGPARSER_UPGRADE_URL")
    if not pinned:
        if resolved == "master":
            print("⚠ No semver tags on remote; 'latest' resolves to master.")
        else:
            up_to_date = current == target
            print(f"ℹ Installed: {current}    Latest: {resolved}"
                  + ("  (up to date)" if up_to_date else "  (update available)"))
            if check:
                return 0
            if up_to_date:
                print("\033[92m✓\033[0m Already on the latest tag; nothing to do.")
                return 0
    if check:  # --check with an explicit --ref / --dev / override
        print(f"ℹ Installed: {current}    Target: {resolved}")
        return 0

    print(f"ℹ Upgrading ngargparser ({url}) …")
    rc = _run_self_upgrade(url)
    if rc:
        return rc
    print(f"\033[92m✓\033[0m Upgraded ngargparser → {resolved}. Run 'cli --version' to confirm.")
    return 0


def sync_command(args):
    """Synchronize framework files in existing projects to the latest version."""
    try:
        import os
        import shutil
        import filecmp

        # Check if we're in a project directory
        if not os.path.exists('src') or not os.path.exists('scripts'):
            print(f"\033[91m✗\033[0m Error: This doesn't appear to be a valid ngargparser project directory")
            print("Make sure you're in a project directory with src/ and scripts/ subdirectories.")
            return 1

        # Self-upgrade the installed ngargparser, then re-exec so the new code
        # (including __version__ used by the scaffold_version stamp below) takes
        # effect for the rest of this sync. The env-var sentinel breaks recursion.
        if getattr(args, "upgrade", True) and not os.environ.get("NGARGPARSER_NO_SELF_UPGRADE"):
            import sys

            if getattr(args, "dev", False):
                print("ℹ Dev mode: pulling from master.")
            url, resolved = _resolve_upgrade_url(getattr(args, "ref", "latest"), getattr(args, "dev", False))
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
            os.execvpe(sys.argv[0], [sys.argv[0], "s"], env)

        print("Synchronizing framework files to latest version...")
        
        # Get the project name from current directory
        project_name = os.path.basename(os.getcwd())
        print(f"Project: {project_name}")
        
        # Ensure src/core/ directory exists
        if not os.path.exists('src/core'):
            os.makedirs('src/core', exist_ok=True)
            print("  └ Created src/core/ directory")
        
        # Update core files (src/core/*)
        print("\nUpdating src/core/ files...")
        core_files_updated = 0
        
        # Update NGArgumentParser.py
        if os.path.exists('src/core/NGArgumentParser.py'):
            if not filecmp.cmp(f'{NGPARSER_DIR}/NGArgumentParser.py', 'src/core/NGArgumentParser.py', shallow=False):
                shutil.copy(f'{NGPARSER_DIR}/NGArgumentParser.py', 'src/core/NGArgumentParser.py')
                print("  └ Updated NGArgumentParser.py")
                core_files_updated += 1
            else:
                print("  └ NGArgumentParser.py is already up to date")
        else:
            # Create the file in the correct location
            shutil.copy(f'{NGPARSER_DIR}/NGArgumentParser.py', 'src/core/NGArgumentParser.py')
            print("  └ Created NGArgumentParser.py in src/core/")
            core_files_updated += 1
        
        # Update core_validators.py
        if os.path.exists('src/core/core_validators.py'):
            if not filecmp.cmp(f'{NGPARSER_DIR}/core_validators.py', 'src/core/core_validators.py', shallow=False):
                shutil.copy(f'{NGPARSER_DIR}/core_validators.py', 'src/core/core_validators.py')
                print("  └ Updated core_validators.py")
                core_files_updated += 1
            else:
                print("  └ core_validators.py is already up to date")
        else:
            # Create the file in the correct location
            shutil.copy(f'{NGPARSER_DIR}/core_validators.py', 'src/core/core_validators.py')
            print("  └ Created core_validators.py in src/core/")
            core_files_updated += 1
        
        # Update set_pythonpath.py
        if os.path.exists('src/core/set_pythonpath.py'):
            if not filecmp.cmp(f'{TEMPLATE_DIR}/set_pythonpath.py', 'src/core/set_pythonpath.py', shallow=False):
                shutil.copy(f'{TEMPLATE_DIR}/set_pythonpath.py', 'src/core/set_pythonpath.py')
                print("  └ Updated set_pythonpath.py")
                core_files_updated += 1
            else:
                print("  └ set_pythonpath.py is already up to date")
        else:
            # Create the file in the correct location
            shutil.copy(f'{TEMPLATE_DIR}/set_pythonpath.py', 'src/core/set_pythonpath.py')
            print("  └ Created set_pythonpath.py in src/core/")
            core_files_updated += 1
        
        # Update configure.py
        if os.path.exists('src/core/configure.py'):
            if not filecmp.cmp(f'{TEMPLATE_DIR}/configure.py', 'src/core/configure.py', shallow=False):
                shutil.copy(f'{TEMPLATE_DIR}/configure.py', 'src/core/configure.py')
                os.chmod('src/core/configure.py', 0o755)  # Make executable
                print("  └ Updated configure.py")
                core_files_updated += 1
            else:
                print("  └ configure.py is already up to date")
        else:
            # Create the file in the correct location
            shutil.copy(f'{TEMPLATE_DIR}/configure.py', 'src/core/configure.py')
            os.chmod('src/core/configure.py', 0o755)  # Make executable
            print("  └ Created configure.py in src/core/")
            core_files_updated += 1
        
        # Update scripts files (except hooks.sh, build.conf, do-not-distribute.txt — user-owned)
        print("\nUpdating scripts/ files...")
        script_files_updated = 0

        # Migration: pre-scripts/core layout had build.sh at scripts/build.sh.
        # Move it (and the directory) to scripts/core/build.sh on first sync.
        if os.path.exists('scripts/build.sh') and not os.path.exists('scripts/core/build.sh'):
            os.makedirs('scripts/core', exist_ok=True)
            shutil.move('scripts/build.sh', 'scripts/core/build.sh')
            print("  └ \033[93mMigrated\033[0m scripts/build.sh → scripts/core/build.sh "
                  "(framework-owned scripts now live under scripts/core/)")
            script_files_updated += 1

        # Migration: legacy hook filenames → hooks.sh. Content is preserved exactly.
        # Two prior names existed: dependencies.sh (original) and build_hooks.sh (brief interim).
        for legacy in ('scripts/dependencies.sh', 'scripts/build_hooks.sh'):
            if os.path.exists(legacy) and not os.path.exists('scripts/hooks.sh'):
                shutil.move(legacy, 'scripts/hooks.sh')
                print(f"  └ \033[93mMigrated\033[0m {legacy} → scripts/hooks.sh")
                script_files_updated += 1
                break

        # Ensure scripts/core/ exists
        if not os.path.exists('scripts/core'):
            os.makedirs('scripts/core', exist_ok=True)
            print("  └ Created scripts/core/ directory")

        # Update scripts/core/build.sh
        if os.path.exists('scripts/core/build.sh'):
            if not filecmp.cmp(f'{TEMPLATE_DIR}/build.sh', 'scripts/core/build.sh', shallow=False):
                shutil.copy(f'{TEMPLATE_DIR}/build.sh', 'scripts/core/build.sh')
                os.chmod('scripts/core/build.sh', 0o755)
                print("  └ Updated scripts/core/build.sh")
                script_files_updated += 1
            else:
                print("  └ scripts/core/build.sh is already up to date")
        else:
            shutil.copy(f'{TEMPLATE_DIR}/build.sh', 'scripts/core/build.sh')
            os.chmod('scripts/core/build.sh', 0o755)
            print("  └ Created scripts/core/build.sh")
            script_files_updated += 1
        
        # Update root-level Makefile (no longer under scripts/)
        if os.path.exists('Makefile'):
            if not filecmp.cmp(f'{TEMPLATE_DIR}/Makefile', 'Makefile', shallow=False):
                shutil.copy(f'{TEMPLATE_DIR}/Makefile', 'Makefile')
                print("  └ Updated root Makefile")
                script_files_updated += 1
            else:
                print("  └ Root Makefile is already up to date")
        else:
            # Create the file in the project root
            shutil.copy(f'{TEMPLATE_DIR}/Makefile', 'Makefile')
            print("  └ Created root Makefile")
            script_files_updated += 1
        
        # # Update do-not-distribute.txt
        # if os.path.exists('scripts/do-not-distribute.txt'):
        #     shutil.copy(f'{TEMPLATE_DIR}/do-not-distribute.txt', 'scripts/do-not-distribute.txt')
        #     print("  └ Updated do-not-distribute.txt")
        #     script_files_updated += 1

        # Ensure deploy/install.sh exists. User-owned (like scripts/hooks.sh) — sync
        # creates it from the template only if missing; never overwrites existing content.
        # Required for nxg-tools-deployments: orchestrator runs `bash deploy/install.sh`
        # after extracting the tarball on the target host.
        if not os.path.exists('deploy/install.sh'):
            os.makedirs('deploy', exist_ok=True)
            shutil.copy(f'{TEMPLATE_DIR}/deploy/install.sh', 'deploy/install.sh')
            replace_text_in_place('deploy/install.sh', '{TOOL_NAME}', project_name)
            print(f"  └ Created \033[92mdeploy/install.sh\033[0m from template (user-owned; sync won't overwrite from here on)")
            script_files_updated += 1

        # Advisory: legacy projects without pyproject.toml
        if not os.path.exists('pyproject.toml') and os.path.exists('requirements.txt'):
            print(
                "\n\033[93m⚠\033[0m  This project still uses 'requirements.txt'. "
                "ngargparser scaffolds now generate 'pyproject.toml' + 'uv.lock'.\n"
                f"   To migrate: copy '{TEMPLATE_DIR}/pyproject.toml.tmpl' to './pyproject.toml', "
                "edit name/dependencies, then run 'uv lock'."
            )

        # Stamp the project with the framework version it was last synced against.
        # Future `cli` commands can read [tool.ngargparser] scaffold_version to dispatch
        # version-specific migrations.
        stamp_updated = False
        if os.path.exists('pyproject.toml'):
            prev = write_scaffold_version('pyproject.toml', __version__)
            if prev is None:
                print(f"\nStamping framework version...")
                print(f"  └ Added scaffold_version = \033[92m{__version__}\033[0m to [tool.ngargparser] (was missing)")
                stamp_updated = True
            elif prev != __version__:
                print(f"\nStamping framework version...")
                print(f"  └ scaffold_version: \033[93m{prev}\033[0m → \033[92m{__version__}\033[0m")
                stamp_updated = True

        # Summary
        print(f"\nSynchronization Summary:")
        print(f"  └ Core files updated: \033[92m{core_files_updated}\033[0m")
        print(f"  └ Script files updated: \033[92m{script_files_updated}\033[0m")
        print(f"  └ Total files updated: \033[92m{core_files_updated + script_files_updated}\033[0m")
        if stamp_updated:
            print(f"  └ scaffold_version stamp: \033[92mupdated\033[0m")

        # Update README badges to current ngargparser version (green) — only in README.md
        print("\nUpdating README version badges...")
        readme_updates = 0
        if upsert_readme_badge('README.md', __version__, color='green'):
            print("  └ Updated README.md badge")
            readme_updates += 1
        if readme_updates == 0:
            print("  └ No README files updated (none found or already current)")

        if core_files_updated + script_files_updated > 0 or stamp_updated:
            print(f"\n\033[92m✓\033[0m Framework synchronization completed successfully!")
            print(f"Your {project_name} project now has the latest framework files.")
        else:
            print(f"\n\033[93m⚠\033[0m No files needed updating - your project is already up to date!")
        
        return 0
        
    except Exception as e:
        print(f"\033[91m✗\033[0m Error during synchronization: {e}")
        return 1


def main():
    parser = argparse.ArgumentParser(description='NG Argument Parser Framework')
    
    # Add version argument
    parser.add_argument('-v', '--version', action='version', version=f'%(prog)s {__version__}')
    
    subparsers = parser.add_subparsers(dest='command')

    # Create 'generate' sub-command
    startapp_parser = subparsers.add_parser('generate',  aliases=["g"], allow_abbrev=True, help='Create a new custom app project structure')
    startapp_parser.add_argument('project_name', type=str, help='Name of the project to create')

    # Create 'deps' sub-command (manages external tool deps in paths.py)
    deps_parser = subparsers.add_parser('deps', aliases=["d"], allow_abbrev=True,
        help='Manage external tool dependencies declared in paths.py')
    deps_subparsers = deps_parser.add_subparsers(dest='deps_action')
    deps_add = deps_subparsers.add_parser('add', help='Add one or more dependency stubs to paths.py')
    deps_add.add_argument('names', nargs='*', help='Dependency names to add (interactive if omitted)')
    deps_remove = deps_subparsers.add_parser('remove', aliases=['rm'], help='Remove one or more dependency blocks from paths.py')
    deps_remove.add_argument('names', nargs='*', help='Dependency names to remove (interactive if omitted)')
    deps_subparsers.add_parser('list', aliases=['ls'], help='List dependencies declared in paths.py')

    # Create 'config-paths' sub-command (deprecated alias for `deps`)
    config_paths_parser = subparsers.add_parser('config-paths', aliases=["c"], allow_abbrev=True,
        help='[deprecated] Use `cli deps` instead')

    # Create 'sync' sub-command
    sync_parser = subparsers.add_parser('sync', aliases=["s"], allow_abbrev=True, help='Synchronize framework files in existing projects to the latest version.')
    sync_parser.add_argument(
        "--no-upgrade",
        dest="upgrade",
        action="store_false",
        default=True,
        help="Skip the self-upgrade step; only sync templates from the currently-installed ngargparser.",
    )
    sync_parser.add_argument(
        "--ref",
        default="latest",
        help="Git ref to upgrade ngargparser to. 'latest' (default) resolves to the highest semver tag on the remote, falling back to 'master' if no tags exist. Pass a branch/tag/sha (e.g., 'master', 'v0.2.2') to override.",
    )
    sync_parser.add_argument(
        "--dev",
        action="store_true",
        help="Dev mode: pull the bleeding-edge tip of 'master' instead of the latest semver tag. Shortcut for --ref master; overrides --ref if both are given.",
    )

    # Create 'upgrade' sub-command (upgrades ngargparser itself; works anywhere)
    upgrade_parser = subparsers.add_parser('upgrade', aliases=["up"], allow_abbrev=True,
        help='Upgrade ngargparser itself to the latest release tag on GitLab.')
    upgrade_parser.add_argument(
        "--check",
        action="store_true",
        help="Report the installed version vs the latest remote tag without installing anything.",
    )
    upgrade_parser.add_argument(
        "--ref",
        default="latest",
        help="Git ref to upgrade to. 'latest' (default) resolves to the highest semver tag on the remote, falling back to 'master' if no tags exist. Pass a branch/tag/sha (e.g., 'master', 'v0.2.2') to override.",
    )
    upgrade_parser.add_argument(
        "--dev",
        action="store_true",
        help="Upgrade to the bleeding-edge tip of 'master' instead of the latest semver tag. Shortcut for --ref master.",
    )


    args = parser.parse_args()

    # Normalize 'rm' → 'remove' and 'ls' → 'list' for the dispatcher
    if getattr(args, 'deps_action', None) == 'rm':
        args.deps_action = 'remove'
    elif getattr(args, 'deps_action', None) == 'ls':
        args.deps_action = 'list'

    if args.command == 'generate' or args.command == 'g':
        return startapp_command(args) or 0
    elif args.command == 'deps' or args.command == 'd':
        return deps_command(args) or 0
    elif args.command == 'config-paths' or args.command == 'c':
        return config_paths_command(args) or 0
    elif args.command == 'sync' or args.command == 's':
        return sync_command(args) or 0
    elif args.command == 'upgrade' or args.command == 'up':
        return upgrade_command(args) or 0
    else:
        parser.print_help()  # Print help message if no command is specified
        return 0

if __name__ == '__main__':
    import sys
    sys.exit(main() or 0)