# Подготовка Docker-образа для ExecutionBackend

**Статус:** нормативная инструкция для образа `local-agent-python-test:3.12`.

Эта инструкция описывает подготовку образа, который используется текущим
`ExecutionBackend`. Backend запускает контейнер от UID/GID `10000:10000`,
монтирует Workspace в `/workspace` и не предоставляет контейнеру сеть.

## 1. Требования

- Windows с Docker Desktop и включённым режимом Linux containers;
- работающий Docker Engine;
- PowerShell;
- локальный клон репозитория `local-agent`;
- `uv.lock` и `pyproject.toml` из того же checkout, для которого
  подготавливается образ.

Docker Desktop может использовать WSL 2 backend или Hyper-V backend.
Это разные конфигурации. Успешная проверка одной из них не является
подтверждением проверки другой. В отчёте о проверке необходимо явно
указать выбранный backend.

## 2. Проверка Docker Engine

В PowerShell из корня репозитория:

```powershell
docker version
docker context show
docker info --format 'OSType={{.OSType}} Architecture={{.Architecture}} Kernel={{.KernelVersion}}'
```

Ожидается доступный Server, `OSType=linux` и архитектура,
соответствующая хосту. Для Docker Desktop обычно используется context
`desktop-linux`.

Для фиксации конфигурации сохраните также:

```powershell
Get-ComputerInfo |
    Select-Object WindowsProductName, WindowsVersion, OsBuildNumber, CsSystemType

wsl --status
docker version
docker info
```

Backend Docker Desktop (WSL 2 или Hyper-V) зафиксируйте отдельно по
фактически выбранной настройке Docker Desktop. Не выводите тип backend
только из наличия WSL или из имени Docker context.

## 3. Создание Dockerfile

Из корня репозитория:

```powershell
$repoRoot = (Get-Location).Path
$testRoot = Join-Path $env:SystemDrive "local-agent-isolation-test"

New-Item -ItemType Directory -Force -Path $testRoot | Out-Null
```

Создайте файл `$testRoot\Dockerfile`:

```powershell
@'
FROM python:3.12-slim

RUN python -m pip install --no-cache-dir uv

WORKDIR /opt/local-agent-env

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --group dev --no-install-project

ENV PATH="/opt/local-agent-env/.venv/bin:$PATH"
ENV PYTHONPATH="/workspace/src"
ENV UV_CACHE_DIR=/tmp/uv-cache

WORKDIR /workspace
'@ | Set-Content -LiteralPath (Join-Path $testRoot "Dockerfile") -Encoding ascii
```

### Почему нужен UV_CACHE_DIR

`ExecutionBackend` запускает контейнер с `--user 10000:10000`.
При запуске `uv run pytest -v` без явной настройки uv может выбрать
`/.cache/uv`. Непривилегированный пользователь не может создать там
каталог кэша, и uv завершается до запуска pytest.

`UV_CACHE_DIR=/tmp/uv-cache` направляет кэш в каталог `/tmp`, доступный
для записи непривилегированному пользователю. Это настройка образа;
изменение реализации `ExecutionBackend` для исправления данной ошибки
не требуется.

Остальные строки Dockerfile сохраняют прежнюю схему: зависимости
устанавливаются из lock-файла во время сборки, исходники проекта
поступают через Workspace, а виртуальное окружение доступно через PATH.

## 4. Сборка образа

Выполнить из корня репозитория:

```powershell
docker build `
    -f "$testRoot\Dockerfile" `
    -t local-agent-python-test:3.12 `
    $repoRoot
```

Сборка требует доступа к сети для получения базового образа и зависимостей.
Сами контейнеры исполнения запускаются без сети. Существующий тег образа
не нужно удалять вручную: команда сборки назначает указанный тег
собранному образу.

## 5. Проверка образа и пользователя

```powershell
docker run --rm --network none --user 10000:10000 `
    --entrypoint sh `
    local-agent-python-test:3.12 `
    -c 'id; python --version; uv --version; pytest --version; ruff --version; test -w /tmp && echo TMP_WRITABLE'
```

Ожидается:

- UID 10000 и GID 10000;
- Python 3.12;
- доступные `uv`, `pytest` и `ruff`;
- строка `TMP_WRITABLE`.

Версии инструментов нужно записывать по фактическому выводу, а не
предполагать по минимальным версиям из `pyproject.toml`.

## 6. Проверка uv и pytest в контейнере с теми же ограничениями

Создайте временный Workspace с одним тестом:

```powershell
$smokeWorkspace = Join-Path $testRoot "uv-smoke-workspace"
New-Item -ItemType Directory -Force -Path $smokeWorkspace | Out-Null

@'
import os
import sys

def test_container_identity_and_platform():
    assert sys.platform == "linux"
    assert os.getuid() == 10000
    assert os.getgid() == 10000
'@ | Set-Content -LiteralPath (Join-Path $smokeWorkspace "test_sample.py") -Encoding ascii
```

Запустите точную команду pytest через uv от UID/GID 10000:

```powershell
docker run --rm `
    --network none `
    --memory 512m `
    --cpus 1 `
    --pids-limit 64 `
    --cap-drop ALL `
    --security-opt no-new-privileges `
    --user 10000:10000 `
    --mount "type=bind,source=$smokeWorkspace,target=/workspace" `
    --workdir /workspace `
    local-agent-python-test:3.12 `
    uv run pytest -v
```

Ожидается `1 passed` без ошибки доступа к `/.cache/uv`.

## 7. Проверка интеграции с ExecutionBackend

Из корня репозитория, на хосте Windows, с работающим Docker Engine
и собранным образом:

```powershell
uv run pytest -v test/integration/test_execution_backend_integration.py::test_run_tests_tool_executes_pytest_through_docker_backend
```

Этот тест сам создаёт тестовый Workspace, запускает
`RunTestsTool → RunCommandTool → ExecutionBackend → Docker` и проверяет,
что команда `uv run pytest -v` проходит внутри Linux-контейнера
от UID/GID `10000:10000`.

Затем выполните полный набор проверок проекта на хосте:

```powershell
uv run pytest -v
uv run ruff check .
git diff --check
```

Полный набор тестов запускается на хосте, потому что интеграционные тесты
сами обращаются к Docker Engine через Docker CLI. Не следует запускать
весь набор тестов внутри изолированного ExecutionBackend-контейнера:
Docker socket в этот контейнер намеренно не монтируется.

## 8. Что записывать в отчёте

Для каждой физической машины и каждой конфигурации backend отдельно
зафиксируйте:

- модель/редакцию Windows и build;
- Docker Desktop version и Docker Engine version;
- Docker context, тип контейнеров и архитектуру;
- фактически выбранный backend: WSL 2 или Hyper-V;
- версии Python, uv, pytest и Ruff внутри образа;
- результат smoke-теста из раздела 6;
- результат указанного интеграционного теста;
- полный результат pytest, Ruff и `git diff --check`.

Не переносите результат Hyper-V-проверки на WSL 2 или наоборот.
Если backend не был отдельно проверен, укажите это явно.

## 9. Границы и воспроизводимость

Тег `python:3.12-slim` является плавающим: он не фиксирует patch-версию
Python или digest базового образа. Поэтому Dockerfile воспроизводит набор
зависимостей проекта по `uv.lock`, но не гарантирует побитово одинаковый
образ при сборке в разные даты. Для полной фиксации базового слоя требуется
отдельное решение о pinning digest.

Эта инструкция устраняет известную проблему каталога кэша uv при запуске
от UID/GID `10000:10000`. Она не меняет и не расширяет гарантии
Docker-изоляции, описанные в
`docs/EXECUTION_ISOLATION_RESEARCH.md`.
