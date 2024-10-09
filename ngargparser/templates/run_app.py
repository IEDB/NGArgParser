from CHILDPARSER import CHILDPARSER
import preprocess, postprocess


def main():
    parser = CHILDPARSER()
    args = parser.parse_args()

    if args.subcommand == 'predict':
        # ADD CODE LOGIC HERE.
        pass

    if args.subcommand == 'preprocess':
        # ADD CODE LOGIC ON HOW TO SPLIT JOBS.
        preprocess.run(**vars(args))

    if args.subcommand == 'postprocess':
        # ADD CODE LOGIC TO COMBINE RESULTS.
        postprocess.run(**vars(args))

if __name__=='__main__':
    main()