#!/usr/bin/env bash
# Builds the Python tests' virtual environment at $VENV for one matrix leg
# ($FIXTURE names its Home Assistant test package). tests.yml keys the cached
# environment on this file and pyproject.toml, so any change here rebuilds it.
set -euo pipefail
python -m venv "$VENV"
"$VENV/bin/python" -m pip install -e ".[test]"
# The two test packages supply the same fixtures; only one may be installed.
if [ "$FIXTURE" != "pytest-homeassistant-custom-component==0.13.357" ]; then
  "$VENV/bin/python" -m pip uninstall -y pytest-homeassistant-custom-component
  "$VENV/bin/python" -m pip install "$FIXTURE"
fi
frontend_requirement=$("$VENV/bin/python" -c 'import json, pathlib, homeassistant; path = pathlib.Path(homeassistant.__file__).parent / "components/frontend/manifest.json"; print(json.loads(path.read_text())["requirements"][0])')
"$VENV/bin/python" -m pip install "$frontend_requirement"
# Voice and camera tests import Home Assistant components whose
# requirements are installed only when Home Assistant sets them up, so install the versions this
# Home Assistant release declares.
voice_requirements=$("$VENV/bin/python" -c 'import json, pathlib, homeassistant; root = pathlib.Path(homeassistant.__file__).parent / "components"; seen, todo, reqs = set(), ["assist_satellite", "assist_pipeline", "conversation", "camera", "stream"], []
while todo:
    c = todo.pop()
    if c in seen or not (root / c / "manifest.json").exists(): continue
    seen.add(c); m = json.loads((root / c / "manifest.json").read_text()); reqs += m.get("requirements", []); todo += m.get("dependencies", []) + m.get("after_dependencies", [])
print(" ".join(sorted(set(reqs))))')
# Word splitting is the point: one argument per requirement.
# shellcheck disable=SC2086
if [ -n "$voice_requirements" ]; then "$VENV/bin/python" -m pip install $voice_requirements; fi
