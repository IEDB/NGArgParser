"""The framework's built-in help texts must read like the tool's own help.

They were lowercase fragments, such as "prediction result output prefix." and
"flag to indicate validation can be skipped", next to the sentence-case help a
tool gives its own options. Each one is now a sentence: a capital letter first,
a period last.
"""

import json
import subprocess
import sys
from argparse import Namespace

import pytest

from ngargparser import cli

PROBE = """\
import argparse, json, sys
sys.path.insert(0, "src")
from DemoArgumentParser import DemoArgumentParser
p = DemoArgumentParser()
texts = {}
for name in ("preprocess", "predict", "postprocess"):
    sub = p.subparser.choices[name]
    texts[f"{name} description"] = sub.description
    for action in sub._framework_actions:
        text = sub._get_formatter()._expand_help(action)
        texts[f"{name} {'/'.join(action.option_strings)}"] = " ".join(text.split())
    texts[f"{name} help"] = getattr(p, f"parser_{name}").help
print(json.dumps(texts))
"""


@pytest.fixture
def texts(in_tmp_dir):
    assert not cli.startapp_command(Namespace(project_name="demo"))
    root = in_tmp_dir / "demo"
    (root / "probe_help.py").write_text(PROBE)
    proc = subprocess.run([sys.executable, "probe_help.py"], cwd=root, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_every_built_in_help_text_is_a_sentence(texts):
    # predict's description is the tool's own (empty in a fresh tool).
    texts.pop("predict description")
    assert len(texts) == 17

    not_sentences = {name: text for name, text in texts.items() if not (text[:1].isupper() and text.endswith("."))}

    assert not_sentences == {}


def test_built_in_help_texts_keep_what_they_say(texts):
    assert {name: text for name, text in texts.items() if "output" in name} == {
        "predict --output-prefix/-o": "Output prefix for the prediction result files; the extension (.tsv or "
        ".json) is added.",
        "predict --output-format/-f": "Output format of the prediction result (choices: tsv, json; default: tsv).",
        "postprocess --output-prefix/-o": "Output prefix for the merged result files (the results of every job, "
        "combined); the extension (.tsv or .json) is added.",
        "postprocess --output-format/-f": "Output format of the merged result (choices: tsv, json; default: json).",
        "preprocess --output-dir/-o": "Output folder of the job: job_descriptions.json and the predict-inputs/ "
        "folder layout go here (created if missing).",
    }
    assert texts["preprocess --assume-valid"] == "Skip validation of the inputs (assume they are valid)."
    assert texts["preprocess --params-dir"] == (
        "Folder for the preprocessed JSON input files (default: $OUTPUT_DIR/predict-inputs/params, or "
        "$OUTPUT_DIR/$APP_NAME/predict-inputs/params when paths.py declares dependencies)."
    )
