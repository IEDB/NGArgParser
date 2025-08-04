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
        # ADD CODE LOGIC HERE.
        pass

    if args.subcommand == 'preprocess':
        # ADD CODE LOGIC TO SPLIT INPUTS INSIDE PREPROCESS.PY
        preprocess.run(**vars(args))

    if args.subcommand == 'postprocess':
        # ADD CODE LOGIC TO COMBINE RESULTS INSIDE POSTPROCESS.PY
        postprocess.run(**vars(args))

if __name__=='__main__':
    main()