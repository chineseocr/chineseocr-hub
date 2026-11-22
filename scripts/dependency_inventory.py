"""List installed runtime dependencies and upstream-declared license metadata."""

import importlib.metadata as metadata
import json
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def main():
    pending = [
        Requirement(line).name
        for line in (ROOT / "requirements.txt").read_text().splitlines()
        if "==" in line
    ]
    packages = {}
    while pending:
        name = canonicalize_name(pending.pop())
        if name in packages:
            continue
        dist = metadata.distribution(name)
        meta = dist.metadata
        packages[name] = {
            "name": meta["Name"],
            "version": dist.version,
            "declared_license": meta.get("License-Expression")
            or meta.get("License")
            or "See package license files",
            "license_files": [
                str(f) for f in dist.files or [] if "license" in str(f).lower() or "copying" in str(f).lower()
            ],
        }
        for declaration in dist.requires or []:
            requirement = Requirement(declaration)
            if not requirement.marker or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    destination = ROOT / "docs" / "dependencies.json"
    destination.write_text(
        json.dumps(sorted(packages.values(), key=lambda p: p["name"].lower()), indent=2) + "\n"
    )
    print(f"Recorded {len(packages)} installed runtime dependencies in docs/dependencies.json")


if __name__ == "__main__":
    main()
