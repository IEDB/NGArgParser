import core.set_pythonpath  # This automatically configures PYTHONPATH
import json
import os
import re
import sys
import preprocess
import postprocess
from AACounterArgumentParser import AACounterArgumentParser
from core.NGArgumentParser import ParserSetupError
from core.app_env import DependencyError
from core.result_writer import write_results


def format_result_json(data):
    '''
    This final result JSON file can look different according to each tool's needs.
    In this example, the final JSON format will contain 'warnings', 'type',
    'table_columns', and 'table_data'.
    '''
    counter_col_name = list(data.keys())[-1]

    table_data = []
    for i in range(len(data['peptide'])):
        pep = data['peptide'][i]
        length = len(pep)
        count = data[counter_col_name][i]
        table_data.append([pep, length, count])

    final_data = {
        'warnings': [],
        'results': [
            {
                'type': 'peptide_table',
                'table_columns': [
                    'peptide',
                    'length',
                    counter_col_name
                ],
                'table_data': table_data,
            }
        ]
    }

    return final_data


def countAA(jcontent):
    '''
    Given a JSON data, this will iterate through each peptide and count the 
    occurences of the amino acid.
    {
        "peptide": ["ADMGHLKY", ...],
        "amino_acid": "L"
    }   
    '''
    content = json.loads(jcontent)
    aa = content['amino_acid']
    peptides = content['peptide']
    count_result = []

    # Regex pattern to find amino acid (case-insensitive)
    pattern = rf'{aa}'

    for peptide in peptides:
        matches = re.findall(pattern, peptide, re.IGNORECASE)

        # Count the number of matches
        count = len(matches)
        count_result.append(count)

    return count_result

def update_json_input(jinput):
    '''
    When performing individual prediction (without the use of 'preprocessing'),
    JSON file won't contain 'peptide_file_path'. However, if 'preprocessing' has
    been performed, the 'predict' command from 'job_descrioptions.json' will look
    a little different. It will contain 'peptide_file_path' that points to 
    peptide files.

    (a). Input for prediction without 'preprocessing'/'postprocessing'.
    {
        "peptide": ["ADMGHLKY", "ELDDTLKY", ...],  
        "length": ["8", "8", ...],
        "amino_acid": "L"
    }

    (b). Input after 'preprocessing'.
    {
        "length": ["8", "8" , ...],
        "amino_acid": "L",
        "peptide_file_path": "ABSOLUTE/PATH/TO/preprocess_job/input_units/0-aj9boqn6.txt"
    }

    This function will read 'peptide_file_path' from JSON and grab the list of
    peptides, and reformat JSON so that it has 'peptide' with the list of peptides
    as value. It will also remove 'peptide_file_path' from JSON format.
    '''
    content = json.loads(jinput)

    if 'peptide_file_path' not in content:
        return jinput

    seq_path = content['peptide_file_path']

    with open(seq_path, 'r') as f :
        peptides = [_.strip() for _ in f.readlines()]

    content['peptide'] = peptides

    del content['peptide_file_path']

    return json.dumps(content)

def convert_tsv_to_json(tfile, aa):
    '''
    This function will convert TSV formatted data to the following JSON.
    {
        "peptide": ["ADMGHLKY", "ELDDTLKY", ...],  
        "amino_acid": "L"
    }
    '''
    with open(tfile, 'r') as f :
        header = f.readline().strip().split('	')
        content = [_.strip().split('	') for _ in f.readlines()]

    # Read the peptide column by its name, wherever it is. validators.py's
    # validate_input_tsv has made sure the header names a 'peptide' column.
    peptide_column = header.index('peptide')

    content_dict = dict.fromkeys(header)
    content_dict['peptide'] = []
    content_dict['length'] = []
    content_dict['amino_acid'] = aa

    for row in content:
        pep = row[peptide_column]
        content_dict['peptide'].append(pep)
        content_dict['length'].append(len(pep))

    content = json.dumps(content_dict)

    return content

def read_json(jfile):
    with open(jfile, 'r') as f:
        content = json.load(f)

    return json.dumps(content)


def main():
    try:
        parser = AACounterArgumentParser()
    except ParserSetupError as e:
        # A mistake in the parser code: one line naming the file and line.
        print(e, file=sys.stderr)
        sys.exit(2)
    args = parser.parse_args()

    if args.subcommand == 'predict':
        # ADD CODE LOGIC HERE.
        # Unify inputs into a JSON format.
        if args.input_tsv:
            if not args.aa:
                parser.parser_predict.error("Please specify amino acid using the '-a' flag.")
            json_input = convert_tsv_to_json(args.input_tsv, args.aa)
        elif args.input_json:
            # The JSON input names its amino acid, so -a would be ignored.
            if args.aa is not None:
                parser.parser_predict.error("-a sets the amino acid for -t input; a -j input carries its own amino_acid")
            json_input = read_json(args.input_json)
        else:
            parser.parser_predict.error("Counter app accepts only TSV or JSON file format.")

        # Update JSON to have peptide list instead of file path
        json_input = update_json_input(json_input)

        # The prediction should always take JSON file. The return value is
        # a list of the number of occurences of a certain amino acid.
        result_list = countAA(json_input)

        json_input = json.loads(json_input)

        # Add this AAcount data to JSON
        aa = json_input['amino_acid']
        count_col_header = f'AAcount({aa})'
        json_input[count_col_header] = result_list

        # Reformat JSON so that it has all the keys it needs for the final
        # result JSON file.
        result_json = format_result_json(json_input)

        # Serialize uniformly: tsv by default, json via -f. Prints to stdout
        # when no -o is given, otherwise writes <output_prefix>.<ext>.
        write_results(result_json, args.output_prefix, args.output_format)

    elif args.subcommand == 'preprocess':
        # Run preprocess logic (it also writes job_descriptions.json)
        preprocess.run(**vars(args))

    elif args.subcommand == 'postprocess':
        # Without a job description file, there is no default place for the result.
        if args.postprocess_input_dir and not (args.output_prefix or args.postprocess_result_dir):
            parser.error("with --input-results-dir, give -o <prefix> or -p <folder> for the merged result")

        # Run postprocess logic
        postprocess.run(**vars(args))

    else:
        # A subcommand added with self.add_subcommand needs a branch of its own above.
        sys.exit(f"{args.subcommand} has no branch in src/{os.path.basename(__file__)}: add one in main()")

if __name__=='__main__':
    try:
        main()
    except DependencyError as e:
        # A dependency that can't run (see core.app_env.dependency_command): one line, exit code 1.
        sys.exit(str(e))