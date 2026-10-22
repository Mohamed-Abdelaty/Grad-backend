"""Generate Postman Collection (v2.1) from ChaosFlow OpenAPI schema.

Includes environment variables:
- base_url (default: http://127.0.0.1:8000)
- access_token, refresh_token, admin_token, api_key
- pipeline_id, run_id, schedule_id, template_id, notification_id

Organized into ordered folders matching the test guide:
1. Auth
2. Pipelines
3. Versions & Diff/Rollback
4. Runs
5. Plugins Catalog
6. Schedules
7. Templates
8. Notifications
9. API Keys
10. Audit Log & Admin
11. System Health
"""

from __future__ import annotations

import json
from pathlib import Path

from app.main import create_app


def generate_postman_collection() -> dict:
    app = create_app()
    openapi = app.openapi()

    collection = {
        "info": {
            "name": "ChaosFlow API Collection",
            "_postman_id": "chaosflow-api-collection-v2",
            "description": "Comprehensive Postman collection for ChaosFlow data-pipeline platform API (Phase 1 + Phase 2).",
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "item": [],
        "variable": [
            {"key": "base_url", "value": "http://127.0.0.1:8000", "type": "string"},
            {"key": "access_token", "value": "", "type": "string"},
            {"key": "admin_token", "value": "", "type": "string"},
            {"key": "refresh_token", "value": "", "type": "string"},
            {"key": "api_key", "value": "", "type": "string"},
            {"key": "pipeline_id", "value": "", "type": "string"},
            {"key": "run_id", "value": "", "type": "string"},
            {"key": "schedule_id", "value": "", "type": "string"},
            {"key": "template_id", "value": "", "type": "string"},
            {"key": "notification_id", "value": "", "type": "string"},
        ],
    }

    folders = {
        "1. Auth": [],
        "2. Pipelines": [],
        "3. Versions & Diff": [],
        "4. Runs": [],
        "5. Plugins": [],
        "6. Schedules": [],
        "7. Templates": [],
        "8. Notifications": [],
        "9. API Keys": [],
        "10. Audit Log": [],
        "11. System Health": [],
    }

    # Helper to map paths to folders
    def get_folder_name(path: str) -> str:
        if path.startswith("/auth"):
            return "1. Auth"
        elif "/versions" in path or "/rollback" in path:
            return "3. Versions & Diff"
        elif "/schedule" in path:
            return "6. Schedules"
        elif path.startswith("/pipelines"):
            return "2. Pipelines"
        elif path.startswith("/runs"):
            return "4. Runs"
        elif path.startswith("/plugins"):
            return "5. Plugins"
        elif path.startswith("/templates"):
            return "7. Templates"
        elif path.startswith("/notifications"):
            return "8. Notifications"
        elif path.startswith("/api-keys"):
            return "9. API Keys"
        elif path.startswith("/admin/audit-log"):
            return "10. Audit Log"
        elif path.startswith("/health"):
            return "11. System Health"
        return "11. System Health"

    # Iterate through OpenAPI paths (skip duplicate /api mounts to keep collection clean)
    for path, methods in sorted(openapi.get("paths", {}).items()):
        if path.startswith("/api/"):
            continue

        folder = get_folder_name(path)

        for method, op in methods.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue

            name = op.get("summary") or f"{method.upper()} {path}"
            description = op.get("description", "")

            # Convert OpenAPI {param} into Postman {{param}} or :param
            url_path_parts = []
            url_vars = []
            for part in path.strip("/").split("/"):
                if part.startswith("{") and part.endswith("}"):
                    param_name = part[1:-1]
                    url_path_parts.append(f":{param_name}")
                    # Map to environment variable placeholder if known
                    var_val = f"{{{{{param_name}}}}}"
                    url_vars.append({"key": param_name, "value": var_val, "description": f"URL param {param_name}"})
                else:
                    url_path_parts.append(part)

            # Query params
            query_items = []
            for p in op.get("parameters", []):
                if p.get("in") == "query":
                    example_val = p.get("schema", {}).get("default", "")
                    if "examples" in p.get("schema", {}):
                        example_val = p["schema"]["examples"][0]
                    query_items.append({"key": p["name"], "value": str(example_val), "description": p.get("description", "")})

            # Headers
            headers = []
            sec = op.get("security", [])
            if sec:
                headers.append({
                    "key": "Authorization",
                    "value": "Bearer {{access_token}}",
                    "description": "Bearer JWT or use X-API-Key: {{api_key}}",
                })
                headers.append({
                    "key": "X-API-Key",
                    "value": "{{api_key}}",
                    "description": "API Key authentication (optional alternative to Bearer)",
                })

            # Body
            req_body = {}
            if "requestBody" in op:
                content = op["requestBody"].get("content", {})
                json_content = content.get("application/json", {})
                example_body = json_content.get("example")
                if not example_body and "schema" in json_content:
                    schema_ref = json_content["schema"].get("$ref", "")
                    if schema_ref:
                        schema_name = schema_ref.split("/")[-1]
                        example_body = openapi.get("components", {}).get("schemas", {}).get(schema_name, {}).get("example")

                headers.append({"key": "Content-Type", "value": "application/json"})
                req_body = {
                    "mode": "raw",
                    "raw": json.dumps(example_body, indent=2) if example_body else "{}",
                    "options": {"raw": {"language": "json"}},
                }

            postman_request = {
                "name": name,
                "request": {
                    "method": method.upper(),
                    "header": headers,
                    "description": description,
                    "url": {
                        "raw": "{{base_url}}" + path + ("?" + "&".join([f"{q['key']}={q['value']}" for q in query_items]) if query_items else ""),
                        "host": ["{{base_url}}"],
                        "path": url_path_parts,
                        "variable": url_vars,
                        "query": query_items,
                    },
                },
                "response": [],
            }
            if req_body:
                postman_request["request"]["body"] = req_body

            folders[folder].append(postman_request)

    for folder_name, items in folders.items():
        if items:
            collection["item"].append({
                "name": folder_name,
                "item": items,
            })

    return collection


if __name__ == "__main__":
    coll = generate_postman_collection()
    out_path = Path("docs/ChaosFlow_Postman_Collection.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(coll, f, indent=2)
    print(f"Generated Postman Collection at {out_path} with {len(coll['item'])} folders.")
