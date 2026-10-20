import textwrap
import validators
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
        # Genuinely mutually exclusive, not just labelled that way: argparse
        # rejects passing both with exit 2, rather than silently using the TSV
        # and ignoring the JSON. It also rejects passing neither.
        self.predict_input_group.title = "Input method (mutually exclusive)"
        inputs = self.predict_input_group.add_mutually_exclusive_group(required=True)
        # Each validator checks the file's content too (src/validators.py), so
        # a bad input gives one usage line instead of a traceback.
        inputs.add_argument("--input-tsv", "-t",
                                dest="input_tsv",
                                type=validators.validate_input_tsv,
                                help="Perform counting given a TSV file.",
                                )
        inputs.add_argument("--input-json", "-j",
                                dest="input_json",
                                type=validators.validate_input_json,
                                help="Perform counting given a JSON file.",
                                )
        self.parser_predict.add_argument("--amino-acid", "-a",
                                dest="aa",
                                type=validators.validate_amino_acid,
                                default=None,
                                help="Define the amino acid that needs to be counted.",
                                group="Input TSV-specific options"
                                )

        # preprocess -j comes from the framework, which checks only that the
        # file exists: give it this tool's content check as well.
        self.parser_preprocess.update_arguments("--input-json", "-j",
                                type=validators.validate_preprocess_json)
        # NOTE: --output-prefix/-o and --output-format/-f come from the
        # framework base class (add_predict_subparser). Predict output defaults
        # to tsv and is serialized via core.result_writer.write_results.