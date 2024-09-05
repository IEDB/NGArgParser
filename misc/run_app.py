from CHILDPARSER import CHILDPARSER

def main():
    parser = CHILDPARSER()
    args = parser.parse_args()

    if args.subcommand == 'predict':
        # ADD CODE LOGIC HERE.
        pass

    if args.subcommand == 'preprocess':
        # ADD CODE LOGIC ON HOW TO SPLIT JOBS.
        pass

    if args.subcommand == 'postprocess':
        # ADD CODE LOGIC TO COMBINE RESULTS.
        pass

if __name__=='__main__':
    main()