import textwrap
from NGArgumentParser import NGArgumentParser


class ChildArgumentParser(NGArgumentParser):
    def __init__(self):
        super().__init__()

        # ADD program details by setting params, such as
        # prog, usage, description, epilog, etc.
        # -----------------------------------------------------
        self.description = textwrap.dedent(
            '''
            '''
        )

        # ADD subparser prediction descriptions
        # -----------------------------------------------------
        pred_parser = self.add_predict_subparser(
            help='',
            description=''
        )

        # ADD tool-specific params 
        # -----------------------------------------------------
        pred_parser.add_argument("--input-tsv", "-t",
                                dest="input_tsv",
                                default=None,
                                help="Perform counting given a TSV file.",
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
                                default="json",
                                help="prediction result output format (Default=tsv)",
                                metavar="OUTPUT_FORMAT")

