"""Build a source-only ZIP from Git's tracked files, never from runtime directories."""

import argparse
import hashlib
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

ROOT_FILES = {
    ".gitattributes",
    ".gitignore",
    ".python-version",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "pyproject.toml",
    "uv.lock",
    "launcher.pyw",
    "Open MCP Manager.cmd",
}
SOURCE_DIRS = {"scripts", "docs", "tests", ".github"}
SOURCE_SUFFIXES = {".py", ".ps1", ".md", ".yml", ".yaml", ".json", ".toml", ".png"}


def release_files(root: Path) -> list[str]:
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode("utf-8").split("\0")
    result = []
    for name in sorted(set(paths) - {""}):
        path = PurePosixPath(name)
        if ".." in path.parts or path.is_absolute():
            raise ValueError("Unsafe source path")
        allowed = name in ROOT_FILES or (
            path.parts[0] in SOURCE_DIRS and path.suffix in SOURCE_SUFFIXES
        )
        if not allowed or path.name in {"ngrok.yml", ".env"}:
            raise ValueError(f"Unexpected tracked file; review before publishing: {name}")
        local = root / name
        if local.is_symlink() or not local.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Source link is not allowed in releases: {name}")
        result.append(name)
    required = {"launcher.pyw", "Open MCP Manager.cmd", "scripts/bootstrap-manager.ps1", "uv.lock"}
    if not required.issubset(result):
        raise ValueError("Stage or commit the complete launcher source before building a release.")
    return result


def build(root: Path, output: Path):
    files = release_files(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in files:
            archive.write(root / name, "Infinite_computer_use_mcp/" + name)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip():
            raise RuntimeError("Archive verification failed")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(f"{digest}  {output.name}\n", encoding="ascii")
    print(
        f"Created {output.name}: {len(files)} source files; runtime files and credentials excluded."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".run/releases/Infinite_computer_use_mcp.zip"))
    args = parser.parse_args()
    build(Path(__file__).resolve().parents[1], args.output.resolve())
