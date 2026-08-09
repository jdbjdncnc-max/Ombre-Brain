from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_image_includes_solo_package() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY solo ./solo" in dockerfile


def test_zeabur_serves_relative_companion_assets_at_root() -> None:
    index = (ROOT / "companion_frontend" / "index.html").read_text(encoding="utf-8")
    bootstrap = (ROOT / "zeabur_gateway_bootstrap.py").read_text(encoding="utf-8")

    assert 'href="styles.css' in index
    assert 'src="app.js' in index
    assert 'name="companion_frontend_root"' in bootstrap
    assert bootstrap.index('Route("/api/import/review"') < bootstrap.index(
        'name="companion_frontend_root"'
    )
