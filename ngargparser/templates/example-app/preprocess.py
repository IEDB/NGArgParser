# Here, the code for the following logic should be implemented.
# * Take input JSON file, and parse the sequences. Depending on the tool, split the sequences if needed, and store them under
#     '<output dir>/predict-inputs/data/' folder (--inputs-dir).
# * Take the rest of the parameters from the input JSON file, and split them into an atomic job units.
#   These are recommended to save under '<output dir>/predict-inputs/params/' (--params-dir).
#     * If sequences are split and saved under '<output dir>/predict-inputs/data/', then...
#       * Make sure for each atomic job units have a key/value pair pointing to the input sequence files 
#         under '<output dir>/predict-inputs/data/'.
#       * Each job units should be stored under '<output dir>/predict-inputs/params/' (--params-dir).
#   (NOTE: Every tool may have their own way of 'splitting' the inputs. They may or may not utilize the provided
#          filestructure.)
# * Lastly, it should create 'job_descriptions.json' file in the <output dir> (-o).
#     * This file will have list of descriptions for each job units.
#     * Each description will contain a command that runs single prediction (utilizes 'predict' subcommand).
#     * Note that the last command in the description file will use 'postprocess' subcommand.
import json
import tempfile
from pathlib import Path

from core.NGArgumentParser import JobDescriptionParams


def read_json(jfile):
    with open(jfile, 'r') as f:
        content = json.load(f)

    return json.dumps(content)
    
def split_by_length(jdata, input_dir_path, param_dir_path):
    data = json.loads(jdata)
    peptides = data['peptide']
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
        
    '''Clean old files'''
    for file in input_dir_path.iterdir():
        # Delete the file
        if file.is_file(): file.unlink()  

    for file in param_dir_path.iterdir():
        # Delete the file
        if file.is_file(): file.unlink()

    for i in range(len(splitted_input_params)):
        abs_path_seqs_tmpfile = None
        # create temporary file to store each input
        with tempfile.NamedTemporaryFile(dir=input_dir_path, prefix=f'{i}-', suffix='.txt', mode='w', delete=False) as tmpfile:
            tmpfile.write('\n'.join(splitted_input_seqs[i]))
            abs_path_seqs_tmpfile = Path(tmpfile.name).resolve()

        # Update peptide_file_path
        splitted_input_params[i]['peptide_file_path'] = str(abs_path_seqs_tmpfile)

        with tempfile.NamedTemporaryFile(dir=param_dir_path, prefix=f'{i}-', suffix='.json', mode='w', delete=False) as tmpfile:
            json.dump(splitted_input_params[i], tmpfile, indent=4)


def write_job_descriptions(**kwargs):
    params_dir = kwargs.get('preprocess_parameters_dir')

    # output path
    OUTPUT_DIR_PATH = kwargs.get('output_dir')

    # job description file path - use the output directory
    JD_PATH = Path(OUTPUT_DIR_PATH) / 'job_descriptions.json'

    # exec file path - the run script next to this file
    EXEC_FILE_PATH = str(Path(__file__).parent) + '/run_aa_counter.py'
    files_with_ctime = []

    for file_path in Path(params_dir).iterdir():
        if file_path.is_file():
            # Get the creation time of the file
            creation_time = file_path.stat().st_ctime

            # Store the file path and its creation time as a tuple
            files_with_ctime.append((file_path, creation_time))

    # Sort files by creation time
    files_with_ctime.sort(key=lambda x: x[1])

    '''
    In case user forgets to or doesn't want to remove previous input files
    in the directory, then group the files by timeframe, then
    create job_description file by taking only the last group.
    '''
    grouped_files = []
    current_group = []
    timeframe_seconds=0.1

    for i, (file, ctime) in enumerate(files_with_ctime):
        if not current_group:
            current_group.append(file)  # Start a new group
        else:
            # Check if the current file's creation time is within the timeframe
            if ctime - files_with_ctime[i - 1][1] <= timeframe_seconds:
                current_group.append(file)
            else:
                grouped_files.append(current_group)  # Save the current group
                current_group = [file]  # Start a new group

    # Add the last group if it exists
    if current_group:
        grouped_files.append(current_group)

    '''
    Take the last group that was newly created, and create
    job_description file out of it.
    '''
    job_files = grouped_files[-1]
    with open(JD_PATH, 'w') as f :
        jd_cmds = []
        for i, job in enumerate(job_files):
            param_file_path = str(job)

            shell_cmd = f'{EXEC_FILE_PATH} predict -j {param_file_path} -o {OUTPUT_DIR_PATH}/predict-outputs/result.{i} -f json'
            job_id = i
            job_type = 'prediction'
            expected_outputs = [
                f'{OUTPUT_DIR_PATH}/predict-outputs/result.{i}.json'
            ]

            jd: JobDescriptionParams = {
                'shell_cmd': shell_cmd,
                'job_id': job_id,
                'job_type': job_type,
                'depends_on_job_ids': [],
                'expected_outputs': expected_outputs,
            }

            jd_cmds.append(jd)

        # Add command for postprocessing
        i += 1
        shell_cmd = f'{EXEC_FILE_PATH} postprocess --job-desc-file={JD_PATH} -o {OUTPUT_DIR_PATH}/aggregate/final-result -f json'
        job_id = i
        job_type = 'postprocess'
        depends_on_job_ids = list(range(i))
        expected_outputs = [
            f'{OUTPUT_DIR_PATH}/aggregate/final-result.json'
        ]

        jd: JobDescriptionParams = {
            'shell_cmd': shell_cmd,
            'job_id': job_id,
            'job_type': job_type,
            'depends_on_job_ids': depends_on_job_ids,
            'expected_outputs': expected_outputs,
        }

        jd_cmds.append(jd)

        # Write the entire list of jobs to job description file
        json.dump(jd_cmds, f, indent=4)

    print(f"Created job descriptions file: {JD_PATH}")
    print(f"Total jobs: {len(jd_cmds)}")


def run(**kwargs):
    # ADD CODE LOGIC TO SPLIT RESULTS.
    data = kwargs.get('input_json')
    data = read_json(data)
    input_dir_path = kwargs.get('preprocess_inputs_dir')
    param_dir_path = kwargs.get('preprocess_parameters_dir')
    
    # Convert string paths to Path objects
    if input_dir_path:
        input_dir_path = Path(input_dir_path)
    if param_dir_path:
        param_dir_path = Path(param_dir_path)

    split_by_length(data, input_dir_path, param_dir_path)

    # Create job description file
    write_job_descriptions(**kwargs)
