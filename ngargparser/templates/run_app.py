import core.set_pythonpath  # This automatically configures PYTHONPATH and loads this tool's .env
import os
import sys
import preprocess
import postprocess
import validators
from core.NGArgumentParser import ParserSetupError
from core.app_env import DependencyError
from CHILDPARSER import CHILDPARSER


def main():
    try:
        parser = CHILDPARSER()
    except ParserSetupError as e:
        # A mistake in the parser code, such as a misspelled option name in
        # update_arguments: one line naming the file and line, as argparse does.
        print(e, file=sys.stderr)
        sys.exit(2)
    args = parser.parse_args()

    if args.subcommand == 'predict':
        # ADD PREDICTION LOGIC HERE, in place of the sys.exit line below.
        # Build the standard result envelope and serialize it uniformly
        # (defaults to tsv; pass -f json for JSON). Example:
        #
        #   from core.result_writer import write_results
        #   input_path = args.input_json or args.input_tsv  # predict takes -j or -t
        #   ...read input_path...
        #   result = {
        #       "warnings": [], "errors": [],
        #       "results": [{
        #           "type": "my_table",
        #           "table_columns": ["col1", "col2"],
        #           "table_data": [[...], ...],
        #       }],
        #   }
        #   write_results(result, args.output_prefix, args.output_format)
        sys.exit(f"predict is not implemented yet: edit src/{os.path.basename(__file__)}")

    elif args.subcommand == 'preprocess':
        # ADD CODE LOGIC TO SPLIT INPUTS INSIDE PREPROCESS.PY
        preprocess.run(**vars(args))

    elif args.subcommand == 'postprocess':
        # ADD CODE LOGIC TO COMBINE RESULTS INSIDE POSTPROCESS.PY
        postprocess.run(**vars(args))

    # ADD a branch for a subcommand of your own (self.add_subcommand) above this
    # line: elif args.subcommand == 'list-alleles':
    else:
        # Without a branch, the subcommand printed nothing and exited 0.
        sys.exit(f"{args.subcommand} has no branch in src/{os.path.basename(__file__)}: add one in main()")

if __name__=='__main__':
    try:
        main()
    except DependencyError as e:
        # A dependency that can't run, such as one ./configure has not set up
        # yet: one line and exit code 1. Any other error keeps its traceback.
        sys.exit(str(e))
