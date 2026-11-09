"""Tests never see your own plugins (~/.local/share/chimp/plugins/): set before Chimp loads."""

import os
import tempfile
from pathlib import Path

os.environ["CHIMP_PLUGINS_DIR"] = str(Path(tempfile.gettempdir()) / "chimp-tests-no-plugins-here")
