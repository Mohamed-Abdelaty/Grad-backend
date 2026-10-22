# ChaosFlow API Manual Testing Guide & Step-by-Step Walkthrough

Welcome! This guide is written in plain English to make testing the ChaosFlow backend easy, fast, and repeatable.

---

## Permission Matrix

| Endpoint | Method | No Auth | User A (Owner) | User B (Non-Owner) | Admin JWT | Read API Key | Run API Key | Write API Key |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| `/health` | GET | 200 | 200 | 200 | 200 | 200 | 200 | 200 |
| `/auth/register` | POST | 201 | 201 | 201 | 201 | 201 | 201 | 201 |
| `/auth/login` | POST | 200 | 200 | 200 | 200 | 200 | 200 | 200 |
| `/auth/refresh` | POST | 200 | 200 | 200 | 200 | 200 | 200 | 200 |
| `/plugins` | GET | 200 | 200 | 200 | 200 | 200 | 200 | 200 |
| `/pipelines` | GET | 401 | 200 | 200 | 200 | 200 | 403 | 403 |
| `/pipelines` | POST | 401 | 201 | 201 | 201 | 403 | 403 | 201 |
| `/pipelines/{id}` | GET | 401 | 200 | 404 | 200 | 200 | 403 | 403 |
| `/pipelines/{id}` | PUT | 401 | 200 | 404 | 200 | 403 | 403 | 200 |
| `/pipelines/{id}` | DELETE | 401 | 204 | 404 | 204 | 403 | 403 | 204 |
| `/pipelines/{id}/versions` | GET | 401 | 200 | 404 | 200 | 200 | 403 | 403 |
| `/pipelines/{id}/versions/diff` | GET | 401 | 200 | 404 | 200 | 200 | 403 | 403 |
| `/pipelines/{id}/rollback/{v}` | POST | 401 | 201 | 404 | 201 | 403 | 403 | 201 |
| `/pipelines/{id}/runs` | POST | 401 | 202 | 404 | 202 | 403 | 202 | 403 |
| `/runs` | GET | 401 | 200 | 200 | 200 | 200 | 403 | 403 |
| `/runs/{id}` | GET | 401 | 200 | 404 | 200 | 200 | 200 | 403 |
| `/runs/{id}/logs` | GET | 401 | 200 | 404 | 200 | 200 | 200 | 403 |
| `/runs/{id}/stop` | POST | 401 | 200 | 404 | 200 | 403 | 200 | 403 |
| `/pipelines/schedules/preview`| GET | 200 | 200 | 200 | 200 | 200 | 200 | 200 |
| `/pipelines/{id}/schedule` | GET | 401 | 200 | 404 | 200 | 200 | 403 | 403 |
| `/pipelines/{id}/schedule` | POST | 401 | 201 | 404 | 201 | 403 | 403 | 201 |
| `/pipelines/{id}/schedule` | PUT | 401 | 200 | 404 | 200 | 403 | 403 | 200 |
| `/pipelines/{id}/schedule` | DELETE| 401 | 204 | 404 | 204 | 403 | 403 | 204 |
| `/templates` | GET | 401 | 200 | 200 | 200 | 200 | 403 | 403 |
| `/templates` | POST | 401 | 201 | 201 | 201 | 403 | 403 | 201 |
| `/templates/{id}` | GET | 401 | 200 | 404*| 200 | 200 | 403 | 403 |
| `/templates/{id}` | PUT | 401 | 200 | 404 | 200 | 403 | 403 | 200 |
| `/templates/{id}` | DELETE | 401 | 204 | 404 | 204 | 403 | 403 | 204 |
| `/templates/{id}/publish` | POST | 401 | 403 | 403 | 200 | 403 | 403 | 403 |
| `/templates/{id}/use` | POST | 401 | 200 | 200 | 200 | 403 | 403 | 200 |
| `/templates/pipelines/{id}/save-as-template` | POST | 401 | 201 | 404 | 201 | 403 | 403 | 201 |
| `/notifications` | GET | 401 | 200 | 200 | 200 | 200 | 403 | 403 |
| `/notifications/unread-count`| GET | 401 | 200 | 200 | 200 | 200 | 403 | 403 |
| `/notifications/{id}/read` | PATCH | 401 | 200 | 404 | 200 | 403 | 403 | 200 |
| `/notifications/read-all` | PATCH | 401 | 200 | 200 | 200 | 403 | 403 | 200 |
| `/notifications/preferences` | GET | 401 | 200 | 200 | 200 | 200 | 403 | 403 |
| `/notifications/preferences` | PUT | 401 | 200 | 200 | 200 | 403 | 403 | 200 |
| `/notifications/preferences/email` | PUT | 401 | 200 | 200 | 200 | 403 | 403 | 200 |
| `/notifications/preferences/webhook` | PUT | 401 | 200 | 200 | 200 | 403 | 403 | 200 |
| `/notifications/deliveries` | GET | 401 | 200 | 200 | 200 | 200 | 403 | 403 |
| `/api-keys` | GET | 401 | 200 | 200 | 200 | 403 | 403 | 403 |
| `/api-keys` | POST | 401 | 201 | 201 | 201 | 403 | 403 | 403 |
| `/api-keys/{id}` | DELETE | 401 | 204 | 404 | 204 | 403 | 403 | 403 |
| `/admin/audit-log` | GET | 401 | 403 | 403 | 200 | 403 | 403 | 403 |
| `/admin/audit-log/export` | GET | 401 | 403 | 403 | 200 | 403 | 403 | 403 |

*\* Note: Public system templates return 200 for all authenticated users; private templates return 404 to other users.*

---

## Step-by-Step Walkthrough

Follow these steps in numerical order. Each step produces variables that subsequent steps consume.

### Quick Setup with Demo Data
To immediately populate the database with users, pipeline, runs, and tokens, run:
```bash
python scripts/seed_demo.py
```

---

### GROUP 1: Authentication

#### 1.1 Register User A
- **Endpoint:** `POST /auth/register`
- **What it does:** Registers a new standard user account.
- **Auth:** None
- **Prerequisites:** None
- **Request Body:** (`samples/api/register_user_a.json`)
  ```json
  {
    "email": "user_a@example.com",
    "password": "Password123!"
  }
  ```
- **Success Status:** `201 Created`
- **Expected Response:**
  ```json
  {
    "id": "<USER_A_ID>",
    "email": "user_a@example.com",
    "role": "user",
    "created_at": "2026-10-23T00:00:00Z"
  }
  ```
- **Negative Test:** Send email without password.
  - Body: `{"email": "bad@example.com"}`
  - Status: `422 Unprocessable Entity`
- **What to check:** `<USER_A_ID>` is generated.

#### 1.2 Register User B (for cross-user isolation tests)
- **Endpoint:** `POST /auth/register`
- **What it does:** Registers a second user to test that User B cannot access User A's data.
- **Auth:** None
- **Prerequisites:** None
- **Request Body:** (`samples/api/register_user_b.json`)
  ```json
  {
    "email": "user_b@example.com",
    "password": "Password123!"
  }
  ```
- **Success Status:** `201 Created`
- **Negative Test:** Register user with duplicate email.
  - Status: `409 Conflict` (`"email_taken"`)

#### 1.3 Login User A
- **Endpoint:** `POST /auth/login`
- **What it does:** Authenticates User A and returns a JWT access token and a refresh token.
- **Auth:** None
- **Prerequisites:** User A registered
- **Request Body:** (`samples/api/login_user_a.json`)
  ```json
  {
    "email": "user_a@example.com",
    "password": "Password123!"
  }
  ```
- **Success Status:** `200 OK`
- **Expected Response:**
  ```json
  {
    "access_token": "<USER_A_TOKEN>",
    "refresh_token": "<USER_A_REFRESH>",
    "token_type": "bearer"
  }
  ```
- **Negative Test:** Wrong password.
  - Body: `{"email": "user_a@example.com", "password": "WrongPassword!"}`
  - Status: `401 Unauthorized` (`"invalid_credentials"`)
- **What to save:** Save `access_token` as `<USER_A_TOKEN>`, and `refresh_token` as `<USER_A_REFRESH>`.

#### 1.4 Login Admin
- **Endpoint:** `POST /auth/login`
- **What it does:** Authenticates the seeded administrator.
- **Auth:** None
- **Prerequisites:** Server started or `scripts/seed_demo.py` run
- **Request Body:** (`samples/api/login_admin.json`)
  ```json
  {
    "email": "admin@test.com",
    "password": "adminpassword"
  }
  ```
- **Success Status:** `200 OK`
- **What to save:** Save `access_token` as `<ADMIN_TOKEN>`.

#### 1.5 Refresh Access Token
- **Endpoint:** `POST /auth/refresh`
- **What it does:** Rotates the refresh token and issues a brand-new access token.
- **Auth:** None
- **Prerequisites:** Received `<USER_A_REFRESH>` from login
- **Request Body:**
  ```json
  {
    "refresh_token": "<USER_A_REFRESH>"
  }
  ```
- **Success Status:** `200 OK`
- **Negative Test:** Re-using the old refresh token immediately.
  - Status: `401 Unauthorized` (Token rotation revokes the previous token)

---

### GROUP 2: Pipelines

#### 2.1 Create Pipeline
- **Endpoint:** `POST /pipelines`
- **What it does:** Validates the pipeline DAG and creates a new pipeline at version 1.
- **Auth:** Bearer `<USER_A_TOKEN>` (or API Key with `write` scope)
- **Prerequisites:** User A logged in
- **Request Body:** (`samples/api/create_pipeline.json`)
  ```json
  {
    "name": "orders-pipeline",
    "config": {
      "pipeline": {
        "id": "11111111-1111-4111-8111-111111111111",
        "name": "orders-pipeline",
        "version": 1,
        "nodes": [
          {"id": "src_1", "type": "source", "plugin": "csv", "config": {"path": "samples/data/orders.csv", "delimiter": ","}},
          {"id": "tr_1", "type": "transform", "plugin": "filter", "config": {"expression": "amount > 100"}},
          {"id": "snk_1", "type": "sink", "plugin": "csv", "config": {"path": "outputs/orders_clean.csv", "mode": "overwrite"}}
        ],
        "edges": [
          {"from": "src_1", "to": "tr_1"},
          {"from": "tr_1", "to": "snk_1"}
        ],
        "retry": {"max_attempts": 3, "backoff_seconds": 5}
      }
    }
  }
  ```
- **Success Status:** `201 Created`
- **Negative Test:** Create with invalid cyclic or unknown plugin config.
  - Body: `{"name": "bad", "config": {"pipeline": {"nodes": [{"id": "1", "plugin": "nonexistent"}]}}}`
  - Status: `422 Unprocessable Entity` (`"invalid_pipeline_config"`)
- **What to save:** Save `id` from response as `<PIPELINE_ID>`.

#### 2.2 List Pipelines
- **Endpoint:** `GET /pipelines?page=1&page_size=20`
- **What it does:** Lists pipelines owned by the authenticated user with pagination.
- **Auth:** Bearer `<USER_A_TOKEN>` (or API key with `read` scope)
- **Success Status:** `200 OK`
- **Negative Test:** Request with no auth header.
  - Status: `401 Unauthorized`

#### 2.3 Get Pipeline Details
- **Endpoint:** `GET /pipelines/<PIPELINE_ID>`
- **What it does:** Retrieves pipeline metadata and the current version's configuration snapshot.
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Success Status:** `200 OK`
- **Negative Test (IDOR):** Request with `<USER_B_TOKEN>`.
  - Status: `404 Not Found` (User B cannot see User A's pipeline)

#### 2.4 Update Pipeline
- **Endpoint:** `PUT /pipelines/<PIPELINE_ID>`
- **What it does:** Creates an immutable new version (Version 2) for the pipeline.
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Request Body:** (`samples/api/update_pipeline.json`)
- **Success Status:** `200 OK`
- **What to check:** Response `current_version` is now `2`.

---

### GROUP 3: Versions, Diff & Rollback

#### 3.1 List Pipeline Versions
- **Endpoint:** `GET /pipelines/<PIPELINE_ID>/versions`
- **What it does:** Lists all historic configuration versions for this pipeline.
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Success Status:** `200 OK` (Shows versions 1 and 2)

#### 3.2 Diff Pipeline Versions
- **Endpoint:** `GET /pipelines/<PIPELINE_ID>/versions/diff?from=1&to=2`
- **What it does:** Returns structured differences in nodes and edges between two versions.
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Success Status:** `200 OK`
- **Negative Test:** Foreign version index `from=1&to=999`.
  - Status: `404 Not Found`

#### 3.3 Rollback Pipeline Version
- **Endpoint:** `POST /pipelines/<PIPELINE_ID>/rollback/1`
- **What it does:** Re-applies version 1 config as a brand-new version 3 (never mutates history).
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Success Status:** `201 Created`
- **What to check:** Response `version` is `3`.

---

### GROUP 4: Runs & Execution

#### 4.1 Start Pipeline Run
- **Endpoint:** `POST /pipelines/<PIPELINE_ID>/runs`
- **What it does:** Queues execution of the pipeline in a background worker.
- **Auth:** Bearer `<USER_A_TOKEN>` (or API key with `run` scope)
- **Success Status:** `202 Accepted`
- **Expected Response:**
  ```json
  {
    "id": "<RUN_ID>",
    "pipeline_id": "<PIPELINE_ID>",
    "version": 3,
    "status": "queued",
    "progress": 0.0,
    "records_in": 0,
    "records_out": 0
  }
  ```
- **What to save:** Save `id` as `<RUN_ID>`.

#### 4.2 Get Run Status
- **Endpoint:** `GET /runs/<RUN_ID>`
- **What it does:** Checks current status, counters, and metrics of the run.
- **Auth:** Bearer `<USER_A_TOKEN>` (or API key with `read` or `run` scope)
- **Success Status:** `200 OK` (Status transitions: queued -> running -> succeeded)

#### 4.3 Get Run Logs
- **Endpoint:** `GET /runs/<RUN_ID>/logs`
- **What it does:** Returns granular execution logs and node step records.
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Success Status:** `200 OK`

#### 4.4 Stop Run
- **Endpoint:** `POST /runs/<RUN_ID>/stop`
- **What it does:** Sends cancellation request to a running pipeline.
- **Auth:** Bearer `<USER_A_TOKEN>` (or API key with `run` scope)
- **Success Status:** `200 OK` (or `409 Conflict` if already completed)

#### 4.5 WebSocket Live Progress (`/ws/runs/{id}`)
Connect to receive real-time streaming progress frames for the run:
- **CLI Command (`websocat`):**
  ```bash
  websocat "ws://127.0.0.1:8000/ws/runs/<RUN_ID>?token=<USER_A_TOKEN>"
  ```
- **Browser JavaScript snippet:**
  ```javascript
  const ws = new WebSocket("ws://127.0.0.1:8000/ws/runs/<RUN_ID>?token=<USER_A_TOKEN>");
  ws.onmessage = (event) => console.log("Run Progress Event:", JSON.parse(event.data));
  ws.onopen = () => console.log("Connected to run stream.");
  ```

---

### GROUP 5: Plugins Catalog

#### 5.1 List Plugins
- **Endpoint:** `GET /plugins`
- **What it does:** Returns all available source, transform, and sink plugins with their JSON schema.
- **Auth:** None
- **Success Status:** `200 OK`

---

### GROUP 6: Schedules

#### 6.1 Preview Schedule Execution Times
- **Endpoint:** `GET /pipelines/schedules/preview?cron=0%20*%20*%20*%20*&timezone=UTC&count=5`
- **What it does:** Calculates next 5 trigger times without saving.
- **Auth:** None
- **Success Status:** `200 OK`
- **Negative Test:** Invalid cron syntax.
  - Query: `?cron=99%20*%20*%20*&timezone=UTC`
  - Status: `422 Unprocessable Entity`

#### 6.2 Create Pipeline Schedule
- **Endpoint:** `POST /pipelines/<PIPELINE_ID>/schedule`
- **What it does:** Registers a cron schedule for automatic pipeline execution.
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Request Body:** (`samples/api/create_schedule.json`)
  ```json
  {
    "cron": "0 * * * *",
    "timezone": "UTC",
    "enabled": true,
    "misfire_policy": "skip"
  }
  ```
- **Success Status:** `201 Created`
- **Negative Test:** Creating duplicate schedule on same pipeline.
  - Status: `409 Conflict` (`"already_exists"`)

#### 6.3 Get Pipeline Schedule
- **Endpoint:** `GET /pipelines/<PIPELINE_ID>/schedule`
- **What it does:** Retrieves active schedule details and computed `next_run_at`.
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Success Status:** `200 OK`

#### 6.4 Update Pipeline Schedule
- **Endpoint:** `PUT /pipelines/<PIPELINE_ID>/schedule`
- **What it does:** Updates cron, timezone, or misfire policy (`skip` or `run_once`).
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Request Body:** (`samples/api/update_schedule.json`)
- **Success Status:** `200 OK`

#### 6.5 Delete Pipeline Schedule
- **Endpoint:** `DELETE /pipelines/<PIPELINE_ID>/schedule`
- **What it does:** Removes the schedule and de-registers background jobs.
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Success Status:** `204 No Content`

---

### GROUP 7: Templates

#### 7.1 List Templates
- **Endpoint:** `GET /templates?category=ETL&page=1&page_size=20`
- **What it does:** Lists public system templates and private templates owned by the user.
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Success Status:** `200 OK`

#### 7.2 Create Private Template
- **Endpoint:** `POST /templates`
- **What it does:** Creates a private reusable template for User A.
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Request Body:** (`samples/api/create_template.json`)
- **Success Status:** `201 Created`
- **What to save:** Save `id` as `<TEMPLATE_ID>`.

#### 7.3 Use Template to Clone Pipeline
- **Endpoint:** `POST /templates/<TEMPLATE_ID>/use`
- **What it does:** Instantiates a new pipeline from the template and atomically increments `usage_count`.
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Success Status:** `200 OK`
- **What to check:** Returns new `pipeline_id`.

#### 7.4 Publish Template (Admin Only)
- **Endpoint:** `POST /templates/<TEMPLATE_ID>/publish`
- **What it does:** Promotes a private template to public visibility.
- **Auth:** Admin only (`<ADMIN_TOKEN>`)
- **Success Status:** `200 OK`
- **Negative Test:** Attempt by regular user.
  - Header: `Authorization: Bearer <USER_A_TOKEN>`
  - Status: `403 Forbidden`

#### 7.5 Save Pipeline as Template
- **Endpoint:** `POST /templates/pipelines/<PIPELINE_ID>/save-as-template`
- **What it does:** Snapshot-copies current pipeline config into a new private template.
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Success Status:** `201 Created`

---

### GROUP 8: Notifications

#### 8.1 List Notifications
- **Endpoint:** `GET /notifications?page=1&page_size=20`
- **What it does:** Lists user's in-app notifications.
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Success Status:** `200 OK`
- **What to save:** Save `id` of first item as `<NOTIFICATION_ID>`.

#### 8.2 Get Unread Notification Count
- **Endpoint:** `GET /notifications/unread-count`
- **What it does:** Returns integer tally of unread notifications.
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Success Status:** `200 OK`

#### 8.3 Mark Notification Read
- **Endpoint:** `PATCH /notifications/<NOTIFICATION_ID>/read`
- **What it does:** Sets `read: true` for the specified notification.
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Request Body:** (`samples/api/mark_notification_read.json`)
  ```json
  {"read": true}
  ```
- **Success Status:** `200 OK`

#### 8.4 Update Notification Preferences (Email & Webhook)
- **Endpoint:** `PUT /notifications/preferences`
- **What it does:** Toggles dispatch channels (in_app, email, webhook) per event.
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Request Body:** (`samples/api/update_notification_preferences.json`)
- **Success Status:** `200 OK`

- **Update Webhook Config:** `PUT /notifications/preferences/webhook`
  - Body: `{"url": "http://127.0.0.1:9000/webhook", "secret": "top-secret-webhook-key-123456"}`
  - Status: `200 OK`
  - *Verify webhook receipt with `python scripts/webhook_listener.py`.*

#### 8.5 WebSocket Notifications Push (`/ws/notifications`)
- **CLI Command (`websocat`):**
  ```bash
  websocat "ws://127.0.0.1:8000/ws/notifications?token=<USER_A_TOKEN>"
  ```
- **Browser JavaScript snippet:**
  ```javascript
  const ws = new WebSocket("ws://127.0.0.1:8000/ws/notifications?token=<USER_A_TOKEN>");
  ws.onmessage = (event) => console.log("Realtime Notification:", JSON.parse(event.data));
  ```

---

### GROUP 9: API Keys

#### 9.1 Create Read-Only API Key
- **Endpoint:** `POST /api-keys`
- **What it does:** Creates a key with `["read"]` scope. Shows the raw key ONCE.
- **Auth:** Bearer `<USER_A_TOKEN>` (API Keys CANNOT create API keys)
- **Request Body:** (`samples/api/create_api_key_read.json`)
  ```json
  {
    "name": "ci-read-only-key",
    "scopes": ["read"],
    "expires_at": null
  }
  ```
- **Success Status:** `201 Created`
- **What to save:** Save `key` as `<API_KEY_READ>` and `id` as `<API_KEY_ID>`.
- **Negative Test:** API Key tries calling `POST /api-keys`.
  - Header: `X-API-Key: <API_KEY_READ>`
  - Status: `403 Forbidden`

#### 9.2 Create Run-Only API Key
- **Endpoint:** `POST /api-keys`
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Request Body:** (`samples/api/create_api_key_run.json`)
- **Success Status:** `201 Created`
- **What to save:** Save `key` as `<API_KEY_RUN>`.

#### 9.3 Verify API Key Permissions
- **Test 1 (Read key gets pipelines):**
  - `GET /pipelines` with `X-API-Key: <API_KEY_READ>` -> **`200 OK`**
- **Test 2 (Read key denied starting runs):**
  - `POST /pipelines/<PIPELINE_ID>/runs` with `X-API-Key: <API_KEY_READ>` -> **`403 Forbidden`**
- **Test 3 (Run key allowed starting runs):**
  - `POST /pipelines/<PIPELINE_ID>/runs` with `X-API-Key: <API_KEY_RUN>` -> **`202 Accepted`**

#### 9.4 List API Keys
- **Endpoint:** `GET /api-keys`
- **What it does:** Lists keys showing name, prefix, and last_used (raw secret is never shown).
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Success Status:** `200 OK`

#### 9.5 Revoke API Key
- **Endpoint:** `DELETE /api-keys/<API_KEY_ID>`
- **What it does:** Deactivates the API key immediately.
- **Auth:** Bearer `<USER_A_TOKEN>`
- **Request Body:** (`samples/api/revoke_api_key.json`)
  ```json
  {"confirm": true}
  ```
- **Success Status:** `204 No Content`
- **What to check:** Subsequent requests with this key return `401 Unauthorized`.

---

### GROUP 10: Audit Log (Admin Only)

#### 10.1 List Audit Events
- **Endpoint:** `GET /admin/audit-log?page=1&page_size=20`
- **What it does:** Returns append-only log of security and mutation events.
- **Auth:** Admin only (`<ADMIN_TOKEN>`)
- **Success Status:** `200 OK`
- **Negative Test:** Non-admin attempts to view audit logs.
  - Header: `Authorization: Bearer <USER_A_TOKEN>`
  - Status: `403 Forbidden`

#### 10.2 Export Audit Log as CSV
- **Endpoint:** `GET /admin/audit-log/export`
- **What it does:** Streams audit trail in CSV format with formula injection escaping.
- **Auth:** Admin only (`<ADMIN_TOKEN>`)
- **Success Status:** `200 OK` (`Content-Type: text/csv`)

---

### GROUP 11: System Health

#### 11.1 Health Check
- **Endpoint:** `GET /health`
- **What it does:** Returns application status and version.
- **Auth:** None
- **Success Status:** `200 OK`
  ```json
  {
    "status": "ok",
    "version": "1.0.0"
  }
  ```

---

## Postman Collection Usage
Import `docs/ChaosFlow_Postman_Collection.json` into Postman or Insomnia.
1. Set the collection variable `base_url` to `http://127.0.0.1:8000`.
2. Run `POST /auth/login` to obtain and set `access_token`.
3. Requests are pre-populated with realistic bodies from `samples/api/`.
