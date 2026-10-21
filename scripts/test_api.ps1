$base = "http://127.0.0.1:8000"
$headers = @{}
$tokens = @{}

function Invoke-Api {
    param($method, $path, $body = $null, $auth = $true)
    $h = @{}
    if ($auth -and $tokens.access) { $h["Authorization"] = "Bearer $($tokens.access)" }
    if ($body) { $h["Content-Type"] = "application/json" }
    try {
        $params = @{ Uri = "$base$path"; Method = $method; Headers = $h; ErrorAction = "Stop" }
        if ($body) { $params["Body"] = ($body | ConvertTo-Json -Depth 10) }
        $r = Invoke-RestMethod @params
        return $r
    } catch {
        $status = $_.Exception.Response.StatusCode.value__
        Write-Host "  [$method] $path -> HTTP $status" -ForegroundColor Red
        return $null
    }
}

Write-Host "=== 1. HEALTH ===" -ForegroundColor Cyan
Invoke-Api "GET" "/health" -auth $false

Write-Host "`n=== 2. AUTH ===" -ForegroundColor Cyan
Write-Host "--- Register ---"
$reg = Invoke-Api "POST" "/auth/register" @{ email = "testuser@test.com"; password = "testpass123"; full_name = "Test User" } -auth $false
if ($reg) { Write-Host "  Registered: $($reg.email) (role: $($reg.role))" -ForegroundColor Green }

Write-Host "--- Login ---"
$login = Invoke-Api "POST" "/auth/login" @{ email = "admin@test.com"; password = "123456" } -auth $false
if ($login) {
    $tokens.access = $login.access_token
    $tokens.refresh = $login.refresh_token
    Write-Host "  Logged in: $($login.email) | role: $($login.role)" -ForegroundColor Green
    Write-Host "  Access token: $($login.access_token.Substring(0,30))..." -ForegroundColor Gray
}

Write-Host "--- Refresh ---"
$refreshed = Invoke-Api "POST" "/auth/refresh" @{ refresh_token = $tokens.refresh } -auth $false
if ($refreshed) {
    $tokens.access = $refreshed.access_token
    Write-Host "  Refreshed access token OK" -ForegroundColor Green
}

Write-Host "`n=== 3. PIPELINES ===" -ForegroundColor Cyan
Write-Host "--- Create Pipeline 1: CSV ETL ---"
$pipe1 = Invoke-Api "POST" "/pipelines" @{
    name = "csv-etl-demo"
    config = @{
        pipeline = @{
            id = [guid]::NewGuid().ToString()
            name = "csv-etl-demo"
            version = 1
            schedule = $null
            nodes = @(
                @{ id = "src"; type = "source"; plugin = "csv"; config = @{ path = "data/orders.csv" } }
                @{ id = "flt"; type = "transform"; plugin = "filter"; config = @{ expression = "amount > 50" } }
                @{ id = "snk"; type = "sink"; plugin = "console"; config = @{} }
            )
            edges = @(@{ from = "src"; to = "flt" }, @{ from = "flt"; to = "snk" })
            retry = @{ max_attempts = 2; backoff_seconds = 1 }
        }
    }
}
if ($pipe1) {
    Write-Host "  Created: $($pipe1.name) (id: $($pipe1.id), version: $($pipe1.current_version))" -ForegroundColor Green
    $pipe1Id = $pipe1.id
}

Write-Host "--- Create Pipeline 2: JSON Transform ---"
$pipe2 = Invoke-Api "POST" "/pipelines" @{
    name = "json-transform-demo"
    config = @{
        pipeline = @{
            id = [guid]::NewGuid().ToString()
            name = "json-transform-demo"
            version = 1
            schedule = $null
            nodes = @(
                @{ id = "src"; type = "source"; plugin = "json"; config = @{ path = "data/users.jsonl" } }
                @{ id = "cast"; type = "transform"; plugin = "cast"; config = @{ fields = @{ age = "int" } } }
                @{ id = "snk"; type = "sink"; plugin = "json"; config = @{ path = "output/users.json" } }
            )
            edges = @(@{ from = "src"; to = "cast" }, @{ from = "cast"; to = "snk" })
            retry = @{ max_attempts = 2; backoff_seconds = 1 }
        }
    }
}
if ($pipe2) {
    Write-Host "  Created: $($pipe2.name) (id: $($pipe2.id), version: $($pipe2.current_version))" -ForegroundColor Green
    $pipe2Id = $pipe2.id
}

Write-Host "--- List Pipelines ---"
$pipes = Invoke-Api "GET" "/pipelines?page=1&page_size=10"
if ($pipes) { Write-Host "  Found $($pipes.items.Count) pipeline(s)" -ForegroundColor Green }

Write-Host "--- Get Pipeline ---"
$got = Invoke-Api "GET" "/pipelines/$pipe1Id"
if ($got) { Write-Host "  Got: $($got.name) v$($got.current_version)" -ForegroundColor Green }

Write-Host "--- Update Pipeline (new version) ---"
$upd = Invoke-Api "PUT" "/pipelines/$pipe1Id" @{
    config = @{
        pipeline = @{
            id = $pipe1Id
            name = "csv-etl-demo"
            version = 2
            schedule = "0 * * * *"
            nodes = @(
                @{ id = "src"; type = "source"; plugin = "csv"; config = @{ path = "data/orders.csv" } }
                @{ id = "flt"; type = "transform"; plugin = "filter"; config = @{ expression = "amount > 100" } }
                @{ id = "map"; type = "transform"; plugin = "map"; config = @{ select = @("id", "name", "amount") } }
                @{ id = "snk"; type = "sink"; plugin = "console"; config = @{} }
            )
            edges = @(@{ from = "src"; to = "flt" }, @{ from = "flt"; to = "map" }, @{ from = "map"; to = "snk" })
            retry = @{ max_attempts = 3; backoff_seconds = 2 }
        }
    }
}
if ($upd) { Write-Host "  Updated to v$($upd.current_version)" -ForegroundColor Green }

Write-Host "--- List Versions ---"
$vers = Invoke-Api "GET" "/pipelines/$pipe1Id/versions"
if ($vers) { Write-Host "  Versions: $($vers | ForEach-Object { "v$($_.version)" })" -ForegroundColor Green }

Write-Host "`n=== 4. RUNS ===" -ForegroundColor Cyan
Write-Host "--- Start Run (pipeline v1) ---"
$run1 = Invoke-Api "POST" "/pipelines/$pipe1Id/runs" @{ version = 1 }
if ($run1) {
    Write-Host "  Run started: $($run1.id) status=$($run1.status)" -ForegroundColor Green
    $run1Id = $run1.id
}

Start-Sleep -Seconds 3

Write-Host "--- Get Run Details ---"
$runDetail = Invoke-Api "GET" "/runs/$run1Id"
if ($runDetail) {
    Write-Host "  Status: $($runDetail.status) | Progress: $($runDetail.progress)%" -ForegroundColor Green
    Write-Host "  Records: in=$($runDetail.records_in) out=$($runDetail.records_out)" -ForegroundColor Green
}

Write-Host "--- Get Run Logs ---"
$logs = Invoke-Api "GET" "/runs/$run1Id/logs"
if ($logs) {
    Write-Host "  Logs ($($logs.Count) entries):" -ForegroundColor Green
    $logs | Select-Object -First 5 | ForEach-Object { Write-Host "    [$($_.level)] $($_.node_id): $($_.event) - $($_.message)" }
}

Write-Host "--- List Runs ---"
$runs = Invoke-Api "GET" "/runs?page=1&page_size=10"
if ($runs) { Write-Host "  Total runs: $($runs.items.Count)" -ForegroundColor Green }

Write-Host "`n=== 5. PLUGINS ===" -ForegroundColor Cyan
$plugins = Invoke-Api "GET" "/plugins"
if ($plugins) {
    Write-Host "  Plugins ($($plugins.Count)):" -ForegroundColor Green
    $plugins | ForEach-Object { Write-Host "    [$($_.type)] $($_.name)" }
}

Write-Host "`n=== 6. WEBSOCKET ===" -ForegroundColor Cyan
Write-Host "  (Test via browser: ws://127.0.0.1:8000/ws/runs/$run1Id`?token=$($tokens.access))" -ForegroundColor Gray

Write-Host "`n=== DONE ===" -ForegroundColor Cyan
