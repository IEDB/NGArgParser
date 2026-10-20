import argparse
import textwrap
import string
import random
import json
import os
import sys
import core.core_validators as validators
from gettext import gettext
from pathlib import Path
from typing import Dict, TypedDict, List, Optional, Sequence
from core.app_env import PROJECT_ROOT, load_app_env

# Load this tool's .env (its own values win over inherited ones; see app_env)
load_app_env()

# Get the project root directory from environment variable
APP_ROOT = os.getenv('APP_ROOT')

# The formats core.result_writer.write_results writes (it ignores case).
_OUTPUT_FORMATS = ("tsv", "json")


def _writable_format(value):
    """True when write_results writes output format `value`."""
    return isinstance(value, str) and value.lower() in _OUTPUT_FORMATS


def _not_a_file(value):
    """argparse check for a folder the preprocess step creates or fills. It only
    rejects a path that exists and isn't a folder, and an empty one (Path('') is
    the current folder, so -o '' filled it); it creates nothing."""
    if not value:
        raise argparse.ArgumentTypeError("the path is empty; give a folder.")
    path = Path(value)
    if path.exists() and not path.is_dir():
        raise argparse.ArgumentTypeError(f"'{value}' exists and is not a directory.")
    return path


def _output_prefix(value):
    """argparse check for -o of predict and postprocess, a prefix the output file
    names start with, kept as the text given (postprocess gave a Path, so
    args.output_prefix + ".json" failed there only). A folder is refused: 'out/'
    would write the hidden file out/.tsv. So is a format name, which write_results
    refuses as a prefix: './tsv' still names tsv.tsv. An empty value is refused
    too: -o "" (an unset shell variable) wrote the hidden file .tsv. So is a
    prefix that ends in a format's extension: -o res.tsv wrote res.tsv.tsv. A
    missing folder in it is created when the result is written."""
    if not value:
        raise argparse.ArgumentTypeError("the value is empty; -o takes an output prefix such as 'result'")
    if value.endswith(("/", os.sep)) or os.path.isdir(value):
        example = value.rstrip("/" + os.sep) + "/result"
        raise argparse.ArgumentTypeError(f"'{value}' is a folder; -o takes an output prefix such as '{example}'")
    if _writable_format(value):
        raise argparse.ArgumentTypeError(
            f"'{value}' is a format name; -o takes an output prefix such as 'result' "
            f"(give the format with -f {value.lower()}, or name the files {value}.* with -o ./{value})"
        )
    extension = next((f".{name}" for name in _OUTPUT_FORMATS if value.lower().endswith(f".{name}")), None)
    if extension:
        stem = value[:-len(extension)]
        example = stem + "result" if not stem or stem.endswith(("/", os.sep)) else stem
        raise argparse.ArgumentTypeError(
            f"'{value}' ends in {value[-len(extension):]}; -o takes a prefix, and the extension is added: "
            f"use -o {example}"
        )
    # A folder that can't be written failed only once the result was written,
    # after the prediction had run.
    problem = _unwritable_folder(value)
    if problem:
        raise argparse.ArgumentTypeError(problem)
    return value


def _unwritable_folder(prefix):
    """Why write_results can't write files named `prefix`.*: its folder, or the
    closest folder above it that exists (write_results creates the rest), is a
    file or can't be written. None when it can."""
    folder = os.path.dirname(prefix) or "."
    existing = folder
    while not os.path.exists(existing):
        parent = os.path.dirname(existing) or "."
        if parent == existing:
            break
        existing = parent
    if not os.path.isdir(existing):
        return f"'{existing}' is a file, not a folder, so -o {prefix} can't write there"
    if not os.access(existing, os.W_OK | os.X_OK):
        return f"can't write in the folder '{existing}' (permission denied); give -o a prefix in a folder you can write"
    return None


def _after_output_prefix(check):
    """type= for -o that update_arguments was given: `check` runs after -o's own
    (_output_prefix), on the text given, so a folder, a format name or an
    extension is still refused. update_arguments("-o", type=str) dropped them."""
    if check is None or check is _output_prefix or not callable(check):
        return _output_prefix if check is None else check

    def output_prefix_then(value):
        return check(_output_prefix(value))

    # Errors of `check` name it, as for any type= of the tool's own (see _checked_value).
    output_prefix_then.__name__ = getattr(check, "__name__", None) or type(check).__name__
    return output_prefix_then


def _project_version(text):
    """version = "..." of the [project] table in pyproject.toml text `text`, or None.
    A line parse, as Python 3.9 has no tomllib: a quoted value on one line."""
    import re

    table = None
    for line in text.splitlines():
        bare = line.split("#", 1)[0].strip()
        if bare.startswith("["):
            table = bare.replace(" ", "")
            continue
        if table == "[project]":
            found = re.match(r"""version\s*=\s*(?:"([^"]*)"|'([^']*)')""", bare)
            if found:
                return found.group(1) if found.group(1) is not None else found.group(2)
    return None


def _tool_version():
    """The tool's version, for --version: the first line of the VERSION file that
    make build writes into the tool folder, else version in the [project] table
    of its pyproject.toml, else 'unknown'. A build run without TOOL_VERSION
    writes 'local' (an empty line too means no version was given), which a
    deployed copy printed as its version: it shows the pyproject version with
    " (local build)" instead."""
    first = None
    try:
        with open(os.path.join(PROJECT_ROOT, "VERSION"), encoding="utf-8", errors="replace") as f:
            first = f.readline().strip()
        if first and first != "local":
            return first
    except OSError:
        pass
    try:
        with open(os.path.join(PROJECT_ROOT, "pyproject.toml"), encoding="utf-8", errors="replace") as f:
            project = _project_version(f.read())
    except OSError:
        project = None
    if first is None:
        return project or "unknown"
    return f"{project} (local build)" if project else (first or "unknown")


class _ToolVersionAction(argparse.Action):
    """The framework's --version of the top-level command: prints '<prog> <version>'
    (see _tool_version) and exits 0. Without it, run_x.py --version said "the
    following arguments are required: subcommand". A --version of the tool's own
    replaces it (see NGArgumentParser._check_conflict)."""

    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings=option_strings, dest=dest, default=default, nargs=0, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        parser._print_message(f"{parser.prog} {_tool_version()}\n", sys.stdout)
        parser.exit()


def _option_names(parser):
    """Every option string `parser` has, sorted."""
    return sorted({s for action in parser._actions for s in action.option_strings})


def _option_named(word, parser):
    """The option string of `parser` that command-line word `word` gives, as
    --name, --name=value or -nVALUE, or None."""
    options = parser._option_string_actions
    return next((name for name in (word, word.split("=", 1)[0], word[:2])
                 if name.startswith("-") and name in options), None)


def _unknown_options(parser, words, before_positional=False):
    """The command-line words among `words`, given to `parser`, that look like an
    option and name none of its options, read as argparse reads them (--name,
    --name=value, -nVALUE, or the start of a --long option), in order: what argparse
    reports as unrecognized arguments. It stops at '--' and, with
    `before_positional`, at the first word that is no option (argparse takes it as
    the subcommand). A word after an option that takes one value is its value."""
    options = parser._option_string_actions
    unknown: List[str] = []
    value_next = False
    for word in words:
        if value_next:
            value_next = False
            continue
        if word == "--":
            break
        # As argparse's _parse_optional: a negative number or a word with a space is a value.
        if (not word or word[0] not in parser.prefix_chars or len(word) == 1 or " " in word
                or (parser._negative_number_matcher.match(word) and not parser._has_negative_number_optionals)):
            if before_positional:
                break
            continue
        name = _option_named(word, parser)
        if name is None and parser.allow_abbrev and word.startswith("--"):
            name = next((option for option in options if option.startswith(word.split("=", 1)[0])), None)
        if name is None:
            unknown.append(word)
        elif word == name and options[name].nargs in (None, 1):
            value_next = True
    return unknown


def _required_error(message):
    """True for argparse's message about a required argument left out."""
    return any(message.startswith(gettext(text).split("%")[0])
               for text in ("the following arguments are required: %s", "one of the arguments %s is required"))


def _choice_error(message):
    """True for argparse's message about a value not among the choices."""
    return gettext("invalid choice: %(value)r (choose from %(choices)s)").split("%")[0] in message


def _listed(names):
    """'a', 'a and b', or 'a, b and c'."""
    return ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]


def _closest(name, known):
    """The entry of `known` closest to `name`, or None when none is close."""
    import difflib

    close = difflib.get_close_matches(name, known, n=1)
    return close[0] if close else None


def _unknown_option(name, parser, where):
    """ParserSetupError for an option `parser` doesn't have, naming the closest one."""
    known = _option_names(parser)
    close = _closest(name, known)
    hint = f"; did you mean {close}?" if close else f". Its options: {', '.join(known)}"
    return _setup_error(f"No option {name} in {where}{hint}")


def _read_by_parse_args(action):
    """True for a framework option NGArgumentParser.parse_args reads as args.<dest>
    (marked in __init__): without it, parse_args failed with AttributeError."""
    return getattr(action, "_read_by_parse_args", False)


def _remove_option(parser, argument_name, where):
    """Remove option `argument_name` from `parser` (subcommand `where`), under every
    name it has. Raises the _unknown_option ParserSetupError when it has no such option,
    and a ParserSetupError for an option parse_args reads (_read_by_parse_args)."""
    action = parser._option_string_actions.get(argument_name)
    if action is None:
        raise _unknown_option(argument_name, parser, where)
    if _read_by_parse_args(action):
        raise _setup_error(
            f"remove_argument can't remove {'/'.join(action.option_strings)} from {where}: the framework "
            f"reads args.{action.dest}; change the option with self.parser_{where}.update_arguments("
            f"\"{_long_name(action)}\", ...) instead, or hide it from -h with help=argparse.SUPPRESS"
        )

    # argparse records an option in three places: the parser's actions (what
    # parses), its groups (what --help shows) and the option-string table
    # (what each typed name maps to, and what a new declaration conflicts with).
    parser._actions.remove(action)
    for group in [*parser._action_groups, *parser._mutually_exclusive_groups]:
        if action in group._group_actions:
            group._group_actions.remove(action)
    # A mutually exclusive group left empty (both template inputs removed)
    # would make argparse fail every parse with "empty group", even when
    # it isn't required.
    parser._mutually_exclusive_groups[:] = [
        group for group in parser._mutually_exclusive_groups if group._group_actions
    ]
    for option_string in action.option_strings:
        parser._option_string_actions.pop(option_string, None)


def _declared_at():
    """'file:line' of the innermost call from outside argparse and this module (the
    tool's own add_argument line), or None."""
    import traceback

    skip = {os.path.abspath(argparse.__file__), os.path.abspath(__file__)}
    for frame in reversed(traceback.extract_stack()):
        if os.path.abspath(frame.filename) not in skip:
            try:
                return f"{os.path.relpath(frame.filename)}:{frame.lineno}"
            except ValueError:  # another drive on Windows
                return f"{frame.filename}:{frame.lineno}"
    return None


class ParserSetupError(ValueError):
    """A mistake in the tool's own parser code, found while the parser is built.

    update_arguments, remove_argument and set_subcommand_order raise it for a
    name or value they can't use, and self.parser_predict raises it before
    add_predict_subparser() has run. So do a second add_predict_subparser(),
    self.add_subcommand() or self.subparser.add_parser() given a subcommand name
    already taken (the framework's three included), a second add_subparsers(),
    two of the tool's options that share a flag,
    group= on the top-level parser's add_argument, and a group= title that
    differs from a section's title only in case, surrounding spaces or a
    trailing s ("output option" for "output options"). The message starts with
    the file and line of the call (src/MyAppArgumentParser.py:40: ...), and the
    generated run script prints only that line and exits 2, as for a framework
    option clash.

    A ValueError, so `except ValueError` still catches it. ParserNotReadyError
    is an AttributeError too, ParserKeywordError a TypeError too, and
    ParserConflictError an argparse.ArgumentError too.
    """


class ParserNotReadyError(ParserSetupError, AttributeError):
    """A framework attribute used before it exists, or one that never does, such
    as self.subparsers for self.subparser (see ParserSetupError)."""


class ParserKeywordError(ParserSetupError, TypeError):
    """update_arguments() given a keyword it does not take, or group= given
    to the top-level parser's add_argument (see ParserSetupError)."""


class ParserConflictError(ParserSetupError, argparse.ArgumentError):
    """Two declarations of one name: two of the tool's options on a subcommand
    that share a flag, a second add_predict_subparser(), or a subcommand name
    given to self.add_subcommand() or self.subparser.add_parser() that is taken
    (see ParserSetupError).
    An argparse.ArgumentError too, as argparse raised one for these."""

    def __init__(self, message):
        ParserSetupError.__init__(self, message)
        # What argparse.ArgumentError's str() reads: the whole line, no argument name.
        self.argument_name = None
        self.message = message


def _setup_error(message, error_class=ParserSetupError):
    """`error_class` with `message`, after the file:line of the tool's call."""
    where = _declared_at()
    return error_class(f"{where}: {message}" if where else message)


def _section_of(value, parser):
    """True when group= `value` is a help section of subcommand parser `parser`:
    one of its argument groups (not a mutually exclusive group, which has no
    section of its own). Such a group= is the same as that section's title."""
    return (isinstance(value, argparse._ArgumentGroup)
            and not isinstance(value, argparse._MutuallyExclusiveGroup)
            and any(value is group for group in parser._action_groups))


def _section_owner(group):
    """The subcommand whose parser argparse group `group` was made on, or None."""
    parser = getattr(group, "_ng_parser", None)
    return getattr(parser, "_subcommand", None) if parser is not None else None


def _bare_title(title):
    """Section title `title` without case, surrounding spaces or one trailing s."""
    bare = title.strip().lower()
    return bare[:-1] if bare.endswith("s") else bare


def _near_section(name, parser):
    """The title of a help section of `parser` that group= `name` differs from only
    in case, surrounding spaces or one trailing s, or None (also when a section has
    title `name` exactly). group="output option" made a second section, shown next
    to 'output options'."""
    titles = [group.title for group in parser._action_groups if isinstance(group.title, str)]
    if not isinstance(name, str) or name in titles:
        return None
    return next((title for title in titles if _bare_title(title) == _bare_title(name)), None)


def _near_section_error(label, args, kwargs, name, title):
    """ParserSetupError for group= `name`, given to add_argument(*args, **kwargs) of
    subcommand `label`, which is nearly section `title` (see _near_section)."""
    where = f"{label} argument {'/'.join(map(str, args)) or kwargs.get('dest', '')}"
    return _setup_error(f"{where}: group=\"{name}\" would add a section next to '{title}'; did you mean '{title}'?")


def _group_keyword_error(label, args, kwargs, value, on_group=None):
    """ParserKeywordError when group= `value`, given to add_argument(*args,
    **kwargs) of command `label` (predict, ..., top-level), or of argparse group
    `on_group`, is no section title, else None. The caller takes a group that is
    a help section of its subcommand (_section_of) before it asks. A group object
    became a help section titled "<argparse._ArgumentGroup object at 0x...>:"."""
    if value is None or isinstance(value, str):
        return None
    where = f"{label} argument {'/'.join(map(str, args)) or kwargs.get('dest', '')}"
    if value is on_group:
        fix = "drop group=, as this is that group's own add_argument"
    elif on_group is not None:
        # A group's add_argument takes no other section, so the title won't do.
        fix = "call that group's own add_argument"
    elif type(value) is argparse._ArgumentGroup and isinstance(value.title, str) and value.title:
        fix = f'use group="{value.title}", or call that group\'s own add_argument'
    else:
        fix = "call that group's own add_argument"
    if isinstance(value, argparse._MutuallyExclusiveGroup):
        message = f"a section title (a str), not a mutually exclusive group: {fix}"
    elif isinstance(value, argparse._ArgumentGroup) and label != "top-level":
        # A group of another subcommand (or parser): its title would make a new
        # section here, and its own add_argument would add the option there.
        owner = _section_owner(value)
        other = f"a group of {owner}" if owner and owner != label else "a group of another parser"
        message = f"a section of {label} (a title, or one of its groups), not {other}: {fix}"
    elif isinstance(value, argparse._ArgumentGroup):
        message = f"a section title (a str), not an argument group: {fix}"
    else:
        message = f"a section title (a str), not {type(value).__name__}: use group=\"my options\""
    return _setup_error(f"{where}: group= takes {message}", ParserKeywordError)


def _action_keywords():
    """The keywords argparse's add_argument takes for its built-in actions, plus
    group= (Python 3.13 adds deprecated)."""
    import inspect

    own = [name for name in inspect.signature(argparse.Action.__init__).parameters
           if name not in ("self", "option_strings")]
    return ["action", *own, "version", "group"]


def _update_keywords(action):
    """The keywords update_arguments takes for option `action`: those add_argument
    takes (_action_keywords), but not action= (an option can't change its class),
    and version= only for a version option."""
    return [key for key in _action_keywords()
            if key != "action" and (key != "version" or hasattr(action, "version"))]


def _argparse_error(error, where, kwargs, args=()):
    """ParserSetupError for the TypeError or ValueError argparse's add_argument
    raised for argument `where` ("predict argument --beta"; `args` its names),
    which gave a traceback ending in "_StoreAction.__init__() got an unexpected
    keyword argument 'hepl'" or "mutually exclusive arguments must be optional". A
    TypeError gives a ParserKeywordError (a TypeError too)."""
    import re

    text = str(error)
    if isinstance(error, TypeError):
        unexpected = re.search(r"unexpected keyword argument '(\w+)'", text)
        if unexpected:
            key = unexpected.group(1)
            known = _action_keywords()
            if key in known:
                action = kwargs.get("action", "store")
                shown = repr(action) if isinstance(action, str) else getattr(action, "__name__", repr(action))
                text = f"action={shown} takes no {key}="
            else:
                close = _closest(key, known)
                hint = f"did you mean {close}=?" if close else f"add_argument takes {', '.join(known)}"
                text = f"add_argument() got an unexpected keyword argument {key!r}; {hint}"
        return _setup_error(f"{where}: {text}", ParserKeywordError)
    if text == "mutually exclusive arguments must be optional":
        if args and not str(args[0]).startswith("-"):
            # A positional argument is required unless nargs='?', though no required= was given.
            text = (f"a positional argument can't be in a mutually exclusive group unless it has "
                    f"nargs='?': make it an option, such as --{args[0]}, or add it outside the group")
        else:
            text = ("an option of a mutually exclusive group can't be required=True: make the group "
                    "required instead, with add_mutually_exclusive_group(required=True)")
    return _setup_error(f"{where}: {text}")


def _default_not_in_choices(kwargs, choices):
    """The default of add_argument keywords `kwargs` (or one of its items) that is
    not among `choices`, or None. Only for the actions that check choices; a
    text default with a type= is skipped (argparse converts it first), as are
    choices that are an iterator (searching would use them up)."""
    if choices is None or kwargs.get("action") not in (None, "store", "append", "extend"):
        return None
    default = kwargs.get("default")
    if isinstance(default, str) and kwargs.get("type") is not None:
        return None
    try:
        if iter(choices) is choices:
            return None
    except TypeError:
        return None
    return _default_outside(default, choices)


def _option_mistake(action):
    """What in option `action`, as add_argument or update_arguments would leave
    it, fails only once the tool runs, as text, or None: type=bool (every value
    given is true, so --keep-ties False gave True), choices that are not text
    without a type= (every value given is text, so none matched: "invalid
    choice: '8'"), and a help= that -h can't expand (a lone %, as in "top 10%",
    stopped -h with a TypeError traceback)."""
    if action.type is bool:
        return ('type=bool makes every value given true (--x False gives True): use action="store_true" '
                'for a flag, or a type= check that reads yes/no')
    choices = action.choices
    if choices is not None and action.type is None and not isinstance(choices, str):
        try:
            # An iterator would be used up by the search.
            others = [] if iter(choices) is choices else [c for c in choices if not isinstance(c, str)]
        except TypeError:
            others = []
        if others:
            return (f"choices {_shown_choices(choices)} are not text, and without type= every value given is "
                    f"text, so none would match: add type={type(others[0]).__name__}")
    if isinstance(action.help, str) and "%" in action.help:
        try:
            argparse.HelpFormatter("prog")._expand_help(action)
        except (KeyError, TypeError, ValueError) as e:
            return f"-h can't show help={action.help!r} ({type(e).__name__}: {e}): use %% for a literal %"
    return None


def _declared_mistake(args, kwargs):
    """_option_mistake of the option add_argument(*args, **kwargs) would add, made
    on a parser of its own (the tool's parsers don't change), or None. None too
    when argparse refuses the call: the tool's own add_argument then says why."""
    if not (kwargs.get("type") is bool or kwargs.get("choices") is not None
            or "%" in str(kwargs.get("help") or "")):
        return None
    try:
        action = argparse.ArgumentParser(add_help=False).add_argument(
            *args, **{key: value for key, value in kwargs.items() if key != "group"})
    except Exception as e:
        # Python 3.14 checks help= itself, and says only this.
        if str(e) == "badly formed help string":
            return f"-h can't show help={kwargs.get('help')!r} ({e}): use %% for a literal %"
        return None
    return _option_mistake(action)


def _reporting_errors(add_argument, label):
    """`add_argument`, raising ParserSetupError with the tool's file:line for
    the TypeError or ValueError argparse raises (see _argparse_error), for
    a default that is not among the choices (argparse took it, and args.<dest>
    held a value -h said was not allowed), and for a mistake that showed only
    once the tool ran (see _option_mistake). `label()` names the command:
    predict, preprocess, postprocess or top-level."""

    def add_argument_reporting_errors(*args, **kwargs):
        where = f"{label()} argument {'/'.join(map(str, args)) or kwargs.get('dest', '')}"
        missing = _default_not_in_choices(kwargs, kwargs.get("choices"))
        if missing is not None:
            raise _setup_error(
                f"{where}: default {missing!r} is not among the choices "
                f"{_shown_choices(kwargs['choices'])}: give a default from the choices, or add it to them"
            )
        mistake = _declared_mistake(args, kwargs)
        if mistake:
            raise _setup_error(f"{where}: {mistake}")
        try:
            action = add_argument(*args, **kwargs)
            if kwargs.get("type") is not None:
                # For the message of a default its type= check refuses (see _checked_value).
                action._ng_declared_at = _declared_at()
            return action
        except ParserSetupError:
            raise
        except (TypeError, ValueError) as e:
            raise _argparse_error(e, where, kwargs, args) from e

    return add_argument_reporting_errors


def _checked_value(parser, action, arg_string, get_value):
    """_type_checked_value, but an error for the default of `action` (an option not
    given, whose default argparse checks too) says so: --model-dir with
    default="models", run from another folder, said only "'models' does not exist"
    for an option the user never typed. `parser._ng_typed` holds the words given
    to `action` while argparse reads them (see _note_typed)."""
    try:
        return _type_checked_value(parser, action, arg_string, get_value)
    except argparse.ArgumentError as e:
        typed = vars(parser).get("_ng_typed")
        if arg_string is not action.default or not isinstance(arg_string, str) or (typed and arg_string in typed):
            raise
        name = argparse._get_action_name(action) or action.dest
        detail = e.message.rstrip(".")
        # A relative path is read from the folder the tool runs in; a check of a
        # path says the file or folder is missing or of the wrong kind.
        path = not os.path.isabs(arg_string) and any(word in detail for word in ("exist", "folder", "file"))
        where = f", relative to {os.getcwd()}" if path else ""
        declared = getattr(action, "_ng_declared_at", None)
        parser_file = declared.rsplit(":", 1)[0] if declared else "the tool's parser"
        raise argparse.ArgumentError(
            None, f"the default of {name} ({arg_string!r}{where}): {detail}; "
                  f"fix the default in {parser_file}, or pass {name}"
        ) from e


def _note_typed(parser, get_values, action, arg_strings):
    """`get_values(action, arg_strings)` (argparse's _get_values), with the words
    given to `action` in `parser._ng_typed` meanwhile: argparse checks a default
    outside it, with the default itself as the word (see _checked_value)."""
    parser._ng_typed = arg_strings
    try:
        return get_values(action, arg_strings)
    finally:
        parser._ng_typed = None


def _type_checked_value(parser, action, arg_string, get_value):
    """`get_value(action, arg_string)` (argparse's _get_value) for option `action`
    of the tool's own, with every error its type= check raises as one usage line
    (exit code 2), never a traceback. For a check such as a validator in
    src/validators.py, argparse gave "invalid validate_peptide_length value: 'abc'"
    for a ValueError or TypeError, and a traceback with exit code 1 for any other
    exception. An ArgumentTypeError keeps its message, and Python's own int, float
    and complex keep argparse's "invalid int value: 'abc'". A check that returns
    None for a value that is not empty is refused too: one that only checks gave
    args.<dest> None, exit code 0."""
    check = parser._registry_get("type", action.type, action.type)
    if action.type is None or not callable(check):
        return get_value(action, arg_string)
    try:
        if isinstance(check, type) and check.__module__ == "builtins":
            return get_value(action, arg_string)
        value = check(arg_string)
        if value is None and arg_string:
            name = getattr(check, "__name__", None) or type(check).__name__
            raise argparse.ArgumentError(
                action, f"the check {name} returned None for '{arg_string}': return the value "
                        f"(or raise ArgumentTypeError)"
            )
        return value
    except argparse.ArgumentError:
        raise
    except argparse.ArgumentTypeError as e:
        raise argparse.ArgumentError(action, str(e)) from e
    except (TypeError, ValueError) as e:
        raise argparse.ArgumentError(action, f"'{arg_string}' is not valid: {str(e) or type(e).__name__}") from e
    except Exception as e:
        name = getattr(check, "__name__", None) or type(check).__name__
        detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        raise argparse.ArgumentError(action, f"the check {name} failed on '{arg_string}': {detail}") from e


def _not_initialized(parser):
    """ParserNotReadyError for a parser whose NGArgumentParser.__init__ never ran."""
    name = type(parser).__name__
    return _setup_error(
        f"{name} has not run NGArgumentParser.__init__: call super().__init__() at the start of {name}.__init__",
        ParserNotReadyError,
    )


class _SetByInit:
    """Class-level stand-in for an attribute NGArgumentParser.__init__ sets. The
    instance's own value hides it once __init__ has run; before that (a subclass
    that never called super().__init__()), reading it says so, instead of
    "'MyAppArgumentParser' object has no attribute 'subparser'"."""

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        raise _not_initialized(instance)


class _Misnamed:
    """Class-level stand-in for a likely misspelling of a framework attribute:
    reading it raises ParserNotReadyError (an AttributeError) with `message`.
    The tool may still set an attribute of that name for itself."""

    def __init__(self, message):
        self._message = message

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        raise _setup_error(self._message, ParserNotReadyError)


def _default_outside(default, choices):
    """The value of `default` (or of its items, for a list) not in `choices`, or
    None. A None or suppressed default, and choices that can't be searched, count as in."""
    values = default if isinstance(default, (list, tuple)) else [default]
    for value in values:
        if value is None or value is argparse.SUPPRESS:
            continue
        try:
            if value not in choices:
                return value
        except TypeError:
            return None
    return None


def _shown_choices(choices):
    """`choices` as text: 'tsv, json'."""
    try:
        return ", ".join(map(str, choices))
    except TypeError:
        return repr(choices)


def _long_name(action):
    """The first --long option string of `action`, else its first one."""
    return next((s for s in action.option_strings if s.startswith('--')), action.option_strings[0])


def _parser_of(subcommand):
    """How a message names the parser of `subcommand` before a method call:
    'self.parser_predict.', or, for a subcommand of the tool's own (it has no
    self.parser_<name>), "the list-alleles parser's "."""
    if subcommand in _SubcommandsAction._FRAMEWORK_SUBCOMMANDS:
        return f"self.parser_{subcommand}."
    return f"the {subcommand} parser's "


def _remove_call(subcommand, flag):
    """The one remove_argument call every message recommends:
    self.parser_predict.remove_argument("--output-format")."""
    return f'{_parser_of(subcommand)}remove_argument("{flag}")'


class _NotReady:
    """Stands in for self.parser_predict and self.predict_input_group until
    add_predict_subparser() creates them. Reading or setting anything on it
    raises ParserNotReadyError (an AttributeError) saying so, instead of
    "'NoneType' object has no attribute 'add_argument'". It is falsy, like the
    None it replaces."""

    _name = "this attribute"

    def __init__(self, name):
        object.__setattr__(self, "_name", name)

    def _error(self):
        return _setup_error(
            f"{self._name} is not ready yet: call self.add_predict_subparser(...) first", ParserNotReadyError
        )

    def __getattr__(self, attribute):
        raise self._error()

    def __setattr__(self, attribute, value):
        raise self._error()

    def __bool__(self):
        return False

    def __repr__(self):
        return f"<{self._name}: not ready until self.add_predict_subparser(...) runs>"


def _own_title(group):
    """The title of the help section `group`'s options show in, or None. A
    mutually exclusive group shows them in the group it was made from."""
    if isinstance(group, argparse._MutuallyExclusiveGroup):
        container = group._container
        return _own_title(container) if isinstance(container, argparse._ArgumentGroup) else None
    return group.title


def _group_label(group):
    """What the group= message calls `group`: self.<attribute> for a framework
    group, else its title."""
    if getattr(group, "_framework_attribute", None):
        return group._framework_attribute
    if isinstance(group, argparse._MutuallyExclusiveGroup):
        title = _own_title(group)
        return f"the mutually exclusive group in '{title}'" if title else "the mutually exclusive group"
    return f"'{group.title}'"


def _take_group_keyword(group, parser):
    """Let `group`, an argparse group of subcommand parser `parser`, take group=,
    and the groups made from it too.

    group= naming the section the option shows in (the group's current title) is
    dropped, so the option goes here as asked; a help section of the subcommand,
    given as its group, counts as its title. Any other group= stops the tool
    with one line, since an argparse group can't hold another section; argparse
    alone raised "TypeError: ... got an unexpected keyword argument 'group'".
    """
    def subcommand_name():
        return parser._subcommand or parser.prog.rsplit(" ", 1)[-1]

    # The parser it was made on, so a group= message can name its subcommand.
    group._ng_parser = parser

    # argparse's TypeError or ValueError (a misspelled keyword, required=True in
    # a mutually exclusive group) becomes one line naming the tool's file:line.
    add_argument = _reporting_errors(group.add_argument, subcommand_name)

    def add_argument_taking_group(*args, **kwargs):
        named = kwargs.pop('group', None)
        if _section_of(named, parser):
            # A help section of this subcommand, given as its group: the same as its title.
            named = None if named is group else named.title
        wrong = _group_keyword_error(subcommand_name(), args, kwargs, named, on_group=group)
        if wrong:
            raise wrong
        if named and named != _own_title(group):
            where = _declared_at()
            subcommand = subcommand_name()
            # A typo of a section's title gets that title, which the subcommand's
            # add_argument takes (it refuses the typo; see _near_section).
            suggested = _near_section(named, parser) or named
            # A subcommand of the tool's own (self.add_subcommand) has no self.parser_<name>.
            own = _parser_of(subcommand)
            parser.exit(2, (
                f"{where + ': ' if where else ''}{subcommand} argument {'/'.join(args) or kwargs.get('dest', '')}: "
                f"{_group_label(group)} is already a group; drop group=, or use "
                f"{own}add_argument(..., group=\"{suggested}\")\n"
            ))
        return add_argument(*args, **kwargs)

    group.add_argument = add_argument_taking_group

    for method in ("add_argument_group", "add_mutually_exclusive_group"):
        def make_group_taking_group(*args, _make=getattr(group, method), **kwargs):
            inner = _make(*args, **kwargs)
            if not isinstance(inner, argparse._MutuallyExclusiveGroup):
                # A mutually exclusive group hands its options to the group it
                # was made from, which checks them; an argument group checks its own.
                parser._report_clashes(inner)
            _take_group_keyword(inner, parser)
            return inner

        setattr(group, method, make_group_taking_group)


def _take_own_group_name(group, attribute):
    """Name the framework's argparse group `group` self.<attribute> in its group=
    message (it takes group= like every group of a subcommand parser)."""
    group._framework_attribute = attribute


def _patch_for_groups(parser):
    """NGArgumentParser.patch_parser_for_groups(parser): give `parser` the
    add_argument that takes group= (see there)."""
    if not hasattr(parser, '_argument_groups'):
        parser._argument_groups = {}

    def label():
        return (getattr(parser, '_subparser_name', None) or getattr(parser, '_subcommand', None)
                or parser.prog.rsplit(" ", 1)[-1])

    # argparse's TypeError or ValueError (a misspelled keyword, a type= that
    # is not callable) becomes one line naming the tool's file:line.
    original_add_argument = _reporting_errors(parser.add_argument, label)
    
    def add_argument_with_groups(*args, **kwargs):
        group_name = kwargs.pop('group', None)
        # A help section of this subcommand, given as its group: the same as its title.
        if _section_of(group_name, parser):
            return group_name.add_argument(*args, **kwargs)
        # Any other group object made a section titled "<argparse._ArgumentGroup object at 0x...>".
        wrong = _group_keyword_error(label(), args, kwargs, group_name)
        if wrong:
            raise wrong

        group_mapping = {
            group.title: group
            for group in getattr(parser, '_action_groups', [])
        }

        if group_name:
            if group_name in group_mapping:
                return group_mapping[group_name].add_argument(*args, **kwargs)
            else:
                # Create new group if not in mapping
                if group_name not in parser._argument_groups:
                    # A typo of a section's title made a second section next to it.
                    close = _near_section(group_name, parser)
                    if close is not None:
                        raise _near_section_error(label(), args, kwargs, group_name, close)
                    parser._argument_groups[group_name] = parser.add_argument_group(group_name)
                return parser._argument_groups[group_name].add_argument(*args, **kwargs)
        return original_add_argument(*args, **kwargs)
    
    parser.add_argument = add_argument_with_groups
    return parser


class _SubcommandParser(argparse.ArgumentParser):
    """The parser class of predict, preprocess and postprocess.

    An option that reuses a name of one of the framework's built-in options, or
    the name it is stored under (its dest, as in args.output_format), stops the
    tool with one line that names the framework option, instead of argparse's
    traceback ending in "conflicting option string" (or, for a dest, a value the
    framework then reads from the wrong option). A clash between two of the
    tool's own options raises ParserConflictError, one line naming both.
    """
    # The built-in options and the subcommand name, set by SubparserWrapper._mark_framework_options.
    _framework_actions: Sequence[argparse.Action] = ()
    _subcommand: Optional[str] = None
    # The top-level NGArgumentParser (set by _SubcommandsAction._add_framework_parser),
    # and the words this subcommand was given, for the hints of error().
    _ng_top = None
    _ng_words: Sequence[str] = ()

    def parse_known_args(self, args=None, namespace=None):
        self._ng_words = list(sys.argv[1:] if args is None else args)
        return super().parse_known_args(args, namespace)

    def error(self, message):
        """argparse's error, but an option argparse did not know is reported
        before a required argument left out: predict --input_json f said only "one
        of the arguments --input-tsv/-t --input-json/-j is required". It names the
        closest option, or where the option goes."""
        top = self._ng_top
        if top is not None and _required_error(message):
            unknown = top._early_unknown() + _unknown_options(self, self._ng_words)
            if unknown:
                message = top._unrecognized_message(self._subcommand or self.prog.rsplit(" ", 1)[-1], unknown)
        super().error(message)

    def add_argument_group(self, *args, **kwargs):
        group = super().add_argument_group(*args, **kwargs)
        self._report_clashes(group)
        _take_group_keyword(group, self)
        return group

    def add_mutually_exclusive_group(self, *args, **kwargs):
        group = super().add_mutually_exclusive_group(*args, **kwargs)
        _take_group_keyword(group, self)
        return group

    def _get_value(self, action, arg_string):
        # A built-in option keeps argparse's handling: its checks raise
        # ArgumentTypeError with their own message. A type= of the tool's own,
        # on its own option or given to a built-in one, reports every error.
        if action in self._framework_actions and getattr(action, "_framework_type", None) is action.type:
            return super()._get_value(action, arg_string)
        return _checked_value(self, action, arg_string, super()._get_value)

    def _get_values(self, action, arg_strings):
        return _note_typed(self, super()._get_values, action, arg_strings)

    def _report_clashes(self, group):
        # add_argument puts an option in a group ('options' by default), and the
        # group checks it for clashes, so every group reports through this parser.
        group._handle_conflict_error = self._handle_conflict_error
        group._check_conflict = self._check_conflict

    def _check_conflict(self, action):
        # Names first, so an option that reuses a framework flag gets that message.
        super()._check_conflict(action)
        # A removed framework option leaves its dest free.
        owner = next((o for o in self._framework_actions
                      if o.dest == action.dest and o in self._actions), None)
        if owner is not None:
            self._stop_for_clash(action, f"dest {action.dest} belongs", [owner], "dest")
        # args.subcommand says which subcommand ran; an option stored there
        # would overwrite it, and the run script would take no branch at all.
        if action.dest == "subcommand":
            where = _declared_at()
            self.exit(2, (
                f"{where + ': ' if where else ''}{self._subcommand} argument "
                f"{'/'.join(action.option_strings) or action.dest}: dest subcommand belongs to the "
                f"framework (it records which subcommand ran); pick another dest\n"
            ))

    def _handle_conflict_error(self, action, conflicting_actions):
        flags, owners = [], []
        for option_string, owner in conflicting_actions:
            if owner in self._framework_actions:
                flags.append(option_string)
                if owner not in owners:
                    owners.append(owner)
        if not owners:
            raise self._own_clash(action, conflicting_actions)

        taken = f"{', '.join(flags)} {'belongs' if len(flags) == 1 else 'belong'}"
        self._stop_for_clash(action, taken, owners, "flag")

    def _own_clash(self, action, conflicting_actions):
        """ParserConflictError: `action` reuses flags of the tool's own options
        (argparse raised "conflicting option string" with a traceback)."""
        flags = [option_string for option_string, _ in conflicting_actions]
        owners = list(dict.fromkeys(owner for _, owner in conflicting_actions))
        subcommand = self._subcommand or self.prog.rsplit(" ", 1)[-1]
        return _setup_error(
            f"{subcommand} argument {'/'.join(action.option_strings)}: {', '.join(flags)} "
            f"{'is' if len(flags) == 1 else 'are'} already taken by "
            f"{', '.join('/'.join(owner.option_strings) for owner in owners)}; pick another flag",
            ParserConflictError,
        )

    def _stop_for_clash(self, action, taken, owners, what):
        """Exit 2 with one line: `action` takes what `owners` (framework options) own.
        It names both fixes: update_arguments changes the framework option, and
        remove_argument frees its names for your own (update_arguments refuses,
        for one, choices of -f that write_results can't write)."""
        names = [_long_name(o) for o in owners]
        where = _declared_at()
        own = f"self.parser_{self._subcommand}"
        update = f"change the framework option with {own}.update_arguments(\"{names[0]}\", ...)"
        # An option parse_args reads can't be removed (see _remove_option).
        fixes = (f"pick another {what}, or {update}" if _read_by_parse_args(owners[0]) else
                 f"pick another {what}, {update}, or replace it with your own: "
                 f"call {_remove_call(self._subcommand, names[0])} first")
        self.exit(2, (
            f"{where + ': ' if where else ''}{self._subcommand} argument "
            f"{'/'.join(action.option_strings) or action.dest}: "
            f"{taken} to the framework ({', '.join(names)}); {fixes}\n"
        ))


def _check_parser_keywords(method, kwargs, refused):
    """Raise ParserKeywordError for a keyword of `kwargs` that argparse's
    add_parser, on this Python, does not take (naming the closest one), or one
    of `refused` ({keyword: what it would break}). `method` names the call."""
    import inspect

    # add_parser reads help= and aliases= from its **kwargs, so its signature lacks them.
    taken = {"help", "description", "formatter_class", "aliases"}
    for function in (argparse.ArgumentParser.__init__, argparse._SubParsersAction.add_parser):
        taken.update(name for name, parameter in inspect.signature(function).parameters.items()
                     if parameter.kind not in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD))
    taken -= {"self", "name", "parser_class", *refused}
    for key in kwargs:
        if key in refused:
            raise _setup_error(f"{method}() takes no {key}=: {refused[key]}", ParserKeywordError)
        if key not in taken:
            close = _closest(key, sorted(taken))
            hint = f"did you mean {close}=?" if close else f"it takes {', '.join(sorted(taken))}"
            raise _setup_error(
                f"{method}() got an unexpected keyword argument {key!r}; {hint}", ParserKeywordError
            )


class _SubcommandsAction(argparse._SubParsersAction):
    """argparse's subcommand action, self.subparser. A subcommand the tool adds
    with self.add_subcommand(...), or self.subparser.add_parser(...), comes back
    as a SubparserWrapper, as self.parser_predict does: it has update_arguments,
    remove_argument and .help, and its add_argument takes group=. A setup
    mistake, such as a misspelled keyword here or in its add_argument, stops the
    tool with one line (argparse stopped on each with a traceback, and the parser
    had none of the three). The framework adds its own subcommands with
    _add_framework_parser."""

    _FRAMEWORK_SUBCOMMANDS = ("predict", "preprocess", "postprocess")
    # The NGArgumentParser it belongs to (set by its __init__), for SubparserWrapper.
    _top_parser = None

    def __call__(self, parser, namespace, values, option_string=None):
        super().__call__(parser, namespace, values, option_string)
        # An alias (aliases=["la"]) set args.subcommand to 'la', and the run
        # script, which branches on 'list-alleles', said "la has no branch".
        own = getattr(self._name_parser_map.get(values[0]), "_subcommand", None)
        if self.dest is not argparse.SUPPRESS and own and own != values[0]:
            setattr(namespace, self.dest, own)

    def add_parser(self, name, **kwargs):
        return self._add_tool_parser("self.subparser.add_parser", name, kwargs)

    def _add_tool_parser(self, method, name, kwargs):
        """add_parser for a subcommand of the tool's own; `method` names the call
        (self.add_subcommand or self.subparser.add_parser) in its messages."""
        # argparse's parser class raised "unexpected keyword argument" with a traceback.
        _check_parser_keywords(method, kwargs, {})
        for taken in (name, *(kwargs.get("aliases") or ())):
            if taken in self._FRAMEWORK_SUBCOMMANDS:
                # On Python 3.9 and 3.10 the new parser took the place of the
                # framework's (and add_predict_subparser then said "called a second
                # time"); later ones gave "conflicting subparser" with a traceback.
                use = ("self.add_predict_subparser(...) adds it" if taken == "predict" else
                       f"change its options with self.parser_{taken}")
                raise _setup_error(
                    f"{method}({name!r}): {taken} is a subcommand of the framework; "
                    f"{use}, or pick another name",
                    ParserConflictError,
                )
            if taken in self._name_parser_map:
                raise _setup_error(
                    f"{method}({name!r}): subcommand {taken} exists already; "
                    f"add it once, or pick another name",
                    ParserConflictError,
                )
        parser = self._add_framework_parser(name, **kwargs)
        # Its name, for the messages of _SubcommandParser and its groups.
        parser._subcommand = name
        _patch_for_groups(parser)
        return SubparserWrapper(parser, name, self._top_parser)

    def _add_framework_parser(self, name, **kwargs):
        """argparse's add_parser: predict, preprocess and postprocess, which the
        framework wraps and patches itself."""
        parser = super().add_parser(name, **kwargs)
        # Its command, for the hints of an error (see _SubcommandParser.error).
        parser._ng_top = self._top_parser
        return parser


class SubparserWrapper:
    """
    A wrapper class for argparse subparsers that allows setting help text 
    using the .help attribute, which will automatically update the correct 
    location in the argument parser structure.
    """
    def __init__(self, subparser, subparser_name, parent_parser):
        self._subparser = subparser
        self._subparser_name = subparser_name
        self._parent_parser = parent_parser
        
        # Capture the initial help text from the subparser action
        self._help_text = self._get_current_help_from_action()
    
    def __getattr__(self, name):
        """Delegate attribute access to the wrapped subparser. A name neither has
        raises ParserNotReadyError (an AttributeError) naming the closest method:
        self.parser_predict.update_argumens(...) gave "'_SubcommandParser' object
        has no attribute 'update_argumens'" with a traceback."""
        try:
            return getattr(self._subparser, name)
        except AttributeError:
            if name.startswith("_"):
                raise
        own = [n for n in dir(type(self)) if not n.startswith("_")]
        known = sorted({*own, *(n for n in dir(self._subparser) if not n.startswith("_"))})
        close = _closest(name, known)
        hint = (f"did you mean {close}?" if close else
                f"its methods include {', '.join(sorted(own))}, add_argument and add_argument_group")
        raise _setup_error(
            f"{_parser_of(self._subparser_name)}{name} does not exist; {hint}", ParserNotReadyError
        ) from None
    
    def __setattr__(self, name, value):
        if name.startswith('_'):
            # Internal attributes
            super().__setattr__(name, value)
        elif name == 'help':
            # Intercept help attribute setting
            self._help_text = value
            self._update_help_in_subparser_action()
        else:
            # Delegate to the wrapped subparser
            setattr(self._subparser, name, value)
    
    @property
    def help(self):
        """Get the current help text"""
        return self._help_text
    
    @help.setter
    def help(self, value):
        """Set the help text and update the subparser action"""
        self._help_text = value
        self._update_help_in_subparser_action()
    
    def _update_help_in_subparser_action(self):
        """Update the help text in the _SubParsersAction"""
        # Find the _SubParsersAction in the parent parser
        subparser_action = None
        for action in self._parent_parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                subparser_action = action
                break
        
        if subparser_action and hasattr(subparser_action, '_choices_actions'):
            for choice_action in subparser_action._choices_actions:
                if choice_action.dest == self._subparser_name:
                    choice_action.help = self._help_text
                    break
            else:
                # Added without help= (self.subparser.add_parser), so -h did not
                # list it and .help changed nothing: list it now, in its place.
                parsers = subparser_action._name_parser_map
                aliases = [n for n, p in parsers.items() if p is self._subparser and n != self._subparser_name]
                subparser_action._choices_actions.append(
                    subparser_action._ChoicesPseudoAction(self._subparser_name, aliases, self._help_text))
                names = list(parsers)
                subparser_action._choices_actions.sort(
                    key=lambda ca: names.index(ca.dest) if ca.dest in names else len(names))

    def _get_current_help_from_action(self):
        """Get the current help text from the subparser action"""
        subparser_action = None
        for action in self._parent_parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                subparser_action = action
                break
        
        if subparser_action and hasattr(subparser_action, '_choices_actions'):
            for choice_action in subparser_action._choices_actions:
                if choice_action.dest == self._subparser_name:
                    return choice_action.help
        return None

    def update_arguments(self, *args, **new_kwargs):
        """Update an existing argument with new properties.

        This method finds an existing argument in this subparser and redefines it with new properties
        such as help text, default value, group, etc.

        Args:
            *args: Argument name and option string (e.g., "--output-format", "-f")
            **new_kwargs: New properties to apply to the argument: the keywords
                add_argument takes, except action= (dest, nargs, const, default,
                type, choices, required, help and metavar; deprecated on Python
                3.13 and later; version for an option with action='version'),
                and group.

        A type= for the framework's -o runs after -o's own check, which keeps
        refusing a folder, a format name and a name ending in .tsv or .json.

        Returns:
            bool: True once the argument is updated.

        Raises:
            ParserSetupError (a ValueError): no option is named, the option doesn't
                exist here (the message suggests the closest name), or `args` names
                options other than its current ones. Renaming is not supported: remove
                the option with self.parser_<subcommand>.remove_argument("<flag>"), then
                add_argument() the new one (an option parse_args reads, which
                remove_argument refuses, keeps its names). Also
                raised for a dest= that changes a framework option's dest (the framework
                reads args.<dest>), that takes a dest a framework option has, or that
                is subcommand (the run script reads args.subcommand), for a new nargs=
                or const= of a framework option (it holds the one value the framework
                reads; type= may change), and for
                choices or a default of the framework's --output-format that
                write_results can't write (it writes tsv and json). Also raised for
                new choices without default= that leave out the current default,
                for a new default that is not among the choices (new or kept),
                for required=True on an option of a mutually exclusive group
                (make the group required instead), and for a group= title that
                differs from a section's title only in case, surrounding spaces or
                a trailing s (the message names the section).
            ParserKeywordError (a TypeError): a keyword add_argument does not take
                (the message suggests the closest one, or lists those it takes),
                action= (remove the option and add_argument() your own instead),
                version= for an option that is not a version option, or `group`
                is neither a section title (a str) nor an argument group of this
                subcommand (such as self.predict_input_group).
            Each message starts with the file:line of the call.

        Example:
            self.parser_postprocess.update_arguments("--custom-output-format", "-c",
                                dest="custom_output_format",
                                default="json",
                                help="Custom output format for postprocessed results (Default=json)",
                                metavar="CUSTOM_OUTPUT_FORMAT",
                                group="custom options"
                                )
        """
        if not args:
            raise _setup_error(
                f"update_arguments() got no option name; pass one of {self._subparser_name}'s "
                f"options first: {', '.join(_option_names(self._subparser))}"
            )

        # Get the primary argument name (first argument)
        argument_name = args[0]
        
        # Find the argument in any existing group
        target_action = None
        original_group = None
        
        for group in self._subparser._action_groups:
            for action in group._group_actions:
                if argument_name in action.option_strings:
                    target_action = action
                    original_group = group
                    break
            if target_action:
                break
        
        if not target_action or not original_group:
            raise _unknown_option(argument_name, self._subparser, self._subparser_name)

        # Option strings live in argparse's lookup table too, which this method
        # does not touch, so a new name would show in help but never parse.
        if len(args) > 1 and set(args) != set(target_action.option_strings):
            # remove_argument refuses an option parse_args reads, so it keeps its names.
            fix = (f"the framework reads args.{target_action.dest}, so it keeps its names."
                   if _read_by_parse_args(target_action) else
                   f"remove the option with {_remove_call(self._subparser_name, argument_name)}, "
                   f"then add_argument() the new one.")
            raise _setup_error(
                f"update_arguments can't rename {'/'.join(target_action.option_strings)} to "
                f"{'/'.join(args)}: {fix}"
            )

        # Only the keywords add_argument takes, and a misspelled one gets the
        # closest, as in add_argument (defualt= got no suggestion). Any attribute
        # of the option used to pass: option_strings= renamed it in -h only.
        where = f"{self._subparser_name} argument {'/'.join(args)}"
        for key in new_kwargs:
            if key == "action":
                fix = (f"the framework reads args.{target_action.dest}, so it keeps its action"
                       if _read_by_parse_args(target_action) else
                       f"remove the option with {_remove_call(self._subparser_name, argument_name)}, "
                       f"then add_argument() the new one")
                raise _setup_error(f"{where}: update_arguments() can't change action=: {fix}", ParserKeywordError)
            if key not in _update_keywords(target_action):
                if key == "version":
                    raise _setup_error(
                        f"{where}: update_arguments() can't set version=: only an option with "
                        f"action='version' has one",
                        ParserKeywordError,
                    )
                accepted = _update_keywords(target_action)
                close = _closest(key, accepted)
                hint = f"did you mean {close}=?" if close else f"update_arguments takes {', '.join(accepted)}"
                raise _setup_error(
                    f"{where}: update_arguments() got an unexpected keyword argument {key!r}; {hint}",
                    ParserKeywordError,
                )
        # A help section of this subcommand, given as its group: the same as its title.
        section = new_kwargs.get('group') if _section_of(new_kwargs.get('group'), self._subparser) else None
        if section is None:
            wrong = _group_keyword_error(self._subparser_name, target_action.option_strings, {},
                                         new_kwargs.get('group'))
            if wrong:
                raise wrong
            # A typo of a section's title made a second section next to it.
            close = _near_section(new_kwargs.get('group'), self._subparser)
            if close is not None:
                raise _near_section_error(self._subparser_name, target_action.option_strings, {},
                                          new_kwargs['group'], close)

        # The framework and the generated code read a framework option as
        # args.<dest>, so its dest stays, and no other option may take it.
        framework = [a for a in self._subparser._framework_actions if a in self._subparser._actions]
        replace = ("" if _read_by_parse_args(target_action) else
                   f" To replace the option, remove it with "
                   f"{_remove_call(self._subparser_name, argument_name)}, then add_argument() your own.")
        new_dest = new_kwargs.get('dest', target_action.dest)
        if new_dest != target_action.dest:
            if target_action in framework:
                raise _setup_error(
                    f"update_arguments can't change dest of {'/'.join(target_action.option_strings)} to "
                    f"{new_dest}: the framework reads args.{target_action.dest}; dest cannot change." + replace
                )
            owner = next((a for a in framework if a.dest == new_dest), None)
            if owner is not None:
                raise _setup_error(
                    f"update_arguments can't set dest of {'/'.join(target_action.option_strings)} to "
                    f"{new_dest}: dest {new_dest} belongs to the framework ({_long_name(owner)}); "
                    f"pick another dest."
                )
            # As add_argument refuses it (_SubcommandParser._check_conflict): the run
            # script would take no branch at all.
            if new_dest == "subcommand":
                raise _setup_error(
                    f"update_arguments can't set dest of {'/'.join(target_action.option_strings)} to "
                    f"subcommand: dest subcommand belongs to the framework (it records which subcommand "
                    f"ran); pick another dest."
                )

        # A framework option holds the one value the framework reads as
        # args.<dest>: nargs="+" on -f made it a list, and write_results stopped
        # with AttributeError; const= changes what a flag such as --assume-valid
        # stores. type= stays open: a check of your own still gives one value.
        if target_action in framework:
            for key in ("nargs", "const"):
                if key in new_kwargs and new_kwargs[key] != getattr(target_action, key):
                    raise _setup_error(
                        f"update_arguments can't set {key} of {'/'.join(target_action.option_strings)} to "
                        f"{new_kwargs[key]!r}: the framework reads args.{target_action.dest} as "
                        f"{'one value' if key == 'nargs' else 'the value it stores now'}." + replace
                    )

        # write_results writes only these formats, so the framework's -f may not
        # offer or default to another one ("unsupported output format" came only
        # once a result was written).
        if target_action.dest == "output_format" and target_action in self._subparser._framework_actions:
            names = '/'.join(target_action.option_strings)
            supported = " and ".join(_OUTPUT_FORMATS)
            # A tool that writes other formats itself replaces the option instead.
            own = (f" To offer formats you write yourself, remove the option with "
                   f"{_remove_call(self._subparser_name, target_action.option_strings[0])}, "
                   f"then add_argument() your own.")
            choices = new_kwargs.get("choices", ())
            if choices is None or not all(_writable_format(c) for c in choices):
                shown = ", ".join(map(str, choices)) if choices is not None else "None"
                raise _setup_error(
                    f"update_arguments can't set choices of {names} to {shown}: write_results writes only {supported}."
                    + own
                )
            default = new_kwargs.get("default")
            if default is not None and not _writable_format(default):
                raise _setup_error(
                    f"update_arguments can't set default of {names} to {default!r}: "
                    f"write_results writes only {supported}." + own
                )

        # New choices kept the old default: update_arguments("-f", choices=["json"])
        # left -f at tsv, which -h listed as "(choices: json; default: tsv)".
        if new_kwargs.get('choices') is not None and 'default' not in new_kwargs:
            missing = _default_outside(target_action.default, new_kwargs['choices'])
            if missing is not None:
                raise _setup_error(
                    f"update_arguments can't set choices of {'/'.join(target_action.option_strings)}: "
                    f"default {missing!r} is not among the new choices "
                    f"{_shown_choices(new_kwargs['choices'])}: pass default= too"
                )
        # A new default outside the choices, new or kept, as add_argument refuses it.
        if new_kwargs.get('default') is not None:
            choices = new_kwargs.get('choices', target_action.choices)
            missing = _default_not_in_choices(
                {"default": new_kwargs['default'], "type": new_kwargs.get('type', target_action.type)}, choices
            )
            if missing is not None:
                raise _setup_error(
                    f"update_arguments can't set default of {'/'.join(target_action.option_strings)}: "
                    f"{missing!r} is not among the choices "
                    f"{_shown_choices(choices)}: give a default from the choices, or pass choices= too"
                )

        # What add_argument refuses (type=bool, choices that need a type=, a
        # help= -h can't show), checked on a copy so a refusal changes nothing.
        import copy

        trial = copy.copy(target_action)
        for key, value in new_kwargs.items():
            if key != 'group':
                setattr(trial, key, value)
        mistake = _option_mistake(trial)
        if mistake:
            raise _setup_error(f"{where}: {mistake}")

        # -o keeps its own check under a type= of yours (see _after_output_prefix).
        if 'type' in new_kwargs and getattr(target_action, "_framework_type", None) is _output_prefix:
            new_kwargs['type'] = _after_output_prefix(new_kwargs['type'])

        # argparse refuses required=True in a mutually exclusive group, since the
        # other options could never be given: -j required made -t unusable.
        if new_kwargs.get('required'):
            exclusive = next((group for group in self._subparser._mutually_exclusive_groups
                              if target_action in group._group_actions), None)
            others = [a for a in exclusive._group_actions if a is not target_action] if exclusive else []
            if exclusive is not None and others:
                members = " / ".join(min(a.option_strings, key=len) if a.option_strings else a.dest
                                     for a in exclusive._group_actions)
                raise _setup_error(
                    f"{_long_name(target_action)} is one of a group of mutually exclusive options "
                    f"({members}): make the group required instead, or remove the other "
                    f"option{'s' if len(others) > 1 else ''}"
                )

        # Update option strings if new ones are provided
        if len(args) > 1:
            target_action.option_strings = list(args)
        
        # Apply new properties to the action
        for key, value in new_kwargs.items():
            if key != 'group':
                setattr(target_action, key, value)
        
        # Handle group change if specified
        new_group_name = new_kwargs.get('group')
        if new_group_name:
            # Find or create the new group
            new_group = section
            if new_group is None:
                for group in self._subparser._action_groups:
                    if group.title == new_group_name:
                        new_group = group
                        break

            if not new_group:
                # Create new group if it doesn't exist
                new_group = self._subparser.add_argument_group(new_group_name)
            
            # Move to the end of the new group; group= naming the option's own
            # section leaves it in place.
            if new_group is not original_group:
                original_group._group_actions.remove(target_action)
                new_group._group_actions.append(target_action)
        # Otherwise the option keeps its place in its group: moving it to the end
        # put -o below -f in predict -h after update_arguments("-o", help=...).

        return True

    # The singular, as remove_argument is: update_argument gave an AttributeError traceback.
    update_argument = update_arguments

    def remove_argument(self, argument_name, subparser_name=None):
        """Remove an option from this subparser, under every name it has.

        The same as NGArgumentParser.remove_argument(argument_name, '<this subcommand>'):
        self.parser_predict.remove_argument("--output-format") also frees '-f'.
        `subparser_name` may be given, as for that form, and must name this
        subcommand: self.parser_predict.remove_argument("--output-format",
        "predict") gave a TypeError traceback.

        Raises:
            ParserSetupError (a ValueError): this subparser has no such option (the
                message suggests the closest name), the option is one parse_args
                reads: preprocess --params-dir and --inputs-dir, postprocess
                --job-desc-file and --input-results-dir (change it with
                update_arguments instead), or `subparser_name` names another
                subcommand. The message starts with the file:line of the call.
        """
        if subparser_name is not None:
            parsers = self._parent_parser.subparser._name_parser_map
            if parsers.get(subparser_name) is not self._subparser:
                # Which parser was meant: the one named, when it is a subcommand.
                meant = next((name for name, parser in parsers.items() if parser is parsers.get(subparser_name)),
                             self._subparser_name)
                raise _setup_error(
                    f"{_parser_of(self._subparser_name)}remove_argument({argument_name!r}, {subparser_name!r}): "
                    f"this is the {self._subparser_name} parser, not {subparser_name}; use "
                    f"{_remove_call(meant, argument_name)}"
                )
        _remove_option(self._subparser, argument_name, self._subparser_name)

    def _mark_framework_options(self):
        """Record the options declared so far as the framework's built-in ones, so
        a tool option that reuses one of their names is reported by name."""
        self._subparser._framework_actions = [
            action for action in self._subparser._actions
            if action.option_strings and not isinstance(action, argparse._HelpAction)
        ]
        # Their own type= checks, which keep argparse's error handling (see _get_value).
        for action in self._subparser._framework_actions:
            action._framework_type = action.type
        self._subparser._subcommand = self._subparser_name




class NGArgumentParser(argparse.ArgumentParser):
    ''' Setting default paths '''
    # Not a test class: pytest collected a parser named Test...ArgumentParser
    # imported into a test file, and warned that it could not.
    __test__ = False

    # defaults for preprocessing
    PROJECT_ROOT_PATH = Path(__file__).resolve().parents[1]

    # Set by __init__. A subclass that never calls super().__init__() is told so
    # when it reads one of them (see _SetByInit).
    subparser = _SetByInit()
    parser_predict = _SetByInit()
    predict_input_group = _SetByInit()
    parser_preprocess = _SetByInit()
    preprocess_required_group = _SetByInit()
    preprocess_optional_group = _SetByInit()
    parser_postprocess = _SetByInit()
    postprocess_required_input_group = _SetByInit()
    postprocess_optional_group = _SetByInit()
    # Not an attribute: Python suggested argparse's _subparsers, which has no add_parser.
    subparsers = _Misnamed("self.subparsers does not exist: add a subcommand of your own with "
                           "self.add_subcommand(name, help=...)")

    # The keywords __init__ takes, for the parser as a whole (the generated parser
    # sets them as attributes after super().__init__() too).
    _INIT_KEYWORDS = ("prog", "usage", "description", "epilog", "formatter_class")

    def _require_init(self):
        """Stop with one line when NGArgumentParser.__init__ has not run."""
        if "subparser" not in vars(self):
            raise _not_initialized(self)

    def add_argument(self, *args, **kwargs):
        """argparse's add_argument, for an option of the top-level command (given
        before the subcommand).

        Raises:
            ParserKeywordError (a TypeError): group= was given. It works on a
                subcommand's parser (self.parser_predict.add_argument(..., group=...)),
                not here; argparse said only "unexpected keyword argument 'group'".
                Also raised for a keyword argparse refuses, such as a misspelled
                help= (the message suggests the closest keyword).
            ParserSetupError (a ValueError): argparse refused a value, such as
                type="int".
            Each message starts with the file:line of the call.
        """
        if 'group' in kwargs:
            wrong = _group_keyword_error("top-level", args, kwargs, kwargs['group'])
            if wrong:
                raise wrong
            raise _setup_error(
                f"top-level argument {'/'.join(args) or kwargs.get('dest', '')}: group= works only on "
                f"a subcommand's options; use self.parser_predict.add_argument(..., group=\"{kwargs['group']}\") "
                f"(or parser_preprocess, parser_postprocess), or drop group=",
                ParserKeywordError,
            )
        return _reporting_errors(super().add_argument, lambda: "top-level")(*args, **kwargs)

    def _get_value(self, action, arg_string):
        # Every top-level option with a type= is the tool's own (see _checked_value).
        return _checked_value(self, action, arg_string, super()._get_value)

    def _get_values(self, action, arg_strings):
        return _note_typed(self, super()._get_values, action, arg_strings)

    def _check_conflict(self, action):
        # A --version of the tool's own replaces the framework's: tcell_mhcii
        # prints its own, and two of them gave "conflicting option string".
        version = vars(self).get("_version_action")
        if version is not None and version in self._actions and set(action.option_strings) & set(
                version.option_strings):
            _remove_option(self, version.option_strings[0], "top-level")
        super()._check_conflict(action)

    def add_argument_group(self, *args, **kwargs):
        group = super().add_argument_group(*args, **kwargs)
        # An option added through the group replaces the framework's --version too.
        setattr(group, "_check_conflict", self._check_conflict)
        return group

    def add_subparsers(self, **kwargs):
        """argparse's add_subparsers, which the framework calls once, in __init__.
        A second call raises ParserSetupError naming self.add_subcommand: argparse
        said "error: cannot have multiple subparser arguments", as for a
        command-line mistake."""
        if "subparser" in vars(self):
            raise _setup_error(
                "add_subparsers(): the framework has added the subcommands already (self.subparser); "
                "add one of your own with self.add_subcommand(name, help=...)"
            )
        return super().add_subparsers(**kwargs)

    def __init__(self, *args, **kwargs):
        """Set up the framework's subcommands and options.

        Args:
            **kwargs: prog, usage, description, epilog and formatter_class of the
                parser as a whole, as in argparse; the generated parser sets them
                as attributes after super().__init__() instead. The framework's
                epilog (where to ask for help) is kept when no epilog is given.

        Raises:
            ParserKeywordError (a TypeError): a positional argument, or another
                keyword (the message suggests the closest one). Python gave a
                TypeError traceback for super().__init__(description="My tool").
                The message starts with the file:line of the call.
        """
        if args:
            raise _setup_error(
                f"{type(self).__name__}: super().__init__() takes keywords only, such as "
                f"super().__init__(description=\"My tool\")",
                ParserKeywordError,
            )
        for key in kwargs:
            if key not in self._INIT_KEYWORDS:
                close = _closest(key, list(self._INIT_KEYWORDS))
                hint = (f"did you mean {close}=?" if close else
                        f"it takes {', '.join(self._INIT_KEYWORDS)}; set another setting as self.<name> after it")
                raise _setup_error(
                    f"super().__init__() got an unexpected keyword argument {key!r}; {hint}", ParserKeywordError
                )
        # prog goes to argparse itself, so the subcommands' usage lines show it too.
        super().__init__(**{key: kwargs[key] for key in ("prog", "usage") if key in kwargs})
        # self.prog='The name of the program (default: os.path.basename(sys.argv[0]))'
        # self.usage='The string describing the program usage (default: generated from arguments added to parser)'
        self.formatter_class = kwargs.get("formatter_class", argparse.RawDescriptionHelpFormatter)
        self.description = kwargs.get("description")
        self.epilog=kwargs.get("epilog", textwrap.dedent('''
        Please contact us with any issues encountered or questions about the software
        through the channel listed below.

        IEDB Discussion Forum: https://discuss.iedb.org
        '''))

        # run_x.py --version: the tool's version (see _ToolVersionAction).
        self._version_action = super().add_argument(
            "--version", action=_ToolVersionAction, help="show the tool's version and exit"
        )
        
        self.subparser = self.add_subparsers(
            title='subcommands',
            dest='subcommand',
            # Explicitly set this to prevent the following error.
            # TypeError: __init__() got an unexpected keyword argument 'prog'
            # (an argparse.ArgumentParser that names the framework option in a clash).
            parser_class=_SubcommandParser,
            # A subcommand the tool adds with self.add_subcommand takes
            # group=, as predict does (see _SubcommandsAction).
            action=_SubcommandsAction,
            required=True
            )
        self.subparser._top_parser = self
        
        # Placeholders for subparser 'predict' and its input group, until
        # add_predict_subparser() creates them: using one says so.
        self.parser_predict=_NotReady("self.parser_predict")
        self.predict_input_group=_NotReady("self.predict_input_group")

        # Variable to decide whether or not the default output file structure
        # should be used or not.
        self.use_default_fs=True
        
        # Create subparser 'preprocess'
        # -----------------------------------------------------
        # The framework's subcommands keep the line breaks of a description
        # (RawDescriptionHelpFormatter), as the top-level command does.
        preprocess_parser = self.subparser._add_framework_parser('preprocess', 
                                                 help='Preprocess jobs.',
                                                 description='Split a JSON input file into smaller job units, if possible, and create\n'
                                                             'job_descriptions.json, which lists the command of every job in the workflow.',
                                                 formatter_class=argparse.RawDescriptionHelpFormatter)
        
        # Wrap the preprocess parser to enable help text modification
        self.parser_preprocess = SubparserWrapper(preprocess_parser, 'preprocess', self)

        # Create argument groups
        self.preprocess_required_group = self.parser_preprocess.add_argument_group('required parameters')
        self.preprocess_optional_group = self.parser_preprocess.add_argument_group('optional parameters')

        # Define required parameters for preprocess subcommand
        self.preprocess_required_group.add_argument("--input-json", "-j",
                                        dest="input_json",
                                        type=validators.validate_file,
                                        required=True,
                                        help="JSON file containing input parameters.",
                                        metavar="JSON_FILE")
        
        # The checks below only check. The folders are created in parse_args(),
        # once the whole command line has been read and is valid.
        self.preprocess_required_group.add_argument("--output-dir", "-o",
                                        dest="output_dir",
                                        type=_not_a_file,
                                        required=True,
                                        help="Output folder of the job: job_descriptions.json and the "
                                             "predict-inputs/ folder layout go here (created if missing).",
                                        metavar="OUTPUT_DIR")

        # Define optional parameters for preprocess subcommand
        # defaults will be set dynamically based on output-dir
        self.preprocess_optional_group.add_argument("--params-dir",
                                        dest="preprocess_parameters_dir",
                                        type=_not_a_file,
                                        help="""
                                        Folder for the preprocessed JSON input files
                                        (default: $OUTPUT_DIR/predict-inputs/params, or
                                        $OUTPUT_DIR/$APP_NAME/predict-inputs/params when
                                        paths.py declares dependencies).
                                        """)

        # defaults will be set dynamically based on output-dir
        self.preprocess_optional_group.add_argument("--inputs-dir",
                                        dest="preprocess_inputs_dir",
                                        type=_not_a_file,
                                        help="""
                                        Folder for the other, non-JSON inputs, such as FASTA files
                                        (default: $OUTPUT_DIR/predict-inputs/data, or
                                        $OUTPUT_DIR/$APP_NAME/predict-inputs/data when
                                        paths.py declares dependencies).
                                        """)

        self.preprocess_optional_group.add_argument("--assume-valid",
                                        action="store_true",
                                        dest="assume_valid_flag",
                                        default=False,
                                        help="Skip validation of the inputs (assume they are valid).")
        
        # Create subparser 'postprocess'
        # -----------------------------------------------------
        postprocess_parser = self.subparser._add_framework_parser('postprocess', 
                                                        help='Postprocess jobs.',
                                                        description='Aggregate the results of the individual prediction jobs into one result.',
                                                        epilog='Note: Exactly one of --job-desc-file or --input-results-dir must be specified.',
                                                        formatter_class=argparse.RawDescriptionHelpFormatter,
                                                        )

        # Wrap the postprocess parser to enable help text modification
        self.parser_postprocess = SubparserWrapper(postprocess_parser, 'postprocess', self)

        # Create input source group (mutually exclusive options)
        self.postprocess_required_input_group = self.parser_postprocess.add_argument_group('input source (choose exactly one)')

        self.postprocess_required_input_group.add_argument("--job-desc-file", "-j",
                                dest="job_desc_file",
                                type=validators.validate_file,
                                help="Job description file (job_descriptions.json) that preprocess wrote.")

        self.postprocess_required_input_group.add_argument("--input-results-dir", "-i",
                                dest="postprocess_input_dir",
                                type=validators.validate_directory,
                                help="Folder of the result files to postprocess.")

        # Optional parameters group. -p sits here alongside -o/-f: all three
        # concern where output goes. It is deliberately not required: tools
        # such as axelf, conservancy, and rate accept -o instead or fall back
        # to the working directory.
        self.postprocess_optional_group = self.parser_postprocess.add_argument_group('optional parameters')

        self.postprocess_optional_group.add_argument("--postprocessed-results-dir", "-p",
                                dest="postprocess_result_dir",
                                type=validators.validate_directory,
                                help="Existing folder for the postprocessed results.")

        self.postprocess_optional_group.add_argument("--output-prefix", "-o",
                                dest="output_prefix",
                                type=_output_prefix,
                                help="Output prefix for the merged result files (the results of every job, "
                                     "combined); the extension (.tsv or .json) is added.",
                                metavar="OUTPUT_PREFIX")

        self.postprocess_optional_group.add_argument("--output-format", "-f",
                                dest="output_format",
                                choices=["tsv", "json"],
                                default="json",
                                help="Output format of the merged result "
                                     "(choices: %(choices)s; default: %(default)s).")

        # Add patch for groups
        self.patch_parser_for_groups(self.parser_preprocess)
        self.patch_parser_for_groups(self.parser_postprocess)
        for attribute in ("preprocess_required_group", "preprocess_optional_group",
                          "postprocess_required_input_group", "postprocess_optional_group"):
            _take_own_group_name(getattr(self, attribute), attribute)

        # Everything above is framework-owned; a tool option reusing a name gets told so.
        self.parser_preprocess._mark_framework_options()
        self.parser_postprocess._mark_framework_options()

        # parse_args reads these as args.<dest> (the preprocess folders, the postprocess
        # inputs), so remove_argument refuses them. The others may go: parse_args
        # checks for args.output_dir first, and reads none of the rest.
        for parser, flags in ((self.parser_preprocess, ("--params-dir", "--inputs-dir")),
                              (self.parser_postprocess, ("--job-desc-file", "--input-results-dir"))):
            for flag in flags:
                parser._option_string_actions[flag]._read_by_parse_args = True


    def add_predict_subparser(self, help='', description='', formatter_class=argparse.RawDescriptionHelpFormatter,
                              **kwargs):
        '''
        Creates and returns a 'predict' subparser with customizable help and description text.
        
        This method adds a new subparser for prediction-related commands. The subparser is 
        configured with user-provided help and description text, and can be further customized
        by adding tool-specific arguments. Common arguments that are shared across different
        tools can also be added here.

        Args:
            help (str): Help text displayed in the main command list
            description (str): Detailed description shown in the predict command's help
            formatter_class: The help formatter class of predict -h; the default,
                RawDescriptionHelpFormatter, keeps the line breaks of `description`
            **kwargs: Other keywords argparse's add_parser takes, such as epilog,
                usage or prog, passed on to it. Not aliases (the run script runs
                predict only when args.subcommand is 'predict') or
                conflict_handler (it would let an option take a framework flag).

        Returns:
            SubparserWrapper: The configured predict subparser wrapper that can be customized with
                            additional tool-specific arguments and allows help text modification

        Raises:
            ParserNotReadyError (an AttributeError): NGArgumentParser.__init__ has not run
                (the subclass never called super().__init__()).
            ParserConflictError (an argparse.ArgumentError): predict exists already
                (a second call).
            ParserKeywordError (a TypeError): a keyword add_parser does not take
                (the message suggests the closest one), or aliases= or conflict_handler=.
            Each message starts with the file:line of the call.
        '''
        self._require_init()
        # argparse raised "conflicting subparser: predict" with a traceback (3.11
        # and later), or added a second predict (3.9, 3.10).
        if 'predict' in self.subparser.choices:
            raise _setup_error(
                "add_predict_subparser() was called a second time: predict already exists; "
                "call it once, and add options to the self.parser_predict it returns",
                ParserConflictError,
            )
        # Python said only "add_predict_subparser() got an unexpected keyword argument 'epilog'".
        self._check_subparser_keywords("add_predict_subparser", kwargs)
        # RawDescriptionHelpFormatter by default (formatter_class), which keeps the
        # line breaks of a multi-line description; HelpFormatter joined its lines.
        predict_parser = self.subparser._add_framework_parser('predict', 
                                                  help=help, 
                                                  description=description,
                                                  formatter_class=formatter_class,
                                                  **kwargs)
        
        # Wrap the predict parser to enable help text modification
        self.parser_predict = SubparserWrapper(predict_parser, 'predict', self)

        # Add patch for groups
        self.patch_parser_for_groups(self.parser_predict)

        # Created before the output arguments so 'input options' sorts above
        # 'output options' in help (argparse orders groups by creation, not by
        # when they're populated). Tool-specific input arguments go here; an
        # unpopulated group renders nothing, so tools with no inputs are
        # unaffected. Rename per-tool with:
        #   self.predict_input_group.title = "..."
        self.predict_input_group = self.parser_predict.add_argument_group("input options")
        _take_own_group_name(self.predict_input_group, "predict_input_group")

        # Common output arguments shared across all tools (framework-owned).
        # Results serialize uniformly via core.result_writer.write_results: tsv by
        # default (to stdout when no -o, else <prefix>.tsv), or json via -f.
        self.parser_predict.add_argument("--output-prefix", "-o",
                                dest="output_prefix",
                                type=_output_prefix,
                                help="Output prefix for the prediction result files; the extension "
                                     "(.tsv or .json) is added.",
                                metavar="OUTPUT_PREFIX",
                                group="output options")
        self.parser_predict.add_argument("--output-format", "-f",
                                dest="output_format",
                                choices=["tsv", "json"],
                                default="tsv",
                                help="Output format of the prediction result "
                                     "(choices: %(choices)s; default: %(default)s).",
                                group="output options")
        self.parser_predict._mark_framework_options()

        # add common arguments across tools
        # self.parser_predict.add_argument("--input-json", "-j",
        #                          dest="input_json",
        #                          help="JSON file containing input parameters.",
        #                          metavar="JSON_FILE")
        # self.parser_predict.add_argument("--assume-valid",
        #                                 action="store_true",
        #                                 dest="assume_valid_flag",
        #                                 default=False,
        #                                 help="flag to indicate validation can be skipped")
        
        return self.parser_predict

    # What each keyword would break, for the add_parser keywords add_predict_subparser refuses.
    _REFUSED_SUBPARSER_KEYWORDS = {
        "aliases": "the run script runs predict only when args.subcommand is 'predict', "
                   "and an alias would set args.subcommand to the alias",
        "conflict_handler": "it would let an option of yours take a flag of a framework option; "
                            "to replace one, remove it with self.parser_predict.remove_argument(...) first",
    }

    def _check_subparser_keywords(self, method, kwargs):
        """Raise ParserKeywordError for a keyword of `kwargs` that argparse's
        add_parser, on this Python, does not take (naming the closest one), or
        one that _REFUSED_SUBPARSER_KEYWORDS refuses."""
        _check_parser_keywords(method, kwargs, self._REFUSED_SUBPARSER_KEYWORDS)

    def add_subcommand(self, name, help=None, **kwargs):
        """Add a subcommand of the tool's own, such as list-alleles, and return its
        parser: a SubparserWrapper, as self.parser_predict is. Its add_argument
        takes group=, and it has update_arguments, remove_argument and .help. The
        run script runs it in a branch of its own in main():
        elif args.subcommand == 'list-alleles':.

        Args:
            name (str): The subcommand's name.
            help (str): Its line in the top-level -h.
            **kwargs: Other keywords argparse's add_parser takes, such as
                description, epilog or aliases.

        The same as self.subparser.add_parser(name, help=help, **kwargs).

        Raises:
            ParserConflictError (an argparse.ArgumentError): `name` (or an alias)
                is a subcommand already, the framework's three included.
            ParserKeywordError (a TypeError): a keyword add_parser does not take
                (the message suggests the closest one).
            Each message starts with the file:line of the call.
        """
        self._require_init()
        return self.subparser._add_tool_parser("self.add_subcommand", name, {"help": help, **kwargs})

    def set_subcommand_order(self, order):
        """Reorder subcommands in help output.
        
        This method changes the display order of subcommands in the help text
        without affecting the actual parsing behavior.

        The named subcommands come first, in that order. Every other subcommand
        follows in the order it was added, including one added after this call
        (with add_predict_subparser() or self.add_subcommand()), which
        parses as any other.
        
        Args:
            order (list): List of subcommand names in desired display order.
                         e.g., ['predict', 'preprocess', 'postprocess']

        Raises:
            ParserSetupError (a ValueError): a name is not a subcommand (the message
                suggests the closest one, or says it is not added yet: call this after
                self.add_subcommand() of a subcommand of your own), names predict
                before add_predict_subparser() has run, or is listed twice (-h listed
                that subcommand twice). The message starts with the file:line of the call.
        """
        self._require_init()
        # Find the _SubParsersAction
        for action in self._actions:
            if isinstance(action, argparse._SubParsersAction):
                # Check every name before reordering, so a typo changes nothing.
                seen = []
                for name in order:
                    if name in seen:
                        raise _setup_error(
                            f"set_subcommand_order: subcommand {name!r} is listed twice; name each subcommand once."
                        )
                    seen.append(name)
                    if name in action.choices:
                        continue
                    if name == 'predict':
                        raise _setup_error(
                            "set_subcommand_order: the predict subparser hasn't been added yet; "
                            "call self.add_predict_subparser(...) first."
                        )
                    names = list(action.choices)
                    close = _closest(name, names)
                    listed = ', '.join(names[:-1]) + ' or ' + names[-1] if len(names) > 1 else names[0]
                    if close:
                        raise _setup_error(
                            f"set_subcommand_order: unknown subcommand {name!r}; did you mean '{close}'?"
                        )
                    # Most likely a subcommand of the tool's own, named before it is added.
                    raise _setup_error(
                        f"set_subcommand_order: subcommand {name!r} is not added (yet); call "
                        f"set_subcommand_order after self.add_subcommand({name!r}, ...), or use {listed}."
                    )
                # Reorder argparse's own records in place: its help listing, and
                # its map of subcommand parsers, which is also action.choices (what
                # parses, and the {a,b,c} of the usage line). A new map broke every
                # subcommand added later: add_parser wrote to argparse's old one, so
                # -h listed it and running it gave "invalid choice".
                # The named subcommands come first, in that order; the others follow
                # in the order they were added, as does one added after this call.
                # A subcommand's aliases stay right after its own name, as add_parser
                # puts them: {predict,list-alleles,la,...}, not at the end.
                parsers = action._name_parser_map

                def names_of(name):
                    """Every name of `name`'s subcommand, its own first."""
                    return [other for other, parser in parsers.items() if parser is parsers[name]]

                ordered: Dict[str, argparse.ArgumentParser] = {}
                for name in [*order, *parsers]:
                    for other in names_of(name):
                        ordered.setdefault(other, parsers[other])
                own = [names_of(name)[0] for name in order]
                named = [ca for name in own for ca in action._choices_actions if ca.dest == name]
                action._choices_actions[:] = named + [ca for ca in action._choices_actions if ca not in named]
                parsers.clear()
                parsers.update(ordered)
                break

    def validate_mutually_exclusive_args(self, args):
        """Manually validate that exactly one of the mutually exclusive args is provided"""
        job_desc_provided = args.job_desc_file is not None
        input_dir_provided = args.postprocess_input_dir is not None
        # Every name of each, as argparse names a mutually exclusive option (predict
        # says --input-tsv/-t): the short flags were left out.
        job_desc, input_dir = ('/'.join(self.parser_postprocess._option_string_actions[flag].option_strings)
                               for flag in ("--job-desc-file", "--input-results-dir"))
        
        if job_desc_provided and input_dir_provided:
            self.parser_postprocess.error(f"argument {input_dir}: not allowed with argument {job_desc}")
        elif not job_desc_provided and not input_dir_provided:
            self.parser_postprocess.error(f"one of the arguments {job_desc} {input_dir} is required")
    
    def parse_args(self, args=None, namespace=None):
        """Parse command line arguments and perform validation.

        This method extends the base ArgumentParser's parse_args() to add custom validation
        for mutually exclusive arguments in the postprocess command. It ensures that exactly
        one of --job-desc-file or --input-results-dir is provided when using postprocess.

        Args:
            args: The arguments to parse, as a list of strings. None (the default) reads the
                command line (sys.argv), so a test can pass a list instead:
                parse_args(["predict", "-j", "in.json"]).
            namespace: An object to put the results on, as in argparse.

        Returns:
            argparse.Namespace: The parsed command-line arguments with all validations passed

        Raises:
            ArgumentError: If validation fails for mutually exclusive arguments

        An unrecognized option that belongs to the top-level command (given after
        the subcommand) or to another subcommand fails as in argparse (exit code 2),
        and the message says where the option belongs. A misspelled option fails
        the same way, and the message names the closest option.
        """
        self._require_init()
        args, unrecognized = self.parse_known_args(args, namespace)
        subcommand = getattr(args, 'subcommand', None)
        if unrecognized:
            # argparse's own check, plus where each option belongs: argparse said
            # only "unrecognized arguments: --min-length 3".
            message = self._unrecognized_message(subcommand, unrecognized)
            if self.exit_on_error:
                # With the usage of the subcommand the options were given to, not
                # the top-level one; a top-level option alone keeps the top-level usage.
                given = self.subparser._name_parser_map.get(subcommand)
                options = [word for word in unrecognized if word.startswith("-")]
                if given is None or (options and all(_option_named(word, self) for word in options)):
                    self.error(message)
                given.error(message)
            raise argparse.ArgumentError(None, message)

        # -o naming an input (-t peptides.tsv -o peptides) wrote the result over it.
        self._refuse_output_over_input(args)

        # If preprocess command is used and output_dir is set, set defaults for other args
        if hasattr(args, 'subcommand') and args.subcommand == 'preprocess':
            if hasattr(args, 'output_dir') and args.output_dir:
                from pathlib import Path
                # A named folder must exist already or be one the layout creates;
                # check that before creating anything, so a failed command leaves
                # nothing behind.
                named = (("--params-dir", args.preprocess_parameters_dir),
                         ("--inputs-dir", args.preprocess_inputs_dir))
                if any(value and not Path(value).is_dir() for _, value in named):
                    layout = self._preprocess_layout(args.output_dir)
                    for flag, value in named:
                        if value and not Path(value).is_dir() and Path(value).resolve() not in layout:
                            # The type check refused an existing non-folder, so it is missing.
                            self.parser_preprocess.error(f"argument {flag}: '{value}' does not exist.")
                # The whole command line is valid now, so create the folders. Then
                # check the folders the user named (they may live under -o), so the
                # order of -o, --params-dir and --inputs-dir doesn't matter.
                created = self._create_preprocess_dirs(args.output_dir)
                for flag, value in (("--params-dir", args.preprocess_parameters_dir),
                                    ("--inputs-dir", args.preprocess_inputs_dir)):
                    if value and not Path(value).is_dir():
                        self.parser_preprocess.error(f"argument {flag}: '{value}' does not exist.")
                # The defaults sit in the tool's own tree, the one holding results/:
                # $OUTPUT_DIR without dependencies, $OUTPUT_DIR/<APP_NAME> with them.
                own_tree = next((Path(d).parent for dirs in created.values() for d in dirs
                                 if Path(d).name == "results"), Path(args.output_dir))
                # A str whether named or defaulted (the type check returns a Path).
                args.preprocess_inputs_dir = str(args.preprocess_inputs_dir
                                                 or own_tree / "predict-inputs" / "data")
                args.preprocess_parameters_dir = str(args.preprocess_parameters_dir
                                                     or own_tree / "predict-inputs" / "params")
            
        # If postprocess command is used, validate mutual exclusion
        if hasattr(args, 'subcommand') and args.subcommand == 'postprocess':
            self.validate_mutually_exclusive_args(args)

        return args

    def parse_known_args(self, args=None, namespace=None):
        # The words given, for the hints of error().
        self._ng_words = list(sys.argv[1:] if args is None else args)
        return super().parse_known_args(args, namespace)

    def error(self, message):
        """argparse's error, but an option given before the subcommand that the
        top-level command lacks is reported first, with where it goes: -j f predict
        said "invalid choice: 'f'", and --versoin "the following arguments are
        required: subcommand"."""
        if _required_error(message) or (_choice_error(message) and "did you mean" not in message):
            unknown = self._early_unknown()
            if unknown:
                message = self._unrecognized_message(self._named_subcommand(), unknown)
        super().error(message)

    def _check_value(self, action, value):
        # A mistyped subcommand (predcit, Predict) got no suggestion.
        try:
            super()._check_value(action, value)
        except argparse.ArgumentError as e:
            if action is not vars(self).get("subparser") or not isinstance(value, str) or "maybe you meant" in str(e):
                raise
            names = [name for name in action.choices if isinstance(name, str)]
            close = next((name for name in names if name.lower() == value.lower()), None) or _closest(value, names)
            if close is None:
                raise
            raise argparse.ArgumentError(action, f"{e.message}; did you mean '{close}'?") from None

    def _early_unknown(self):
        """The words given before the subcommand that look like options the
        top-level command lacks (see _unknown_options)."""
        return _unknown_options(self, vars(self).get("_ng_words", ()), before_positional=True)

    def _named_subcommand(self):
        """The first subcommand named among the words given (by its own name, for
        an alias), or None."""
        parsers = self.subparser._name_parser_map
        for word in vars(self).get("_ng_words", ()):
            if word == "--":
                break
            if word in parsers:
                return getattr(parsers[word], "_subcommand", None) or word
        return None

    def _unrecognized_message(self, subcommand, words):
        """argparse's "unrecognized arguments: ..." for `words`, then a hint for each
        (see _unrecognized_hints)."""
        return "; ".join([gettext('unrecognized arguments: %s') % ' '.join(words),
                          *self._unrecognized_hints(subcommand, words)])

    def _unrecognized_hints(self, subcommand, words):
        """Where each option among `words` (what `subcommand` did not take) belongs:
        the top-level command, `subcommand` itself when given before it, or other
        subcommands. An option of none gets the closest option of `subcommand` or
        the top-level command. Words that are no option and close to none, such
        as values, get no hint. `subcommand` None: none was given."""
        others: Dict[str, argparse.ArgumentParser] = {}
        for name, parser in self.subparser.choices.items():
            if name != subcommand and parser is not self.subparser.choices.get(subcommand) and all(
                    parser is not other for other in others.values()):
                others[name] = parser  # an alias of one listed already is left out
        early = self._early_unknown()
        hints = []
        for word in words:
            name = _option_named(word, self)
            own = _option_named(word, self.subparser.choices[subcommand]) if subcommand else None
            if name:
                where = f"'{subcommand}'" if subcommand else "the subcommand"
                hint = f"{name} is an option of the top-level command; put it before {where}"
            elif own and word in early:
                hint = f"{own} is an option of {subcommand}; put it after '{subcommand}'"
            else:
                owners = [(other, _option_named(word, parser)) for other, parser in others.items()]
                owners = [(other, option) for other, option in owners if option]
                if owners and subcommand:
                    hint = f"{owners[0][1]} is an option of {_listed([o for o, _ in owners])}, not {subcommand}"
                elif owners:
                    where = f"'{owners[0][0]}'" if len(owners) == 1 else "the subcommand"
                    hint = f"{owners[0][1]} is an option of {_listed([o for o, _ in owners])}; put it after {where}"
                else:
                    hint = self._closest_option_hint(subcommand, word)
                    if hint is None:
                        continue
            if hint not in hints:
                hints.append(hint)
        return hints

    def _refuse_output_over_input(self, args):
        """Stop with a usage error when the file -o and -f name (<prefix>.<format>,
        as write_results writes it) is a file given to another option of the
        subcommand, such as -t or a positional input (each file of a list): predict
        -t peptides.tsv -o peptides wrote the result over the input."""
        parser = self.subparser._name_parser_map.get(getattr(args, "subcommand", None))
        prefix, output_format = getattr(args, "output_prefix", None), getattr(args, "output_format", None)
        # A type= of the tool's own may give a Path (type=pathlib.Path, or
        # validators.validate_directory_given_filename): it names the same files,
        # and skipping it let -o data/in write data/in.tsv over -t data/in.tsv.
        if isinstance(prefix, os.PathLike):
            prefix = os.fspath(prefix)
        if parser is None or not isinstance(prefix, str) or not isinstance(output_format, str):
            return
        option = next((a for a in parser._actions if a.dest == "output_prefix"), None)
        output = f"{prefix}.{output_format.lower()}"
        if (option is None or getattr(option, "_framework_type", None) is not _output_prefix
                or not _writable_format(output_format) or not os.path.isfile(output)):
            return
        for action in parser._actions:
            if action is option:
                continue
            given = getattr(args, action.dest, None)
            for value in given if isinstance(given, (list, tuple)) else [given]:
                if isinstance(value, (str, os.PathLike)) and os.path.isfile(value):
                    try:
                        same = os.path.samefile(value, output)
                    except OSError:
                        same = False
                    if same:
                        parser.error(
                            f"argument {'/'.join(option.option_strings)}: -o {prefix} would write {output} over "
                            f"the input given to {argparse._get_action_name(action) or action.dest}; pick another prefix"
                        )

    def _closest_option_hint(self, subcommand, word):
        """'did you mean --min-length?' for command-line word `word`, a misspelled
        option (--min-lenght, --min-lenght=3) given to `subcommand`: its closest
        option among those of `subcommand` and the top-level command. A top-level
        option says where it goes. None for a word that is no option (a value) or
        is close to none. Options hidden from -h (help=SUPPRESS) are not offered."""
        if not word.startswith("-"):
            return None

        def shown(parser):
            return {s for action in parser._actions if action.help != argparse.SUPPRESS
                    for s in action.option_strings}

        own = shown(self.subparser.choices[subcommand]) if subcommand else set()
        top = shown(self)
        typed = word.split("=", 1)[0]
        # A wrong case (-J for -j) is close to none, as difflib compares case.
        option = next((option for option in sorted(own | top) if option.lower() == typed.lower()), None)
        if option is None:
            # Compared without their dashes, which every option shares: with them,
            # --bogus came out close to --verbose.
            bare: Dict[str, str] = {}
            for name in sorted(own | top):
                bare.setdefault(name.lstrip("-"), name)
            close = _closest(typed.lstrip("-"), list(bare))
            if close is None:
                return None
            option = bare[close]
        if option in own or not subcommand:
            return f"did you mean {option}?"
        return f"did you mean {option} before '{subcommand}'?"


    def patch_parser_for_groups(self, parser):
        """Patch an argparse.ArgumentParser instance to support argument grouping.
        
        This helper function adds group functionality to any parser by modifying its add_argument
        method to support an optional 'group' parameter. Arguments can be organized into logical
        groups which will be displayed together in the help output.

        Args:
            parser (argparse.ArgumentParser): The parser instance to patch

        Returns:
            argparse.ArgumentParser: The patched parser with group functionality

        Example:
            parser.add_argument('--flag', help='Some help text', group='My Group')
            # This will add the argument to a group named 'My Group' in the help output

        A group= title that differs from a section's title only in case,
        surrounding spaces or a trailing s ('my group' or 'My Groups' for 'My
        Group') raises ParserSetupError naming that section, instead of adding a
        second section next to it. Any other new title adds a section.
        """
        return _patch_for_groups(parser)



    def remove_argument(self, argument_name, subparser_name=None):
        """Remove a specific argument from a specified subparser.

        This method removes an argument (identified by its name/flag) from a specified subparser
        and its associated action groups. The removal is done both from the subparser's action
        groups and from the subparser's main actions list.

        Args:
            argument_name (str): The name of the argument to remove (e.g., '--output-format')
            subparser_name (str): The name of the subparser ('predict', 'preprocess', or 'postprocess')
                                from which to remove the argument, or of one the tool added
                                with self.add_subcommand(). Without it, the one subcommand
                                that has the option (two or more raise ParserSetupError).

        Note:
            The whole option goes, under every name it has: removing '--output-format'
            also frees '-f', so add_argument() can declare a replacement afterwards.
            self.parser_predict.remove_argument("--output-format") does the same,
            and is the form the framework's messages name.

        Raises:
            ParserSetupError (a ValueError): `subparser_name` is not predict, preprocess,
                postprocess or a subcommand the tool added, that subparser hasn't been
                added yet, it has no such
                option (the message suggests the closest name), or the option is one
                parse_args reads: preprocess --params-dir and --inputs-dir, postprocess
                --job-desc-file and --input-results-dir (change it with update_arguments
                instead). Without `subparser_name`: no subcommand, or more than one,
                has the option. The message starts with the file:line of the call.
        """
        self._require_init()
        if subparser_name is None:
            subparser_name = self._only_subcommand_with(argument_name)
        framework = _SubcommandsAction._FRAMEWORK_SUBCOMMANDS
        # Subcommands of the tool's own (self.subparser.add_parser), and their aliases.
        own = [name for name in self.subparser._name_parser_map if name not in framework]
        if subparser_name in own:
            _remove_option(self.subparser._name_parser_map[subparser_name], argument_name, subparser_name)
            return
        _remove_option(self._subcommand_parser("remove_argument", subparser_name), argument_name, subparser_name)

    def _subcommand_parser(self, method, subparser_name):
        """The parser of framework subcommand `subparser_name`, for `method`
        (remove_argument or update_arguments). Raises ParserSetupError for a
        name that is no subcommand, or predict before add_predict_subparser()."""
        framework = _SubcommandsAction._FRAMEWORK_SUBCOMMANDS
        own = [name for name in self.subparser._name_parser_map if name not in framework]
        if subparser_name not in framework:
            names = [*framework, *own]
            raise _setup_error(
                f"{method}: unknown subparser {subparser_name!r}; use {', '.join(names[:-1])} or {names[-1]}."
            )
        subparser = getattr(self, f"parser_{subparser_name}", None)
        if subparser is None or isinstance(subparser, _NotReady):
            raise _setup_error(f"{method}: the {subparser_name} subparser hasn't been added yet.")
        return subparser

    def update_arguments(self, argument_name, subparser_name=None, **new_kwargs):
        """Change option `argument_name` of subcommand `subparser_name`, as
        self.parser_<subcommand>.update_arguments(argument_name, **new_kwargs)
        does (see SubparserWrapper.update_arguments). Without `subparser_name`,
        the one subcommand that has the option, as remove_argument finds it.
        self.update_arguments(...) gave an AttributeError traceback.

        Raises:
            ParserSetupError (a ValueError): as remove_argument's lookup does, and
                as SubparserWrapper.update_arguments does; also for a second
                argument that is an option name (it names the subcommand).
            ParserKeywordError (a TypeError): as SubparserWrapper.update_arguments.
            Each message starts with the file:line of the call.
        """
        self._require_init()
        if isinstance(subparser_name, str) and subparser_name.startswith("-"):
            raise _setup_error(
                f"update_arguments({argument_name!r}, {subparser_name!r}): the second argument names the "
                f"subcommand, such as 'predict'; to give every name of the option, use "
                f"self.parser_<subcommand>.update_arguments({argument_name!r}, {subparser_name!r}, ...)"
            )
        if subparser_name is None:
            subparser_name = self._only_subcommand_with(argument_name, "update_arguments")
        parser = self.subparser._name_parser_map.get(subparser_name)
        if parser is not None and subparser_name not in _SubcommandsAction._FRAMEWORK_SUBCOMMANDS:
            # A subcommand of the tool's own (or an alias): its own name, as self.add_subcommand gave it.
            wrapper = SubparserWrapper(parser, getattr(parser, "_subcommand", None) or subparser_name, self)
        else:
            wrapper = self._subcommand_parser("update_arguments", subparser_name)
        return wrapper.update_arguments(argument_name, **new_kwargs)

    # The singular, as remove_argument is.
    update_argument = update_arguments

    def _only_subcommand_with(self, argument_name, method="remove_argument"):
        """The subcommand that has option `argument_name`, for `method`
        (remove_argument or update_arguments) without a subcommand
        (remove_argument said "missing 1 required positional argument").
        Raises ParserSetupError when none has it, or more than one does."""
        having: List[str] = []
        seen: List[argparse.ArgumentParser] = []
        for name, parser in self.subparser._name_parser_map.items():
            if any(parser is other for other in seen):
                continue  # an alias
            seen.append(parser)
            if argument_name in parser._option_string_actions:
                having.append(name)
        if len(having) == 1:
            return having[0]
        if having:
            calls = " or ".join(f'{_parser_of(name)}{method}("{argument_name}")' for name in having)
            raise _setup_error(
                f"{method}({argument_name!r}): {_listed(having)} each have {argument_name}; "
                f"say which: {calls}"
            )
        close = _closest(argument_name, sorted({s for parser in seen for s in _option_names(parser)}))
        hint = f"; did you mean {close}?" if close else ""
        raise _setup_error(f"{method}({argument_name!r}): no subcommand has {argument_name}{hint}")


    @staticmethod
    def get_app_root_dir(start_dir=None, anchor_files=None):
        """Find the app root directory by looking for known anchor files."""
        if start_dir is None:
            start_dir = os.getcwd()  # Default to the current working directory
        
        if anchor_files is None:
            anchor_files = ['LICENSE']
        
        # Normalize to absolute path
        current_dir = os.path.abspath(start_dir)
        
        # Traverse up the directory tree until an anchor file is found or root is reached
        while current_dir != os.path.dirname(current_dir):
            # Check for anchor files in the current directory
            for anchor in anchor_files:
                if os.path.isfile(os.path.join(current_dir, anchor)) or os.path.isdir(os.path.join(current_dir, anchor)):
                    return current_dir  # Return the directory where the anchor file is found

            # Check for anchor files in child directories
            for root, dirs, files in os.walk(current_dir):
                for anchor in anchor_files:
                    if anchor in dirs or anchor in files:
                        return root  # Return the child directory where the anchor file is found

            current_dir = os.path.dirname(current_dir)  # Move up one level
        
        # If no anchor file is found, return None or raise an error
        return None


    @staticmethod
    def find_file_path(start_dir=None, filename=None):
        """Find the full path of a given file by searching both upwards and downwards from the start directory."""
        if start_dir is None:
            start_dir = os.getcwd()  # Default to the current working directory
        
        if filename is None:
            raise ValueError("Filename must be provided")
        
        # Normalize to absolute path
        start_dir = os.path.abspath(start_dir)
        
        # Check upwards
        current_dir = start_dir
        while current_dir != os.path.dirname(current_dir):
            target_path = os.path.join(current_dir, filename)
            if os.path.isfile(target_path):
                return target_path  # Return the full path of the file if found
            
            current_dir = os.path.dirname(current_dir)  # Move up one level
        
        # Check downwards
        for root, _, files in os.walk(start_dir):
            if filename in files:
                return os.path.join(root, filename)
        
        return None  # Return None if the file is not found
    

    @staticmethod
    def merge_parsers(p1, p2):
        '''
        Merges two argument parsers together.

        Args:
            p1 (argparse): The main argument parser.
            p2 (argparse): The secondary argument parser that will be added to first argument parser.

        Returns:
            None
        
        Raises:
            None
        '''
        for action in p2._actions:
            # Skip the help action (usually the first action)
            if action.dest == "help":
                continue

            # Filter out unsupported keys
            valid_keys = ["option_strings", "dest", "nargs", "const", "default", "type", "choices", "required", "help", "metavar"]
            kwargs = {key: getattr(action, key) for key in valid_keys if hasattr(action, key)}

            # By default, override 'required' to make arguments optional
            kwargs['required'] = False

            # Add the argument to p1
            p1.add_argument(*action.option_strings, **kwargs)


    def _preprocess_layout(self, output_dir):
        """The folders _create_preprocess_dirs would create under `output_dir`, resolved.

        Builds the same layout in a temporary folder and maps it back, so the
        layout rule lives only in core_validators. Creates nothing under
        `output_dir` and prints nothing.
        """
        import contextlib
        import io
        import tempfile

        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            planned = validators.create_directory_structure_for_dependencies(tmp, errors=[])
        return {
            (Path(output_dir) / Path(d).relative_to(tmp)).resolve()
            for dirs in planned.values()
            for d in dirs
        }

    def _create_preprocess_dirs(self, output_dir):
        """Create the preprocess folder layout under `output_dir` (the layout depends
        on whether paths.py declares dependencies; see
        core_validators.create_directory_structure_for_dependencies).

        Called by parse_args() only after the whole command line is valid, so a
        command that fails leaves nothing behind. A folder that can't be created
        stops the command with a usage error instead of printing and carrying on.

        Returns the folders created, as {tool name: [folder, ...]}.
        """
        print(f"Creating directory structure under: {output_dir}")
        errors: List[str] = []
        created = validators.create_directory_structure_for_dependencies(output_dir, errors=errors)
        if errors:
            self.parser_preprocess.error(
                f"argument --output-dir: could not create the folders under '{output_dir}': {errors[0]}"
            )

        print(f"Successfully created directory structures for {len(created)} tools:")
        for tool_name, dirs in created.items():
            print(f"\n{tool_name}:")
            for dir_path in dirs:
                print(f"  - {dir_path}")
        return created


class JobDescriptionParams(TypedDict):
    # Blueprint for creating job description file
    shell_cmd: str
    job_id: int
    job_type: str
    depends_on_job_ids: List[int]
    expected_outputs: List[str]