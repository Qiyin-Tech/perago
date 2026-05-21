from perago import __version__
from perago._version import __version__ as source_version


def test_package_exports_version() -> None:
    assert __version__ == source_version
