import textwrap
from NGArgumentParser import NGArgumentParser

class ClusterArgumentParser(NGArgumentParser):
    def __init__(self):
        super().__init__()

        # Set program details by setting params, such as
        # prog, usage, description, epilog, etc.
        # -----------------------------------------------------
        self.description=textwrap.dedent(
        '''\
            This is an example description.
        '''    
        )


        # Add/Modify subparser prediction descriptions
        # -----------------------------------------------------
        pred_parser = self.add_predict_subparser(
            help='Perform individual prediction.',
            description='This is where the users can perform indifidual predictions.'
        )

        
        # Add tool-specific params 
        # -----------------------------------------------------
        pred_parser.add_argument("--output-prefix", "-o",
                                 dest="output_prefix",
                                 help="prediction result output prefix.",
                                 metavar="OUTPUT_PREFIX")
        pred_parser.add_argument("--output-format", "-f",
                                 dest="output_format",
                                 default="tsv",
                                 help="prediction result output format (Default=tsv)",
                                 metavar="OUTPUT_FORMAT")  
    
    