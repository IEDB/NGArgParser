# Here, the code for the following logic should be implemented.
# * Take input JSON file, and parse the sequences. Depending on the tool, split the sequences if needed, and store them under
#     'preprocess_job/input_units' folder.
# * Take the rest of the parameters from the input JSON file, and split them into an atomic job units.
#   These are recommended to save under 'preprocess_job/'parameter_units'.
#     * If sequences are split and saved under 'preprocess_job/input_units', then...
#       * Make sure for each atomic job units have a key/value pair pointing to the input sequence files 
#         under 'preprocess_job/input_units'.
#       * Each job units should be stored under 'preprocess_job/parameter_units'.
#   (NOTE: Every tool may have their own way of 'splitting' the inputs. They may or may not utilize the provided
#          filestructure.)
# * Lastly, it should create 'job_descriptions.json' file under 'preprocess_job/'.
#     * This file will have list of descriptions for each job units.
#     * Each description will contain a command that runs single prediction (utilizes 'predict' subcommand).
#     * Note that the last command in the description file will use 'postprocess' subcommand.
import json
import tempfile
from pathlib import Path
from NGArgumentParser import JobDescriptionParams


def split_by_length(jdata):
    # job_files = []
    data = json.loads(jdata)
    peptides = data['peptide']
    # pep_lengths = data['length']
    pep_lengths = [len(p) for p in peptides]
    aa = data['amino_acid']

    # -------------------------------------------------------------------
    # STEP 1. Main logic to plit inputs by length
    # -------------------------------------------------------------------
    splitted_input_params = []
    splitted_input_seqs = []
    for l in set(pep_lengths):
        indices = [i for i, peplen in enumerate(pep_lengths) if peplen == l]

        same_len_peptides = []
        for idx in indices:
            same_len_peptides.append(peptides[idx])

        d = {
            'length': [l]*len(same_len_peptides),
            'amino_acid': aa,
            'peptide_file_path': ''
        }

        splitted_input_params.append(d)
        splitted_input_seqs.append(same_len_peptides)

    # -------------------------------------------------------------------
    # STEP 2. Create temp dir where the splitted jobs can be stored
    # -------------------------------------------------------------------
    PROJ_ROOT = str(Path(__file__).parent.parent)
    input_dir = str(PROJ_ROOT) + '/output-directory/predict-inputs/data/'
    param_dir = str(PROJ_ROOT) + '/output-directory/predict-inputs/params/'
    # input_dir = Path('./examples/preprocess_job/input_units/')
    # param_dir = Path('./examples/preprocess_job/parameter_units/')



    # temp_dir = tempfile.mkdtemp(dir='./examples/preprocess_job/', prefix='inputs-')

    # for i, each_input in enumerate(splitted_input_params):
    for i in range(len(splitted_input_params)):
        # abs_path_param_tmpfile = None
        abs_path_seqs_tmpfile = None
        # create temporary file to store each input
        with tempfile.NamedTemporaryFile(dir=input_dir, prefix=f'{i}-', suffix='.txt', mode='w', delete=False) as tmpfile:
            tmpfile.write('\n'.join(splitted_input_seqs[i]))
            abs_path_seqs_tmpfile = Path(tmpfile.name).resolve()

        # Update peptide_file_path
        splitted_input_params[i]['peptide_file_path'] = str(abs_path_seqs_tmpfile)

        with tempfile.NamedTemporaryFile(dir=param_dir, prefix=f'{i}-', suffix='.json', mode='w', delete=False) as tmpfile:
            json.dump(splitted_input_params[i], tmpfile, indent=4)
            # abs_path_param_tmpfile = Path(tmpfile.name).resolve()


    #     job_files.append(abs_path_param_tmpfile)

    # return job_files