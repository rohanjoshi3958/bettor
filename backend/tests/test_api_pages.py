"""The static UI routes served by the backend in single-origin deployments."""

from __future__ import annotations

import pytest

from app.config import STATIC


class TestHtmlPages:
    @pytest.mark.parametrize("path", ["/", "/supported-leagues"])
    def test_pages_are_served_as_html(self, client, path):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "<html" in response.text.lower()

    def test_how_it_works_route_is_removed(self, client):
        assert client.get("/how-it-works").status_code == 404

    @pytest.mark.parametrize("path", ["/favicon.ico", "/favicon.svg"])
    def test_favicon_routes_serve_the_svg(self, client, path):
        response = client.get(path)
        assert response.status_code == 200
        assert response.text.lstrip().startswith("<svg")

    def test_frontend_assets_are_mounted(self, client):
        response = client.get("/assets/js/config.js")
        assert response.status_code == 200

    def test_unknown_routes_are_not_silently_served(self, client):
        assert client.get("/does-not-exist").status_code == 404


class TestStaticPathResolution:
    def test_static_root_contains_the_expected_entry_points(self):
        """Docker bundles `frontend/` inside `backend/`; local dev uses the repo sibling."""
        assert STATIC.is_dir()
        for name in ("index.html", "supported-leagues.html", "favicon.svg"):
            assert (STATIC / name).is_file()
        assert not (STATIC / "how-it-works.html").exists()
        assert (STATIC / "assets").is_dir()
