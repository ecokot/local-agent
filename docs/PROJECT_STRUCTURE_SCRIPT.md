# Скрипт создания PROJECT_STRUCTURE.txt

**Назначение:** сформировать снимок структуры отслеживаемых Git-файлов
проекта, короткий SHA исходного commit, дату создания и метку текущего
состояния.

Снимок описывает версионируемое содержимое проекта, а не локальную
рабочую среду. Источник перечня файлов — `git ls-files`; незакоммиченные
untracked-файлы не включаются. Локальные базы, traces, логи, workspace,
IDE-настройки, виртуальные окружения и прочие runtime-данные не должны
попадать в снимок.

## Команда PowerShell

Откройте PowerShell в корне репозитория и выполните скрипт целиком.
Скрипт запросит метку этапа и не использует устаревшее значение по
умолчанию.

```powershell
$ErrorActionPreference = "Stop"
$root = (Get-Location).Path

$stage = Read-Host "Введите актуальную метку этапа/состояния"
if ([string]::IsNullOrWhiteSpace($stage)) {
    throw "Метка этапа обязательна. Снимок не создан."
}

$commit = git -C $root rev-parse --short HEAD
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($commit)) {
    throw "Не удалось получить Git-коммит. Запустите скрипт из корня репозитория."
}

$trackedOutput = @(git -C $root ls-files)
if ($LASTEXITCODE -ne 0) {
    throw "Не удалось получить список отслеживаемых Git-файлов."
}

# Защита на случай, если runtime-каталог был добавлен в Git до появления
# соответствующего правила .gitignore.
$excludedDirectories = @(
    ".git", ".venv", "venv", ".idea", ".vscode", ".gigaide",
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "data", "logs", "traces", "workspace", "htmlcov"
)

$tracked = @(
    $trackedOutput | Where-Object {
        $normalized = $_ -replace "\\", "/"
        $segments = $normalized.Split("/")
        $blocked = $false
        foreach ($segment in $segments) {
            if ($segment -in $excludedDirectories) {
                $blocked = $true
                break
            }
        }
        -not $blocked
    } | Sort-Object
)

if ($tracked.Count -eq 0) {
    throw "После фильтрации не осталось отслеживаемых файлов."
}

function Get-ProjectTree {
    param(
        [string]$RelativePath = "",
        [string]$Prefix = ""
    )

    $directoryPrefix = if ($RelativePath) { "$RelativePath/" } else { "" }
    $children = foreach ($file in $tracked) {
        if ($file.StartsWith($directoryPrefix, [System.StringComparison]::Ordinal)) {
            $rest = $file.Substring($directoryPrefix.Length)
            $parts = $rest.Split("/", 2)
            [pscustomobject]@{
                Name = $parts[0]
                IsDirectory = ($parts.Count -gt 1)
            }
        }
    }

    $items = @(
        $children |
        Group-Object Name |
        ForEach-Object {
            [pscustomobject]@{
                Name = $_.Name
                IsDirectory = [bool]($_.Group | Where-Object { $_.IsDirectory })
            }
        } |
        Sort-Object @{ Expression = { -not $_.IsDirectory } }, Name
    )

    for ($i = 0; $i -lt $items.Count; $i++) {
        $item = $items[$i]
        $last = $i -eq ($items.Count - 1)
        $branch = if ($last) { "\--- " } else { "+--- " }
        "$Prefix$branch$($item.Name)"

        if ($item.IsDirectory) {
            $childPath = if ($RelativePath) {
                "$RelativePath/$($item.Name)"
            } else {
                $item.Name
            }
            $nextPrefix = $Prefix + $(if ($last) { "    " } else { "|   " })
            Get-ProjectTree -RelativePath $childPath -Prefix $nextPrefix
        }
    }
}

$lines = @(
    "ЭТАП: $stage"
    "КОММИТ: $commit"
    "ДАТА: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    "ИСТОЧНИК: git ls-files (tracked files only)"
    ""
    "СТРУКТУРА ПРОЕКТА"
    (Split-Path $root -Leaf)
)
$lines += @(Get-ProjectTree)

$output = Join-Path $root "PROJECT_STRUCTURE.txt"
$lines | Set-Content -LiteralPath $output -Encoding UTF8
Write-Host "Снимок структуры проекта создан: $output"
Write-Host "Включено отслеживаемых файлов: $($tracked.Count)"
```

## Проверка результата

1. Убедиться, что метка соответствует текущему состоянию: не указывать
   следующий подэтап как уже начатый или принятый.
2. Проверить, что commit — ожидаемый исходный HEAD локальной `main`.
3. Убедиться, что в дереве нет `.gigaide`, `data`, `logs`, `traces`,
   `workspace`, виртуальных окружений, IDE-каталогов и кэшей.
4. Убедиться, что добавленные в Git task/report/docs файлы присутствуют.
5. Включать обновлённый снимок в документационный PR. В нём будет
   указан исходный commit, на котором снимок был построен; это ожидаемо,
   поскольку сам снимок становится частью следующего commit.
