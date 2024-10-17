# Add any validation logic here. These files can be used along with argument parser.
# For example, all or most arguments should have a validation function defined here.
# Then, these functions can be called from add_argument() by setting the 'type'.
# ex) self.parser_preprocess.add_argument(
#           "--inputs-dir",
#           dest="preprocess_inputs_dir",
#           type=validators.validate_directory)
import argparse
from pathlib import Path


def validate_file(path_str):
    path = Path(path_str)
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"'{path_str}' is not a valid file.")
    return path

def validate_directory(path_str):
    path = Path(path_str)
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"'{path_str}' is not a valid directory.")
    return path

def validate_directory_given_filename(path_str):
    path = Path(path_str)
    parent_dir = path.parent
    
    if not parent_dir.is_dir():
        raise argparse.ArgumentTypeError(f"'{path_str}' is not a valid directory.")
    return path

# ADD ADDITIONAL VALIDATION LOGIC HERE
# ------------------------------------