from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_image_includes_solo_package() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY solo ./solo" in dockerfile
