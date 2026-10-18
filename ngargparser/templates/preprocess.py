# Here, the code for the following logic should be implemented.
# * Take input JSON file, and parse the sequences. Depending on the tool, split the sequences if needed, and store them under
#     '<output dir>/predict-inputs/data/' folder (--inputs-dir).
# * Take the rest of the parameters from the input JSON file, and split them into an atomic job units.
#     * Make sure for each atomic job units have a key/value pair pointing to the input sequence files under '<output dir>/predict-inputs/data/'.
#     * Each job units should be stored under '<output dir>/predict-inputs/params/' (--params-dir).
# * Lastly, it should create 'job_descriptions.json' file in the <output dir> (-o).
#     * This file will have list of descriptions for each job units.
#     * Each description will contain a command that runs single prediction (utilizes 'predict' subcommand).
#     * Note that the last command in the description file will use 'postprocess' subcommand.

def run(**kwargs):
    # ADD CODE LOGIC TO SPLIT INPUTS
    pass