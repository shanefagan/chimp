"""Tests never see your own plugins or controller files (~/.local/share/chimp/): set before Chimp loads."""

import os
import tempfile
from pathlib import Path

os.environ["CHIMP_PLUGINS_DIR"] = str(Path(tempfile.gettempdir()) / "chimp-tests-no-plugins-here")
os.environ["CHIMP_CONTROLLERS_DIR"] = str(Path(tempfile.gettempdir()) / "chimp-tests-no-controllers-here")
