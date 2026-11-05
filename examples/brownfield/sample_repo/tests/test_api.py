def test_create_link_returns_code_and_short_url(client):
    response = client.post("/api/v1/links", json={"url": "https://example.com/page"})
    assert response.status_code == 201
    body = response.json()
    assert len(body["code"]) == 7
    assert body["short_url"].endswith(f"/{body['code']}")
    assert body["clicks"] == 0


def test_custom_alias_is_used(client):
    response = client.post("/api/v1/links", json={"url": "https://example.com", "custom_alias": "my-link"})
    assert response.status_code == 201
    assert response.json()["code"] == "my-link"


def test_redirect_counts_clicks(client):
    code = client.post("/api/v1/links", json={"url": "https://example.com/x"}).json()["code"]
    response = client.get(f"/{code}", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "https://example.com/x"
    assert client.get(f"/api/v1/links/{code}").json()["clicks"] == 1


def test_unknown_code_returns_404(client):
    assert client.get("/missing", follow_redirects=False).status_code == 404
    assert client.get("/api/v1/links/missing").status_code == 404


def test_invalid_url_is_rejected(client):
    assert client.post("/api/v1/links", json={"url": "not a url"}).status_code == 422
