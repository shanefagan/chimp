"""Every place the version is recorded agrees (packaging/bump-version.sh keeps them so)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"

pytestmark = pytest.mark.skipif(not (PACKAGING / "rpm").is_dir(), reason="packaging/ not in this tree")


def _find(path: Path, pattern: str) -> str:
    m = re.search(pattern, path.read_text(), re.MULTILINE)
    assert m, f"{pattern!r} not in {path}"
    return m.group(1)


def test_versions_agree():
    version = _find(ROOT / "pyproject.toml", r'^version = "([^"]+)"')
    assert _find(PACKAGING / "arch/PKGBUILD", r"^pkgver=(\S+)") == version
    assert _find(PACKAGING / "rpm/chimp.spec", r"^Version:\s+(\S+)") == version
    assert _find(PACKAGING / "rpm/chimp.spec", r"^\* .* - (\S+)-\d+$") == version
    assert _find(PACKAGING / "debian/changelog", r"^chimp \(([^-)]+)-") == version
    for page in ("chimp.1", "chimpd.1"):
        assert _find(PACKAGING / "man" / page, r'^\.TH .* "chimp ([^"]+)"') == version
