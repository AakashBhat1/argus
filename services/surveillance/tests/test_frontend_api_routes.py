import re
from pathlib import Path
import pytest
from app.main import app


def extract_api_ts_endpoints(content_or_path: Path | str) -> list[str]:
    if isinstance(content_or_path, Path):
        content = content_or_path.read_text(encoding="utf-8")
    else:
        content = content_or_path

    pattern_call = re.compile(r'\bfetchApi\b')
    matches = [m.start() for m in pattern_call.finditer(content)]

    call_indices = []
    for idx in matches:
        prefix = content[max(0, idx - 20):idx]
        if "function " in prefix:
            continue
        call_indices.append(idx)

    if not call_indices:
        raise ValueError("No fetchApi call sites found in content")

    endpoints = []
    for idx in call_indices:
        pos = idx + len("fetchApi")
        while pos < len(content) and content[pos].isspace():
            pos += 1

        if pos < len(content) and content[pos] == '<':
            depth = 0
            while pos < len(content):
                if content[pos] == '<':
                    depth += 1
                elif content[pos] == '>':
                    depth -= 1
                    if depth == 0:
                        pos += 1
                        break
                pos += 1

        while pos < len(content) and content[pos].isspace():
            pos += 1

        if pos >= len(content) or content[pos] != '(':
            raise ValueError(f"Could not find '(' after fetchApi at character index {idx}")

        pos += 1  # skip '('
        while pos < len(content) and content[pos].isspace():
            pos += 1

        if pos >= len(content) or content[pos] not in ('"', "'", "`"):
            raise ValueError(f"Expected string literal quote after fetchApi( at character index {idx}, got {content[pos:pos+10]!r}")

        quote_char = content[pos]
        pos += 1
        start_str = pos
        while pos < len(content) and content[pos] != quote_char:
            if content[pos] == '\\':
                pos += 2
            else:
                pos += 1

        if pos >= len(content):
            raise ValueError(f"Unterminated string literal in fetchApi at character index {idx}")

        raw_path = content[start_str:pos]

        clean_path = raw_path
        if clean_path.startswith("${API_BASE}"):
            clean_path = clean_path.replace("${API_BASE}", "")

        if "${qs}" in clean_path:
            clean_path = clean_path.replace("${qs}", "")

        if "?" in clean_path:
            clean_path = clean_path.split("?")[0]

        clean_path = clean_path.rstrip("?")

        if clean_path and (clean_path.startswith("/") or clean_path.startswith("${")):
            endpoints.append(clean_path)
        else:
            raise ValueError(f"Extracted endpoint '{clean_path}' at character index {idx} is invalid")

    if len(endpoints) != len(call_indices):
        raise ValueError(f"Expected {len(call_indices)} endpoints, but extracted {len(endpoints)}")

    return endpoints


def validate_endpoints_against_openapi(endpoints: list[str]) -> list[tuple[str, str]]:
    openapi = app.openapi()
    openapi_paths = set(openapi.get("paths", {}).keys())

    openapi_regexes = []
    for path in openapi_paths:
        pattern = "^" + re.sub(r"\{[^}]+\}", r"[^/]+", path) + "$"
        openapi_regexes.append(re.compile(pattern))

    missing_routes = []
    for ep in endpoints:
        full_path = ep if ep.startswith("/api/v1") else f"/api/v1{ep}"
        test_path = re.sub(r"\$\{[^}]+\}", "dummy_param", full_path)

        candidates = [
            test_path,
            test_path.rstrip("/") if test_path.endswith("/") and len(test_path) > 7 else test_path + "/",
        ]

        matched = any(reg.match(cand) for reg in openapi_regexes for cand in candidates)
        if not matched:
            missing_routes.append((ep, full_path))

    return missing_routes


def test_frontend_api_routes_match_openapi():
    # 1. Locate frontend/src/lib/api.ts
    repo_root = Path(__file__).resolve().parents[3]
    api_ts_path = repo_root / "frontend" / "src" / "lib" / "api.ts"
    assert api_ts_path.exists(), f"api.ts not found at {api_ts_path}"

    endpoints = extract_api_ts_endpoints(api_ts_path)
    assert len(endpoints) >= 39, f"Expected at least 39 endpoints extracted from api.ts, got {len(endpoints)}"

    missing_routes = validate_endpoints_against_openapi(endpoints)
    assert not missing_routes, f"The following frontend api.ts paths do not exist in FastAPI OpenAPI schema: {missing_routes}"


def test_frontend_api_routes_fails_on_broken_path():
    # Test fixture containing a zero-argument arrow with generic type parameter and a broken path
    fixture_content = '''
export const testApi = {
    me: () => fetchApi<{ id: string; username: string }>("/users/me"),
};
'''
    endpoints = extract_api_ts_endpoints(fixture_content)
    assert endpoints == ["/users/me"], f"Expected extracted endpoint ['/users/me'], got {endpoints}"

    missing_routes = validate_endpoints_against_openapi(endpoints)
    assert missing_routes == [("/users/me", "/api/v1/users/me")], f"Expected missing_routes to catch broken path '/users/me', got {missing_routes}"


def test_frontend_api_routes_unparseable_fails():
    fixture_content = '''
export const testApi = {
    invalidCall: () => fetchApi(123),
};
'''
    with pytest.raises(ValueError):
        extract_api_ts_endpoints(fixture_content)
