# dep_names.py
# Which names a dependency can have - DO NOT MODIFY
# Framework-owned: `cli sync` overwrites this file.
#
# The one copy of the rule: `cli deps` refuses a name it gives, and ./configure
# a <name>_path it finds in paths.py. Plain Python 3.9, with nothing to install,
# so ./configure can read it before the tool has an environment.

import re
from typing import Optional

# What makes a dependency is ./configure's rule (configure.py,
# detect_dependency_tools): a top-level `<name>_path = ...` whose <name> doesn't
# end in one of these. The ''' [ Title ] ''' line above a block is only a label.
DEP_NAME_FIELD_SUFFIXES = ("_lib", "_venv", "_module")
# The settings of one dependency, by variable suffix.
DEP_FIELDS = ("_path", "_venv", "_module", "_lib_path")
# Variables the system, the shell or Python reads. ./configure writes a dependency's
# settings to .env as <NAME>_PATH, <NAME>_VENV, <NAME>_MODULE, <NAME>_LIB_PATH and
# <NAME>_ENTRY, and setup_<name>_env.sh exports <NAME>_PATH, so a dependency named
# ld-library would replace the user's LD_LIBRARY_PATH. The tool's process also gets
# <NAME>_PYTHON (see app_env.PYTHON_SUFFIX).
SYSTEM_ENV_VARS = frozenset(
    {
        "LD_LIBRARY_PATH",
        "LIBRARY_PATH",
        "DYLD_LIBRARY_PATH",
        # The rest of what the macOS loader reads that a dependency's key can spell.
        "DYLD_FALLBACK_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
        "DYLD_FALLBACK_FRAMEWORK_PATH",
        "DYLD_VERSIONED_LIBRARY_PATH",
        "DYLD_VERSIONED_FRAMEWORK_PATH",
        "DYLD_ROOT_PATH",
        "LD_RUN_PATH",
        "C_INCLUDE_PATH",
        "CPLUS_INCLUDE_PATH",
        "PATH",
        "PYTHONPATH",
        "PYTHONHOME",
        "MODULEPATH",
        "MANPATH",
        "PKG_CONFIG_PATH",
        "HOME",
        "VIRTUAL_ENV",
    }
)
# The start of every variable a program reads as its settings, with that program: a
# dependency named uv would give the tool's process UV_PYTHON, which picks the Python
# every `uv run` and `uv sync` in it uses, and one named python would set PYTHON_*.
RESERVED_ENV_PREFIXES = (
    ("UV_", "uv", "UV_PYTHON, for one, picks the Python that uv uses"),
    ("PYTHON", "Python", "PYTHONPATH, for one, changes where it finds modules"),
)
# Variables another program reads as its own setting, with that program, each spelled the
# way one of a dependency's keys is (<NAME>_PATH, ..._ENTRY, ..._PYTHON): a dependency named
# node would set NODE_PATH, which changes where Node.js finds modules, and one named pyspark
# PYSPARK_PYTHON, which picks the Python PySpark runs. Only exact keys: a name like GEM_HOME
# or LUA_CPATH, which no dependency's key can be, is not listed. Nor are JAVA_HOME, R_LIBS,
# MATLABPATH or MAMBA_ROOT_PREFIX: the framework writes no <NAME>_HOME, and the others
# end in no suffix a key has.
PROGRAM_ENV_VARS = {
    "NODE_PATH": "Node.js",
    "GEM_PATH": "RubyGems",
    "LUA_PATH": "Lua",
    "GIT_EXEC_PATH": "git",
    "PIP_PYTHON": "pip",
    "VIRTUALENV_PYTHON": "virtualenv",
    "PYSPARK_PYTHON": "PySpark",
    "PYSPARK_DRIVER_PYTHON": "PySpark",
    # HPC and Python tooling: a dependency named cuda would set CUDA_PATH, and one named jupyter JUPYTER_PATH.
    "CUDA_PATH": "CUDA",
    "CUDA_CACHE_PATH": "CUDA",
    "JUPYTER_PATH": "Jupyter",
    "JUPYTER_CONFIG_PATH": "Jupyter",
    "CONDA_ENVS_PATH": "conda",
    "LMOD_PACKAGE_PATH": "Lmod",
    "SPACK_PYTHON": "Spack",
    "JULIA_LOAD_PATH": "Julia",
    "JULIA_DEPOT_PATH": "Julia",
    "CMAKE_PREFIX_PATH": "CMake",
    "CMAKE_LIBRARY_PATH": "CMake",
    "CMAKE_INCLUDE_PATH": "CMake",
    "CMAKE_PROGRAM_PATH": "CMake",
    "RETICULATE_PYTHON": "reticulate",
    "PYO3_PYTHON": "PyO3",
    "PIPX_DEFAULT_PYTHON": "pipx",
}


def normalize_name(name: str) -> str:
    """
    Normalize a dependency name to a valid Python variable name.

    Args:
        name (str): The original dependency name

    Returns:
        str: Normalized variable name
    """
    # Convert to lowercase and replace spaces/special chars with underscores
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", name.lower())
    # Remove multiple consecutive underscores
    normalized = re.sub(r"_+", "_", normalized)
    # Remove leading/trailing underscores
    normalized = normalized.strip("_")
    return normalized


def invalid_dep_name(name: str) -> Optional[str]:
    """Why `name` can't be a dependency name, or None when it can. `name` is what someone
    typed (cli deps add net-mhc) or the <name> of a `<name>_path` in paths.py."""
    var = normalize_name(name)
    if not name.isascii():
        # normalize_name drops every other letter, so ünï would become n.
        shown = f", and cli deps would turn it into '{var}'" if var else ""
        return (
            f"'{name}' has characters that are not plain ASCII{shown}. Use letters a-z, digits, '-' and '_' "
            "(e.g. 'netmhcpan')."
        )
    if not var:
        return f"'{name}' has no letters or digits"
    others = sorted(set(re.findall(r"[^A-Za-z0-9 _.-]", name)))
    if others:
        # cli deps writes the name into paths.py as the title of its block, between ''' quotes,
        # where a quote or a backslash would end the title early, or break the file.
        return (
            f"'{name}' has characters a dependency name cannot have ({' '.join(map(repr, others))}). Use letters, "
            "digits, spaces, '_', '.' and '-' (e.g. 'netmhcpan')."
        )
    if not f"{var}_path".isidentifier():
        return (
            f"'{name}' becomes '{var}', which starts with a digit, so '{var}_path' is not a valid "
            f"Python name. Pick a name that starts with a letter."
        )
    for suffix in (*DEP_NAME_FIELD_SUFFIXES, "_path"):
        if var.endswith(suffix):
            return (
                f"'{name}' becomes '{var}', which ends in '{suffix}': ./configure would read "
                f"{var}_path as a setting of another tool, not as a tool. Pick a name that doesn't "
                f"end in _lib, _venv, _module or _path."
            )
    if var == "app":
        # ./configure writes a dependency's settings to .env as <NAME>_PATH, <NAME>_VENV,
        # <NAME>_ENTRY, ...; for 'app' those are the tool's own APP_VENV and APP_ENTRY.
        return (
            f"'{name}' becomes 'app', and ./configure would write its settings to .env as APP_PATH, "
            "APP_VENV, APP_ENTRY and so on. APP_VENV and APP_ENTRY are this tool's own settings, which "
            "tools that depend on it read, so the dependency would take them over. Pick another name."
        )
    keys = [f"{var}{suffix}".upper() for suffix in (*DEP_FIELDS, "_entry")]
    # Every key the tool sets for the dependency: the ones in .env, and <NAME>_PYTHON.
    exported = [*keys, f"{var}_python".upper()]
    for prefix, reader, example in RESERVED_ENV_PREFIXES:
        if exported[0].startswith(prefix):  # every key starts as <NAME>_PATH does
            listed = ", ".join(exported[:-1]) + f" and {exported[-1]}"
            return (
                f"'{name}' becomes '{var}', and the tool would set {listed} for it, in .env and in its own "
                f"environment. {reader} takes its settings from variables that start with {prefix} ({example}), "
                f"so these could change how {reader} runs in the tool. Pick another name."
            )
    taken = [key for key in exported if key in SYSTEM_ENV_VARS]
    if taken:
        what = "is a variable" if len(taken) == 1 else "are variables"
        return (
            f"'{name}' becomes '{var}', and ./configure would write its settings to .env as {', '.join(keys[:2])}, "
            f"{keys[-1]} and so on. {' and '.join(taken)} {what} the system or Python reads, and sourcing "
            f"setup_{var}_env.sh would replace yours. Pick another name."
        )
    read_by = [(key, PROGRAM_ENV_VARS[key]) for key in exported if key in PROGRAM_ENV_VARS]
    if read_by:
        key, reader = read_by[0]
        listed = ", ".join(exported[:-1]) + f" and {exported[-1]}"
        return (
            f"'{name}' becomes '{var}', and the tool would set {listed} for it, in .env and in its own "
            f"environment. {reader} reads {key} as its own setting, so it could change how {reader} runs in "
            "the tool. Pick another name."
        )
    return None
