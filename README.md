

# NGArgumentParser Framework Documentation

## Overview

The NGArgumentParser Framework provides a structured approach to building command-line applications with standardized argument parsing, dependency management, and workflow orchestration. After installation, the framework exposes a `cli` command that facilitates project generation, configuration, and build management.

The framework features a unique `SubparserWrapper` class that simplifies customization of help text and descriptions, comprehensive argument grouping and validation systems, automatic dependency management with cleanup, and a robust build system for packaging and distribution.

## Installation

Install the framework with the following command:
```bash
pip install .
```

## Key Features

### 1. Enhanced Argument Parser
- **SubparserWrapper**: Simplified syntax for modifying help text and descriptions dynamically
- **Argument Grouping**: Organize arguments into logical groups for better help display  
- **Formatter Classes**: Support for multiline descriptions with preserved formatting
- **Argument Updates**: Easy modification of existing arguments with new properties
- **Automatic Validation**: Built-in validation system with extensible custom validators

### 2. Robust Dependency Management
- **Automatic Detection**: Dynamically detects dependencies from `paths.py`
- **Smart Cleanup**: Removes old dependencies and shell scripts automatically
- **Cross-Platform**: Works on Linux, macOS, and Windows (WSL)
- **Environment Isolation**: Creates dedicated environment setup scripts

### 3. Comprehensive Build System
- **Cross-Platform Building**: Handles Linux/macOS differences automatically
- **Dependency Packaging**: Structured approach to including external dependencies
- **Clean Distribution**: Excludes development files from distribution packages
- **Makefile Integration**: Complete build automation with `build` and `clean` targets

### 4. Template-Based Project Generation
- **Structured Layout**: Separates core framework files from user-modifiable code
- **Example Applications**: Pre-built example projects for rapid development
- **Variable Substitution**: Automatic replacement of placeholders in generated files
- **Executable Scripts**: Properly configured permissions for all scripts

## Command-Line Interface

The framework provides the following commands:

```bash
cli --help
```

```
usage: cli [-h] {generate,g,config-paths,c,build,clean} ...

NG Argument Parser Framework

positional arguments:
  {generate,g,config-paths,c,build,clean}
    generate (g)        Create a new custom app project structure
    config-paths (c)    Configure paths.py with tool dependencies in current directory
    build               Build the project
    clean               Clean the project

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
This will create an example app called `aa-counter` with a complete working implementation.

### Generated Project Structure

The framework creates a standardized project structure:

```
project-root/
├── configure                    # Main configuration executable
├── requirements.txt             # Project dependencies
├── README                       # Usage instructions
├── license-LJI.txt             # Application license
├── src/                        # Source code directory
│   ├── core/                   # Framework core files (protected)
│   │   ├── NGArgumentParser.py # Core argument parser
│   │   └── core_validators.py  # Core validation functions
│   ├── configure.py            # Configuration script
│   ├── preprocess.py           # Input processing logic
│   ├── postprocess.py          # Result aggregation logic  
│   ├── validators.py           # Custom validation functions
│   ├── run_{app_name}.py       # Main application entry point
│   └── {AppName}ArgumentParser.py # Application-specific parser
└── scripts/                    # Build and deployment scripts
    ├── Makefile                # Build orchestration
    ├── build.sh                # Build script
    └── do-not-distribute.txt   # File exclusion list
```

#### File Descriptions

| File/Directory | Purpose |
|----------------|---------|
| `configure` | Main executable that runs the configuration process |
| `requirements.txt` | Python dependencies for the project |
| `src/core/` | Framework core files (managed by framework, do not modify) |
| `src/configure.py` | Configuration script for dependency setup |
| `src/{AppName}ArgumentParser.py` | Application-specific argument parser |
| `src/run_{app_name}.py` | Main application entry point |
| `src/preprocess.py` | Input processing and job preparation |
| `src/postprocess.py` | Result aggregation and post-processing logic |
| `src/validators.py` | Custom validation functions |
| `scripts/Makefile` | Build orchestration with `build` and `clean` targets |
| `scripts/build.sh` | Build script for packaging and distribution |
| `scripts/do-not-distribute.txt` | File exclusion list for distribution packages |

## NGArgumentParser Core Features

### Built-in Subparsers

The NGArgumentParser class automatically creates three subparsers:
1. `preprocess` - For input processing and job preparation
2. `predict` - For core prediction/analysis (customizable by developer)  
3. `postprocess` - For result aggregation

### SubparserWrapper Enhancement

The framework includes a `SubparserWrapper` class that enables easy modification of help text and descriptions:

```python
# Simple syntax to change help text
self.parser_preprocess.help = 'Custom preprocessing description'
self.parser_postprocess.help = 'Custom postprocessing description'

# Modify descriptions that appear in subcommand help
self.parser_preprocess.description = 'Detailed preprocessing instructions'
```

### Argument Grouping System

Arguments can be organized into logical groups for better help display:

```python
self.parser_predict.add_argument("--output-prefix", "-o",
                        dest="output_prefix",
                        help="prediction result output prefix.",
                        group="output options")  # Groups related arguments
```

### Preprocess Subparser

The `preprocess` subparser includes comprehensive built-in options:

```bash
usage: run_app.py preprocess --input-json JSON_FILE --output-dir OUTPUT_DIR
                             [-h] [--params-dir PREPROCESS_PARAMETERS_DIR]
                             [--inputs-dir PREPROCESS_INPUTS_DIR] [--assume-valid]

required parameters:
  --input-json JSON_FILE, -j JSON_FILE
                        JSON file containing input parameters.
  --output-dir OUTPUT_DIR, -o OUTPUT_DIR
                        prediction result output directory.

optional parameters:
  -h, --help            show this help message and exit
  --params-dir PREPROCESS_PARAMETERS_DIR
                        a directory to store preprocessed JSON input files
                        (default: $OUTPUT_DIR/predict-inputs/params)
  --inputs-dir PREPROCESS_INPUTS_DIR
                        a directory to store other, non-JSON inputs (e.g., fasta files)
                        (default: $OUTPUT_DIR/predict-inputs/data)
  --assume-valid        flag to indicate validation can be skipped
```

### Postprocess Subparser

The `postprocess` subparser supports multiple input methods:

```bash
usage: run_app.py postprocess [-h] [--job-desc-file JOB_DESC_FILE | --input-results-dir POSTPROCESS_INPUT_DIR]
                              [--postprocessed-results-dir POSTPROCESS_RESULT_DIR]
                              [--output-prefix OUTPUT_PREFIX] [--output-format OUTPUT_FORMAT]

input source (choose exactly one):
  --job-desc-file JOB_DESC_FILE, -j JOB_DESC_FILE
                        Path to job description file.
  --input-results-dir POSTPROCESS_INPUT_DIR, -i POSTPROCESS_INPUT_DIR
                        directory containing the result files to postprocess

other required parameters:
  --postprocessed-results-dir POSTPROCESS_RESULT_DIR, -p POSTPROCESS_RESULT_DIR
                        a directory to contain the post-processed results

optional parameters:
  -h, --help            show this help message and exit
  --output-prefix OUTPUT_PREFIX, -o OUTPUT_PREFIX
                        prediction result output prefix.
  --output-format OUTPUT_FORMAT, -f OUTPUT_FORMAT
                        prediction result output format (Default=json)
```

### Predict Subparser

The `predict` subparser is customizable by developers. It supports various formatter classes for proper handling of multiline descriptions:

```python
# Use RawDescriptionHelpFormatter to preserve line breaks
self.parser_predict = self.add_predict_subparser(
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

## Creating a Child Class

### Basic Implementation

```python
import textwrap
import argparse
from core.NGArgumentParser import NGArgumentParser

class ExampleArgumentParser(NGArgumentParser):
    def __init__(self):
        super().__init__()
        
        # Customize program details
        self.description = textwrap.dedent('''
            This is an example application using the NGArgumentParser framework.
        ''')
        
        # Add predict subparser with custom configuration
        self.parser_predict = self.add_predict_subparser(
            help='Perform individual prediction.',
            description='This is where users can perform individual predictions.',
            formatter_class=argparse.RawDescriptionHelpFormatter
        )
        
        # Add tool-specific parameters with grouping
        self.parser_predict.add_argument("--output-prefix", "-o",
                                dest="output_prefix",
                                help="prediction result output prefix.",
                                metavar="OUTPUT_PREFIX",
                                group="output options")
        
        self.parser_predict.add_argument("--output-format", "-f",
                                dest="output_format",
                                default="json",
                                help="prediction result output format (Default=json)",
                                metavar="OUTPUT_FORMAT",
                                group="output options")
```

### Advanced Features

#### Argument Updates
Modify existing arguments dynamically:

```python
# Update an existing argument with new properties
self.parser_predict.update_arguments("--output-format", "-f",
                            default="tsv",
                            help="Updated output format description",
                            group="modified options")
```

#### Custom Validation
Add custom validation functions:

```python
# In validators.py
def validate_custom_input(value):
    # Custom validation logic
    if not value.endswith('.txt'):
        raise argparse.ArgumentTypeError("File must be a .txt file")
    return value

# In ArgumentParser
self.parser_predict.add_argument("--custom-input",
                        type=validate_custom_input,
                        help="Custom input file (must be .txt)")
```

## Dependency Management

### Configuration

After adding dependencies with `cli config-paths` (or `cli c`), configure the project:

```bash
./configure
```

This creates:
- Environment setup scripts for each dependency
- Updated `.env` file with all environment variables
- Automatic cleanup of removed dependencies

### Enhanced Configuration Behavior

- **Always regenerates** the `.env` file based on current `paths.py` content
- **Automatic cleanup**: Removes environment variables and scripts for deleted dependencies
- **APP_ROOT preservation**: Maintains essential environment variables

## Build System

### Building Applications

```bash
# Build the application
cli build
# or
make -f scripts/Makefile build

# Clean build artifacts  
cli clean
# or
make -f scripts/Makefile clean
```

### Build Features

- **Cross-platform compatibility**: Handles OS-specific differences
- **Dependency inclusion**: Packages external dependencies correctly
- **Clean distribution**: Excludes development files automatically
- **Version management**: Automatic version handling

## Workflow Operations

The framework supports a three-stage workflow:

### 1. Preprocessing
```bash
python src/run_{app_name}.py preprocess -j input.json -o output-directory
```

Creates structured job units and generates `job_descriptions.json` for workflow orchestration.

### 2. Prediction  
```bash
python src/run_{app_name}.py predict -j input.json -o output-file
```

Executes the core prediction logic on individual job units.

### 3. Postprocessing
```bash
python src/run_{app_name}.py postprocess --job-desc-file job_descriptions.json -p final-results/
```

Aggregates individual results into consolidated output files.

## Validation System

### Built-in Validators

The framework includes comprehensive validation functions:

```python
from core.core_validators import (
    validate_file,                    # File existence and readability
    validate_directory,               # Directory validation and creation
    validate_directory_given_filename, # Directory validation from file path
    validate_preprocess_dir          # Special preprocessing directory setup
)
```

### Custom Validators

Add application-specific validation in `src/validators.py`:

```python
def validate_peptide_length(value):
    """Validate peptide length is within acceptable range"""
    try:
        length = int(value)
        if not 1 <= length <= 50:
            raise argparse.ArgumentTypeError("Peptide length must be 1-50")
        return length
    except ValueError:
        raise argparse.ArgumentTypeError("Peptide length must be a number")
```

## Development Best Practices

### File Organization
- **Core files**: Never modify files in `src/core/` - these are framework-managed
- **Application logic**: Implement in `src/run_{app_name}.py`
- **Custom validation**: Add to `src/validators.py`
- **Argument parsing**: Customize in `src/{AppName}ArgumentParser.py`

### Error Handling
- Use built-in validation functions for common checks
- Implement custom validators for domain-specific requirements
- Leverage argument grouping for organized help display

### Testing
- Use the example application as a reference implementation
- Test all three workflow stages independently
- Validate argument parsing with various input combinations

## Notes

- The framework enforces a standardized workflow for consistency across applications
- All generated projects include comprehensive build and deployment scripts
- The SubparserWrapper system allows dynamic help text modification
- Argument grouping enhances user experience with organized help display
- The validation system ensures robust input handling across all applications