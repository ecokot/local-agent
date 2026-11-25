# Скрипт создания PROJECT_STRUCTURE.txt

**Назначение:** сформировать в корне проекта снимок структуры файлов
и каталогов, короткий SHA текущего Git-коммита, дату создания и метку
текущего этапа.

**Использование:** открыть PowerShell в корне репозитория, установить
актуальное значение переменной $stage в начале скрипта и выполнить
его целиком. После генерации проверить содержимое PROJECT_STRUCTURE.txt
и при необходимости добавить обновлённый снимок в Git.

**Важно:** метку этапа нужно обновлять вручную перед каждым запуском.
Значение «Этап 9.Б» ниже — отправная настройка для актуального снимка;
перед каждым запуском его необходимо сверять с текущим этапом.

---

## Снимок структуры проекта

Выполнить из корневой папки проекта в PowerShell.

Скрипт создаёт файл `PROJECT_STRUCTURE.txt`, содержащий:
- текущий этап разработки;
- короткий SHA текущего Git-коммита;
- дату и время создания снимка;
- актуальное дерево файлов и каталогов проекта.

Из дерева исключаются каталоги `.venv`, `.git`, `.idea`, `.pytest_cache`, `.ruff_cache`, `__pycache__` на всех уровнях вложенности.

### Команда

```powershell
$stage = "Этап 9.Б"
$root = (Get-Location).Path
$commit = git rev-parse --short HEAD

if ($LASTEXITCODE -ne 0) {
    throw "Не удалось получить Git-коммит. Проверьте, что текущая папка является Git-репозиторием."
}

$exclude = @(
    ".venv",
    ".git",
    ".idea",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__"
)

function Get-ProjectTree {
    param(
        [string]$Path,
        [string]$Prefix = ""
    )

    $items = @(
        Get-ChildItem -LiteralPath $Path -Force |
        Where-Object {
            -not ($_.PSIsContainer -and $_.Name -in $exclude)
        } |
        Sort-Object @{ Expression = { -not $_.PSIsContainer } }, Name
    )

    for ($i = 0; $i -lt $items.Count; $i++) {
        $item = $items[$i]
        $last = $i -eq ($items.Count - 1)

        $branch = if ($last) { "\--- " } else { "+--- " }

        "$Prefix$branch$($item.Name)"

        if ($item.PSIsContainer) {
            $nextPrefix = $Prefix + $(if ($last) { "    " } else { "|   " })
            Get-ProjectTree -Path $item.FullName -Prefix $nextPrefix
        }
    }
}

$lines = @(
    "ЭТАП: $stage"
    "КОММИТ: $commit"
    "ДАТА: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    ""
    "СТРУКТУРА ПРОЕКТА"
    (Split-Path $root -Leaf)
)

$lines += @(Get-ProjectTree -Path $root)

$output = Join-Path $root "PROJECT_STRUCTURE.txt"
$lines | Set-Content -LiteralPath $output -Encoding UTF8

Write-Host "Снимок структуры проекта создан: $output"
```
------------------------------------------------------------------------
