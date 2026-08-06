import uuid
from types import SimpleNamespace

from app.api.dependencies import get_document_service
from app.main import app


def test_health_and_request_id(api_client):
    request_id = str(uuid.uuid4())
    response = api_client.get("/health", headers={"X-Request-ID": request_id})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id
    assert response.json()["service"] == "document-extraction-api"


def test_test_interface_is_served(api_client):
    redirect = api_client.get("/", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "/ui/"
    response = api_client.get("/ui/")
    assert response.status_code == 200
    assert "Leitor" in response.text
    assert "/ui/app.js" in response.text


def test_cors_preflight_allowed(api_client):
    response = api_client.options(
        "/api/v1/documents",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization,Content-Type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "Authorization" in response.headers["access-control-allow-headers"]


def test_cors_preflight_blocked(api_client):
    response = api_client.options(
        "/api/v1/documents",
        headers={
            "Origin": "https://blocked.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_upload_valid_pdf_and_idempotency(api_client, sample_pdf):
    headers = {"Idempotency-Key": "same-request", "Origin": "http://localhost:5173"}
    files = {"file": ("orçamento.pdf", sample_pdf, "application/pdf")}
    data = {"engine": "native", "output_formats": "text,json", "retain_original": "true"}
    first = api_client.post("/api/v1/documents", files=files, data=data, headers=headers)
    second = api_client.post("/api/v1/documents", files=files, data=data, headers=headers)
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["document_id"] == second.json()["document_id"]
    assert first.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_upload_validation_errors_have_standard_shape(api_client):
    response = api_client.post(
        "/api/v1/documents",
        files={"file": ("bad.txt", b"not pdf", "text/plain")},
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "invalid_file_extension"
    assert response.json()["error"]["request_id"]
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_empty_file_is_400_with_cors(api_client):
    response = api_client.post(
        "/api/v1/documents",
        files={"file": ("empty.pdf", b"", "application/pdf")},
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_file"
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_invalid_signature(api_client):
    response = api_client.post(
        "/api/v1/documents",
        files={"file": ("bad.pdf", b"not pdf", "application/pdf")},
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_pdf_signature"
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_unexpected_error_is_safe_and_has_cors(api_client):
    original = app.dependency_overrides[get_document_service]

    async def broken_service():
        raise RuntimeError("secret internal path")

    app.dependency_overrides[get_document_service] = broken_service
    try:
        response = api_client.get("/api/v1/documents", headers={"Origin": "http://localhost:5173"})
    finally:
        app.dependency_overrides[get_document_service] = original
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "secret internal path" not in response.text
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_openapi_contains_contract(api_client):
    response = api_client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/documents" in paths
    assert "/api/v1/documents/{document_id}/result" in paths
    assert "/v1/extractions" in paths
    assert "/v1/extractions/{job_id}/events" in paths


def test_legacy_json_result_projects_text_for_test_interface(api_client):
    original = app.dependency_overrides[get_document_service]

    class ResultService:
        async def get_result(self, document_id):
            return SimpleNamespace(
                schema_version="1.0",
                metadata_json={},
                plain_text="Página 1\ntexto reconhecido",
                markdown="# Página 1\n\ntexto reconhecido",
                structured_json={"schema_version": "1.0", "processing": {}, "pages": []},
            )

    app.dependency_overrides[get_document_service] = lambda: ResultService()
    try:
        response = api_client.get(f"/api/v1/documents/{uuid.uuid4()}/result?format=json")
    finally:
        app.dependency_overrides[get_document_service] = original
    assert response.status_code == 200
    assert response.json()["document"]["plain_text"] == "Página 1\ntexto reconhecido"
