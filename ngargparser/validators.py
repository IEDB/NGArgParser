# Add any validation logic here. These files can be used along with argument parser.
# For example, all or most arguments should have a validation function defined here.
# Then, these functions can be called from add_argument() by setting the 'type'.
# ex) self.parser_predict.add_argument(
#           "--reference-dir",
#           dest="reference_dir",
#           type=validators.validate_directory)
# ====================================================================================
# A custom validator raises argparse.ArgumentTypeError for a bad value.
import core.set_pythonpath  # This automatically configures PYTHONPATH (first, so libs/ imports work)
import argparse

# The built-in validators, from core/core_validators.py. The parser uses
# validators.validate_file, so keep that one.
from core.core_validators import (
    validate_file,                      # an existing file, not a folder; returns a Path
    validate_directory,                 # an existing folder; returns a Path
    validate_directory_given_filename,  # a file path (not a folder) in an existing folder; returns a Path
)


# ADD ADDITIONAL VALIDATION LOGIC HERE
# ------------------------------------
