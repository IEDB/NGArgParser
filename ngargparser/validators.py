import argparse
from pathlib import Path

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