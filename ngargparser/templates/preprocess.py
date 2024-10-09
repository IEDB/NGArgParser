# Here, the code for the following logic should be implemented.
# * Take input JSON file, and parse the sequences. Depending on the tool, split the sequences if needed, and store them under
#     'preprocess_job/input_units' folder.
# * Take the rest of the parameters from the input JSON file, and split them into an atomic job units.
#     * Make sure for each atomic job units have a key/value pair pointing to the input sequence files under 'preprocess_job/input_units'.
#     * Each job units should be stored under 'preprocess_job/parameter_units'.
# * Lastly, it should create 'job_descriptions.json' file under 'preprocess_job/'.
#     * This file will have list of descriptions for each job units.
#     * Each description will contain a command that runs single prediction (utilizes 'predict' subcommand).
#     * Note that the last command in the description file will use 'postprocess' subcommand.

def run(**kwargs):
    pass