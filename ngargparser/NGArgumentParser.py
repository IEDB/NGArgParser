import argparse
import textwrap
import string
import random
import json
import os
import validators
from pathlib import Path
from typing import TypedDict, List


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
            bool: True if the argument was successfully updated, False otherwise

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
            return False
        
        # Remove from original group
        original_group._group_actions.remove(target_action)
        
        # Update option strings if new ones are provided
        if len(args) > 1:
            target_action.option_strings = list(args)
        
        # Apply new properties to the action
        for key, value in new_kwargs.items():
            if hasattr(target_action, key):
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
        self.description='Text to display before the argument help (by default, no text)'
        self.epilog=textwrap.dedent('''
        Please contact us with any issues encountered or questions about the software
        through any of the channels listed below.

        IEDB Help Desk: https://help.iedb.org/
        Email: help@iedb.org
        ''')
        
        self.subparser = self.add_subparsers(
            title='subcommands',
            description='Here are list of valid subcommands.',
            help='additional help',
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
                                        type=argparse.FileType('r'),
                                        required=True,
                                        help="JSON file containing input parameters.",
                                        metavar="JSON_FILE")
        
        self.preprocess_required_group.add_argument("--output-dir", "-o",
                                        dest="output_dir",
                                        type=validators.validate_preprocess_dir, # this will create subdirectories
                                        required=True,
                                        help="prediction result output directory.",
                                        metavar="OUTPUT_DIR")
        
        # Define optional parameters for preprocess subcommand
        # default will be set in validate_preprocess_args()
        self.preprocess_optional_group.add_argument("--params-dir",
                                        dest="preprocess_parameters_dir",
                                        type=validators.validate_directory,
                                        help="""
                                        a directory to store preprocessed JSON input files
                                        (default: $OUTPUT_DIR/predict-inputs/params)
                                        """)

        # default will be set in validate_preprocess_args()
        self.preprocess_optional_group.add_argument("--inputs-dir",
                                        dest="preprocess_inputs_dir",
                                        type=validators.validate_directory,
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
                                type=argparse.FileType('r'),
                                help="Path to job description file.")

        self.postprocess_required_input_group.add_argument("--input-results-dir", "-i",
                                dest="postprocess_input_dir",
                                type=validators.validate_directory,
                                help="directory containing the result files to postprocess")

        # Other required parameters
        self.postprocess_required_group = self.parser_postprocess.add_argument_group('other required parameters')

        self.postprocess_required_group.add_argument("--postprocessed-results-dir", "-p",
                                dest="postprocess_result_dir",
                                type=validators.validate_directory,
                                help="a directory to contain the post-processed results")

        # Optional parameters group  
        self.postprocess_optional_group = self.parser_postprocess.add_argument_group('optional parameters')

        self.postprocess_optional_group.add_argument("--output-prefix", "-o",
                                dest="output_prefix",
                                type=validators.validate_directory_given_filename,
                                help="prediction result output prefix.",
                                metavar="OUTPUT_PREFIX")

        self.postprocess_optional_group.add_argument("--output-format", "-f",
                                dest="output_format",
                                default="json",
                                help="prediction result output format (Default=json)",
                                metavar="OUTPUT_FORMAT")

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

    def validate_mutually_exclusive_args(self, args):
        """Manually validate that exactly one of the mutually exclusive args is provided"""
        job_desc_provided = args.job_desc_file is not None
        input_dir_provided = args.postprocess_input_dir is not None
        
        if job_desc_provided and input_dir_provided:
            self.parser_postprocess.error("argument --input-results-dir: not allowed with argument --job-desc-file")
        elif not job_desc_provided and not input_dir_provided:
            self.parser_postprocess.error("one of the arguments --job-desc-file --input-results-dir is required")
    
    def parse_args(self):
        """Parse command line arguments and perform validation.

        This method extends the base ArgumentParser's parse_args() to add custom validation
        for mutually exclusive arguments in the postprocess command. It ensures that exactly 
        one of --job-desc-file or --input-results-dir is provided when using postprocess.

        Returns:
            argparse.Namespace: The parsed command-line arguments with all validations passed

        Raises:
            ArgumentError: If validation fails for mutually exclusive arguments
        """
        args = super().parse_args()
            
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
            The method handles removal from both the argument group and the main subparser
            to ensure complete cleanup of the argument.
        """

        actions_to_remove = []
        subparser = None

        if subparser_name == 'predict':
            subparser = self.parser_predict

        if subparser_name == 'preprocess':
            subparser = self.parser_preprocess

        if subparser_name == 'postprocess':
            subparser = self.parser_postprocess

        for group in subparser._action_groups:
            for action in group._group_actions:
                if argument_name in action.option_strings:
                    actions_to_remove.append((action, group._group_actions))

        for action, group_actions in actions_to_remove:
            # Need to remove from both the group and the subparser
            group_actions.remove(action)
            subparser._actions.remove(action)


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


class JobDescriptionParams(TypedDict):
    # Blueprint for creating job description file
    shell_cmd: str
    job_id: int
    job_type: str
    depends_on_job_ids: List[int]
    expected_outputs: List[str]