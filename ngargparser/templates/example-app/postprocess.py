# Here, the code for the following logic should be implemented.
# * This will take the 'job_descriptions.json' file and read through each job's 'expected_outputs'.
# * The 'expected_outputs' is where each job's result is stored.
# * Then, it will gather all of them into a list.
# NOTE: Every tool will differ, but logic to combine all the results into single file is needed.
import json


def read_json(jfile):
    content = json.load(jfile)
    jfile.seek(0)

    return json.dumps(content)

def collect_all_job_results(job_descriptions):
    aggregate_job = job_descriptions[-1]
    dependent_jobs = aggregate_job['depends_on_job_ids']

    # Iterate through expected_outputs from all the dependent jobs and combine the results
    final_table_data = []
    final_table_header = []
    for job in job_descriptions:
        if job['job_id'] not in dependent_jobs:
            continue

        job_result_file = job['expected_outputs'][0]

        with open(job_result_file, 'r') as f :
            table_data = json.load(f)

        job_result_table_data = table_data['results'][0]['table_data']

        if not final_table_header :
            final_table_header = table_data['results'][0]['table_columns']

        for td in job_result_table_data:
            final_table_data.append(td)

    return final_table_header, final_table_data

def collect_all_job_results_without_jd(args):
    '''
    Namespace(subcommand='postprocess', job_desc_file=None, 
              postprocess_input_dir=PosixPath('custom-output-dir/predict-outputs'), 
              postprocess_result_dir=PosixPath('custom-output-dir'), 
              output_prefix=None, 
              output_format='json')
    '''
    preprocess_results_dir = args.postprocess_input_dir

    final_table_data = []
    final_table_header = []
    for job_result_file in preprocess_results_dir.iterdir():
        with open(job_result_file, 'r') as f :
            table_data = json.load(f)

        job_result_table_data = table_data['results'][0]['table_data']

        if not final_table_header :
            final_table_header = table_data['results'][0]['table_columns']

        for td in job_result_table_data:
            final_table_data.append(td)
    
    return final_table_header, final_table_data

def save_results_to(path, header, data):
    final_data = {
        'warnings': [],
        'errors': [],
        'results': [
            {
                'type': 'peptide_table',
                'table_columns': header,
                'table_data': data,
            }
        ]
    }

    with open(path, 'w') as f :
        json.dump(final_data, f, indent=4)


def run(**kwargs):
    # ADD CODE LOGIC TO COMBINE RESULTS.
    jd_file = kwargs.get('job_desc_file')
    output_prefix = kwargs.get('output_prefix')
    output_format = kwargs.get('output_format')
    
    if jd_file:
        jd_file = read_json(jd_file)
        job_descriptions = json.loads(jd_file)
        
        post_jd = job_descriptions[-1]
        result_file_path = post_jd['expected_outputs'][0]

        # 2.1 Aggregate all the results.
        final_header, final_data = collect_all_job_results(job_descriptions)

    else:
        # allow user to perform postprocess without job-description
        result_dir = kwargs.get('postprocess_result_dir')
        final_header, final_data = collect_all_job_results_without_jd(kwargs)
        result_file_path = result_dir / 'final-result-no-jd.json'          

    if output_prefix:
        result_file_path = output_prefix.with_suffix(f'.{output_format}')
    
    save_results_to(result_file_path, final_header, final_data)
