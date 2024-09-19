import argparse
import textwrap
import os
import shutil

def format_project_name(name):
    pname = name.replace('_', '-').split('-')
    pname = [_.capitalize() for _ in pname]
    return ''.join(pname)

def create_example_structure():
    try:
        project_name = 'aa-counter'

        # Create directory structure
        os.makedirs(project_name)
        os.makedirs(os.path.join(project_name, 'src'))
        os.makedirs(os.path.join(project_name, 'examples'))
        os.makedirs(os.path.join(project_name, 'examples', 'preprocess_job', 'input_units'))
        os.makedirs(os.path.join(project_name, 'examples', 'preprocess_job', 'results'))
        os.makedirs(os.path.join(project_name, 'examples', 'preprocess_job', 'parameter_units'))
        os.makedirs(os.path.join(project_name, 'examples', 'postprocess_job'))


        # Create necessary files
        parser_file = 'AACounterArgumentParser.py'
        shutil.copy('./misc/README', f'{project_name}/README')
        shutil.copy('./misc/example.json', f'{project_name}/examples/example.json')
        shutil.copy('./misc/example.tsv', f'{project_name}/examples/example.tsv')
        shutil.copy('./misc/run_aacounter.py', f'{project_name}/src/run_aacounter.py')
        shutil.copy(f'./misc/{parser_file}', f'{project_name}/src/{parser_file}')
        shutil.copy('./misc/preprocess-example.py', f'{project_name}/src/preprocess.py')
        shutil.copy('./misc/postprocess-example.py', f'{project_name}/src/postprocess.py')
        shutil.copy('./NGArgumentParser.py', f'{project_name}/src/NGArgumentParser.py')

        print(f"Created '{project_name}' project structure successfully.")
    except Exception as e:
        print(f"Error: {e}")


def create_project_structure(project_name):
    try:
        # Create directory structure
        os.makedirs(project_name)
        os.makedirs(os.path.join(project_name, 'src'))
        os.makedirs(os.path.join(project_name, 'examples'))
        os.makedirs(os.path.join(project_name, 'examples', 'preprocess_job', 'input_units'))
        os.makedirs(os.path.join(project_name, 'examples', 'preprocess_job', 'results'))
        os.makedirs(os.path.join(project_name, 'examples', 'preprocess_job', 'parameter_units'))
        os.makedirs(os.path.join(project_name, 'examples', 'postprocess_job'))

        # Create necessary files
        exec_file = f'run_{project_name}.py'
        parser_file = f'{project_name.capitalize()}ArgumentParser.py'
        parser_name = f'{project_name.capitalize()}ArgumentParser'
        shutil.copy('./misc/README', f'{project_name}/README')
        shutil.copy('./misc/run_app.py', f'{project_name}/src/{exec_file}')
        shutil.copy('./misc/ChildArgumentParser.py', f'{project_name}/src/{parser_file}')
        shutil.copy('./misc/preprocess.py', f'{project_name}/src/preprocess.py')
        shutil.copy('./misc/postprocess.py', f'{project_name}/src/postprocess.py')        
        shutil.copy('./NGArgumentParser.py', f'{project_name}/src/NGArgumentParser.py')

        # Add default content to all the files
        replace_text_in_place(f'{project_name}/src/{exec_file}', 'CHILDPARSER', parser_name)
        replace_text_in_place(f'{project_name}/src/{parser_file}', 'ChildArgumentParser', parser_name)        

        print(f"Created '{project_name}' project structure successfully.")
    except Exception as e:
        print(f"Error: {e}")


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
    parser = argparse.ArgumentParser(description='Loki Framework')
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
