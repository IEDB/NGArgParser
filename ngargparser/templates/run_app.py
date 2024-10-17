from CHILDPARSER import CHILDPARSER
import preprocess, postprocess


def main():
    parser = CHILDPARSER()
    args = parser.parse_args()

    if args.subcommand == 'predict':
        # ADD CODE LOGIC HERE.
        pass

    if args.subcommand == 'preprocess':
        # Validate Arguments
        parser.validate_args(**vars(args))

        # ADD CODE LOGIC TO SPLIT RESULTS.
        postprocess.run(**vars(args))

    if args.subcommand == 'postprocess':
        # Validate Arguments
        parser.validate_args(**vars(args))

        # ADD CODE LOGIC TO COMBINE RESULTS.
        postprocess.run(**vars(args))

if __name__=='__main__':
    main()