import subprocess
from pathlib import Path


def test_git_index_does_not_track_node_modules():
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["git", "ls-files", "node_modules"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == ""
