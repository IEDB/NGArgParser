import argparse
import textwrap
import string
import random
import json
import os
import core.core_validators as validators
from pathlib import Path
from typing import TypedDict, List
from core.app_env import load_app_env

# Load this tool's .env (APP_ROOT and APP_NAME always from its own file)
load_app_env()

# Get the project root directory from environment variable
APP_ROOT = os.getenv('APP_ROOT')


def _not_a_file(value):
    """argparse check for a folder the preprocess step creates or fills. It only
    rejects a path that exists and isn't a folder; it creates nothing."""
    path = Path(value)
    if path.exists() and not path.is_dir():
        raise argparse.ArgumentTypeError(f"'{value}' exists and is not a directory.")
    return path


def _unknown_option(name, parser, where):
    """ValueError for an option `parser` doesn't have, naming the closest one."""
    import difflib

    known = sorted({s for action in parser._actions for s in action.option_strings})
    close = difflib.get_close_matches(name, known, n=1)
    hint = f"; did you mean {close[0]}?" if close else f". Its options: {', '.join(known)}"
    return ValueError(f"No option {name} in {where}{hint}")


class SubparserWrapper:
    """
    A wrapper class for argparse subparsers that allows setting help text 
    using the .help attribute, which will automatically update the correct 
    location in the argument parser structure.
    """
    def __init__(self, subparser, subparser_name, parent_parser):
        self._subparser = subparser
        self._subparser_name = subparser_name
        self._parent_parser = parent_parser
        
        # Capture the initial help text from the subparser action
        self._help_text = self._get_current_help_from_action()
    
    def __getattr__(self, name):
        """Delegate attribute access to the wrapped subparser"""
        return getattr(self._subparser, name)
    
    def __setattr__(self, name, value):
        if name.startswith('_'):
            # Internal attributes
            super().__setattr__(name, value)
        elif name == 'help':
            # Intercept help attribute setting
            self._help_text = value
            self._update_help_in_subparser_action()
        else:
            # Delegate to the wrapped subparser
            setattr(self._subparser, name, value)
    
    @property
    def help(self):
        """Get the current help text"""
        return self._help_text
    
    @help.setter
    def help(self, value):
        """Set the help text and update the subparser action"""
        self._help_text = value
        self._update_help_in_subparser_action()
    
    def _update_help_in_subparser_action(self):
        """Update the help text in the _SubParsersAction"""
        # Find the _SubParsersAction in the parent parser
        subparser_action = None
        for action in self._parent_parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                subparser_action = action
                break
        
        if subparser_action and hasattr(subparser_action, '_choices_actions'):
            for choice_action in subparser_action._choices_actions:
                if choice_action.dest == self._subparser_name:
                    choice_action.help = self._help_text
                    break

    def _get_current_help_from_action(self):
        """Get the current help text from the subparser action"""
        subparser_action = None
        for action in self._parent_parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                subparser_action = action
                break
        
        if subparser_action and hasattr(subparser_action, '_choices_actions'):
            for choice_action in subparser_action._choices_actions:
                if choice_action.dest == self._subparser_name:
                    return choice_action.help
        return None

    def update_arguments(self, *args, **new_kwargs):
        """Update an existing argument with new properties.

        This method finds an existing argument in this subparser and redefines it with new properties
        such as help text, default value, group, etc.

        Args:
            *args: Argument name and option string (e.g., "--output-format", "-f")
            **new_kwargs: New properties to apply to the argument (help, default, group, metavar, etc.)

        Returns:
            bool: True once the argument is updated.

        Raises:
            ValueError: the option doesn't exist here (the message suggests the closest
                name), or `args` names options other than its current ones. Renaming
                is not supported: remove_argument() it, then add_argument() the new one.
            TypeError: a keyword is neither an attribute of the option nor `group`.

        Example:
            self.parser_postprocess.update_arguments("--custom-output-format", "-c",
                                dest="custom_output_format",
                                default="json",
                                help="Custom output format for postprocessed results (Default=json)",
                                metavar="CUSTOM_OUTPUT_FORMAT",
                                group="custom options"
                                )
        """
        if not args:
            return False
        
        # Get the primary argument name (first argument)
        argument_name = args[0]
        
        # Find the argument in any existing group
        target_action = None
        original_group = None
        
        for group in self._subparser._action_groups:
            for action in group._group_actions:
                if argument_name in action.option_strings:
                    target_action = action
                    original_group = group
                    break
            if target_action:
                break
        
        if not target_action or not original_group:
            raise _unknown_option(argument_name, self._subparser, self._subparser_name)

        # Option strings live in argparse's lookup table too, which this method
        # does not touch, so a new name would show in help but never parse.
        if len(args) > 1 and set(args) != set(target_action.option_strings):
            raise ValueError(
                f"update_arguments can't rename {'/'.join(target_action.option_strings)} to "
                f"{'/'.join(args)}: remove_argument('{argument_name}', '{self._subparser_name}') "
                f"it, then add_argument() the new option."
            )

        unknown = [key for key in new_kwargs if key != 'group' and not hasattr(target_action, key)]
        if unknown:
            raise TypeError(
                f"update_arguments() got keyword(s) {argument_name} doesn't have: {', '.join(unknown)}"
            )

        # Remove from original group
        original_group._group_actions.remove(target_action)
        
        # Update option strings if new ones are provided
        if len(args) > 1:
            target_action.option_strings = list(args)
        
        # Apply new properties to the action
        for key, value in new_kwargs.items():
            if key != 'group':
                setattr(target_action, key, value)
        
        # Handle group change if specified
        new_group_name = new_kwargs.get('group')
        if new_group_name:
            # Find or create the new group
            new_group = None
            for group in self._subparser._action_groups:
                if group.title == new_group_name:
                    new_group = group
                    break
            
            if not new_group:
                # Create new group if it doesn't exist
                new_group = self._subparser.add_argument_group(new_group_name)
            
            # Add to new group
            new_group._group_actions.append(target_action)
        else:
            # Keep in original group
            original_group._group_actions.append(target_action)
        
        return True




class NGArgumentParser(argparse.ArgumentParser):
    ''' Setting default paths '''
    # defaults for preprocessing
    PROJECT_ROOT_PATH = Path(__file__).resolve().parents[1]


    def __init__(self):
        super().__init__()
        # self.prog='The name of the program (default: os.path.basename(sys.argv[0]))'
        # self.usage='The string describing the program usage (default: generated from arguments added to parser)'
        self.formatter_class=argparse.RawDescriptionHelpFormatter
        self.description=None
        self.epilog=textwrap.dedent('''
        Please contact us with any issues encountered or questions about the software
        through the channel listed below.

        IEDB Discussion Forum: https://discuss.iedb.org
        ''')
        
        self.subparser = self.add_subparsers(
            title='subcommands',
            dest='subcommand',
            # Explicitly set this to prevent the following error.
            # TypeError: __init__() got an unexpected keyword argument 'prog'
            parser_class=argparse.ArgumentParser,
            required=True
            )
        
        # Create a placeholder for subparser 'predict'
        self.parser_predict=None

        # Variable to decide whether or not the default output file structure
        # should be used or not.
        self.use_default_fs=True
        
        # Create subparser 'preprocess'
        # -----------------------------------------------------
        preprocess_parser = self.subparser.add_parser('preprocess', 
                                                 help='Preprocess jobs.',
                                                 description='Preprocess JSON input files into smaller units, if possible and create a job_descriptions.json file that includes all commands to run the workflow')
        
        # Wrap the preprocess parser to enable help text modification
        self.parser_preprocess = SubparserWrapper(preprocess_parser, 'preprocess', self)

        # Create argument groups
        self.preprocess_required_group = self.parser_preprocess.add_argument_group('required parameters')
        self.preprocess_optional_group = self.parser_preprocess.add_argument_group('optional parameters')

        # Define required parameters for preprocess subcommand
        self.preprocess_required_group.add_argument("--input-json", "-j",
                                        dest="input_json",
                                        type=validators.validate_file,
                                        required=True,
                                        help="JSON file containing input parameters.",
                                        metavar="JSON_FILE")
        
        # The checks below only check. The folders are created in parse_args(),
        # once the whole command line has been read and is valid.
        self.preprocess_required_group.add_argument("--output-dir", "-o",
                                        dest="output_dir",
                                        type=_not_a_file,
                                        required=True,
                                        help="prediction result output directory.",
                                        metavar="OUTPUT_DIR")

        # Define optional parameters for preprocess subcommand
        # defaults will be set dynamically based on output-dir
        self.preprocess_optional_group.add_argument("--params-dir",
                                        dest="preprocess_parameters_dir",
                                        type=_not_a_file,
                                        help="""
                                        a directory to store preprocessed JSON input files
                                        (default: $OUTPUT_DIR/predict-inputs/params)
                                        """)

        # defaults will be set dynamically based on output-dir
        self.preprocess_optional_group.add_argument("--inputs-dir",
                                        dest="preprocess_inputs_dir",
                                        type=_not_a_file,
                                        help="""
                                        a directory to store other, non-JSON inputs (e.g., fasta files)
                                        (default: $OUTPUT_DIR/predict-inputs/data)
                                        """)

        self.preprocess_optional_group.add_argument("--assume-valid",
                                        action="store_true",
                                        dest="assume_valid_flag",
                                        default=False,
                                        help="flag to indicate validation can be skipped")
        
        # Create subparser 'postprocess'
        # -----------------------------------------------------
        postprocess_parser = self.subparser.add_parser('postprocess', 
                                                        help='Postprocess jobs.',
                                                        description='results from individual prediction jobs are aggregated',
                                                        epilog='Note: Exactly one of --job-desc-file or --input-results-dir must be specified.',
                                                        )

        # Wrap the postprocess parser to enable help text modification
        self.parser_postprocess = SubparserWrapper(postprocess_parser, 'postprocess', self)

        # Create input source group (mutually exclusive options)
        self.postprocess_required_input_group = self.parser_postprocess.add_argument_group('input source (choose exactly one)')

        self.postprocess_required_input_group.add_argument("--job-desc-file", "-j",
                                dest="job_desc_file",
                                type=validators.validate_file,
                                help="Path to job description file.")

        self.postprocess_required_input_group.add_argument("--input-results-dir", "-i",
                                dest="postprocess_input_dir",
                                type=validators.validate_directory,
                                help="directory containing the result files to postprocess")

        # Optional parameters group. -p sits here alongside -o/-f: all three
        # concern where output goes. It is deliberately not required — tools
        # such as axelf, conservancy, and rate accept -o instead or fall back
        # to the working directory.
        self.postprocess_optional_group = self.parser_postprocess.add_argument_group('optional parameters')

        self.postprocess_optional_group.add_argument("--postprocessed-results-dir", "-p",
                                dest="postprocess_result_dir",
                                type=validators.validate_directory,
                                help="a directory to contain the post-processed results")

        self.postprocess_optional_group.add_argument("--output-prefix", "-o",
                                dest="output_prefix",
                                type=validators.validate_directory_given_filename,
                                help="prediction result output prefix.",
                                metavar="OUTPUT_PREFIX")

        self.postprocess_optional_group.add_argument("--output-format", "-f",
                                dest="output_format",
                                choices=["tsv", "json"],
                                default="json",
                                help="postprocessed result output format "
                                     "(choices: %(choices)s; default: %(default)s).")

        # Add patch for groups
        self.patch_parser_for_groups(self.parser_preprocess)
        self.patch_parser_for_groups(self.parser_postprocess)


    def add_predict_subparser(self, help='', description='', formatter_class=argparse.HelpFormatter):
        '''
        Creates and returns a 'predict' subparser with customizable help and description text.
        
        This method adds a new subparser for prediction-related commands. The subparser is 
        configured with user-provided help and description text, and can be further customized
        by adding tool-specific arguments. Common arguments that are shared across different
        tools can also be added here.

        Args:
            help (str): Help text displayed in the main command list
            description (str): Detailed description shown in the predict command's help

        Returns:
            SubparserWrapper: The configured predict subparser wrapper that can be customized with
                            additional tool-specific arguments and allows help text modification
        '''
        # add subparser with RawDescriptionHelpFormatter to preserve line breaks
        predict_parser = self.subparser.add_parser('predict', 
                                                  help=help, 
                                                  description=description,
                                                  formatter_class=formatter_class)
        
        # Wrap the predict parser to enable help text modification
        self.parser_predict = SubparserWrapper(predict_parser, 'predict', self)

        # Add patch for groups
        self.patch_parser_for_groups(self.parser_predict)

        # Created before the output arguments so 'input options' sorts above
        # 'output options' in help (argparse orders groups by creation, not by
        # when they're populated). Tool-specific input arguments go here; an
        # unpopulated group renders nothing, so tools with no inputs are
        # unaffected. Rename per-tool with:
        #   self.predict_input_group.title = "..."
        self.predict_input_group = self.parser_predict.add_argument_group("input options")

        # Common output arguments shared across all tools (framework-owned).
        # Results serialize uniformly via core.result_writer.write_results: tsv by
        # default (to stdout when no -o, else <prefix>.tsv), or json via -f.
        self.parser_predict.add_argument("--output-prefix", "-o",
                                dest="output_prefix",
                                help="prediction result output prefix.",
                                metavar="OUTPUT_PREFIX",
                                group="output options")
        self.parser_predict.add_argument("--output-format", "-f",
                                dest="output_format",
                                choices=["tsv", "json"],
                                default="tsv",
                                help="prediction result output format "
                                     "(choices: %(choices)s; default: %(default)s).",
                                group="output options")

        # add common arguments across tools
        # self.parser_predict.add_argument("--input-json", "-j",
        #                          dest="input_json",
        #                          help="JSON file containing input parameters.",
        #                          metavar="JSON_FILE")
        # self.parser_predict.add_argument("--assume-valid",
        #                                 action="store_true",
        #                                 dest="assume_valid_flag",
        #                                 default=False,
        #                                 help="flag to indicate validation can be skipped")
        
        return self.parser_predict

    def set_subcommand_order(self, order):
        """Reorder subcommands in help output.
        
        This method changes the display order of subcommands in the help text
        without affecting the actual parsing behavior.
        
        Args:
            order (list): List of subcommand names in desired display order.
                         e.g., ['predict', 'preprocess', 'postprocess']
        """
        # Find the _SubParsersAction
        for action in self._actions:
            if isinstance(action, argparse._SubParsersAction):
                # Build a dict of choice actions by their dest (subcommand name)
                choices_dict = {ca.dest: ca for ca in action._choices_actions}
                # Reorder _choices_actions based on the provided order
                new_choices_actions = []
                for name in order:
                    if name in choices_dict:
                        new_choices_actions.append(choices_dict[name])
                # Add any remaining choices not in the order list (to be safe)
                for ca in action._choices_actions:
                    if ca not in new_choices_actions:
                        new_choices_actions.append(ca)
                action._choices_actions = new_choices_actions
                
                # Also reorder the choices dict to fix the {a,b,c} metavar display
                # Python 3.7+ dicts maintain insertion order
                old_choices = action.choices
                new_choices = {}
                for name in order:
                    if name in old_choices:
                        new_choices[name] = old_choices[name]
                # Add any remaining choices not in the order list
                for name, parser in old_choices.items():
                    if name not in new_choices:
                        new_choices[name] = parser
                action.choices = new_choices
                break

    def validate_mutually_exclusive_args(self, args):
        """Manually validate that exactly one of the mutually exclusive args is provided"""
        job_desc_provided = args.job_desc_file is not None
        input_dir_provided = args.postprocess_input_dir is not None
        
        if job_desc_provided and input_dir_provided:
            self.parser_postprocess.error("argument --input-results-dir: not allowed with argument --job-desc-file")
        elif not job_desc_provided and not input_dir_provided:
            self.parser_postprocess.error("one of the arguments --job-desc-file --input-results-dir is required")
    
    def parse_args(self, args=None, namespace=None):
        """Parse command line arguments and perform validation.

        This method extends the base ArgumentParser's parse_args() to add custom validation
        for mutually exclusive arguments in the postprocess command. It ensures that exactly
        one of --job-desc-file or --input-results-dir is provided when using postprocess.

        Args:
            args: The arguments to parse, as a list of strings. None (the default) reads the
                command line (sys.argv), so a test can pass a list instead:
                parse_args(["predict", "-j", "in.json"]).
            namespace: An object to put the results on, as in argparse.

        Returns:
            argparse.Namespace: The parsed command-line arguments with all validations passed

        Raises:
            ArgumentError: If validation fails for mutually exclusive arguments
        """
        args = super().parse_args(args, namespace)
        
        # If preprocess command is used and output_dir is set, set defaults for other args
        if hasattr(args, 'subcommand') and args.subcommand == 'preprocess':
            if hasattr(args, 'output_dir') and args.output_dir:
                from pathlib import Path
                # The whole command line is valid now, so create the folders. Then
                # check the folders the user named (they may live under -o), so the
                # order of -o, --params-dir and --inputs-dir doesn't matter.
                self._create_preprocess_dirs(args.output_dir)
                for flag, value in (("--params-dir", args.preprocess_parameters_dir),
                                    ("--inputs-dir", args.preprocess_inputs_dir)):
                    if value and not Path(value).is_dir():
                        self.parser_preprocess.error(f"argument {flag}: '{value}' is not a valid directory.")
                if not args.preprocess_inputs_dir:
                    args.preprocess_inputs_dir = str(Path(args.output_dir) / "predict-inputs" / "data")
                if not args.preprocess_parameters_dir:
                    args.preprocess_parameters_dir = str(Path(args.output_dir) / "predict-inputs" / "params")
            
        # If postprocess command is used, validate mutual exclusion
        if hasattr(args, 'subcommand') and args.subcommand == 'postprocess':
            self.validate_mutually_exclusive_args(args)

        return args


    def patch_parser_for_groups(self, parser):
        """Patch an argparse.ArgumentParser instance to support argument grouping.
        
        This helper function adds group functionality to any parser by modifying its add_argument
        method to support an optional 'group' parameter. Arguments can be organized into logical
        groups which will be displayed together in the help output.

        Args:
            parser (argparse.ArgumentParser): The parser instance to patch

        Returns:
            argparse.ArgumentParser: The patched parser with group functionality

        Example:
            parser.add_argument('--flag', help='Some help text', group='My Group')
            # This will add the argument to a group named 'My Group' in the help output
        """
        if not hasattr(parser, '_argument_groups'):
            parser._argument_groups = {}

        original_add_argument = parser.add_argument
        
        def add_argument_with_groups(*args, **kwargs):
            group_name = kwargs.pop('group', None)

            group_mapping = {
                group.title: group
                for group in getattr(parser, '_action_groups', [])
            }

            if group_name:
                if group_name in group_mapping:
                    return group_mapping[group_name].add_argument(*args, **kwargs)
                else:
                    # Create new group if not in mapping
                    if group_name not in parser._argument_groups:
                        parser._argument_groups[group_name] = parser.add_argument_group(group_name)
                    return parser._argument_groups[group_name].add_argument(*args, **kwargs)
            return original_add_argument(*args, **kwargs)
        
        parser.add_argument = add_argument_with_groups
        return parser



    def remove_argument(self, argument_name, subparser_name):
        """Remove a specific argument from a specified subparser.

        This method removes an argument (identified by its name/flag) from a specified subparser
        and its associated action groups. The removal is done both from the subparser's action
        groups and from the subparser's main actions list.

        Args:
            argument_name (str): The name of the argument to remove (e.g., '--output-format')
            subparser_name (str): The name of the subparser ('predict', 'preprocess', or 'postprocess')
                                from which to remove the argument

        Note:
            The whole option goes, under every name it has: removing '--output-format'
            also frees '-f', so add_argument() can declare a replacement afterwards.

        Raises:
            ValueError: `subparser_name` is not predict, preprocess or postprocess, that
                subparser hasn't been added yet, or it has no such option (the message
                suggests the closest name).
        """
        if subparser_name not in ('predict', 'preprocess', 'postprocess'):
            raise ValueError(
                f"remove_argument: unknown subparser {subparser_name!r}; use predict, preprocess or postprocess."
            )
        subparser = getattr(self, f"parser_{subparser_name}", None)
        if subparser is None:
            raise ValueError(f"remove_argument: the {subparser_name} subparser hasn't been added yet.")

        action = subparser._option_string_actions.get(argument_name)
        if action is None:
            raise _unknown_option(argument_name, subparser, subparser_name)

        # argparse records an option in three places: the parser's actions (what
        # parses), its groups (what --help shows) and the option-string table
        # (what each typed name maps to, and what a new declaration conflicts with).
        subparser._actions.remove(action)
        for group in [*subparser._action_groups, *subparser._mutually_exclusive_groups]:
            if action in group._group_actions:
                group._group_actions.remove(action)
        for option_string in action.option_strings:
            subparser._option_string_actions.pop(option_string, None)


    @staticmethod
    def get_app_root_dir(start_dir=None, anchor_files=None):
        """Find the app root directory by looking for known anchor files."""
        if start_dir is None:
            start_dir = os.getcwd()  # Default to the current working directory
        
        if anchor_files is None:
            anchor_files = ['LICENSE']
        
        # Normalize to absolute path
        current_dir = os.path.abspath(start_dir)
        
        # Traverse up the directory tree until an anchor file is found or root is reached
        while current_dir != os.path.dirname(current_dir):
            # Check for anchor files in the current directory
            for anchor in anchor_files:
                if os.path.isfile(os.path.join(current_dir, anchor)) or os.path.isdir(os.path.join(current_dir, anchor)):
                    return current_dir  # Return the directory where the anchor file is found

            # Check for anchor files in child directories
            for root, dirs, files in os.walk(current_dir):
                for anchor in anchor_files:
                    if anchor in dirs or anchor in files:
                        return root  # Return the child directory where the anchor file is found

            current_dir = os.path.dirname(current_dir)  # Move up one level
        
        # If no anchor file is found, return None or raise an error
        return None


    @staticmethod
    def find_file_path(start_dir=None, filename=None):
        """Find the full path of a given file by searching both upwards and downwards from the start directory."""
        if start_dir is None:
            start_dir = os.getcwd()  # Default to the current working directory
        
        if filename is None:
            raise ValueError("Filename must be provided")
        
        # Normalize to absolute path
        start_dir = os.path.abspath(start_dir)
        
        # Check upwards
        current_dir = start_dir
        while current_dir != os.path.dirname(current_dir):
            target_path = os.path.join(current_dir, filename)
            if os.path.isfile(target_path):
                return target_path  # Return the full path of the file if found
            
            current_dir = os.path.dirname(current_dir)  # Move up one level
        
        # Check downwards
        for root, _, files in os.walk(start_dir):
            if filename in files:
                return os.path.join(root, filename)
        
        return None  # Return None if the file is not found
    

    @staticmethod
    def merge_parsers(p1, p2):
        '''
        Merges two argument parsers together.

        Args:
            p1 (argparse): The main argument parser.
            p2 (argparse): The secondary argument parser that will be added to first argument parser.

        Returns:
            None
        
        Raises:
            None
        '''
        for action in p2._actions:
            # Skip the help action (usually the first action)
            if action.dest == "help":
                continue

            # Filter out unsupported keys
            valid_keys = ["option_strings", "dest", "nargs", "const", "default", "type", "choices", "required", "help", "metavar"]
            kwargs = {key: getattr(action, key) for key in valid_keys if hasattr(action, key)}

            # By default, override 'required' to make arguments optional
            kwargs['required'] = False

            # Add the argument to p1
            p1.add_argument(*action.option_strings, **kwargs)


    def _create_preprocess_dirs(self, output_dir):
        """Create the preprocess folder layout under `output_dir` (the layout depends
        on whether paths.py declares dependencies; see
        core_validators.create_directory_structure_for_dependencies).

        Called by parse_args() only after the whole command line is valid, so a
        command that fails leaves nothing behind. A folder that can't be created
        stops the command with a usage error instead of printing and carrying on.
        """
        print(f"Creating directory structure under: {output_dir}")
        errors = []
        created = validators.create_directory_structure_for_dependencies(output_dir, errors=errors)
        if errors:
            self.parser_preprocess.error(
                f"argument --output-dir: could not create the folders under '{output_dir}': {errors[0]}"
            )

        print(f"Successfully created directory structures for {len(created)} tools:")
        for tool_name, dirs in created.items():
            print(f"\n{tool_name}:")
            for dir_path in dirs:
                print(f"  - {dir_path}")


class JobDescriptionParams(TypedDict):
    # Blueprint for creating job description file
    shell_cmd: str
    job_id: int
    job_type: str
    depends_on_job_ids: List[int]
    expected_outputs: List[str]