"""Windows tasinabilir paketini uretir.

Girdi: indirilmis python-embed zip'i ve wheels klasoru.
Cikti: dist/holocron-windows-<surum>.zip -> ac, holocron.bat'a cift tikla.

Embed dagitimi varsayilan olarak site-packages'i devre disi birakir; bu yuzden
`._pth` dosyasina `import site` ve proje kokunu (`..`) ekliyoruz.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_CONTENT = ("app", "holocron.bat", "requirements.txt", "README.md", "LICENSE")


def extract_embed(zip_path: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(target)


def patch_pth(embed_dir: Path) -> Path:
    """`._pth` dosyasini site-packages ve proje kokunu gorecek hale getirir."""
    candidates = sorted(embed_dir.glob("python*._pth"))
    if not candidates:
        raise SystemExit("python*._pth dosyasi bulunamadi, zip gercekten embed dagitimi mi?")
    pth = candidates[0]
    lines = [line.strip() for line in pth.read_text(encoding="utf-8").splitlines()]
    lines = [line for line in lines if line and line != "#import site"]
    for extra in ("Lib\\site-packages", "..", "import site"):
        if extra not in lines:
            lines.append(extra)
    pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pth


def install_dependencies(embed_dir: Path, wheels: Path, python_version: str) -> None:
    """Bagimliliklari embed icine kurar; get-pip indirmeye gerek kalmaz."""
    target = embed_dir / "Lib" / "site-packages"
    target.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--no-index",
        "--find-links",
        str(wheels),
        "--target",
        str(target),
        "-r",
        str(ROOT / "requirements.txt"),
    ]
    if sys.platform != "win32":
        # Linux'tan uretirken tekerlekler Windows icin secilmeli.
        command += [
            "--platform",
            "win_amd64",
            "--python-version",
            python_version,
            "--only-binary=:all:",
        ]
    subprocess.run(command, check=True)


def copy_project(package_dir: Path) -> None:
    for name in PACKAGE_CONTENT:
        source = ROOT / name
        if not source.exists():
            continue
        destination = package_dir / name
        if source.is_dir():
            shutil.copytree(
                source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
            )
        else:
            shutil.copy2(source, destination)


def make_zip(package_dir: Path, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(package_dir.parent))
    return output


def build(embed_zip: Path, wheels: Path, version: str, python_version: str, dist: Path) -> Path:
    package_dir = dist / "holocron"
    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True)

    embed_dir = package_dir / "python-embed"
    extract_embed(embed_zip, embed_dir)
    patch_pth(embed_dir)
    install_dependencies(embed_dir, wheels, python_version)
    copy_project(package_dir)
    return make_zip(package_dir, dist / f"holocron-windows-{version}.zip")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Holocron tasinabilir Windows paketi")
    parser.add_argument("--embed-zip", required=True, type=Path, help="python-3.x.y-embed-amd64.zip")
    parser.add_argument("--wheels", default=ROOT / "wheels", type=Path, help="wheel klasoru")
    parser.add_argument("--version", default="dev", help="paket surumu (etiket adi)")
    parser.add_argument("--python-version", default="3.12", help="hedef Python surumu")
    parser.add_argument("--dist", default=ROOT / "dist", type=Path, help="cikti klasoru")
    args = parser.parse_args(argv)

    if not args.embed_zip.exists():
        raise SystemExit(f"Embed zip bulunamadi: {args.embed_zip}")
    if not args.wheels.exists():
        raise SystemExit(f"Wheels klasoru bulunamadi: {args.wheels}")

    output = build(args.embed_zip, args.wheels, args.version, args.python_version, args.dist)
    print(f"Paket hazir: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
