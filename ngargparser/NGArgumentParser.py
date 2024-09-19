import argparse
import textwrap
import os
from typing import TypedDict, List

# get the current directory and define some defaults
cwd = os.getcwd()

# defaults for preprocessing
default_params_dir = os.path.join(cwd, 'preprocessing')
default_inputs_dir = default_params_dir

# defaults for postprocessing
default_results_dir = os.path.join(cwd, 'results')
default_postprocessed_results_dir = os.path.join(cwd, 'postprocessing')


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
                                        default=default_params_dir,
                                        help="a directory to store preprocessed JSON input files")
        
        parser_preprocess.add_argument("--inputs-dir",
                                        dest="preprocess_inputs_dir",
                                        default=default_inputs_dir,
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
                                        default=default_results_dir,
                                        help="directory containing the result files to postprocess")

        parser_postprocess.add_argument("--postprocessed-results-dir",
                                        dest="postprocess_result_dir",
                                        default=default_postprocessed_results_dir,
                                        help="a directory to contain the post-processed results")
        
        parser_postprocess.add_argument("--job-desc-file",
                                        dest="job_desc_file",
                                        default=default_postprocessed_results_dir,
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


class JobDescriptionDict(TypedDict):
    # Blueprint for creating job description file
    shell_cmd: str
    job_id: int
    job_type: str
    depends_on_job_ids: List[int]
    expected_outputs: List[str]
