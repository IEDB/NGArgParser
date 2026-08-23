import textwrap
import argparse
import validators
from core.NGArgumentParser import NGArgumentParser


class ChildArgumentParser(NGArgumentParser):
    def __init__(self):
        super().__init__()

        '''
        It is the developer's responsibility to customize these parameters.
        At the minimum, the below parameters should be customized before deploying.

        Developers can choose to further customize other parameters of ArgumentParser()
        from here:
        https://docs.python.org/3/library/argparse.html#argparse.ArgumentParser
        '''
        # ADD program details by setting params, such as
        # prog, usage, description, epilog, etc.
        # -----------------------------------------------------
        self.description = textwrap.dedent(
            '''
            '''
        )

        # ADD subparser prediction descriptions
        # -----------------------------------------------------
        self.parser_predict = self.add_predict_subparser(
            help='',
            description=''
        )

        # Set the display order of subcommands in help output
        self.set_subcommand_order(['predict', 'preprocess', 'postprocess'])

        # ADD tool-specific params 
        # -----------------------------------------------------
        # Inputs are mutually exclusive: argparse rejects passing both with
        # exit 2, rather than silently using one and ignoring the other.
        # predict_input_group comes from the framework base class so it renders
        # above 'output options'; rename it by setting its .title.
        inputs = self.predict_input_group.add_mutually_exclusive_group()
        inputs.add_argument("--input-tsv", "-t",
                                dest="input_tsv",
                                type=argparse.FileType('r'),
                                help="Perform counting given a TSV file.",
                                )
        inputs.add_argument("--input-json", "-j",
                                dest="input_json",
                                type=argparse.FileType('r'),
                                help="Perform counting given a JSON file.",
                                )
        # NOTE: --output-prefix/-o and --output-format/-f are provided by the
        # framework base class (see core.NGArgumentParser.add_predict_subparser).
        # Predict output defaults to tsv and is serialized via
        # core.result_writer.write_results. To customize, use:
        #   self.parser_predict.update_arguments("--output-format", "-f", ...)

