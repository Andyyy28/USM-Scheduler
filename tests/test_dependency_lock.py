"""Keep reviewed exact versions and installer hash enforcement in sync."""

import re
from pathlib import Path


def _pins(text):
    return {
        name.lower().replace("_", "-"): version
        for name, version in re.findall(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", text, re.MULTILINE)
    }


def test_hashed_runtime_lock_matches_reviewed_versions_and_every_entry_has_hashes():
    root = Path(__file__).resolve().parents[1]
    reviewed = (root / "requirements-lock.txt").read_text()
    hashed = (root / "requirements-hashed.txt").read_text()
    assert _pins(hashed) == _pins(reviewed)
    entries = hashed.replace("\\\n", " ").splitlines()
    packages = [entry for entry in entries if entry and not entry.startswith("#")]
    assert len(packages) == len(_pins(reviewed))
    assert all(re.search(r"--hash=sha256:[0-9a-f]{64}", entry) for entry in packages)
    docker = (root / "Dockerfile").read_text()
    assert docker.count("--require-hashes") == 2
    assert "--only-binary=:all:" in docker
    ci = (root / ".github/workflows/ci.yml").read_text()
    assert "--require-hashes --only-binary=:all: --requirement requirements-hashed.txt" in ci


def test_django_uses_supported_content_fingerprinted_static_storage(settings):
    assert settings.STORAGES["staticfiles"]["BACKEND"] == "whitenoise.storage.CompressedManifestStaticFilesStorage"
