"""Tasinabilir paketleri uretir (Windows ve Linux).

Windows: indirilmis `python-3.13.x-embed-amd64.zip` acilir, bagimliliklar
`wheels/` icinden embed dagitimin `Lib\\site-packages` klasorune kurulur.
Sonuc: `holocron.bat`a cift tikla, hicbir kurulum gerekmez.

Windows (lite, `--no-embed`): hedef makinede Python zaten kuruluysa embed
dagitimi olu yuktur (binlerce kucuk dosya, ~28 MB, acilmasi yavas). Lite paket
yalnizca `app/` + `wheels/` tasir; `holocron.bat` mevcut Python'u bulup `.venv`
kurar ve tekerlekleri cevrimdisi yukler.

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

VARIANT_FULL = "full"
VARIANT_LITE = "lite"
VARIANTS = (VARIANT_FULL, VARIANT_LITE)

# Her iki pakette de bulunan dosyalar. Iki baslatici da girer: kullanici
# paketi tasidiginda hangi isletim sisteminde acacagi bilinmez.
PACKAGE_CONTENT = (
    "app",
    "holocron_run.py",
    "holocron.bat",
    "holocron.sh",
    "requirements.txt",
    "README.md",
    "LICENSE",
)

# Pakete asla girmeyen yollar. Kullaniciya faydasi yok, zip'i sisirir ve
# acilmasini yavaslatir; PACKAGE_CONTENT zaten beyaz liste ama niyet burada
# yazili olsun ki test de ayni listeyi okusun.
EXCLUDED_FROM_PACKAGE = (
    "tests",
    "tools",
    ".github",
    ".venv",
    "dist",
    "__pycache__",
    "requirements-dev.txt",
    "pyproject.toml",
)

# Wheel adinda bulunmasi beklenen parcalar: ikisi de gelmezse paket eksiktir.
REQUIRED_WHEELS = {
    "cryptography": "abi3",
    "pydantic_core": "cp313",
}

# Yalnizca Windows paketinde aranan tekerlekler. `pywin32` ayri duruyor cunku
# isaretcisi (`sys_platform == "win32"`) Linux'tan yapilan `pip download`
# sirasinda YANLIS degerlendiriliyor: paket sessizce atlaniyor ve Windows
# paketinde Outlook ozelligi calismiyordu. Bu yuzden ayrica indiriliyor ve
# varligi burada denetleniyor.
WINDOWS_ONLY_WHEELS = {
    "pywin32": "cp313",
}

# Windows'a ozel paketler Linux zip'ine girmez (bos yer, yanlis izlenim).
LINUX_EXCLUDED_WHEEL_PREFIXES = ("pywin32-", "pywin32_ctypes-", "pypiwin32-")

def zip_name(target: str, variant: str = VARIANT_FULL) -> str:
    suffix = "-lite" if variant == VARIANT_LITE else ""
    return f"holocron-{target}-x64{suffix}.zip"


def manifest(target: str, variant: str = VARIANT_FULL) -> list[str]:
    """Paketin kokunde ne olacak? --dry-run ve testler ayni listeyi okur."""
    entries = [name for name in PACKAGE_CONTENT if (ROOT / name).exists()]
    entries.append("wheels/")
    if target == TARGET_WINDOWS and variant == VARIANT_FULL:
        entries.append("python-embed/")
    return sorted(entries)


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


def wanted_wheels(target: str) -> dict[str, str]:
    """Hedefe gore aranan tekerlekler; pywin32 yalnizca Windows'ta gerekir."""
    wanted = dict(REQUIRED_WHEELS)
    if target == TARGET_WINDOWS:
        wanted.update(WINDOWS_ONLY_WHEELS)
    return wanted


def check_wheels(wheels: Path, target: str = TARGET_WINDOWS) -> list[str]:
    """Kritik ikili tekerlekler geldi mi? Gelmediyse adlarini dondurur."""
    names = [path.name for path in wheels.glob("*.whl")]
    missing = []
    for package, marker in wanted_wheels(target).items():
        hit = [name for name in names if name.startswith(package + "-") and marker in name]
        if not hit:
            missing.append(f"{package} ({marker})")
    return missing


def wheel_is_windows_only(name: str) -> bool:
    return name.startswith(LINUX_EXCLUDED_WHEEL_PREFIXES)


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

    if sys.platform != "win32":
        # `sys_platform == "win32"` isaretcisi pip'in CALISTIGI yoruma gore
        # degerlendirilir, `--platform` bayragina gore degil: Linux'tan
        # uretirken pywin32 sessizce atlanir. Elle kuruyoruz.
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(wheels),
                "--target",
                str(target),
                "--platform",
                "win_amd64",
                "--python-version",
                python_version,
                "--implementation",
                "cp",
                "--only-binary=:all:",
                "--no-deps",
                "pywin32",
            ],
            check=True,
        )


def copy_project(package_dir: Path, wheels: Path, target: str = TARGET_WINDOWS) -> None:
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
    # Tekerlekler pakete girer: hedef makinede ag olmayabilir. Windows'a ozel
    # paketler Linux zip'ine alinmaz.
    ignore = (
        shutil.ignore_patterns(*(prefix + "*" for prefix in LINUX_EXCLUDED_WHEEL_PREFIXES))
        if target == TARGET_LINUX
        else None
    )
    shutil.copytree(wheels, package_dir / "wheels", ignore=ignore)
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
    variant: str = VARIANT_FULL,
) -> Path:
    package_dir = dist / "holocron"
    if package_dir.exists():
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True)

    if target == TARGET_WINDOWS and variant == VARIANT_FULL:
        embed_dir = package_dir / "python-embed"
        extract_embed(embed_zip, embed_dir)
        patch_pth(embed_dir)
        install_dependencies(embed_dir, wheels, python_version)

    copy_project(package_dir, wheels, target)
    return make_zip(package_dir, dist / zip_name(target, variant))


def describe(
    target: str,
    embed_zip: Path | None,
    wheels: Path,
    python_version: str,
    dist: Path,
    variant: str = VARIANT_FULL,
) -> int:
    """--dry-run: hicbir sey yazmadan plani ve eksikleri bildirir."""
    wheel_files = sorted(path.name for path in wheels.glob("*.whl"))
    contents = manifest(target, variant)
    embedded = target == TARGET_WINDOWS and variant == VARIANT_FULL

    print(f"Hedef            : {target}")
    note = " (python-embed yok)" if variant == VARIANT_LITE else ""
    print(f"Varyant          : {variant}{note}")
    print(f"Python surumu    : {python_version}")
    if embedded:
        print(f"Embed zip        : {embed_zip}")
        print(f"Yamalanacak _pth : {pth_name(python_version)} (+ Lib\\site-packages, .., import site)")
    elif target == TARGET_WINDOWS:
        print("Embed zip        : yok, hedef makinedeki Python kullanilacak")
    print(f"Wheel klasoru    : {wheels} ({len(wheel_files)} tekerlek)")
    print(f"Paket icerigi    : {', '.join(contents)}")
    print(f"Cikti            : {dist / zip_name(target, variant)}")

    missing = check_wheels(wheels, target)
    if missing:
        print("EKSIK tekerlek   : " + ", ".join(missing))
        return 1
    print("Kritik tekerlek  : " + ", ".join(sorted(wanted_wheels(target))) + " hazir")
    if target == TARGET_LINUX:
        print("Linux disi       : pywin32 tekerlekleri pakete alinmaz")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Holocron tasinabilir paketi")
    parser.add_argument(
        "--target",
        "--platform",
        dest="target",
        default=TARGET_WINDOWS,
        choices=TARGETS,
        help="paket hedefi (--platform ayni secenegin takma adi)",
    )
    parser.add_argument("--embed-zip", type=Path, help="python-3.13.x-embed-amd64.zip (Windows)")
    parser.add_argument("--wheels", default=ROOT / "wheels", type=Path, help="wheel klasoru")
    parser.add_argument("--version", default="dev", help="paket surumu (etiket adi, bilgi amacli)")
    parser.add_argument(
        "--python-version", default=DEFAULT_PYTHON, help="hedef Python surumu (varsayilan 3.13)"
    )
    parser.add_argument("--dist", default=ROOT / "dist", type=Path, help="cikti klasoru")
    parser.add_argument(
        "--variant", default=VARIANT_FULL, choices=VARIANTS, help="paket varyanti"
    )
    parser.add_argument(
        "--no-embed",
        action="store_true",
        help="python-embed'siz hafif paket (--variant lite ile ayni)",
    )
    parser.add_argument("--dry-run", action="store_true", help="yazmadan plani goster")
    args = parser.parse_args(argv)

    variant = VARIANT_LITE if args.no_embed else args.variant

    if not args.wheels.exists():
        raise SystemExit(f"Wheels klasoru bulunamadi: {args.wheels}")
    if variant == VARIANT_LITE and args.target != TARGET_WINDOWS:
        raise SystemExit(
            f"{args.target} paketi zaten embed'siz; --no-embed yalnizca Windows icin anlamli."
        )
    if args.target == TARGET_WINDOWS and variant == VARIANT_FULL:
        if args.embed_zip is None:
            raise SystemExit(
                "Windows tam paketi icin --embed-zip gerekli. "
                "Python'u kurulu makineler icin --no-embed kullanin."
            )
        if not args.dry_run and not args.embed_zip.exists():
            raise SystemExit(f"Embed zip bulunamadi: {args.embed_zip}")

    if args.dry_run:
        return describe(
            args.target, args.embed_zip, args.wheels, args.python_version, args.dist, variant
        )

    missing = check_wheels(args.wheels, args.target)
    if missing:
        raise SystemExit("Eksik tekerlek: " + ", ".join(missing))

    output = build(
        args.target, args.embed_zip, args.wheels, args.python_version, args.dist, variant
    )
    print(f"Paket hazir: {output} ({output.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
