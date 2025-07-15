

# NGArgumentParser Framework Documentation

## Overview

The NGArgumentParser Framework provides a structured approach to building command-line applications with standardized argument parsing, dependency management, and workflow orchestration. After installation, the framework exposes a `cli` command that facilitates project generation and configuration.

The framework features a unique `SubparserWrapper` class that simplifies customization of help text and descriptions, comprehensive dependency management with automatic cleanup, and a robust build system for packaging and distribution.

## Installation

Install the framework with the following command:
```bash
pip install .
```

## Key Features

### 1. Enhanced Customization
- **SubparserWrapper**: Simplified syntax for modifying help text and descriptions
- **Argument Grouping**: Organize arguments into logical groups for better help display
- **Formatter Classes**: Support for multiline descriptions with preserved formatting
- **Argument Removal**: Easy removal of built-in arguments when not needed

### 2. Robust Dependency Management
- **Automatic Detection**: Dynamically detects dependencies from `paths.py`
- **Smart Cleanup**: Removes old dependencies and shell scripts automatically
- **Cross-Platform**: Works on Linux, macOS, and Windows (WSL)
- **Environment Isolation**: Creates dedicated environment setup scripts

### 3. Comprehensive Build System
- **Cross-Platform Building**: Handles Linux/macOS differences automatically
- **Dependency Packaging**: Structured approach to including external dependencies
- **Version Management**: Automatic version substitution in documentation
- **Clean Distribution**: Excludes development files from distribution packages

### 4. Complete Documentation
- **CUSTOMIZATION_GUIDE.md**: Detailed customization instructions with examples
- **BUILD_SYSTEM.md**: Complete build system documentation
- **Template Files**: Pre-configured templates for rapid development

## Command-Line Interface

The framework provides the following commands:

```bash
cli --help
```

```
usage: cli [-h] {generate,g,setup-paths} ...

NG Argument Parser Framework

positional arguments:
  {generate,g,setup-paths}
    generate (g)        Create a new custom app project structure
    setup-paths         Setup or update paths.py with tool dependencies

options:
  -h, --help            show this help message and exit
```

## Project Generation

### Creating a New Application

To create a new application, use the `generate` command with your desired application name:

```bash
cli generate phbr  # or 'cli g phbr'
```

You can also create an example app for reference:
```bash
cli g example
```
This will create an example app called `aa-counter`.

This command will:
1. Generate the complete project structure
2. Create necessary configuration files
3. Prompt for dependency configuration

During project creation, you will be prompted to specify dependencies:

```
Created 'phbr' project structure successfully.
=== Setting up paths.py configuration ===

Enter the names of all dependencies that the current app depends on.
Press Enter after each dependency name.
Press Enter on an empty line when you're done.

Dependency name (or press Enter to finish):
```

If your application has no dependencies or you wish to configure them later, simply press Enter to skip this step.

### Generated Project Structure

The framework creates a standardized project structure:

```
project-root/
├── Makefile
├── README
├── build.sh
├── configure
├── do-not-distribute.txt
├── license-LJI.txt
├── paths.py
└── src/
    ├── NGArgumentParser.py
    ├── PhbrArgumentParser.py
    ├── configure.py
    ├── core_validators.py
    ├── postprocess.py
    ├── preprocess.py
    ├── run_phbr.py
    └── validators.py
```

#### File Descriptions

| File | Purpose |
|------|---------|
| `Makefile` | Build orchestration with `build` and `clean` targets |
| `README` | Usage instructions and documentation |
| `build.sh` | Build script for packaging and distribution |
| `configure` | Executable that runs the configuration process |
| `do-not-distribute.txt` | File exclusion list for distribution packages |
| `license-LJI.txt` | Application license file |
| `paths.py` | Dependency configuration and path management |
| `NGArgumentParser.py` | Core argument parser (framework-managed) |
| `PhbrArgumentParser.py` | Application-specific argument parser |
| `configure.py` | Configuration script for dependency setup |
| `core_validators.py` | Core validation functions (framework-managed) |
| `postprocess.py` | Result aggregation and post-processing logic |
| `preprocess.py` | Input processing and job preparation |
| `run_phbr.py` | Main application entry point |
| `validators.py` | Custom validation functions |

## NGArgumentParser Core Features

### What's in NGArgumentParser?

The parser class inherits directly from `argparse.ArgumentParser`. Once it's instantiated, it will create an ArgumentParser class, and create 3 subparsers:
1. `predict`
2. `preprocess`
3. `postprocess`

### SubparserWrapper Enhancement

The framework includes a `SubparserWrapper` class that enables easy modification of help text and descriptions:

```python
# Simple syntax to change help text
self.parser_preprocess.help = 'Custom preprocessing description'
self.parser_postprocess.help = 'Custom postprocessing description'

# Modify descriptions that appear in subcommand help
self.parser_preprocess.description = 'Detailed preprocessing instructions'
```

This wrapper automatically updates the correct location in the argument parser structure, eliminating the need for complex manual updates.

### Preprocess Subparser

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

### Postprocess Subparser

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

### Predict Subparser

The `predict` subparser has no options available as the NGArgumentParser will only create the subparser and not add any options to it. It is the developer's duty to add arguments and customize the `predict` subparser.

The predict subparser supports customizable formatter classes for proper handling of multiline descriptions:

```python
# Use RawDescriptionHelpFormatter to preserve line breaks
pred_parser = self.add_predict_subparser(
    help='Run prediction algorithms',
    description=textwrap.dedent('''
        Prediction stage executes the core analysis:
        - Loads preprocessed data
        - Runs machine learning models
        - Generates individual predictions
    '''),
    formatter_class=argparse.RawDescriptionHelpFormatter
)
```

Available formatter classes include:
- `argparse.HelpFormatter` (default): Wraps text, removes line breaks
- `argparse.RawDescriptionHelpFormatter`: Preserves line breaks in descriptions  
- `argparse.RawTextHelpFormatter`: Preserves all line breaks
- `argparse.ArgumentDefaultsHelpFormatter`: Adds defaults to help text

## Creating a Child Class

### Basic Implementation

```python
class ExampleArgumentParser(NGArgumentParser):
    def __init__(self):
        super().__init__()
```

To fully create an Argument Parser class using `NGArgumentParser`, a couple of elements must be implemented:

1. Customizing program's detail (e.g. `prog`, `usage`, `description`, `epilog`)
2. Add subparser for prediction
3. Add tool-specific parameters to the prediction subparser

### 1. Customizing Program's Detail

```python
class ExampleArgumentParser(NGArgumentParser):
    def __init__(self):
        super().__init__()

        '''
        It is the developer's responsibility to customize these parameters.
        At the minimum, the below parameters should be customized before deploying.

        Developers can choose to further customize other parameters of ArgumentParser()
        from here:
        https://docs.python.org/3/library/argparse.html#argparse.ArgumentParser
        '''
        # Set program details by setting params, such as
        # prog, usage, description, epilog, etc.
        # -----------------------------------------------------
        self.description=textwrap.dedent(
        '''\
            This is an example description.
        '''    
        )
```

The `self.description` will update the ArgumentParser's description from child Argument Parser.

By doing so, when `--help` is passed, the program's description will be changed.

Please refer to the [argparse document](https://docs.python.org/3/library/argparse.html#argparse.ArgumentParser) to learn about other parameters.

### 2. Add Subparser for Prediction

The `NGArgumentParser` already handles creating the `subparser` inside the `add_predict_subparser()`.

```python
class ExampleArgumentParser(NGArgumentParser):
    def __init__(self):
        .
        .
        pred_parser = self.add_predict_subparser(
            help='Perform individual prediction.',
            description='This is where the users can perform individual predictions.',
            formatter_class=argparse.RawDescriptionHelpFormatter  # Optional: for multiline descriptions
        )
```

The `add_predict_subparser()` will return a `SubparserWrapper` that allows easy modification of help text:

```python
# You can also modify help text after creation
pred_parser.help = 'Updated prediction description'
```

### 3. Add Tool-Specific Parameters to the Prediction Subparser

Since we can access the prediction subparser from the child class, we can add any tool-specific commands to it.

```python
class ExampleArgumentParser(NGArgumentParser):
    def __init__(self):
        .
        .
        pred_parser = self.add_predict_subparser(
            help='Perform individual prediction.',
            description='This is where the users can perform individual predictions.'
        )

        # Add tool-specific params 
        # -----------------------------------------------------
        pred_parser.add_argument("--output-prefix", "-o",
                                 dest="output_prefix",
                                 help="prediction result output prefix.",
                                 metavar="OUTPUT_PREFIX",
                                 group="output options")  # Optional: organize into groups
        pred_parser.add_argument("--output-format", "-f",
                                 dest="output_format",
                                 default="tsv",
                                 help="prediction result output format (Default=tsv)",
                                 metavar="OUTPUT_FORMAT",
                                 group="output options")  # Optional: organize into groups
```

### Usage in Main Application

In the executable file, the child class should be instantiated, and logic to handle the cli-tool should be there. Let's create the executable file called `run_test.py`.

```python
def main():
    parser = ClusterArgumentParser()
    args = parser.parse_args()

    if args.subcommand == 'predict':
        # ADD CODE LOGIC HERE.
        pass

    if args.subcommand == 'preprocess':
        # ADD CODE LOGIC TO SPLIT INPUTS INSIDE PREPROCESS.PY
        preprocess.run(**vars(args))

    if args.subcommand == 'postprocess':
        # ADD CODE LOGIC TO COMBINE RESULTS INSIDE POSTPROCESS.PY
        postprocess.run(**vars(args))

if __name__=='__main__':
    main()
```

## Dependency Management

### Default Configuration

Upon project creation with no dependency, an empty `paths.py` will be created.

### Adding / Removing Dependencies

Dependencies can be added or removed using the `setup-paths` command:

```bash
# cli setup-paths <path-to-paths.py-file>
cli setup-paths paths.py
```
This will prompt the following:
```
=== Paths file exists at paths.py ===

No existing dependencies found.

What would you like to do?
1. Add new dependencies
2. Remove existing dependencies
3. Cancel
Choose an option (1-3):
```

This updates `paths.py` with the necessary configuration variables:<br>
(Example below is when `tcell-class-i` is added as a dependency.)
```python
''' [ tcell-class-i ] '''
# Path to the tcell-class-i (required)
tcell_class_i_path = None

# Path to the tcell-class-i virtual environment (optional)
tcell_class_i_venv = None

# Name of the environment module to be activated (optional)
tcell_class_i_module = None

# Library path configuration for environment isolation (optional)
tcell_class_i_lib_path = None
```

Using the same command, we can remove the `tcell-class-i` dependency as well. Use the same command to view the prompt, and follow the instructions to remove the dependency.

### Configuration Requirements

After adding dependencies, you must:

1. **Configure required paths** - Set the `{dependency}_path` variable to the tool's installation directory
2. **Run the configuration script** - Execute `./configure` to generate environment setup scripts

The configuration process creates environment setup scripts (e.g., `setup_tcell_class_i_env.sh`) that manage the execution environment for each dependency.

**Enhanced Configuration Behavior:**
- The configuration script **always regenerates** the `.env` file based on current `paths.py` content
- **Automatic cleanup**: When dependencies are removed from `paths.py`, the corresponding environment variables are automatically removed from `.env`
- **Shell script cleanup**: Old shell scripts for removed dependencies are automatically deleted
- **APP_ROOT preservation**: The `APP_ROOT` environment variable is always maintained in `.env`

```bash
# Example output when removing a dependency
./configure
* .env file updated
* Removed shell script for 'old_dependency' (no longer in paths.py)
* Detected 2 dependency tools: tool1, tool2
* Shell script for 'tool1' created at 'setup_tool1_env.sh'
* Shell script for 'tool2' created at 'setup_tool2_env.sh'
```

### Recreating Configuration

If `paths.py` is lost or corrupted, recreate it using:

```bash
cli setup-paths <path-to-paths.py-file>
```

## Application Development

### Custom Validation Implementation

Implement custom validation logic in `validators.py`. While not mandatory, this approach promotes code organization and maintainability.

### Argument Parser Customization

Modify `{app_name}ArgumentParser.py` to:
- Add application-specific command-line flags
- Customize help text and descriptions using the `SubparserWrapper` functionality
- Configure the `predict` subparser behavior
- Set formatter classes for multiline descriptions

**For detailed customization guidance, see `CUSTOMIZATION_GUIDE.md`** - a comprehensive guide covering:
- Help text and description modification
- Argument grouping strategies
- Argument removal techniques
- Complete working examples

### Main Application Logic

Implement your core application logic in `run_{app_name}.py`. This file serves as the main entry point and should contain the prediction workflow.

### Build System

The framework includes a robust build system for packaging and distribution:

```bash
# Build the application
make build

# Clean build artifacts
make clean
```

**For complete build system documentation, see `BUILD_SYSTEM.md`** - covering:
- Build system architecture and component relationships
- Cross-platform compatibility features
- Dependency management in build process
- Customization and troubleshooting guides

## Workflow Operations

### Example Input File

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

### Preprocessing Workflow

The preprocessing workflow prepares input data and creates job descriptions for batch processing.

#### Setup Process

1. **Create output directory**:
   ```bash
   mkdir output-directory
   ```

2. **Execute preprocessing**:
   ```bash
   python src/run_{app_name}.py preprocess -j input.json -o output-directory
   ```

#### Generated Structure

In this step, a JSON input file is validated and, if possible, broken down into smaller JSON files that can be passed to the `predict` step. Additionally, a `job_descriptions.json` files is created that lists each of the commands that need to be executed in order to complete the prediction as well as any expected output files.

An output directory structure is created, as below:

```bash
.
└── output-directory/
    ├── predict-inputs/
    │   ├── data/
    │   └── params/
    └── predict-outputs/
```

#### Directory Functions

| Directory | Purpose |
|-----------|---------|
| `inputs-dir` | This directory will hold a temporary file that has all the inputs |
| `params-dir` | This directory will have multiple json files where each json file will have parameters for single prediction |
| `predict-outputs/` | Raw prediction outputs |

##### Example: inputs-dir

This directory will hold a temporary file that has all the inputs.
```python
# file: tmp_pqgszjz_
TMDKSELVQK
EILNSPEKAC
KMKGDYFRYF
```

##### Example: params-dir

This directory will have multiple json files where each json file will have parameters for single prediction.
The `preprocess` will break down `example.json` into 2 small units (`0.json`, `1.json`) by splitting the alleles, and place them under the `params-dir`.

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

### Job Description File

The preprocessing workflow generates `job_descriptions.json` at the output directory root. This file orchestrates the execution of dependent tools and manages the workflow sequence.

The preprocessing step should also include creation of `job_descriptions.json` file.

`job_descriptions.json`: This file contains a list of job descriptions, each with a command that needs to be run to create the resulting output.

* The preprocessing step should include logic to create `job_descriptions.json` file.

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

From the above example, note that the first two are individual predictions, whereas the last command is a postprocessing step that gathers all the result files.

### Prediction Workflow

Execute predictions using the standardized command interface:

```bash
python src/run_{app_name}.py predict -j input.json -o output-file
```

In this step, a prediction is run using one of the JSON input files created in the preprocessing step. Output should include a JSON file of the results. The developer is free to add any other options to this subparser. The only requirement is that there is a way to run a JSON input through and receive a JSON output in the format specified below.

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

### Postprocessing Workflow

The last command of the `job_descriptions.json` will look like this, which aggregates all the results from the individual predictions into a single file.

```bash
>> /ABSOLUTE/PATH/TO/EXECUTABLE_FILE postprocess --job-desc-file=/ABSOLUTE/PATH/TO/APP/ROOT/job_descriptions.json -o /ABSOLUTE/PATH/TO/APP/ROOT/output-directory/final-result -f json
```

Using the above example, the last command should take the two individual prediction result files (`result.0.json`, `result.1.json`) and combine them into a single file.

This step will create a single file called `final-result.json`, which will look like the following:

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

## Notes

- These specifications are subject to change in the future.
- The logic for each of the run modes (`predict`, `preprocess`, `postprocess`) needs to be fleshed out separately. We aim to include as much of the common logic as possible in future releases of NGArgumentParser.
- For the most current information, consult the documentation files (`CUSTOMIZATION_GUIDE.md`, `BUILD_SYSTEM.md`) and template files in your project.