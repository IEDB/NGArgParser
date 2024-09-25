import argparse
import textwrap
import json
from pathlib import Path
from typing import TypedDict, List

# defaults for preprocessing
PROJECT_ROOT_PATH = str(Path(__file__).parent)
OUTPUT_DIR_PATH = PROJECT_ROOT_PATH + '/output-directory'
DEFAULT_PARAMS_DIR = OUTPUT_DIR_PATH + '/predict-inputs/params'
DEFAULT_INPUTS_DIR = OUTPUT_DIR_PATH + '/predict-inputs/data'

# defaults for postprocessing
DEFAULT_RESULTS_DIR = OUTPUT_DIR_PATH + '/predict-outputs'


class NGArgumentParser(argparse.ArgumentParser):
    def __init__(self):
        '''
        It is the developer's responsibility to customize these parameters.
        At the minimum, the below parameters should be customized before deploying.

        Developers can choose to further customize other parameters of ArgumentParser()
        from here:
        https://docs.python.org/3/library/argparse.html#argparse.ArgumentParser
        '''
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
        
        # Create subparser 'preprocess'
        # -----------------------------------------------------
        parser_preprocess = self.subparser.add_parser('preprocess', 
                                                 help='Preprocess jobs.',
                                                 description='Preprocess JSON input files into smaller units, if possible and create a job_descriptions.json file that includes all commands to run the workflow')
        
        parser_preprocess.add_argument("--input-json", "-j",
                                        dest="input_json",
                                        help="JSON file containing input parameters.",
                                        metavar="JSON_FILE")
        
        parser_preprocess.add_argument("--params-dir",
                                        dest="preprocess_parameters_dir",
                                        default=DEFAULT_PARAMS_DIR,
                                        help="a directory to store preprocessed JSON input files")
        
        parser_preprocess.add_argument("--inputs-dir",
                                        dest="preprocess_inputs_dir",
                                        default=DEFAULT_INPUTS_DIR,
                                        help="a directory to store other, non-JSON inputs (e.g., fasta files)")
        
        parser_preprocess.add_argument("--assume-valid",
                                        action="store_true",
                                        dest="assume_valid_flag",
                                        default=False,
                                        help="flag to indicate validation can be skipped")


        # Create subparser 'postprocess'
        # -----------------------------------------------------
        parser_postprocess = self.subparser.add_parser('postprocess', 
                                                        help='Postprocess jobs.',
                                                        description='results from individual prediction jobs are aggregated')

        parser_postprocess.add_argument("--input-results-dir",
                                        dest="postprocess_input_dir",
                                        default=DEFAULT_RESULTS_DIR,
                                        help="directory containing the result files to postprocess")

        parser_postprocess.add_argument("--postprocessed-results-dir",
                                        dest="postprocess_result_dir",
                                        default=OUTPUT_DIR_PATH,
                                        help="a directory to contain the post-processed results")
        
        parser_postprocess.add_argument("--job-desc-file",
                                        dest="job_desc_file",
                                        default=PROJECT_ROOT_PATH,
                                        help="Path to job description file.")
        
        parser_postprocess.add_argument("--output-prefix", "-o",
                                dest="output_prefix",
                                help="prediction result output prefix.",
                                metavar="OUTPUT_PREFIX")
        
        parser_postprocess.add_argument("--output-format", "-f",
                                dest="output_format",
                                default="tsv",
                                help="prediction result output format (Default=tsv)",
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
    
    def format_exec_name(self, name):
        pname = name.replace('-', '_')
        # pname = [_.capitalize() for _ in pname]
        # return ''.join(pname)
        return pname

    def create_job_descriptions_file(self, params_dir):
        # project root
        PROJ_ROOT_PATH = str(Path(__file__).parent.parent)
        
        # output path
        OUTPUT_DIR_PATH = PROJ_ROOT_PATH + '/output-directory'
        PREDICT_OUTPUT_DIR = OUTPUT_DIR_PATH + '/predict-outputs'

        # job description file path
        JD_PATH = PROJ_ROOT_PATH + '/job_descriptions.json'

        # exec file path
        PROJ_NAME = PROJ_ROOT_PATH.split('/')[-1]
        PROJ_NAME = self.format_exec_name(PROJ_NAME)
        EXEC_FILE_PATH = str(Path(__file__).parent) + f'/run_{PROJ_NAME}.py'
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

        # for i, gfiles in enumerate(grouped_files):
        #     print('========================')
        #     print(f'GROUP {i+1}:')
        #     print('========================')
        #     for file in gfiles:
        #         print(str(file))

        '''
        Take the last group that was newly created, and create
        job_description file out of it.
        '''
        job_files = grouped_files[-1]
        with open(JD_PATH, 'w') as f :
            jd_cmds = []
            for i, job in enumerate(job_files):
                param_file_path = str(job)
                
                shell_cmd = f'{EXEC_FILE_PATH} predict -j {param_file_path} -o {PREDICT_OUTPUT_DIR}/result.{i} -f json'
                job_id = i
                job_type = 'prediction'
                expected_outputs = [
                    f'{PREDICT_OUTPUT_DIR}/result.{i}.json'
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
            shell_cmd = f'{EXEC_FILE_PATH} postprocess --job-desc-file={JD_PATH} -o {OUTPUT_DIR_PATH}/final-result -f json'
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


class JobDescriptionParams(TypedDict):
    # Blueprint for creating job description file
    shell_cmd: str
    job_id: int
    job_type: str
    depends_on_job_ids: List[int]
    expected_outputs: List[str]