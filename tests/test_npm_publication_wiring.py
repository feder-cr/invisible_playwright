from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO / ".github" / "workflows" / "publish.yml"
_PACKAGE = _REPO / "typescript" / "package.json"


def test_publish_workflow_uses_idempotent_npm_trusted_publishing():
    workflow = _WORKFLOW.read_text(encoding="utf-8")

    assert "npm-already-published:" in workflow
    assert "https://registry.npmjs.org/invisible-playwright/$VERSION" in workflow
    assert "npm-upload:" in workflow
    assert "environment: npm" in workflow
    assert "id-token: write" in workflow
    assert "npm publish --provenance --access public" in workflow
    assert "already appeared on npm after the publish attempt" in workflow


def test_npm_release_waits_for_a_successful_python_release_path():
    workflow = _WORKFLOW.read_text(encoding="utf-8")

    assert "python-release-ready:" in workflow
    assert "needs: [already-published, gate, upload]" in workflow
    assert "needs.already-published.outputs.present == 'yes'" in workflow
    assert "needs.upload.result == 'success'" in workflow
    assert "npm-gate:\n    needs: [npm-already-published, python-release-ready]" in workflow
    npm_gate = workflow.split("  npm-gate:", 1)[1].split("\n  npm-upload:", 1)[0]
    assert "needs.npm-already-published.outputs.present != 'yes'" not in npm_gate
    assert "if: always()" in npm_gate
    assert "needs.python-release-ready.result == 'success'" in npm_gate
    npm_upload = workflow.split("  npm-upload:", 1)[1]
    assert "needs.npm-already-published.outputs.present == 'no'" in npm_upload
    assert "needs.npm-gate.result == 'success'" in npm_upload


def test_trusted_publishing_uses_the_exact_reviewed_npm_cli():
    workflow = _WORKFLOW.read_text(encoding="utf-8")

    assert "npm install --global npm@11.19.1" in workflow
    assert "npm install --global npm@11\n" not in workflow


def test_npm_and_python_packages_have_the_same_release_version():
    import tomllib

    package = json.loads(_PACKAGE.read_text(encoding="utf-8"))
    project = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))

    assert package["name"] == "invisible-playwright"
    assert package["version"] == project["project"]["version"] == "0.8.3"
    assert package["dependencies"]["playwright-core"] == "1.61.0"
