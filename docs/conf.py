from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
import tomllib

project = "Perago"
author = "Yikai Liao"


def _resolve_release() -> str:
    """Resolve the documentation version from installed metadata or pyproject."""

    try:
        return package_version("perago")
    except PackageNotFoundError:
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        with pyproject.open("rb") as fh:
            return tomllib.load(fh)["project"]["version"]


release = _resolve_release()

extensions = [
    "myst_parser",
    "numpydoc",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
    "sphinx_design",
    "sphinxcontrib.autodoc_pydantic",
]

autosummary_generate = True
autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_typehints_format = "short"
templates_path = ["_templates"]

html_theme = "pydata_sphinx_theme"
html_title = "Perago"
html_static_path = ["_static"]
exclude_patterns = [
    "_build",
    "generated/**",
    "adr/**",
    "architecture/adr/template.md",
    "conductor/**",
    "documentation_development_plan.md",
    "mvp_examples.md",
    "transaction_model/**",
]

myst_enable_extensions = [
    "colon_fence",
    "deflist",
]

numpydoc_show_class_members = False
numpydoc_show_inherited_class_members = False
numpydoc_validation_checks = {
    "GL08",
    "PR01",
    "PR02",
    "PR03",
    "PR04",
    "PR07",
    "RT01",
    "RT03",
}

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "pydantic": ("https://docs.pydantic.dev/latest/", None),
}
