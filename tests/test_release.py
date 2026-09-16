import runpy
import subprocess
import zipfile
from pathlib import Path

import pytest

release = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/build-release.py"))


def source_repo(root):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for name in (
        "launcher.pyw",
        "Open MCP Manager.cmd",
        "scripts/bootstrap-manager.ps1",
        "uv.lock",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("source fixture", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)


def test_release_excludes_untracked_private_files(tmp_path):
    source_repo(tmp_path)
    private = tmp_path / ".run/private/ngrok.yml"
    private.parent.mkdir(parents=True)
    private.write_text("private fixture", encoding="utf-8")
    output = tmp_path / ".run/release.zip"
    release["build"](tmp_path, output)
    with zipfile.ZipFile(output) as archive:
        assert len(archive.namelist()) == 4
        assert all(".run/" not in name for name in archive.namelist())
    assert output.with_suffix(".zip.sha256").exists()


@pytest.mark.parametrize("name", ["docs/ngrok.yml", ".run/log.txt", "secrets.json"])
def test_release_rejects_accidentally_tracked_private_files(tmp_path, name):
    source_repo(tmp_path)
    private = tmp_path / name
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text("fixture", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", name], check=True)
    with pytest.raises(ValueError, match="Unexpected tracked file"):
        release["release_files"](tmp_path)
