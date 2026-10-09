import tomllib
from pathlib import Path

import docusearch


def test_package_version_matches_pyproject() -> None:
    pyproject = Path(__file__).parents[2] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]

    assert docusearch.__version__ == declared
