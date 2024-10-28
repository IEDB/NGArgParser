import preprocess
import postprocess
from CHILDPARSER import CHILDPARSER


def main():
    parser = CHILDPARSER()
    args = parser.parse_args()

    if args.subcommand == 'predict':
        # ADD CODE LOGIC HERE.
        pass

    if args.subcommand == 'preprocess':
        # Validate Arguments
        parser.validate_args(args)

        # ADD CODE LOGIC TO SPLIT RESULTS.
        preprocess.run(**vars(args))

        # Create job description file
        parser.create_job_descriptions_file(**vars(args))

    if args.subcommand == 'postprocess':
        # Validate Arguments
        parser.validate_args(args)

        # ADD CODE LOGIC TO COMBINE RESULTS.
        postprocess.run(**vars(args))

if __name__=='__main__':
    main()