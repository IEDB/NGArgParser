import json
import re
import preprocess
import postprocess
from AACounterArgumentParser import AACounterArgumentParser


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
    with open(tfile.name, 'r') as f :
        header = f.readline().strip().split('	')
        content = [_.strip().split('	') for _ in f.readlines()]

    content_dict = dict.fromkeys(header)
    content_dict['peptide'] = []
    content_dict['length'] = []
    content_dict['amino_acid'] = aa

    for row in content:
        pep = row[0]
        content_dict['peptide'].append(pep)
        content_dict['length'].append(len(pep))

    content = json.dumps(content_dict)

    return content

def read_json(jfile):
    content = json.load(jfile)
    jfile.seek(0)

    return json.dumps(content)


def main():
    parser = AACounterArgumentParser()
    args = parser.parse_args()

    if args.subcommand == 'predict':
        # ADD CODE LOGIC HERE.
        # Unify inputs into a JSON format.
        if args.input_tsv:
            if not args.aa:
                raise parser.error("Please specify amino acid using the '-a' flag.")
            json_input = convert_tsv_to_json(args.input_tsv, args.aa)
        elif args.input_json:
            json_input = read_json(args.input_json)
        else:
            raise parser.error("Counter app accepts only TSV or JSON file format.")

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

        # Write it out to the file
        if args.output_prefix:
            result_file_path = args.output_prefix + '.' + args.output_format

            with open(result_file_path, 'w') as f :
                json.dump(result_json, f, indent=4)
        else:
            # raise parser.error("Please define the output file using the '-o' flag.")
            print(json.dumps(result_json, indent=4))

    if args.subcommand == 'preprocess':
        # Validate Arguments
        parser.validate_args(args)
        
        # Run preprocess logic
        preprocess.run(**vars(args))
        
        # Create job description file
        parser.create_job_descriptions_file(**vars(args))

    if args.subcommand == 'postprocess':
        # Validate Arguments
        parser.validate_args(args)

        # Run postprocess logic
        postprocess.run(**vars(args))

if __name__=='__main__':
    main()