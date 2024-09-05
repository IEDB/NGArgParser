import textwrap
from NGArgumentParser import NGArgumentParser


class AACounterArgumentParser(NGArgumentParser):
    def __init__(self):
        super().__init__()

        # Set program details by setting params, such as
        # prog, usage, description, epilog, etc.
        # -----------------------------------------------------
        self.description = textwrap.dedent(
            '''
            AA Counter: Given a set of peptides and an amino acid, count the number of times that amino acid occurs in each of the peptides.
            '''
        )

        # Add/Modify subparser prediction descriptions
        # -----------------------------------------------------
        pred_parser = self.add_predict_subparser(
            help='Performs counting given a peptide and an amino acid.',
            description='Given a set of peptides and an amino acid, count the number of times that amino acid occurs in each of the peptides.'
        )

        # Add tool-specific params 
        # -----------------------------------------------------
        pred_parser.add_argument("--input-tsv", "-t",
                                dest="input_tsv",
                                default=None,
                                help="Perform counting given a TSV file.",
                                )
        pred_parser.add_argument("--amino-acid", "-a",
                                dest="aa",
                                default=None,
                                help="Define the amino acid that needs to be counted.",
                                )
        pred_parser.add_argument("--input-json", "-j",
                                dest="input_json",
                                default=None,
                                help="Perform counting given a JSON file.",
                                )
        pred_parser.add_argument("--output-prefix", "-o",
                                dest="output_prefix",
                                help="prediction result output prefix.",
                                metavar="OUTPUT_PREFIX")
        pred_parser.add_argument("--output-format", "-f",
                                dest="output_format",
                                default="tsv",
                                help="prediction result output format (Default=tsv)",
                                metavar="OUTPUT_FORMAT")
