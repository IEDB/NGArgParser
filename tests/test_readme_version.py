"""Guards against the README's hardcoded version strings rotting.

The badge and the "pin to a tag" install example both spell out a semver that
nothing derives — the badge can't resolve from the GitLab API because the repo
is private. They drifted four releases behind (0.3.1 while pyproject said
0.3.5) before anyone noticed. These tests fail the release instead.

Only the `## Install` section is checked. The `@v0.2.4` pins further down are
the one-time pre-0.2.4 bootstrap (0.2.4 is the release that introduced
`cli upgrade`) — a historical fact, not a current-version reference.
"""

import re
from pathlib import Path

import pytest

from ngargparser import cli

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"

BADGE_RE = re.compile(r"img\.shields\.io/badge/ngargparser-([0-9]+\.[0-9]+\.[0-9]+)-")
PINNED_TAG_RE = re.compile(r"ngargparser\.git@v([0-9]+\.[0-9]+\.[0-9]+)")


@pytest.fixture(scope="module")
def readme():
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def install_section(readme):
    """The `## Install` section body, up to the next top-level heading."""
    match = re.search(r"(?ms)^## Install\b.*?(?=^## )", readme)
    assert match, "no '## Install' section found in README.md"
    return match.group(0)


@pytest.fixture(scope="module")
def pyproject_version():
    version = cli._checkout_version(str(REPO_ROOT))
    assert version, "could not read [project] version from pyproject.toml"
    return version


def test_badge_matches_pyproject_version(readme, pyproject_version):
    match = BADGE_RE.search(readme)
    assert match, "no ngargparser shields.io version badge found in README.md"
    assert match.group(1) == pyproject_version, (
        f"README badge says {match.group(1)}, pyproject.toml says {pyproject_version} — "
        "bump the badge in README.md"
    )


def test_pinned_install_example_matches_pyproject_version(install_section, pyproject_version):
    tags = PINNED_TAG_RE.findall(install_section)
    assert tags, "no pinned '…ngargparser.git@vX.Y.Z' example found in README.md's Install section"
    stale = sorted({t for t in tags if t != pyproject_version})
    assert not stale, (
        f"README's Install section pins v{', v'.join(stale)}, pyproject.toml says "
        f"{pyproject_version} — bump the pinned install example in README.md"
    )
