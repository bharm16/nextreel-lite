from pathlib import Path


def test_tests_root_contains_only_support_files():
    tests_root = Path(__file__).resolve().parents[1]
    root_files = {
        path.name
        for path in tests_root.iterdir()
        if path.is_file()
    }

    assert root_files == {
        "__init__.py",
        "conftest.py",
        "helpers.py",
    }
