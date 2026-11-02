# Chimp: every build/check command lives here.

python := ".venv/bin/python"

# list recipes
default:
    @just --list

# dev venv on top of the system PySide6/Kirigami, editable install + dev tools
dev:
    test -x {{python}} || uv venv --system-site-packages --python /usr/bin/python3 .venv
    uv pip install --python {{python}} -e . --group dev

# run the editor / the desktop service from the source tree
run *args:
    {{python}} -m chimp {{args}}

service *args:
    {{python}} -u -m chimp.desktop {{args}}

# lint + format check (ruff)
lint:
    {{python}} -m ruff check .
    {{python}} -m ruff format --check .

# apply ruff fixes and formatting
fmt:
    {{python}} -m ruff check --fix .
    {{python}} -m ruff format .

# security scan (bandit)
security:
    {{python}} -m bandit -q -c pyproject.toml -r chimp

# offline unit tests
test:
    {{python}} -m pytest -q -p no:cacheprovider

# everything that must pass before a change goes in
check: lint security test

# hardware/desktop checks: need the LPD8 plugged in and a Plasma session
live:
    PYTHONPATH=. {{python}} tests/live_check.py
    PYTHONPATH=. {{python}} tests/live_desktop_actions.py

# can a muted mic still be level-metered? (interactive: talk when asked)
mic-probe:
    {{python}} tests/mic_mute_probe.py

# knob-sweep benchmark (processes spawned, CPU)
bench:
    PYTHONPATH=. {{python}} tests/bench_knob.py

# build the Arch/CachyOS package
pkg:
    cd packaging/arch && rm -f ./*.pkg.tar.* ./chimp-*.tar.gz && ./make-source.sh && makepkg -sf --cleanbuild

# build and install the package (asks for sudo), then set up the service
install:
    ./install.sh
