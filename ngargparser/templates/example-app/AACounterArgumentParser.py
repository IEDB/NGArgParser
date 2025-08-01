import textwrap
import argparse
from core.NGArgumentParser import NGArgumentParser


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
        self.parser_predict = self.add_predict_subparser(
            help='Performs counting given a peptide and an amino acid.',
            description='Given a set of peptides and an amino acid, count the number of times that amino acid occurs in each of the peptides.'
        )

        # Add tool-specific params 
        # -----------------------------------------------------
        self.parser_predict.add_argument("--input-tsv", "-t",
                                dest="input_tsv",
                                type=argparse.FileType('r'),
                                default=None,
                                help="Perform counting given a TSV file.",
                                group="Input method (mutually exclusive)"
                                )
        self.parser_predict.add_argument("--amino-acid", "-a",
                                dest="aa",
                                default=None,
                                help="Define the amino acid that needs to be counted.",
                                group="Input TSV-specific options"
                                )
        self.parser_predict.add_argument("--input-json", "-j",
                                dest="input_json",
                                type=argparse.FileType('r'),
                                default=None,
                                help="Perform counting given a JSON file.",
                                group="Input method (mutually exclusive)",
                                )
        self.parser_predict.add_argument("--output-prefix", "-o",
                                dest="output_prefix",
                                help="prediction result output prefix.",
                                metavar="OUTPUT_PREFIX",
                                group="Output options"
                                )
        self.parser_predict.add_argument("--output-format", "-f",
                                dest="output_format",
                                default="json",
                                help="prediction result output format (Default=tsv)",
                                metavar="OUTPUT_FORMAT",
                                group="Output options"
                                )