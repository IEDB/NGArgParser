from NGChildArgumentParser import ClusterArgumentParser


def main():
    arg_parser = ClusterArgumentParser()
    args = arg_parser.parse_args()

    if args.subcommand == 'predict':
        print("Running prediction...")

    # split function
    if args.subcommand == 'preprocess':
        json_filename = getattr(args, 'json_filename')
        split_parameters_dir = getattr(args, 'split_parameters_dir')
        split_inputs_dir = getattr(args, 'split_inputs_dir')
        assume_valid_flag = getattr(args, 'assume_valid_flag')
        # split_parameters_file(json_filename, split_parameters_dir, split_inputs_dir, assume_valid=assume_valid_flag)
        
        # TODO: Analylze split/aggregate for other tools, and see
        # if we can move this logic inside the parser parent class
        # so that the parent class can handle all the heavy load.
        # parser.start_split_process()
        arg_parser.process_split(json_filename, split_parameters_dir, split_inputs_dir, assume_valid=assume_valid_flag)
    
    # aggregate function
    if args.subcommand == 'postprocess':
        job_desc_file = getattr(args, 'job_desc_file')
        aggregate_input_dir = getattr(args, 'aggregate_input_dir')
        aggregate_result_dir = getattr(args, 'aggregate_result_dir')
        # aggregate_result_file(job_desc_file, aggregate_input_dir, aggregate_result_dir)


if __name__=='__main__':
    main()
