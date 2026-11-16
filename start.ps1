#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
Set-Location -LiteralPath $PSScriptRoot

$DefaultBaseUrl = 'https://api.deepseek.com'
$DefaultModel = 'deepseek-flash'
$MinDockerMemoryGb = 6
$env:DOCKER_CLI_HINTS = 'false'
$EnvFile = Join-Path $PSScriptRoot '.env'
$KbFile = Join-Path (Join-Path $PSScriptRoot 'data') 'kb.zip'
$Utf8NoBom = New-Object System.Text.UTF8Encoding $false

function Write-Title([string]$Text) { Write-Host ''; Write-Host $Text -ForegroundColor Cyan }
function Write-Warn([string]$Text) { Write-Host $Text -ForegroundColor Yellow }
function Stop-WithError([string]$Text) { Write-Host ''; Write-Host "Ошибка: $Text" -ForegroundColor Red; exit 1 }

function Select-First {
    foreach ($value in $args) { if ($value) { return $value } }
    return ''
}

function Get-EnvLines {
    if (Test-Path -LiteralPath $EnvFile) { return @([IO.File]::ReadAllLines($EnvFile)) }
    return @()
}

function Get-EnvValue([string]$Name) {
    $line = Get-EnvLines | Where-Object { $_ -like "$Name=*" } | Select-Object -Last 1
    if ($line) { return $line.Substring($Name.Length + 1).Trim().Trim('"', "'") }
    return ''
}

function Set-EnvValues([System.Collections.IDictionary]$Values) {
    $names = @($Values.Keys)
    $lines = @(Get-EnvLines | Where-Object { $current = $_; -not ($names | Where-Object { $current -like "$_=*" }) })
    foreach ($name in $names) { $lines += "$name=$($Values[$name])" }
    [IO.File]::WriteAllText($EnvFile, (($lines -join "`n") + "`n"), $Utf8NoBom)
}

function Test-Docker {
    & docker info 2>&1 | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Get-HttpStatus([string]$Url, [string]$Key) {
    try {
        return [int](Invoke-WebRequest -Uri $Url -Headers @{ Authorization = "Bearer $Key" } -UseBasicParsing -TimeoutSec 15).StatusCode
    } catch {
        $exception = $_.Exception
        if ($exception.PSObject.Properties['Response'] -and $exception.Response) { return [int]$exception.Response.StatusCode }
        return 0
    }
}

function Read-Secret([string]$Prompt) {
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    return (New-Object System.Net.NetworkCredential('', $secure)).Password
}

function Test-PortBusy([int]$Port) {
    foreach ($address in [Net.IPAddress]::Loopback, [Net.IPAddress]::IPv6Loopback) {
        $client = $null
        try {
            $client = New-Object System.Net.Sockets.TcpClient($address.AddressFamily)
            if ($client.ConnectAsync($address, $Port).Wait(500)) { return $true }
        } catch {
        } finally {
            if ($client) { $client.Close() }
        }
    }
    return $false
}

function Get-FreePort([int]$Start) {
    $port = $Start
    while (Test-PortBusy $port) { $port++ }
    return $port
}

function Test-Crashed([string]$Service) {
    $id = & docker compose ps -a -q $Service 2>$null | Select-Object -First 1
    if (-not $id) { return $false }
    $state = "$(& docker inspect -f '{{.State.Status}} {{.RestartCount}}' $id 2>$null)".Trim()
    if (-not $state) { return $false }
    $status, $restarts = $state.Split(' ')
    return ($status -eq 'exited' -or $status -eq 'dead' -or [int]$restarts -gt 0)
}

function Invoke-WithRetry([string]$What, [scriptblock]$Action) {
    for ($attempt = 1; $attempt -le 4; $attempt++) {
        & $Action
        if ($LASTEXITCODE -eq 0) { return $true }
        if ($attempt -lt 4) {
            Write-Warn "$What прервалась — чаще всего это сбой сети. Через 20 секунд пробую ещё раз (попытка $($attempt + 1) из 4), готовые шаги не повторяются…"
            Start-Sleep -Seconds 20
        }
    }
    return $false
}

function Get-BaseImages {
    $files = (Join-Path $PSScriptRoot 'Dockerfile'), (Join-Path (Join-Path $PSScriptRoot 'open-webui') 'Dockerfile')
    return @(Get-Content -LiteralPath $files | ForEach-Object { if ($_ -match '^FROM +(--platform=\S+ +)?(\S+:\S+)') { $Matches[2] } } | Sort-Object -Unique)
}

function Wait-Http([string]$Url, [string]$What, [int]$LimitSeconds, [string]$Service) {
    Write-Host "Жду $What" -NoNewline
    $watch = [Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        try {
            Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5 | Out-Null
            Write-Host ' готово'
            return $true
        } catch {
        }
        if ((Test-Crashed $Service) -or $watch.Elapsed.TotalSeconds -ge $LimitSeconds) {
            Write-Host ''
            return $false
        }
        Write-Host '.' -NoNewline
        Start-Sleep -Seconds 5
    }
}

Write-Title 'ПСБ · Юридический ассистент — запуск'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-WithError 'Docker не найден. Установите Docker Desktop (https://www.docker.com/products/docker-desktop/), запустите его и повторите.'
}
& docker compose version 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Stop-WithError 'Нужен Docker Compose v2 — он входит в Docker Desktop. Обновите Docker Desktop.' }
if (-not (Test-Docker)) {
    if ($env:ProgramFiles) {
        $desktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
        if (Test-Path -LiteralPath $desktop) {
            Write-Host 'Запускаю Docker Desktop…'
            Start-Process -FilePath $desktop
        }
    }
    Write-Host 'Жду, пока Docker будет готов (до 3 минут)…'
    for ($i = 0; $i -lt 90 -and -not (Test-Docker); $i++) { Start-Sleep -Seconds 2 }
    if (-not (Test-Docker)) { Stop-WithError 'Docker не отвечает. Запустите Docker Desktop вручную, дождитесь статуса Engine running и повторите.' }
}
$memory = 0L
[void][long]::TryParse("$(& docker info --format '{{.MemTotal}}' 2>$null)".Trim(), [ref]$memory)
if ($memory -gt 0 -and $memory -lt $MinDockerMemoryGb * 1e9) {
    Write-Warn "Docker выделено меньше $MinDockerMemoryGb ГБ памяти — моделям поиска может не хватить. Увеличьте лимит памяти WSL (файл .wslconfig, параметр memory) или в Docker Desktop: Settings → Resources (рекомендуется 8 ГБ)."
}

if (-not (Test-Path -LiteralPath $KbFile)) {
    Write-Title 'Нужна база знаний kb.zip из материалов кейса'
    while ($true) {
        $source = "$(Read-Host 'Перетащите файл kb.zip в это окно (или вставьте путь к нему) и нажмите Enter')".Trim().Trim('"', "'")
        if ($source -and (Test-Path -LiteralPath $source -PathType Leaf)) {
            $head = New-Object byte[] 2
            $stream = [IO.File]::OpenRead((Resolve-Path -LiteralPath $source).ProviderPath)
            try { [void]$stream.Read($head, 0, 2) } finally { $stream.Close() }
            if ($head[0] -eq 0x50 -and $head[1] -eq 0x4B) { break }
        }
        Write-Host "Это не zip-архив или файл не найден: $source"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $KbFile) | Out-Null
    Copy-Item -LiteralPath $source -Destination $KbFile -Force
    Write-Host 'База скопирована в data\kb.zip'
}

$baseUrl = "$(Select-First $env:LLM_BASE_URL (Get-EnvValue 'LLM_BASE_URL') $DefaultBaseUrl)".TrimEnd('/')
$model = Select-First $env:LLM_MODEL (Get-EnvValue 'LLM_MODEL') $DefaultModel
$savedKey = Get-EnvValue 'LLM_API_KEY'

Write-Title "API-ключ языковой модели ($baseUrl)"
while ($true) {
    if ($savedKey) {
        $tail = $savedKey.Substring([Math]::Max(0, $savedKey.Length - 4))
        $prompt = "Ключ уже сохранён (…$tail). Enter — оставить его, или вставьте новый"
    } else {
        $prompt = 'Вставьте API-ключ (Ctrl+V или правый клик; символы не отображаются) и нажмите Enter'
    }
    $key = (Read-Secret $prompt) -replace '\s', ''
    if (-not $key) { $key = $savedKey }
    if (-not $key) { Write-Host 'Ключ не введён.'; continue }
    $code = Get-HttpStatus "$baseUrl/models" $key
    if ($code -eq 200) { Write-Host 'Ключ принят.'; break }
    if ($code -eq 401 -or $code -eq 403) {
        Write-Host "API отклонил ключ (HTTP $code). Проверьте его и вставьте снова."
        $savedKey = ''
        continue
    }
    Write-Warn "Не удалось проверить ключ (HTTP $code) — продолжаю без проверки."
    break
}
if ($baseUrl -like '*api.deepseek.com*') {
    try {
        $balance = Invoke-RestMethod -Uri "$baseUrl/user/balance" -Headers @{ Authorization = "Bearer $key" } -TimeoutSec 15
        if ($balance.is_available -eq $false) { Write-Warn 'На балансе DeepSeek нет средств — ассистент не сможет отвечать. Пополните баланс на platform.deepseek.com.' }
    } catch {
    }
}

& docker compose down --remove-orphans 2>&1 | Out-Null
$uiPort = Get-FreePort ([int](Select-First $env:WEBUI_PORT 3000))
$apiPort = Get-FreePort ([int](Select-First $env:LEGAL_API_PORT 8000))
Set-EnvValues ([ordered]@{
    LLM_API_KEY     = $key
    LLM_BASE_URL    = $baseUrl
    LLM_MODEL       = $model
    WEBUI_PORT      = $uiPort
    LEGAL_API_PORT  = $apiPort
    PUBLIC_BASE_URL = "http://localhost:$apiPort"
})
if (-not $env:WITH_LOCAL_MODELS) { $env:WITH_LOCAL_MODELS = 'true' }

Write-Title 'Собираю и запускаю контейнеры'
Write-Host 'Первый запуск занимает 10–20 минут: скачиваются зависимости, модели поиска (~4,5 ГБ) и собирается интерфейс. Повторные запуски — около минуты.'
foreach ($image in Get-BaseImages) {
    & docker image inspect $image 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0 -and -not (Invoke-WithRetry "Загрузка образа $image" { & docker pull -q $image })) {
        Stop-WithError "Не удалось скачать образ $image. Проверьте интернет и запустите скрипт ещё раз."
    }
}
if (-not (Invoke-WithRetry 'Сборка ассистента' { & docker compose build legal-api })) {
    Stop-WithError 'Не удалось собрать ассистента — причина в сообщениях выше. Если это ошибка сети, запустите скрипт ещё раз.'
}
if (-not (Invoke-WithRetry 'Сборка интерфейса' { & docker compose build webui })) {
    Stop-WithError 'Не удалось собрать интерфейс — причина в сообщениях выше. Если это ошибка сети, запустите скрипт ещё раз.'
}
& docker compose up -d
if ($LASTEXITCODE -ne 0) { Stop-WithError 'Не удалось запустить контейнеры — причина в сообщениях выше.' }

Write-Title 'Запускаю ассистента'
if (-not (Wait-Http "http://localhost:$apiPort/health" 'ассистента (при первом запуске строится индекс базы — несколько минут)' 1800 'legal-api')) {
    & docker compose logs --tail 60 legal-api
    Stop-WithError 'Ассистент не запустился — последние строки журнала выше. Частая причина — мало памяти у Docker (нужно 8 ГБ).'
}
if (-not (Wait-Http "http://localhost:$uiPort/health" 'интерфейс' 600 'webui')) {
    & docker compose logs --tail 60 webui
    Stop-WithError 'Интерфейс не запустился — последние строки журнала выше.'
}

$url = "http://localhost:$uiPort"
try { Start-Process $url } catch { }

Write-Title 'Готово'
Write-Host "Чат с ассистентом:      $url"
Write-Host "Документы базы знаний:  http://localhost:$apiPort/kb"
Write-Host ''
Write-Host 'Остановить:             docker compose down'
Write-Host 'Запустить снова:        start.bat'
Write-Host 'Журнал ассистента:      docker compose logs -f legal-api'
