import argparse
import textwrap
import os
import shutil
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
        os.makedirs(os.path.join(project_name, 'output-directory'))
        os.makedirs(os.path.join(project_name, 'output-directory', 'predict-inputs', 'data'))
        os.makedirs(os.path.join(project_name, 'output-directory', 'predict-inputs', 'params'))
        os.makedirs(os.path.join(project_name, 'output-directory', 'predict-outputs'))
        

        # Create necessary files
        parser_file = 'AACounterArgumentParser.py'
        update_and_place_readme(f'{EXAMPLE_DIR}/README', project_name, is_example=True)
        # shutil.copy('./misc/README', f'{project_name}/README')
        shutil.copy(f'{EXAMPLE_DIR}/example.json', f'{project_name}/output-directory/example.json')
        shutil.copy(f'{EXAMPLE_DIR}/example.tsv', f'{project_name}/output-directory/example.tsv')
        shutil.copy(f'{EXAMPLE_DIR}/run_aa_counter.py', f'{project_name}/src/run_aa_counter.py')
        shutil.copy(f'{EXAMPLE_DIR}/{parser_file}', f'{project_name}/src/{parser_file}')
        shutil.copy(f'{EXAMPLE_DIR}/preprocess.py', f'{project_name}/src/preprocess.py')
        shutil.copy(f'{EXAMPLE_DIR}/postprocess.py', f'{project_name}/src/postprocess.py')
        shutil.copy(f'{NGPARSER_DIR}/NGArgumentParser.py', f'{project_name}/src/NGArgumentParser.py')

        print(f"Created '{project_name}' project structure successfully.")
    except Exception as e:
        print(f"Error: {e}")


def create_project_structure(project_name):
    try:
        # Create directory structure
        os.makedirs(project_name)
        os.makedirs(os.path.join(project_name, 'src'))
        os.makedirs(os.path.join(project_name, 'output-directory'))
        os.makedirs(os.path.join(project_name, 'output-directory', 'predict-inputs', 'data'))
        os.makedirs(os.path.join(project_name, 'output-directory', 'predict-inputs', 'params'))
        os.makedirs(os.path.join(project_name, 'output-directory', 'predict-outputs'))
        
        # Create necessary files
        exec_file = f'run_{format_project_name(project_name)}.py'
        parser_file = f'{format_project_name(project_name, capitalize=True)}ArgumentParser.py'
        parser_name = f'{format_project_name(project_name, capitalize=True)}ArgumentParser'
        update_and_place_readme(f'{TEMPLATE_DIR}/README', project_name)
        shutil.copy(f'{TEMPLATE_DIR}/run_app.py', f'{project_name}/src/{exec_file}')
        shutil.copy(f'{TEMPLATE_DIR}/ChildArgumentParser.py', f'{project_name}/src/{parser_file}')
        shutil.copy(f'{TEMPLATE_DIR}/preprocess.py', f'{project_name}/src/preprocess.py')
        shutil.copy(f'{TEMPLATE_DIR}/postprocess.py', f'{project_name}/src/postprocess.py')        
        shutil.copy(f'{NGPARSER_DIR}/NGArgumentParser.py', f'{project_name}/src/NGArgumentParser.py')

        # Add default content to all the files
        replace_text_in_place(f'{project_name}/src/{exec_file}', 'CHILDPARSER', parser_name)
        replace_text_in_place(f'{project_name}/src/{parser_file}', 'ChildArgumentParser', parser_name)        

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


def startapp_command(args):
    if args.project_name == 'example':
        create_example_structure()
    else:
        create_project_structure(args.project_name)

def main():
    parser = argparse.ArgumentParser(description='NG Argument Parser Framework')
    subparsers = parser.add_subparsers(dest='command')

    # Create 'startapp' sub-command
    startapp_parser = subparsers.add_parser('generate',  aliases=["g"], allow_abbrev=True, help='Create a new custom app project structure')
    startapp_parser.add_argument('project_name', type=str, help='Name of the project to create')

    args = parser.parse_args()

    if args.command == 'generate' or args.command == 'g':
        startapp_command(args)

    else:
        parser.print_help()  # Print help message if 'startapp' command is not specified

if __name__ == '__main__':
    main()
