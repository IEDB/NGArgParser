# NGArgumentParser

This is a standardized parser framework for CLI tools. This class will enforce certain properties or abstract methods to be implemented to properly create an Argument Parser class for other CLI tools.

## What's in NGArgumentParser?
The parser class inherits directly from `argparse.ArgumentParser`. Once it's instantiated, it will create an ArgumentParser class, and create 3 subparsers: 
1. `predict`
2. `preprocess`
3. `postprocess`

The `preprocess` subparser has the following options available:
```bash
>> python run_test.py preprocess -h
usage: run_test.py preprocess --json-input JSON_FILE 
                              [-h] [--params-dir PREPROCESS_PARAMETERS_DIR]
                              [--inputs-dir PREPROCESS_INPUTS_DIR] [--assume-valid]

Preprocess JSON input files into smaller units, if possible and create a job_descriptions.json file that
includes all commands to run the workflow

required parameters:
  --json-input JSON_FILE, -j JSON_FILE
                        JSON file containing input parameters.
  --output-dir OUTPUT_DIRECTORY
                        a directory under which output files will be placed

optional parameters:
  -h, --help            show this help message and exit
  --params-dir PREPROCESS_PARAMETERS_DIR
                        a directory to store preprocessed JSON input files
                        [ default: $OUTPUT_DIRECTORY/predict_inputs/params ]
  --inputs-dir PREPROCESS_INPUTS_DIR
                        a directory to store other, non-JSON inputs (e.g., fasta files)
                        [ default: $OUTPUT_DIRECTORY/predict-inputs/data ]
  --assume-valid        flag to indicate validation can be skipped
```

The `postprocess` subparser has the following options available:
```bash
>> python run_test.py postprocess -h
usage: run_test.py postprocess --input-results-dir POSTPROCESS_INPUT_DIR \
                               --postprocessed-results-dir POSTPROCESS_RESULT_DIR
                               [-h]

results from individual prediction jobs are aggregated

required parameters:
  --input-results-dir POSTPROCESS_INPUT_DIR
                        directory containing the result files to postprocess
  --postprocessed-results-dir POSTPROCESS_RESULT_DIR
                        a directory to contain the post-processed results

optional parameters:
  -h, --help            show this help message and exit

```

The `predict` subparser has no options available as the NGArgumentParser will only create the subparser and not add any options to it. It is the developer's duty to add arguments and customize the `predict` subparser.  More details on filling in the logic for each run mode is below.

Let's now try to create a child class that builds off of this parser framework.


> __NOTE__ :
> 
> These are subject to change in the future.


# Getting Started
Install the framework with the following command:
```bash
pip install .
```

The `cli` command should be available. Let's create an example app that comes with this framework for user's reference.
```bash
cli g example
```
This will create an example app called `aa-counter`. Similarly, users should be able to create an app.
```bash
# cli g <app-name> or cli.py g <app-name>
cli g myapp
```

The `myapp` folder should appear with `README` file and `/src` directory.

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

The logic for each of the run modes (`predict`, `preprocess`, `postprocess`) needs
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

An output directory structure is created, as below:

```bash
.
└── output-directory/
    ├── predict-inputs/
    │   ├── data/
    │   └── params/
    └── predict-outputs/
```

If `--params-dir` and `--inputs-dir` are undefined, they will default to a directory
underneath `output-directory/predict-inputs`, as shown above.

`inputs-dir`: This directory will hold a temporary file that has all the inputs.
```python
# file: tmp_pqgszjz_
TMDKSELVQK
EILNSPEKAC
KMKGDYFRYF
```

`params-dir`: This directory will have multiple json files where each json file will have parameters for single prediction.
The `preprocess` will break down `example.json` into 2 small units (`0.json`, `1.json`) by splitting the alleles, and
place them under the `params-dir`.
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
  // This should point to the `inputs-dir` (i.e. output-directory/predict-inputs/data/tmppqgszjz_)
  "peptide_file_path": "/PATH/TO/CLI-TOOL-PROJECT-ROOT/output-directory/predict-inputs/data/tmppqgszjz_",
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
  // This should point to the `inputs-dir` (i.e. output-directory/predict-inputs/data/tmppqgszjz_)
  "peptide_file_path": "/PATH/TO/CLI-TOOL-PROJECT-ROOT/output-directory/predict-inputs/data/tmppqgszjz_",
  "peptide_length_range": [
    10,
    10
  ]
}
```

The preprocessing step should also include creation of `job_descriptions.json` file. 

`job_descriptions.json`: This is a file that has a list of job descriptions where each job descriptions will have a command that needs to be run in order to create the resulting output.

The function to create this file may be implmented from scratch, or the `NGArgumentParser` can create one given the `params-dir`.

Following snippet should create the `job_descriptions` file.
```python
# child argument parser that extens from NGArgumentParser
parser = ChildArgumentParser()
.
.
.
# steps to split the files
.
.
.
# Provide path to the "params-dir"
# By default, it creates the file in the app root directory.
parser.create_job_descriptions_file(param_dir)
```
At this point, the file structure should look like the following:
```bash
.
├── output-directory/
│   ├── predict-inputs/
│   │   ├── data/
│   │   │   └── tmp_pqgszjz_
│   │   └── params/
│   │       ├── 0.json
│   │       └── 1.json
│   └── predict-outputs/
└── job_descriptions.json
```
Inside the `job_descriptions` file, it should look like the following:
```json
// file: job_descriptions.json
[
    {
        "shell_cmd": "/ABSOLUTE/PATH/TO/EXECUTABLE_FILE predict -j /ABSOLUTE/PATH/TO/APP/ROOT/output-directory/predict-inputs/params/0.json -o /ABSOLUTE/PATH/TO/APP/ROOT/output-directory/predict-outputs/result.0 -f json",
        "job_id": "0",
        "job_type": "prediction",
        "depends_on_job_ids": [],
        "expected_outputs": [
            "/ABSOLUTE/PATH/TO/APP/ROOT/output-directory/predict-outputs/result.0.json"
        ]
    },
    {
        "shell_cmd": "/ABSOLUTE/PATH/TO/EXECUTABLE_FILE predict -j /ABSOLUTE/PATH/TO/APP/ROOT/output-directory/predict-inputs/params/1.json -o /ABSOLUTE/PATH/TO/APP/ROOT/output-directory/predict-outputs/result.1 -f json",
        "job_id": "1",
        "job_type": "prediction",
        "depends_on_job_ids": [],
        "expected_outputs": [
            "/ABSOLUTE/PATH/TO/APP/ROOT/output-directory/predict-outputs/result.1.json"
        ]
    },
    {
        "shell_cmd": "/ABSOLUTE/PATH/TO/EXECUTABLE_FILE postprocess --job-desc-file=/ABSOLUTE/PATH/TO/APP/ROOT/job_descriptions.json -o /ABSOLUTE/PATH/TO/APP/ROOT/output-directory/final-result -f json",
        "job_id": "2",
        "job_type": "postprocess",
        "depends_on_job_ids": [
            "0",
            "1",
        ],
        "expected_outputs": [
            "/ABSOLUTE/PATH/TO/APP/ROOT/output-directory/final-result"
        ]
    }
]
```

From the above example, note that the first two are individual predictions, whereas the last command is a postprocessing step
that gathers all the result files.

### Predict

In this step, a prediction is run using one of the JSON input files created in the
preprocessing step.  Output should include a JSON file of the results.  The developer
is free to add any other options to this subparser.  The only requirement is that
there is a way to run a JSON input through and receive a JSON output in the format specified
below.

```bash
>> python /ABSOLUTE/PATH/TO/EXECUTABLE_FILE predict \
-j /PATH/TO/JSON-INPUT-FILE \
-o /PATH/TO/PREDICTION-RESULT-FILE \
-f json
```

Going back the example, running the first two `predict` commands in order will create result file, and save it under `predict-outputs` folder.
```bash
>> python /ABSOLUTE/PATH/TO/EXECUTABLE_FILE predict -j /ABSOLUTE/PATH/TO/APP/ROOT/output-directory/predict-inputs/params/0.json -o /ABSOLUTE/PATH/TO/APP/ROOT/output-directory/predict-outputs/result.0 -f json

>> python /ABSOLUTE/PATH/TO/EXECUTABLE_FILE predict -j /ABSOLUTE/PATH/TO/APP/ROOT/output-directory/predict-inputs/params/1.json -o /ABSOLUTE/PATH/TO/APP/ROOT/output-directory/predict-outputs/result.1 -f json
```

The file structure should look like the following:
```bash
.
├── output-directory/
│   ├── predict-inputs/
│   │   ├── data/
│   │   │   └── tmp_pqgszjz_
│   │   └── params/
│   │       ├── 0.json
│   │       └── 1.json
│   └── predict-outputs/
│       ├── result.0.json
│       └── result.1.json
└── job_descriptions.json
```

### Postprocess
The last command of the `job_descriptions.json` will look like this, which aggregates all the results from the
individual predictions into a single file.
```bash
>> /ABSOLUTE/PATH/TO/EXECUTABLE_FILE postprocess --job-desc-file=/ABSOLUTE/PATH/TO/APP/ROOT/job_descriptions.json -o /ABSOLUTE/PATH/TO/APP/ROOT/output-directory/final-result -f json
```
Using the above example, the last command should take the two individual prediction result files 
(`result.0.json`, `result.1.json`) and combine them into a single file.

This step will create a single file called  `final-result.json`, which will look like the following:
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


Contributing a standalone
-------------------------
The IEDB team welcomes collaboration in developing new features for our platform. External developers have several opportunities to contribute tools, as outlined below. Contributions from external developers will help accelerate the implementation of tools on the IEDB next-gen tools website. 
> **NOTE:**\
All tools must be pre-approved by the IEDB team before development is started. Submitting a standalone tool to IEDB does not guarantee its integration into the platform.

* 1. Hand off source code or binary (low effort, longest time to completion)
* 2. Create a package with NGArugmentParser and implement the "predict" method (medium effort, medium time to completion)
* 3. Create a package with NGArgumentParser and implement all methods (high effort, shortest time to completion)

> **NOTE:**\
> One of the requirements for the "predict" command is the option to generate output in JSON format that adheres to the NG tools output format specifications. Examples of this format can be found in the 'examples' directory and more details on can be found [here](https://nextgen-tools.iedb.org/docs/).

Once the CLI tool is able to run basic prediction given a JSON file, the next step is to implement preprocessing.