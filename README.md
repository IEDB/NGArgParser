# NGArgumentParser Framework

A standardized parser framework for CLI tools that provides structured argument parsing, dependency management, and workflow orchestration. The framework enforces consistent patterns for building command-line applications with standardized preprocessing, prediction, and postprocessing workflows.

## Overview

The NGArgumentParser Framework provides a structured approach to building command-line applications. The parser class inherits directly from `argparse.ArgumentParser` and automatically creates three standardized subparsers:

1. **`preprocess`** - Processes input data and creates job descriptions for batch processing
2. **`predict`** - Executes core prediction logic (customizable by developers)
3. **`postprocess`** - Aggregates results from individual prediction jobs

## Installation

Install the framework with the following command:

```bash
pip install .
```

After installation, the `cli` command becomes available for project generation and configuration.

## Quick Start

### Creating a New Application

Generate a new application project structure:

```bash
cli generate myapp    # or 'cli g myapp'
```

This creates a complete project structure with all necessary files and prompts for dependency configuration.

### Running the Example

Create the built-in example application:

```bash
cli g example
```

This generates an example app called `aa-counter` for reference.

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

## Project Structure

The framework generates a standardized project structure:

```
project-root/
├── README
├── configure
├── license-LJI.txt
├── paths.py
└── src/
    ├── NGArgumentParser.py
    ├── MyappArgumentParser.py
    ├── configure.py
    ├── core_validators.py
    ├── postprocess.py
    ├── preprocess.py
    ├── run_myapp.py
    └── validators.py
```

### Key Files

- **`README`** - Usage instructions and documentation
- **`configure`** - Executable configuration script
- **`paths.py`** - Dependency configuration and path management
- **`MyappArgumentParser.py`** - Application-specific argument parser
- **`run_myapp.py`** - Main application entry point
- **`preprocess.py`** - Input processing and job preparation logic
- **`postprocess.py`** - Result aggregation and post-processing logic
- **`validators.py`** - Custom validation functions

## Subparser Details

### Preprocess Subparser

The `preprocess` subparser handles input preparation and job creation:

```bash
python run_myapp.py preprocess -h
```

```
usage: run_myapp.py preprocess [-h] [--json-input JSON_FILE] [--params-dir PREPROCESS_PARAMETERS_DIR]
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

### Postprocess Subparser

The `postprocess` subparser aggregates results from individual prediction jobs:

```bash
python run_myapp.py postprocess -h
```

```
usage: run_myapp.py postprocess [-h] [--input-results-dir POSTPROCESS_INPUT_DIR]
                               [--postprocessed-results-dir POSTPROCESS_RESULT_DIR]

results from individual prediction jobs are aggregated

options:
  -h, --help            show this help message and exit
  --input-results-dir POSTPROCESS_INPUT_DIR
                        directory containing the result files to postprocess
  --postprocessed-results-dir POSTPROCESS_RESULT_DIR
                        a directory to contain the post-processed results
```

### Predict Subparser

The `predict` subparser is created by the framework but contains no default options. Developers must customize this subparser by adding arguments and implementing the prediction logic in their application-specific parser class.

## Dependency Management

### Adding Dependencies

Dependencies can be configured during project creation or added later using:

```bash
cli setup-paths paths.py
```

This updates `paths.py` with the necessary configuration variables for each dependency:

```python
from pathlib import Path

# Get the absolute path to the project root directory
APP_ROOT = Path(__file__).parent.absolute()

''' [ dependency-name ] '''
# Path to the dependency (required)
dependency_name_path = None

# Path to the dependency virtual environment (optional)
dependency_name_venv = None

# Name of the environment module to be activated (optional)
dependency_name_module = None

# Library path configuration for environment isolation (optional)
dependency_name_lib_path = None
```

### Configuration Process

After adding dependencies:

1. **Configure required paths** - Set the `{dependency}_path` variable to the tool's installation directory
2. **Run the configuration script** - Execute `./configure` to generate environment setup scripts

The configuration process creates environment setup scripts and a `.env` file for environment variables.

## Workflow Operations

### Basic Prediction

Execute predictions using the standardized command interface:

```bash
python src/run_myapp.py predict -j input.json -o output-file
```

### Preprocessing Workflow

1. **Create output directory**:
   ```bash
   mkdir output-directory
   ```

2. **Execute preprocessing**:
   ```bash
   python src/run_myapp.py preprocess -j input.json -o output-directory
   ```

#### Generated Structure

The preprocessing workflow creates a structured output directory:

```
output-directory/
├── dependency-app-1/
│   ├── aggregate/
│   ├── predict-inputs/
│   │   ├── data/
│   │   └── params/
│   ├── predict-outputs/
│   └── results/
├── dependency-app-2/
│   └── [similar structure]
└── job_descriptions.json
```

The `job_descriptions.json` file orchestrates the execution of dependent tools and manages the workflow sequence.

## Development Guidelines

### Custom Implementation Requirements

Developers must implement:

1. **Prediction Logic** - Core application functionality in `run_myapp.py`
2. **Predict Subparser** - Add arguments and customize the predict subparser in `MyappArgumentParser.py`
3. **Preprocessing Logic** - Input processing and job preparation in `preprocess.py`
4. **Postprocessing Logic** - Result aggregation in `postprocess.py`
5. **Custom Validation** - Application-specific validation in `validators.py`

### Output Format Requirements

The "predict" command must generate output in JSON format that adheres to the NG tools output format specifications. Examples can be found in the 'examples' directory and more details at [nextgen-tools.iedb.org/docs/](https://nextgen-tools.iedb.org/docs/).

## Contributing to IEDB

The IEDB team welcomes collaboration in developing new features. External developers can contribute tools with varying levels of effort:

1. **Hand off source code or binary** (low effort, longest time to completion)
2. **Create a package with NGArgumentParser and implement the "predict" method** (medium effort, medium time to completion)
3. **Create a package with NGArgumentParser and implement all methods** (high effort, shortest time to completion)

> **NOTE**: All tools must be pre-approved by the IEDB team before development begins. Submission does not guarantee integration into the platform.

## Notes

- The framework enforces certain properties and abstract methods for proper ArgumentParser class implementation
- All specifications are subject to change in future versions
- Framework-managed files (`NGArgumentParser.py`, `core_validators.py`) should not be modified directly

<!-- Contributing a standalone
-------------------------
The IEDB team welcomes collaboration in developing new features for our platform. External developers have several opportunities to contribute tools, as outlined below. Contributions from external developers will help accelerate the implementation of tools on the IEDB next-gen tools website. 
> **NOTE:**\
All tools must be pre-approved by the IEDB team before development is started. Submitting a standalone tool to IEDB does not guarantee its integration into the platform.

* 1. Hand off source code or binary (low effort, longest time to completion)
* 2. Create a package with NGArugmentParser and implement the "predict" method (medium effort, medium time to completion)
* 3. Create a package with NGArgumentParser and implement all methods (high effort, shortest time to completion)

> **NOTE:**\
> One of the requirements for the "predict" command is the option to generate output in JSON format that adheres to the NG tools output format specifications. Examples of this format can be found in the 'examples' directory and more details on can be found [here](https://nextgen-tools.iedb.org/docs/).

Once the CLI tool is able to run basic prediction given a JSON file, the next step is to implement preprocessing. -->