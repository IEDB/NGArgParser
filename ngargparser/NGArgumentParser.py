import argparse
import textwrap
import string
import random
import json
import os
import validators
from pathlib import Path
from typing import TypedDict, List


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
        self.parser_preprocess = self.subparser.add_parser('preprocess', 
                                                 help='Preprocess jobs.',
                                                 description='Preprocess JSON input files into smaller units, if possible and create a job_descriptions.json file that includes all commands to run the workflow')
        
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
        self.parser_postprocess = self.subparser.add_parser('postprocess', 
                                                        help='Postprocess jobs.',
                                                        description='results from individual prediction jobs are aggregated')

        # --input-results-dir option and --job-desc-file should be mutually exclusive.
        # Make sure at least one of the two be defined.
        group = self.parser_postprocess.add_mutually_exclusive_group(required=True)

        group.add_argument("--job-desc-file",
                                        dest="job_desc_file",
                                        type=argparse.FileType('r'),
                                        # default=self.PROJECT_ROOT_PATH,
                                        # required=True,
                                        help="Path to job description file.")

        group.add_argument("--input-results-dir",
                                        dest="postprocess_input_dir",
                                        type=validators.validate_directory,
                                        # default=self.DEFAULT_RESULTS_DIR,
                                        # required=True,
                                        help="directory containing the result files to postprocess")

        self.parser_postprocess.add_argument("--postprocessed-results-dir",
                                        dest="postprocess_result_dir",
                                        type=validators.validate_directory,
                                        # default=self.OUTPUT_DIR_PATH,
                                        required=True,
                                        help="a directory to contain the post-processed results")
        
        self.parser_postprocess.add_argument("--output-prefix", "-o",
                                dest="output_prefix",
                                type=validators.validate_directory_given_filename,
                                # default=self.DEFAULT_RESULTS_DIR / self.generate_random_filename(),
                                help="prediction result output prefix.",
                                metavar="OUTPUT_PREFIX")
        
        self.parser_postprocess.add_argument("--output-format", "-f",
                                dest="output_format",
                                default="json",
                                help="prediction result output format (Default=json)",
                                metavar="OUTPUT_FORMAT")


    def add_predict_subparser(self, help='', description=''):
        '''
        This is where prediction subparser will be created with user specified
        help and description texts, and attaching some common arguments across tools.
        '''
        # add subparser
        self.parser_predict = self.subparser.add_parser('predict', help=help, description=description)

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
    

    # def validate_args(self, args):
    #     if args.subcommand == 'preprocess':
    #         self.validate_preprocess_args(args)

    #     if args.subcommand == 'postprocess':
    #         self.validate_postprocess_args(args)


    # def validate_preprocess_args(self, args):
    #     kwargs = vars(args)
    #     output_dir = kwargs.get('output_dir')

    #     # Set params-dir and inputs-dir to the value of '--output-dir' 
    #     # if both are not specified.
    #     if not kwargs.get('preprocess_parameters_dir'):
    #         params_dir = output_dir / 'predict-inputs' / 'params'
    #         self.ensure_directory_exists(params_dir)
    #         setattr(args, 'preprocess_parameters_dir', params_dir)

    #     if not kwargs.get('preprocess_inputs_dir'):
    #         inputs_dir = output_dir / 'predict-inputs' / 'data'
    #         self.ensure_directory_exists(inputs_dir)
    #         setattr(args, 'preprocess_inputs_dir', inputs_dir)

    #     # Also, create predict-output directory in advance.
    #     predict_output_dir = output_dir / 'predict-outputs'
    #     self.ensure_directory_exists(predict_output_dir)

    # def ensure_directory_exists(self, path):
    #     if not path.exists():
    #         path.mkdir(parents=True, exist_ok=True)

    
    def validate_postprocess_args(self, args):
        kwargs = vars(args)
        
        '''
        Note that --input-results-dir option and --job-desc-file are mutually exclusive.
        If --job-desc-file is set, then extract value of "input_result-dir" and set it
        to the Namespace.
        '''
        if kwargs.get('job_desc_file'):
            # Read job-description file, and set the pointer back to beginning
            # in case it needs to be read again
            jd_content = json.load(args.job_desc_file)
            args.job_desc_file.seek(0)
            
            preprocess_result_dir = jd_content[0]['expected_outputs'][0]
            preprocess_result_dir = Path(preprocess_result_dir).parent
            
            setattr(args, 'postprocess_input_dir', preprocess_result_dir)


    def format_exec_name(self, name):
        pname = name.replace('-', '_')
        # pname = [_.capitalize() for _ in pname]
        # return ''.join(pname)
        return pname
    
    def generate_random_filename(self, length=10):
        """Generates a random filename with the specified length and extension."""

        letters = string.ascii_letters
        filename = ''.join(random.choice(letters) for i in range(length))
        return filename


    # def create_job_descriptions_file(self, params_dir):
    def create_job_descriptions_file(self, **kwargs):
        params_dir = kwargs.get('preprocess_parameters_dir')
        
        # output path
        OUTPUT_DIR_PATH = kwargs.get('output_dir')

        # final result directory path
        PRED_OUTPUT_DIR = OUTPUT_DIR_PATH / 'predict-outputs'

        # job description file path
        JD_PATH = self.PROJECT_ROOT_PATH / 'job_descriptions.json'

        # exec file path
        PROJ_NAME = self.PROJECT_ROOT_PATH.name.split('/')[-1]
        PROJ_NAME = self.format_exec_name(PROJ_NAME)
        EXEC_FILE_PATH = Path(__file__).resolve().parent / f'run_{PROJ_NAME}.py'
        
        files_with_ctime = []

        for file_path in Path(params_dir).iterdir():
            if file_path.is_file():
                # Get the creation time of the file
                creation_time = file_path.stat().st_ctime
                
                # Store the file path and its creation time as a tuple
                files_with_ctime.append((file_path, creation_time))

        # Sort files by creation time
        files_with_ctime.sort(key=lambda x: x[1])

        '''
        In case user forgets to or doesn't want to remove previous input files
        in the directory, then group the files by timeframe, then
        create job_description file by taking only the last group.
        '''
        grouped_files = []
        current_group = []
        timeframe_seconds=0.1

        for i, (file, ctime) in enumerate(files_with_ctime):
            if not current_group:
                current_group.append(file)  # Start a new group
            else:
                # Check if the current file's creation time is within the timeframe
                if ctime - files_with_ctime[i - 1][1] <= timeframe_seconds:
                    current_group.append(file)
                else:
                    grouped_files.append(current_group)  # Save the current group
                    current_group = [file]  # Start a new group

        # Add the last group if it exists
        if current_group:
            grouped_files.append(current_group)

        '''
        Take the last group that was newly created, and create
        job_description file out of it.
        '''
        job_files = grouped_files[-1]
        with open(JD_PATH, 'w') as f :
            jd_cmds = []
            for i, job in enumerate(job_files):
                param_file_path = str(job)
                
                shell_cmd = f'{EXEC_FILE_PATH} predict -j {param_file_path} -o {PRED_OUTPUT_DIR}/result.{i} -f json'
                job_id = i
                job_type = 'prediction'
                expected_outputs = [
                    f'{PRED_OUTPUT_DIR}/result.{i}.json'
                ]

                jd: JobDescriptionParams = {
                    'shell_cmd': shell_cmd,
                    'job_id': job_id,
                    'job_type': job_type,
                    'depends_on_job_ids': [],
                    'expected_outputs': expected_outputs,
                }

                jd_cmds.append(jd)

            # Add command for postprocessing
            i += 1
            shell_cmd = f'{EXEC_FILE_PATH} postprocess --job-desc-file={JD_PATH} --postprocessed-results-dir={OUTPUT_DIR_PATH}'
            job_id = i
            job_type = 'postprocess'
            depends_on_job_ids = list(range(i))
            expected_outputs = [
                f'{OUTPUT_DIR_PATH}/final-result.json'
            ]

            jd: JobDescriptionParams = {
                'shell_cmd': shell_cmd,
                'job_id': job_id,
                'job_type': job_type,
                'depends_on_job_ids': depends_on_job_ids,
                'expected_outputs': expected_outputs,
            }

            jd_cmds.append(jd)

            # Write the entire list of jobs to job description file
            json.dump(jd_cmds, f, indent=4)


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