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