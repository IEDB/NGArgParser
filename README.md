# NGArgumentParser

This is a standardized parser framework for CLI tools. This class will enforce certain properties or abstract methods to be implemented to properly create an Argument Parser class for other CLI tools.

## What's in NGArgumentParser?
The parser class inherits directly from `argparse.ArgumentParser`. Once it's instantiated, it will create an ArgumentParser class, and create 3 subparsers: `preprocess`, `predict`, and `postprocess`.

The `preprocess` subparser has the following options available:
```bash
>> python run_test.py preprocess -h
usage: run_test.py preprocess [-h] [--json-input JSON_FILE] [--params-dir PREPROCESS_PARAMETERS_DIR]
                              [--inputs-dir PREPROCESS_INPUTS_DIR] [--assume-valid]

Preprocess JSON input files into smaller units, if possible and create a job_descriptions.json file that
includes all commands to run the workflow

options:
  -h, --help            show this help message and exit
  --json-input JSON_FILE, -j JSON_FILE
                        JSON file containing input parameters.
  --params-dir PREPROCESS_PARAMETERS_DIR
                        a directory to store preprocessed JSON input files
  --inputs-dir PREPROCESS_INPUTS_DIR
                        a directory to store other, non-JSON inputs (e.g., fasta files)
  --assume-valid        flag to indicate validation can be skipped
```

The `postprocess` subparser has the following options available:
```bash
>> python run_test.py postprocess -h
usage: run_test.py postprocess [-h] [--input-results-dir POSTPROCESS_INPUT_DIR]
                               [--postprocessed-results-dir POSTPROCESS_RESULT_DIR]

results from individual prediction jobs are aggregated

options:
  -h, --help            show this help message and exit
  --input-results-dir POSTPROCESS_INPUT_DIR
                        directory containing the result files to postprocess
  --postprocessed-results-dir POSTPROCESS_RESULT_DIR
                        a directory to contain the post-processed results
```

The `predict` subparser has no options available as the NGArgumentParser will only create the subparser and not add any options to it. It is the developer's duty to add arguments and customize the `predict` subparser.  More details on filling in the logic for each run mode is below.

Let's now try to create a child class that builds off of this parser framework.


> __NOTE__ :
> 
> These are subject to change in the future.


## Create a child class
Let's create an example class that extends from this parser class.
```python
class ExampleArgumentParser(NGArgumentParser):
    def __init__(self):
        super().__init__()
```

To fully create an Argument Parser class using `NGArgumentParser`, a couple of elements must be implemented:

1. customizing program's detail (e.g. `prog`, `usage`, `description`, `epilog`)
2. add subparser for prediction
3. add tool-specific parameters to the prediction subparser

### 1. Customizing program's detail
```python
class ExampleArgumentParser(NGArgumentParser):
    def __init__(self):
        super().__init__()

        # Write your description HERE
        self.description="This is an example description code."
```
The `self.description` will update the ArgumentParser's description from NGArgumentParser.
```python
class NGArgumentParser(argparse.ArgumentParser):
    def __init__(self):
        '''
        It is the developer's responsibility to customize these parameters.
        At the minimum, the below parameters should be customized before deploying.

        Developers can choose to futher customize other parameters of ArgumentParser()
        from here:
        https://docs.python.org/3/library/argparse.html#argparse.ArgumentParser
        '''
        super().__init__()
        # self.prog='The name of the program (default: os.path.basename(sys.argv[0]))'
        # self.usage='The string describing the program usage (default: generated from arguments added to parser)'
        self.description='Text to display before the argument help (by default, no text)'
        self.epilog='Text to display after the argument help (by default, no text)'
```
By doing so, when `--help` is passed, it will change the program's description.
```bash
>> python run_test.py --help
usage: run_test.py [-h] {split,aggregate,predict} ...

This is an example description.

options:
  -h, --help            show this help message and exit

...
```
Please refer to the [argparse document](https://docs.python.org/3/library/argparse.html#argparse.ArgumentParser) to learn about other parameters.


### 2. Add subparser for prediction
The `NGArgumentParser` already handles creating the `subparser` inside the `add_predict_subparser()`.
```python
class ExampleArgumentParser(NXGArgumentParserBuilder):
    def __init__(self):
        .
        .
        pred_parser = self.add_predict_subparser(
            help='Perform individual prediction.',
            description='This is where the users can perform indifidual predictions.'
        )
```
The `add_predict_subparser()` will return a `subparser`.

### 3. Add tool-specific parameters to the prediction subparser
Since we can access prediction subparser from the child class, we can add any tool-specific commands to it.

```python
class ExampleArgumentParser(NXGArgumentParserBuilder):
    def __init__(self):
        .
        .
        pred_parser = self.add_predict_subparser(
            help='Perform individual prediction.',
            description='This is where the users can perform indifidual predictions.'
        )

        # Add tool-specific params 
        # -----------------------------------------------------
        pred_parser.add_argument("--output-prefix", "-o",
                                 dest="output_prefix",
                                 help="prediction result output prefix.",
                                 metavar="OUTPUT_PREFIX")
        pred_parser.add_argument("--output-format", "-f",
                                 dest="output_format",
                                 default="tsv",
                                 help="prediction result output format (Default=tsv)",
                                 metavar="OUTPUT_FORMAT")  
```

## Usage
In the executable file, the child class should be instantiated and logic to handle the cli-tool should be there. Let's create the executable file called `run_test.py`.

```python
def main():
    arg_parser = ExampleArgumentParser()
    args = arg_parser.parse_args()

    if args.subcommand == 'predict':
        ########################################################################
        # PREDICTION LOGIC GOES HERE.
        # 
        # 
        ########################################################################
    
    if args.subcommand == 'preprocess':
        # Retrieve attributes
        json_filename = getattr(args, 'json_filename')
        preprocess_parameters_dir = getattr(args, 'preprocess_parameters_dir')
        preprocess_inputs_dir = getattr(args, 'preprocess_inputs_dir')
        assume_valid_flag = getattr(args, 'assume_valid_flag')

        ########################################################################
        # SPLIT FUNCTION FROM "SPLIT.PY" SHOULD GO HERE.
        # 
        # 
        ########################################################################
    
    if args.subcommand == 'postprocess':       
        # Retrieve attributes 
        postprocess_input_dir = getattr(args, 'postprocess_input_dir')
        postprocess_result_dir = getattr(args, 'postprocess_result_dir')

        ########################################################################
        # AGGREGATE FUNCTION FROM "AGGREGATE.PY" SHOULD GO HERE.
        # 
        # 
        ########################################################################

if __name__=='__main__':
    main()
```


## Filling in the logic

The logic for each of the run modes (`preprocess`, `predict`, `postprocess`) needs
to be fleshed out separately.  We aim to include as much of the common logic as
possible in future releases of NGArgumentParser.

We will take the following `example.json` file as the input file to demonstrate what `preprocess` and `postprocess` should generally be handling.

```json
{
"peptide_list": ["TMDKSELVQK", "EILNSPEKAC", "KMKGDYFRYF"],  
"alleles": "HLA-A*02:01,HLA-A*01:01",
  "predictors": [
    {
      "type": "binding",
      "method": "netmhcpan_ba"
    }
  ]
}
```


### Preprocess

In this step, a JSON input file is validated and, if possible, broken down into
smaller JSON files that can be passed to the `predict` step.  Additionally, a
`job_descriptions.json` files is created that lists each of the commands that need
to be executed in order to complete the prediction as well as any expected output
files.

It is essentially creating the following filestructure, where we set the following parameters:
1. `params-dir=splitted_parameters`
2. `inputs-dir=inputs_units`

```bash
.
├── inputs_units/
│   └── tmp_pqgszjz_
├── results/
│   └── sequence_peptide_index.json
├── splitted_parameters/
│   ├── 0.json
│   └── 1.json
└── job_descriptions.json
```

`inputs-dir`: This directory will hold a temporary file that has all the inputs.
```python
# file: tmp_pqgszjz_
TMDKSELVQK
EILNSPEKAC
KMKGDYFRYF
```

`params-dir`: This directory will have multiple json files where each json file will have parameters for single prediction.
The `preprocess` will break down `example.json` into 2 small units (`0.json`, `1.json`).
```json
// file: 0.json
{
  "alleles": "HLA-A*02:01",
  "predictors": [
    {
      "type": "binding",
      "method": "netmhcpan_ba"
    }
  ],
  // This should point to the `inputs-dir` (i.e. inputs_units/tmppqgszjz_)
  "peptide_file_path": "/PATH/TO/CLI-TOOL-PROJECT-ROOT/inputs_units/tmppqgszjz_",
  "peptide_length_range": [
    10,
    10
  ]
}
```

```json
// file: 1.json
{
  "alleles": "HLA-A*01:01",
  "predictors": [
    {
      "type": "binding",
      "method": "netmhcpan_ba"
    }
  ],
  // This should point to the `inputs-dir` (i.e. inputs_units/tmppqgszjz_)
  "peptide_file_path": "/PATH/TO/CLI-TOOL-PROJECT-ROOT/inputs_units/tmppqgszjz_",
  "peptide_length_range": [
    10,
    10
  ]
}
```
`results`: This folder will hold a single file called `sequence_peptide_index.json`. The file should consist of the following:
```json
{
  "warnings": [],
  "results": [
    {
      "type": "peptide_table",
      "table_columns": [
        "sequence_number",
        "peptide",
        "start",
        "end",
        "length",
        "allele",
        "peptide_index"
      ],
      "table_data": [
        [
          1,
          "TMDKSELVQK",
          1,
          10,
          10,
          "HLA-A*02:01",
          1
        ],
        [
          2,
          "EILNSPEKAC",
          1,
          10,
          10,
          "HLA-A*02:01",
          2
        ],
        [
          3,
          "KMKGDYFRYF",
          1,
          10,
          10,
          "HLA-A*02:01",
          3
        ],
        [
          1,
          "TMDKSELVQK",
          1,
          10,
          10,
          "HLA-A*01:01",
          1
        ],
        [
          2,
          "EILNSPEKAC",
          1,
          10,
          10,
          "HLA-A*01:01",
          2
        ],
        [
          3,
          "KMKGDYFRYF",
          1,
          10,
          10,
          "HLA-A*01:01",
          3
        ]
      ]
    }
  ]
}
```

`job_descriptions.json`: This is a file that has a list of job descriptions where each job descriptions will have a command that needs to be run in order to create the resulting output.
```json
[
  {
    "shell_cmd": "/PATH/TO/CLI-TOOL-PROJECT-ROOT/src/run_test.py -j /PATH/TO/CLI-TOOL-PROJECT-ROOT/splitted_parameters/0.json -o /PATH/TO/CLI-TOOL-PROJECT-ROOT/results/0 -f json",
    "job_id": 0,
    "job_type": "prediction",
    "depends_on_job_ids": [],
    "expected_outputs": [
      "/PATH/TO/CLI-TOOL-PROJECT-ROOT/results/0.json"
    ]
  },
  {
    "shell_cmd": "/PATH/TO/CLI-TOOL-PROJECT-ROOT/src/run_test.py -j /PATH/TO/CLI-TOOL-PROJECT-ROOT/splitted_parameters/1.json -o /PATH/TO/CLI-TOOL-PROJECT-ROOT/results/1 -f json",
    "job_id": 1,
    "job_type": "prediction",
    "depends_on_job_ids": [],
    "expected_outputs": [
      "/PATH/TO/CLI-TOOL-PROJECT-ROOT/results/1.json"
    ]
  },
  {
    "shell_cmd": "/PATH/TO/CLI-TOOL-PROJECT-ROOT/src/run_test.py --postprocess --job-desc-file=/PATH/TO/CLI-TOOL-PROJECT-ROOT/job_descriptions.json --input-results-dir=/PATH/TO/CLI-TOOL-PROJECT-ROOT/results --postprocessed-results-dir=/PATH/TO/CLI-TOOL-PROJECT-ROOT/aggregate",
    "job_id": 2,
    "job_type": "aggregate",
    "depends_on_job_ids": [
      0,
      1
    ],
    "expected_outputs": [
      "/PATH/TO/CLI-TOOL-PROJECT-ROOT/aggregate/aggregated_result.json"
    ]
  }
]
```

Run each `shell_cmd` described in `job_descriptions.json` from top to bottom.
```bash
>> python /PATH/TO/CLI-TOOL-PROJECT-ROOT/src/run_test.py -j /PATH/TO/CLI-TOOL-PROJECT-ROOT/splitted_parameters/0.json -o /PATH/TO/CLI-TOOL-PROJECT-ROOT/results/0 -f json

>> python /PATH/TO/CLI-TOOL-PROJECT-ROOT/src/run_test.py -j /PATH/TO/CLI-TOOL-PROJECT-ROOT/splitted_parameters/1.json -o /PATH/TO/CLI-TOOL-PROJECT-ROOT/results/1 -f json

>> python /PATH/TO/CLI-TOOL-PROJECT-ROOT/src/run_test.py --postprocess --job-desc-file=/PATH/TO/CLI-TOOL-PROJECT-ROOT/job_descriptions.json --input-results-dir=/PATH/TO/CLI-TOOL-PROJECT-ROOT/results --postprocessed-results-dir=/PATH/TO/CLI-TOOL-PROJECT-ROOT/aggregate
```

Now, the full project structure should look something like this.
```
.
├── output-directory/
│   ├── predict-inputs/
│   │   ├── data/
│   │   └── params/
│   └── predict-outputs/
├── src/
│   ├── run_test.py
│   ├── preprocess.py
│   └── postprocess.py
├── examples.json
└── job_descriptions.json
```


Also, if the user chooses not to provide those parameters, then the code should create the filestructure for the user.
```bash
python src/run_test.py preprocess -j examples/example.json
```
The above command will create the below filestructure:
```
.
└── job_NX1bAC/
    ├── splitted_parameters/
    │   ├── 0.json
    │   ├── 1.json
    │   └── 2.json
    ├── results/
    └── job_descriptions.json
```


* @hkim, please add a description of what needs to happen in this step, including an example input JSON,
output split JSONs, job_description.json, etc. as well as snippets of code (or links to code) that accomplish 
this.



### Predict

In this step, a prediction is run using one of the JSON input files created in the
preprocessing step.  Output should include a JSON file of the results.  The developer
is free to add any other options to this subparser.  The only requirement is that
there is a way to run a JSON input through and receive a JSON output in the format specified
below.

* @hkim, please include an example of what the JSON format for output should look
like, with an example of multiple tables.

### Postprocess

Here, the individual results from the predictions are aggregated together into a single
JSON output.

* @hkim, please include an example of the aggregated output as well as a code snippet
or link to example code for aggregation.