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