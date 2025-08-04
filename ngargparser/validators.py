# Add any validation logic here. These files can be used along with argument parser.
# For example, all or most arguments should have a validation function defined here.
# Then, these functions can be called from add_argument() by setting the 'type'.
# ex) self.parser_preprocess.add_argument(
#           "--inputs-dir",
#           dest="preprocess_inputs_dir",
#           type=validators.validate_directory)
# ====================================================================================
# Import all core validators (DO NOT MODIFY)
import core.set_pythonpath  # This automatically configures PYTHONPATH
from core.core_validators import (
    get_dependencies_from_paths,
    create_directory_structure_for_dependencies,
    validate_file,
    validate_directory,
    validate_directory_given_filename,
    validate_preprocess_dir
)


# ADD ADDITIONAL VALIDATION LOGIC HERE
# ------------------------------------