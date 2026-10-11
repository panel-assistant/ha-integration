"""The release check must go red when the published installer copy is not the build."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "verify_installer_copy", ROOT / ".github/scripts/verify_installer_copy.py"
)
assert _SPEC and _SPEC.loader
verify = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(verify)


@pytest.fixture
def site(tmp_path: Path) -> tuple[Path, Path]:
    built = tmp_path / "dist"
    (built / "assets").mkdir(parents=True)
    (built / "index.html").write_text("<p>installer</p>")
    (built / "assets" / "index-abc.js").write_text("export {}")
    published = tmp_path / "published" / "0.9.1"
    published.mkdir(parents=True)
    for path in built.rglob("*"):
        if path.is_file():
            target = published / path.relative_to(built)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    return built, published


def _main(built: Path, published: Path, *tags: str) -> int:
    verify.ATTEMPTS = 2
    verify.DELAY_SECONDS = 0
    return verify.main([str(built), published.as_uri() + "/", *tags])


def test_identical_copy_passes(site) -> None:
    assert _main(*site) == 0


def test_changed_file_fails_and_says_where(site, capsys) -> None:
    built, published = site
    (published / "assets" / "index-abc.js").write_text("export { stale }")
    assert _main(built, published) == 1
    assert "first difference at byte 8" in capsys.readouterr().err


def test_missing_file_fails(site) -> None:
    built, published = site
    (published / "index.html").unlink()
    assert _main(built, published) == 1


def test_empty_build_fails(site, tmp_path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _main(empty, site[1]) == 1


def test_stale_deploy_missing_a_newer_release_copy_fails(tmp_path) -> None:
    # An older run built only 0.9.1 and deployed after 0.9.2 had published.
    built = tmp_path / "dist"
    (built / "0.9.1").mkdir(parents=True)
    (built / "0.9.1" / "index.html").write_text("<p>0.9.1</p>")
    live = tmp_path / "live"
    (live / "0.9.1").mkdir(parents=True)
    (live / "0.9.1" / "index.html").write_text("<p>0.9.1</p>")
    assert _main(built, live, "0.9.1", "0.9.2") == 1
    (live / "0.9.2").mkdir()
    (live / "0.9.2" / "index.html").write_text("<p>0.9.2</p>")
    assert _main(built, live, "0.9.1", "0.9.2") == 0


def test_the_check_names_itself_to_the_server(site, monkeypatch) -> None:
    """Cloudflare answers 403 to urllib's default agent, so the check sends its own."""
    import io
    import urllib.request

    built, published = site
    agents: list[str] = []

    def served(request, timeout):
        agents.append(request.get_header("User-agent") or "")
        if not agents[-1] or agents[-1].startswith("Python-urllib"):
            raise OSError("HTTP Error 403: Forbidden")
        name = request.full_url.rsplit("/" + published.name + "/", 1)[1]
        return io.BytesIO((published / name).read_bytes())

    monkeypatch.setattr(urllib.request, "urlopen", served)
    assert _main(built, published) == 0
    assert agents
