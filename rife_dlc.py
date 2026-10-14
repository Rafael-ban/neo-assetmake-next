"""Package the verified optional RIFE files without loading native plugins."""

from __future__ import annotations

import argparse
import os
import tempfile
import zipfile
from pathlib import Path

from plugin_distribution import PluginDistributionError, load_manifest, verify_plugins


_MANIFEST = Path("resources/packaging/vs-rife.json")
_SCRIPT = Path("resources/vapoursynth/rife_pipeline.vpy")
_README_NAME = "RIFE-DLC-README.txt"
_GUIDE = Path(__file__).resolve().parent / "docs" / "rife-dlc-guide.md"


def create_rife_dlc(app_dir: str | Path, output: str | Path) -> Path:
    """Create a standalone DLC ZIP from synced files; never overwrite output."""
    app = Path(app_dir).resolve()
    destination = Path(output).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"RIFE DLC output already exists: {destination}")

    manifest = app / _MANIFEST
    if load_manifest(app, manifest)["install_root"] != "tools/vs-rife":
        raise PluginDistributionError("RIFE DLC manifest must install into tools/vs-rife")
    readme = _GUIDE.read_text(encoding="utf-8")
    files = verify_plugins(app, manifest)
    files.extend((manifest, app / _SCRIPT))
    members: dict[str, Path] = {}
    for source in files:
        # Also check the script/manifest; never follow a path outside the app.
        source.resolve().relative_to(app)
        if not source.is_file() or source.is_symlink():
            raise FileNotFoundError(f"RIFE DLC source is missing or linked: {source}")
        members[source.relative_to(app).as_posix()] = source

    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".partial", dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, source in sorted(members.items()):
                archive.write(source, name)
            archive.writestr(_README_NAME, readme)
        with zipfile.ZipFile(temporary) as archive:
            expected = set(members) | {_README_NAME}
            if set(archive.namelist()) != expected or len(archive.namelist()) != len(expected):
                raise PluginDistributionError("RIFE DLC ZIP has unexpected members")
            bad_member = archive.testzip()
            if bad_member is not None:
                raise PluginDistributionError(f"RIFE DLC ZIP failed CRC check: {bad_member}")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"RIFE DLC output already exists: {destination}")
        temporary.rename(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        output = create_rife_dlc(args.app_dir, args.output)
    except (OSError, ValueError, PluginDistributionError, zipfile.BadZipFile) as error:
        parser.exit(1, f"RIFE DLC packaging failed: {error}\n")
    print(f"RIFE DLC ZIP: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
