"""Tasinabilir paket ureticisi: lite varyanti ve pakete girmeyenler.

Amac iki sey: (1) `--no-embed` paketinde python-embed hic bulunmasin,
(2) depo govdesi -- tests/, tools/, .github/, __pycache__ -- hicbir pakete
sizmasin. Ikisi de zip'in boyutunu ve dosya sayisini belirliyor.
"""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_builder():
    """tools/ bir paket degil; dosyayi dogrudan yukluyoruz."""
    path = ROOT / "tools" / "build_portable.py"
    spec = importlib.util.spec_from_file_location("build_portable", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_builder()


@pytest.fixture
def fake_wheels(tmp_path: Path) -> Path:
    """Gercek tekerlek indirmeden check_wheels'i memnun eden bos dosyalar."""
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    for name in (
        "cryptography-44.0.0-cp39-abi3-win_amd64.whl",
        "pydantic_core-2.27.2-cp313-cp313-win_amd64.whl",
        "fastapi-0.115.6-py3-none-any.whl",
    ):
        (wheels / name).write_bytes(b"")
    return wheels


# --- Isimlendirme -------------------------------------------------------


def test_lite_zip_has_its_own_name():
    assert builder.zip_name("windows", builder.VARIANT_LITE) == "holocron-windows-x64-lite.zip"


def test_full_zip_name_is_unchanged():
    assert builder.zip_name("windows") == "holocron-windows-x64.zip"
    assert builder.zip_name("windows", builder.VARIANT_FULL) == "holocron-windows-x64.zip"
    assert builder.zip_name("linux") == "holocron-linux-x64.zip"


# --- Manifest -----------------------------------------------------------


def test_lite_manifest_has_no_embed():
    entries = builder.manifest("windows", builder.VARIANT_LITE)
    assert "python-embed/" not in entries
    for needed in ("app", "holocron.bat", "holocron.sh", "requirements.txt", "wheels/"):
        assert needed in entries, needed
    assert "README.md" in entries and "LICENSE" in entries


def test_full_windows_manifest_still_has_embed():
    assert "python-embed/" in builder.manifest("windows", builder.VARIANT_FULL)


def test_linux_manifest_never_has_embed():
    assert "python-embed/" not in builder.manifest("linux")


@pytest.mark.parametrize("variant", ["full", "lite"])
def test_repo_scaffolding_never_enters_a_package(variant):
    entries = builder.manifest("windows", variant)
    for unwanted in builder.EXCLUDED_FROM_PACKAGE:
        assert unwanted not in entries, unwanted
        assert not any(entry.startswith(unwanted) for entry in entries), unwanted


def test_excluded_paths_are_not_secretly_listed_as_content():
    assert set(builder.PACKAGE_CONTENT).isdisjoint(builder.EXCLUDED_FROM_PACKAGE)


# --- Komut satiri -------------------------------------------------------


def test_lite_build_does_not_require_an_embed_zip(fake_wheels, tmp_path, capsys):
    code = builder.main(
        ["--platform", "windows", "--no-embed", "--wheels", str(fake_wheels), "--dry-run"]
    )
    output = capsys.readouterr().out
    assert code == 0
    assert "python-embed/" not in output
    assert "holocron-windows-x64-lite.zip" in output


def test_full_windows_build_still_demands_an_embed_zip(fake_wheels):
    with pytest.raises(SystemExit) as error:
        builder.main(["--target", "windows", "--wheels", str(fake_wheels), "--dry-run"])
    assert "--embed-zip" in str(error.value)


def test_lite_is_refused_for_linux(fake_wheels):
    with pytest.raises(SystemExit) as error:
        builder.main(
            ["--target", "linux", "--no-embed", "--wheels", str(fake_wheels), "--dry-run"]
        )
    assert "embed" in str(error.value)


def test_variant_lite_is_the_same_switch(fake_wheels, capsys):
    builder.main(["--variant", "lite", "--wheels", str(fake_wheels), "--dry-run"])
    assert "holocron-windows-x64-lite.zip" in capsys.readouterr().out


# --- Gercek zip ---------------------------------------------------------


def test_lite_zip_contents(fake_wheels, tmp_path):
    """Uretilen zip gercekten inceltilmis mi? Ag gerekmeden dogrulanir."""
    output = builder.build(
        target="windows",
        embed_zip=None,
        wheels=fake_wheels,
        python_version="3.13",
        dist=tmp_path / "dist",
        variant=builder.VARIANT_LITE,
    )
    assert output.name == "holocron-windows-x64-lite.zip"

    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()

    assert names, "zip bos"
    assert all(name.startswith("holocron/") for name in names)
    roots = {name.split("/")[1] for name in names}
    assert roots == {
        "app",
        "holocron.bat",
        "holocron.sh",
        "requirements.txt",
        "README.md",
        "LICENSE",
        "wheels",
    }
    assert not any("python-embed" in name for name in names)
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
    for unwanted in ("holocron/tests/", "holocron/tools/", "holocron/.github/"):
        assert not any(name.startswith(unwanted) for name in names), unwanted
