"""Tasinabilir paketleri uretir (Windows ve Linux).

Windows: indirilmis `python-3.13.x-embed-amd64.zip` acilir, bagimliliklar
`wheels/` icinden embed dagitimin `Lib\\site-packages` klasorune kurulur.
Sonuc: `holocron.bat`a cift tikla, hicbir kurulum gerekmez.

Linux: embed dagitimi yoktur; paket `holocron.sh` + `wheels/` ile gelir,
betik ilk calismada sanal ortami cevrimdisi kurar.

Embed dagitimi varsayilan olarak site-packages'i devre disi birakir; bu yuzden
`._pth` dosyasina `Lib\\site-packages`, `import site` ve proje kokunu (`..`)
ekliyoruz.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TARGET_WINDOWS = "windows"
TARGET_LINUX = "linux"
TARGETS = (TARGET_WINDOWS, TARGET_LINUX)

DEFAULT_PYTHON = "3.13"

# Her iki pakette de bulunan dosyalar. Iki baslatici da girer: kullanici
# paketi tasidiginda hangi isletim sisteminde acacagi bilinmez.
PACKAGE_CONTENT = (
    "app",
    "holocron.bat",
    "holocron.sh",
    "requirements.txt",
    "README.md",
    "LICENSE",
)

# Wheel adinda bulunmasi beklenen parcalar: ikisi de gelmezse paket eksiktir.
REQUIRED_WHEELS = {
    "cryptography": "abi3",
    "pydantic_core": "cp313",
}


def zip_name(target: str) -> str:
    return f"holocron-{target}-x64.zip"


def extract_embed(zip_path: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(target)


def pth_name(python_version: str) -> str:
    """3.13 -> python313._pth"""
    return "python" + python_version.replace(".", "") + "._pth"


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


def check_wheels(wheels: Path) -> list[str]:
    """Kritik ikili tekerlekler geldi mi? Gelmediyse adlarini dondurur."""
    names = [path.name for path in wheels.glob("*.whl")]
    missing = []
    for package, marker in REQUIRED_WHEELS.items():
        hit = [name for name in names if name.startswith(package + "-") and marker in name]
        if not hit:
            missing.append(f"{package} ({marker})")
    return missing


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
            "--implementation",
            "cp",
            "--only-binary=:all:",
        ]
    subprocess.run(command, check=True)


def copy_project(package_dir: Path, wheels: Path) -> None:
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
    # Tekerlekler pakete girer: hedef makinede ag olmayabilir.
    shutil.copytree(wheels, package_dir / "wheels")
    launcher = package_dir / "holocron.sh"
    if launcher.exists():
        launcher.chmod(0o755)


def make_zip(package_dir: Path, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(package_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(package_dir.parent))
    return output


def build(
    target: str,
    embed_zip: Path | None,
    wheels: Path,
    python_version: str,
    dist: Path,
) -> Path:
    package_dir = dist / "holocron"
    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True)

    if target == TARGET_WINDOWS:
        embed_dir = package_dir / "python-embed"
        extract_embed(embed_zip, embed_dir)
        patch_pth(embed_dir)
        install_dependencies(embed_dir, wheels, python_version)

    copy_project(package_dir, wheels)
    return make_zip(package_dir, dist / zip_name(target))


def describe(
    target: str,
    embed_zip: Path | None,
    wheels: Path,
    python_version: str,
    dist: Path,
) -> int:
    """--dry-run: hicbir sey yazmadan plani ve eksikleri bildirir."""
    wheel_files = sorted(path.name for path in wheels.glob("*.whl"))
    contents = [name for name in PACKAGE_CONTENT if (ROOT / name).exists()]
    contents.append("wheels/")
    if target == TARGET_WINDOWS:
        contents.append("python-embed/")

    print(f"Hedef            : {target}")
    print(f"Python surumu    : {python_version}")
    if target == TARGET_WINDOWS:
        print(f"Embed zip        : {embed_zip}")
        print(f"Yamalanacak _pth : {pth_name(python_version)} (+ Lib\\site-packages, .., import site)")
    print(f"Wheel klasoru    : {wheels} ({len(wheel_files)} tekerlek)")
    print(f"Paket icerigi    : {', '.join(sorted(contents))}")
    print(f"Cikti            : {dist / zip_name(target)}")

    missing = check_wheels(wheels)
    if missing:
        print("EKSIK tekerlek   : " + ", ".join(missing))
        return 1
    print("Kritik tekerlek  : cryptography abi3 ve pydantic_core cp313 hazir")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Holocron tasinabilir paketi")
    parser.add_argument("--target", default=TARGET_WINDOWS, choices=TARGETS, help="paket hedefi")
    parser.add_argument("--embed-zip", type=Path, help="python-3.13.x-embed-amd64.zip (Windows)")
    parser.add_argument("--wheels", default=ROOT / "wheels", type=Path, help="wheel klasoru")
    parser.add_argument("--version", default="dev", help="paket surumu (etiket adi, bilgi amacli)")
    parser.add_argument(
        "--python-version", default=DEFAULT_PYTHON, help="hedef Python surumu (varsayilan 3.13)"
    )
    parser.add_argument("--dist", default=ROOT / "dist", type=Path, help="cikti klasoru")
    parser.add_argument("--dry-run", action="store_true", help="yazmadan plani goster")
    args = parser.parse_args(argv)

    if not args.wheels.exists():
        raise SystemExit(f"Wheels klasoru bulunamadi: {args.wheels}")
    if args.target == TARGET_WINDOWS:
        if args.embed_zip is None:
            raise SystemExit("Windows paketi icin --embed-zip gerekli.")
        if not args.dry_run and not args.embed_zip.exists():
            raise SystemExit(f"Embed zip bulunamadi: {args.embed_zip}")

    if args.dry_run:
        return describe(args.target, args.embed_zip, args.wheels, args.python_version, args.dist)

    missing = check_wheels(args.wheels)
    if missing:
        raise SystemExit("Eksik tekerlek: " + ", ".join(missing))

    output = build(args.target, args.embed_zip, args.wheels, args.python_version, args.dist)
    print(f"Paket hazir: {output} ({output.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
