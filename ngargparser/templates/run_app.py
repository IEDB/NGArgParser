import os
import preprocess
import postprocess
import validators
import core.set_pythonpath  # This automatically configures PYTHONPATH
from CHILDPARSER import CHILDPARSER
from dotenv import load_dotenv
load_dotenv()


def main():
    parser = CHILDPARSER()
    args = parser.parse_args()

    if args.subcommand == 'predict':
        # ADD PREDICTION LOGIC HERE.
        # Build the standard result envelope and serialize it uniformly
        # (defaults to tsv; pass -f json for JSON). Example:
        #
        #   from core.result_writer import write_results
        #   result = {
        #       "warnings": [], "errors": [],
        #       "results": [{
        #           "type": "my_table",
        #           "table_columns": ["col1", "col2"],
        #           "table_data": [[...], ...],
        #       }],
        #   }
        #   write_results(result, args.output_prefix, args.output_format)
        pass

    if args.subcommand == 'preprocess':
        # ADD CODE LOGIC TO SPLIT INPUTS INSIDE PREPROCESS.PY
        preprocess.run(**vars(args))

    if args.subcommand == 'postprocess':
        # ADD CODE LOGIC TO COMBINE RESULTS INSIDE POSTPROCESS.PY
        postprocess.run(**vars(args))

if __name__=='__main__':
    main()