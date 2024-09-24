# Here, the code for the following logic should be implemented.
# * This will take the 'job_descriptions.json' file and read through each job's 'expected_outputs'.
# * The 'expected_outputs' is where each job's result is stored.
# * Then, it will gather all of them into a list.
# NOTE: Every tool will differ, but logic to combine all the results into single file is needed.
import json


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