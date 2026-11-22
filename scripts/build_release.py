"""Build a source-only candidate from an explicit allowlist, never the workspace root."""

import hashlib
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    "README.md",
    "README.en.md",
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements.lock",
    "Dockerfile",
    "compose.yaml",
    ".gitignore",
    ".dockerignore",
    ".env.example",
}
ROOT_DIRS = {"workbench", "tests", "examples", "docs", "scripts", ".github"}
SUFFIXES = {".py", ".js", ".css", ".html", ".md", ".json", ".png", ".pdf", ".yaml", ".yml"}


def release_files(root=ROOT):
    for name in sorted(ROOT_FILES):
        file = root / name
        if not file.is_file() or file.is_symlink():
            raise ValueError("Missing or linked required release file: " + name)
        yield file
    for directory in sorted(ROOT_DIRS):
        for file in sorted((root / directory).rglob("*")):
            if file.is_symlink():
                raise ValueError("Symlinks must not be published: " + str(file.relative_to(root)))
            relative = file.relative_to(root)
            if (
                file.is_file()
                and file.suffix in SUFFIXES
                and not any(p.startswith(".") or p == "__pycache__" for p in relative.parts[1:])
            ):
                if file.stat().st_size > 5 * 1024 * 1024:
                    raise ValueError("Unexpected large release asset: " + str(relative))
                yield file


def main():
    from workbench import REPOSITORY_NAME, __version__

    folder = ROOT / "dist"
    folder.mkdir(exist_ok=True)
    name = REPOSITORY_NAME + "-" + __version__
    archive = folder / (name + ".tar.gz")
    files = list(release_files())
    with tarfile.open(archive, "w:gz") as out:
        for file in files:
            info = out.gettarinfo(str(file), arcname=name + "/" + str(file.relative_to(ROOT)))
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            with file.open("rb") as source:
                out.addfile(info, source)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(digest + "  " + archive.name + "\n")
    print(f"Built {archive.name}: {len(files)} files, {archive.stat().st_size:,} bytes")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(ROOT))
    main()
