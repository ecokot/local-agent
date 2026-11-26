# Исследование изоляции выполнения команд в Docker

**Статус:** исследование на рабочем компьютере проведено;
**Проект:** `local-agent`  
**Дата исследования:** 2026-11-23  
**Целевая платформа:** Windows 11, Linux containers, Docker Desktop с Hyper-V backend.

---

## 1. Цель исследования

Определить и практически проверить способ изолированного выполнения команд и тестов проекта `local-agent` на Windows 11.

Исследование должно ответить на следующие вопросы:

- Можно ли запускать Python 3.12 и тесты проекта внутри Linux-контейнера?
- Можно ли предоставить контейнеру доступ только к выделенному Workspace?
- Можно ли запускать процессы от непривилегированного пользователя?
- Можно ли отключить сеть и capabilities контейнера?
- Можно ли ограничить потребление CPU, памяти и количество процессов?
- Как ведёт себя контейнер при превышении тайм-аута?
- Как обнаруживать ошибки запуска Docker Engine и отсутствие образа?
- Какие ограничения и риски остаются у выбранного подхода?

Результат исследования предназначен для последующего проектирования самостоятельного `ExecutionBackend`.

**Важно:** исследование не означает, что изоляция уже реализована в `local-agent`. На момент исследования `RunCommandTool` и `RunTestsTool` не используют Docker и не обеспечивают полноценную изоляцию файловой системы.

## 2. Выбранный подход

В качестве основного кандидата выбран Docker Engine с Linux-контейнерами, работающий через Docker Desktop на Windows 11 с Hyper-V backend.

Схема:

1. Windows выступает хост-системой.
2. Docker Desktop запускает Linux-среду в виртуальной машине Hyper-V.
3. Linux-контейнер запускает Python-команды и тесты.
4. В контейнер монтируется выделенный каталог Workspace.
5. Контейнер запускается с ограниченными правами, отключённой сетью и лимитами ресурсов.
6. После выполнения контейнер удаляется.

Контейнер не должен получать доступ ко всему каталогу проекта или диску Windows без явной необходимости. Для выполнения задачи ему предоставляется только выделенный Workspace.

### 2.1. Почему выбран Hyper-V backend

В ходе исследования использовался Docker Desktop с Hyper-V backend.

Преимущество этого варианта для рассматриваемого сценария — Linux-контейнеры выполняются внутри отдельной Linux-виртуальной машины Docker Desktop, а не непосредственно как Linux-процессы Windows.

Это не является абсолютной гарантией безопасности: остаются доверие к Docker Desktop, Docker Engine, гипервизору и ядру Linux.

### 2.2. Требования Docker Desktop

Согласно актуальной документации Docker:

- Hyper-V backend доступен при установке Docker Desktop в режиме **All-users**.
- Для установки All-users необходимы права администратора.
- Для Hyper-V backend требуется поддерживаемая редакция Windows, включённый Hyper-V и аппаратная виртуализация.
- В установщике Docker Desktop версии 4.97 и новее backend выбирается на этапе настройки.
- Для Hyper-V необходимо выбрать Hyper-V, а не WSL 2.
- Docker Desktop должен быть запущен, прежде чем Docker CLI сможет обращаться к Engine.

Официальная документация:

- [Установка Docker Desktop на Windows](https://docs.docker.com/desktop/setup/install/windows-install/)
- [Требования к разрешениям Docker Desktop в Windows](https://docs.docker.com/desktop/setup/install/windows-permission-requirements/)
- [Docker Desktop Virtual Machine Manager](https://docs.docker.com/desktop/features/vmm/)

Если на домашнем компьютере невозможно использовать Hyper-V backend, это расхождение необходимо зафиксировать. Не следует автоматически подменять Hyper-V на WSL 2: это будет уже другой вариант окружения, требующий отдельной проверки.

## 3. Проверенная конфигурация рабочего компьютера

Исследование проводилось на следующей конфигурации:

| Параметр | Значение |
|---|---|
| ОС | Windows 11 Pro |
| Сборка Windows | 26300 |
| Архитектура | x64 |
| Процессор | Intel |
| Компьютер | MSI Katana GF76 11SC |
| Оперативная память хоста | около 16 ГБ |
| Аппаратная виртуализация | включена |
| SLAT | поддерживается |
| VBS | отключён |
| Hyper-V | включён |
| WSL 2 | не установлен |
| Docker Desktop | 4.99.0 (245822) |
| Docker Engine | 29.9.3 |
| Docker context | `desktop-linux` |
| Тип контейнеров | Linux |
| Ядро Linux VM | 7.0.14-linuxkit |
| Cgroup | v2 |
| CPU Docker VM | 12 |
| Память Docker VM | около 1.915 GiB |

Указанный объём памяти Docker VM не равен объёму оперативной памяти Windows. Это отдельный лимит виртуальной машины Docker Desktop.

## 4. Подготовка окружения для воспроизведения

Все команды в этом документе предназначены для PowerShell.

### 4.1. Проверка Windows и виртуализации

Открыть PowerShell от имени администратора:

```powershell
Get-ComputerInfo |
    Select-Object WindowsProductName, WindowsVersion, OsBuildNumber, CsSystemType

systeminfo
```

В выводе `systeminfo` проверить наличие следующих возможностей:

- VM Monitor Mode Extensions;
- Virtualization Enabled In Firmware;
- Second Level Address Translation;
- Data Execution Prevention Available.

Все необходимые аппаратные возможности должны поддерживаться и быть включены.

Проверить состояние Hyper-V:

```powershell
Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V
```

Если Hyper-V отсутствует и редакция Windows поддерживает его, включить:

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All
```

После включения при необходимости перезагрузить компьютер.

Не следует выполнять включение Hyper-V без предварительной проверки редакции Windows и аппаратных возможностей.

### 4.2. Установка Docker Desktop

Скачать Docker Desktop только с официального сайта:

https://www.docker.com/products/docker-desktop/

При установке:

1. Выбрать режим установки **All-users**.
2. Выбрать backend **Hyper-V**.
3. Не переключать Docker Desktop на Windows Containers.
4. Завершить установку.
5. Перезагрузить компьютер, если установщик этого потребовал.
6. Запустить Docker Desktop.

Для текущего исследования необходимы именно Linux Containers.

### 4.3. Проверка Docker Engine

В PowerShell:

```powershell
docker version
docker context ls
docker context show
docker info
```

Ожидаемые признаки:

- Docker CLI подключается к Engine;
- текущий context — `desktop-linux`;
- `OSType` — `linux`;
- серверная архитектура — `linux/amd64` для x64;
- Docker Engine доступен.

Если Docker Desktop ещё запускается, дождаться его готовности и повторить проверку.

## 5. Создание тестового окружения

Работать из корневой папки репозитория `local-agent`.

### 5.1. Переменные и каталоги

```powershell
$repoRoot = (Get-Location).Path
$testRoot = Join-Path $env:SystemDrive "local-agent-isolation-test"
$workspace = Join-Path $testRoot "workspace"

New-Item -ItemType Directory -Force -Path $testRoot | Out-Null
New-Item -ItemType Directory -Force -Path $workspace | Out-Null

Set-Content -LiteralPath (Join-Path $workspace "workspace-file.txt") `
    -Value "WORKSPACE_ACCESS_OK" -Encoding UTF8

Set-Content -LiteralPath (Join-Path $testRoot "host-secret.txt") `
    -Value "HOST_SECRET_12345" -Encoding UTF8
```

Назначение:

- `workspace` — каталог, который будет доступен контейнеру;
- `host-secret.txt` — контрольный файл за пределами Workspace;
- `testRoot` — временный каталог для тестовых материалов.

### 5.2. Dockerfile

**Устаревшая инструкция:** приведённый в первоначальной версии исследования
Dockerfile не задавал `UV_CACHE_DIR` и не обеспечивает корректный запуск
`uv run pytest -v` при использовании Backend с UID/GID `10000:10000`.

Нормативная инструкция сборки образа, включая актуальный Dockerfile,
проверку UID/GID и запуск интеграционного теста, перенесена в
[`docs/EXECUTION_BACKEND_IMAGE.md`](EXECUTION_BACKEND_IMAGE.md).
Именно этот документ следует использовать при подготовке или пересборке
`local-agent-python-test:3.12`.

В Dockerfile обязательно задаётся:

```dockerfile
ENV UV_CACHE_DIR=/tmp/uv-cache
```

Эта настройка предотвращает попытку uv создать кэш в недоступном для
непривилегированного пользователя каталоге `/.cache/uv`.

Назначение Dockerfile:

- использовать Python 3.12;
- установить `uv`;
- установить зависимости проекта из `uv.lock`;
- установить группу dev, включая инструменты тестирования;
- не устанавливать сам проект в образ;
- получать исходный код проекта через Workspace;
- запускать Python из созданного виртуального окружения.

### 5.3. Сборка тестового образа

Выполнить из корня репозитория:

```powershell
docker build `
    -f "$testRoot\Dockerfile" `
    -t local-agent-python-test:3.12 `
    $repoRoot
```

На этапе сборки требуется доступ к сети для загрузки базового образа и зависимостей.

Сборка образа и выполнение контейнера — разные операции. В дальнейшем контейнеры выполнения должны запускаться с отключённой сетью.

Проверить Python:

```powershell
docker run --rm --network none `
    local-agent-python-test:3.12 `
    python --version
```

Ожидаемый результат: Python версии 3.12.

**Зафиксированный результат рабочего компьютера:** Python 3.12.16.

Примечание: тег `python:3.12-slim` не фиксирует конкретный patch-релиз и digest образа. Для строгой воспроизводимости в дальнейшем следует рассмотреть фиксацию digest базового образа.

## 6. Проверки изоляции

Для основных проверок использовались ограничения:

```powershell
--network none
--memory 512m
--cpus 1
--pids-limit 64
--cap-drop ALL
--security-opt no-new-privileges
--user 10000:10000
```

Их назначение:

| Параметр | Назначение |
|---|---|
| `--network none` | Отключение сетевого подключения контейнера |
| `--memory 512m` | Лимит памяти 512 МБ |
| `--cpus 1` | Лимит CPU в 1 ядро |
| `--pids-limit 64` | Ограничение числа процессов |
| `--cap-drop ALL` | Удаление Linux capabilities |
| `no-new-privileges` | Запрет получения дополнительных привилегий |
| `--user 10000:10000` | Запуск от непривилегированного пользователя |

Эти параметры не заменяют проверку файловой системы и не гарантируют отсутствие уязвимостей ядра или Docker Engine.

### 6.1. Проверка Linux-контейнера

Команда:

```powershell
docker run --rm alpine:latest uname -a
```

Ожидаемый результат: команда выполняется внутри Linux-контейнера и выводит информацию о Linux-ядре.

Фактический результат: контейнер успешно запущен. В выводе присутствовало ядро `7.0.14-linuxkit`.

### 6.2. Проверка пользователя и capabilities

Сначала проверялась конфигурация контейнера по умолчанию:

```powershell
docker run --rm alpine:latest `
    sh -c "id && cat /proc/self/status | grep -E '^(Uid|Gid|CapEff|NoNewPrivs):'"
```

Фактический результат:

- UID 0;
- GID 0;
- присутствовали capabilities;
- `NoNewPrivs` был равен 0.

Вывод: запуск без дополнительных ограничений не соответствует требованиям исследуемого сценария.

Затем проверялась ограниченная конфигурация:

```powershell
docker run --rm `
    --network none `
    --memory 512m `
    --cpus 1 `
    --pids-limit 64 `
    --cap-drop ALL `
    --security-opt no-new-privileges `
    --user 10000:10000 `
    alpine:latest `
    sh -c "id && cat /proc/self/status | grep -E '^(Uid|Gid|CapEff|NoNewPrivs):' && cat /proc/1/cgroup"
```

Ожидаемый результат:

- UID 10000;
- GID 10000;
- `CapEff` равен нулю;
- `NoNewPrivs` равен 1.

Фактический результат: все перечисленные значения подтверждены.

### 6.3. Доступ к Workspace

Команда:

```powershell
docker run --rm `
    --network none `
    --mount "type=bind,source=$workspace,target=/workspace" `
    --workdir /workspace `
    alpine:latest `
    sh -c "pwd && ls -la && cat workspace-file.txt"
```

Ожидаемый результат:

- каталог `/workspace` доступен;
- контрольный файл читается;
- содержимое соответствует созданному на Windows.

Фактический результат: доступ к Workspace подтверждён.

### 6.4. Запись в Workspace и сохранение изменений

Команда:

```powershell
docker run --rm `
    --network none `
    --mount "type=bind,source=$workspace,target=/workspace" `
    --workdir /workspace `
    alpine:latest `
    sh -c "echo AGENT_WRITE_TEST > agent-created.txt"
```

Проверка на Windows:

```powershell
Get-Content (Join-Path $workspace "agent-created.txt")
```

Фактический результат: файл создан контейнером и доступен на Windows. Содержимое `AGENT_WRITE_TEST` сохранилось после завершения контейнера.

Вывод: изменения в смонтированном Workspace сохраняются на хосте.

### 6.5. Проверка отсутствия доступа к остальной файловой системе хоста

Команда:

```powershell
docker run --rm `
    --network none `
    --mount "type=bind,source=$workspace,target=/workspace" `
    --workdir /workspace `
    alpine:latest `
    sh -c "echo '--- Workspace ---'; ls -la /workspace; echo '--- Host paths ---'; ls -ld /mnt/c /host_mnt/c /run/desktop/mnt/host/c 2>&1; echo '--- Secret search ---'; find / -name host-secret.txt 2>/dev/null"
```

Ожидаемый результат:

- Workspace доступен;
- стандартные пути к диску Windows не доступны;
- контрольный файл за пределами Workspace не обнаруживается.

Фактический результат:

- `/mnt/c` отсутствовал;
- `/host_mnt/c` отсутствовал;
- `/run/desktop/mnt/host/c` отсутствовал;
- `find` не обнаружил `host-secret.txt`;
- в mountinfo присутствовал bind mount только исследуемого Workspace, кроме системных mount points контейнера.

Это подтверждает результат конкретной проверки, но не доказывает невозможность доступа к хосту любым другим способом.

### 6.6. Проверка относительной символической ссылки

Создать ссылку из Workspace на контрольный файл за его пределами:

```powershell
New-Item -ItemType SymbolicLink `
    -Path "$workspace\host-secret-relative-link.txt" `
    -Target "$testRoot\host-secret.txt"
```

Проверить внутри контейнера:

```powershell
docker run --rm `
    --network none `
    --mount "type=bind,source=$workspace,target=/workspace" `
    --workdir /workspace `
    alpine:latest `
    sh -c "ls -l host-secret-relative-link.txt; cat host-secret-relative-link.txt"
```

Ожидаемый результат: чтение файла за пределами Workspace не выполняется.

Фактический результат: контейнер видел ссылку, но `cat` завершился ошибкой `No such file or directory`.

Контрольный файл на Windows сохранил исходное содержимое `HOST_SECRET_12345`.

### 6.7. Проверка абсолютной символической ссылки

Создать абсолютную ссылку:

```powershell
New-Item -ItemType SymbolicLink `
    -Path "$workspace\host-secret-absolute-link.txt" `
    -Target "$testRoot\host-secret.txt"
```

Проверка:

```powershell
docker run --rm `
    --network none `
    --mount "type=bind,source=$workspace,target=/workspace" `
    --workdir /workspace `
    local-agent-python-test:3.12 `
    python -c "from pathlib import Path; p=Path('/workspace/host-secret-absolute-link.txt'); print('exists:', p.exists()); print('resolved:', p.resolve()); print('content:', p.read_text() if p.exists() else 'UNAVAILABLE')"
```

Фактический результат:

- `exists: False`;
- содержимое контрольного файла недоступно;
- чтение файла не выполнено.

### 6.8. Проверка записи через символическую ссылку

Создать ссылку на контрольный файл:

```powershell
New-Item -ItemType SymbolicLink `
    -Path "$workspace\host-write-link.txt" `
    -Target "$testRoot\host-secret.txt"
```

Попытаться записать данные через неё:

```powershell
docker run --rm `
    --network none `
    --mount "type=bind,source=$workspace,target=/workspace" `
    --workdir /workspace `
    local-agent-python-test:3.12 `
    python -c "from pathlib import Path; p=Path('/workspace/host-write-link.txt'); p.write_text('MODIFIED_BY_CONTAINER')"
```

Фактический результат: запись завершилась `PermissionError`.

Контрольный файл на Windows сохранил исходное содержимое `HOST_SECRET_12345`.

### 6.9. Проверка Windows Junction

Создать junction на каталог за пределами Workspace. Для создания junction в исследовании использовался PowerShell с повышенными правами:

```powershell
New-Item -ItemType Junction `
    -Path "$workspace\host-folder-link" `
    -Target $testRoot
```

Проверить:

```powershell
Get-Item "$workspace\host-folder-link" |
    Format-List FullName, LinkType, Target
```

Попытаться записать файл через junction из контейнера:

```powershell
docker run --rm `
    --network none `
    --mount "type=bind,source=$workspace,target=/workspace" `
    --workdir /workspace `
    local-agent-python-test:3.12 `
    python -c "from pathlib import Path; p=Path('/workspace/host-folder-link/host-secret.txt'); p.write_text('MODIFIED_BY_CONTAINER')"
```

Фактический результат: запись завершилась `PermissionError: [Errno 1] Operation not permitted`.

Контрольный файл не изменился.

**Ограничение проверки:** чтение файлов через junction отдельно не проверялось. Нельзя считать этот сценарий полностью исследованным.

### 6.10. Проверка сетевой изоляции и Docker socket

Команда:

```powershell
docker run --rm `
    --network none `
    --cap-drop ALL `
    --security-opt no-new-privileges `
    --user 10000:10000 `
    alpine:latest `
    sh -c "echo '--- Network ---'; wget -T 3 -O /dev/null https://example.com 2>&1; echo '--- Docker socket ---'; ls -l /var/run/docker.sock /run/docker.sock 2>&1"
```

Ожидаемый результат:

- сетевое подключение не устанавливается;
- стандартные пути Docker socket внутри контейнера отсутствуют.

Фактический результат:

- `wget` сообщил `bad address 'example.com'`;
- `/var/run/docker.sock` отсутствовал;
- `/run/docker.sock` отсутствовал.

Проверены именно эти стандартные пути. Это не является доказательством отсутствия любых возможных способов управления Docker Engine.

### 6.11. Проверка лимитов ресурсов

Команда:

```powershell
docker run --rm `
    --network none `
    --memory 512m `
    --cpus 1 `
    --pids-limit 64 `
    --cap-drop ALL `
    --security-opt no-new-privileges `
    --user 10000:10000 `
    alpine:latest `
    sh -c "echo 'pids.max:'; cat /sys/fs/cgroup/pids.max; echo 'memory.max:'; cat /sys/fs/cgroup/memory.max; echo 'cpu.max:'; cat /sys/fs/cgroup/cpu.max"
```

Фактический результат:

| Параметр | Значение |
|---|---:|
| `pids.max` | 64 |
| `memory.max` | 536870912 |
| `cpu.max` | `100000 100000` |

Лимиты подтверждены через cgroup v2.

### 6.12. Проверка Python и тестов проекта

Проверка Python:

```powershell
docker run --rm --network none `
    local-agent-python-test:3.12 `
    python --version
```

Фактический результат: Python 3.12.16.

Запуск полного набора тестов проекта:

```powershell
docker run --rm `
    --network none `
    --memory 512m `
    --cpus 1 `
    --pids-limit 64 `
    --cap-drop ALL `
    --security-opt no-new-privileges `
    --user 10000:10000 `
    --mount "type=bind,source=$repoRoot,target=/workspace" `
    --workdir /workspace `
    local-agent-python-test:3.12 `
    python -m pytest -v
```

Фактический результат рабочего компьютера:

**291 passed in 6.56s**

Это результат на версии исходного кода, существовавшей на момент проведения теста. При повторении на домашнем компьютере число тестов и результат необходимо записать заново.

### 6.13. Проверка кода завершения процесса

Команда:

```powershell
docker run --rm --network none `
    local-agent-python-test:3.12 `
    python -c "raise SystemExit(42)"

$LASTEXITCODE
```

Фактический результат: код завершения равен `42`.

Вывод: ненулевой exit code процесса должен возвращаться вызывающей стороне как результат выполнения команды. Его нельзя автоматически трактовать как ошибку запуска Docker Engine.

### 6.14. Проверка тайм-аута и завершения дочерних процессов

Для проверки использовался отдельный Python-скрипт `timeout_probe.py`.

Алгоритм проверки:

1. Сгенерировать уникальное имя контейнера.
2. Запустить контейнер через `subprocess.Popen`.
3. Дождаться завершения с ограничением времени.
4. При тайм-ауте выполнить `docker rm -f` для именованного контейнера.
5. Дождаться завершения Docker CLI.
6. Проверить, что контейнер больше не существует.

Пример основного сценария:

```python
import subprocess
import uuid

container_name = f"local-agent-timeout-{uuid.uuid4().hex[:8]}"

command = [
    "docker", "run",
    "--name", container_name,
    "--network", "none",
    "alpine:latest",
    "sh", "-c", "sleep 300",
]

process = subprocess.Popen(
    command,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)

try:
    stdout, stderr = process.communicate(timeout=8)
    print("EXIT:", process.returncode)
    print(stdout)
    print(stderr)
except subprocess.TimeoutExpired:
    print("TIMEOUT")

    cleanup = subprocess.run(
        ["docker", "rm", "-f", container_name],
        capture_output=True,
        text=True,
        timeout=20,
    )

    print("CLEANUP EXIT:", cleanup.returncode)
    print(cleanup.stdout)
    print(cleanup.stderr)

    try:
        stdout, stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()

    print("DOCKER CLI EXIT:", process.returncode)
    print(stdout)
    print(stderr)

    inspect = subprocess.run(
        ["docker", "container", "inspect", container_name],
        capture_output=True,
        text=True,
        timeout=10,
    )

    if inspect.returncode == 0:
        print("VERIFICATION: container exists.")
    elif (
        "No such object" in inspect.stderr
        or "No such container" in inspect.stderr
    ):
        print("VERIFICATION: container does not exist.")
    else:
        print("VERIFICATION: unable to confirm container state.")
        print(inspect.stderr)
```

Отдельно проверялся контейнер с Python-процессом и дочерним `sleep 300`.

Фактический результат:

- тайм-аут срабатывал через 8 секунд;
- `docker rm -f` завершался с кодом 0;
- Docker CLI завершался с кодом 137;
- `docker container inspect` подтверждал отсутствие контейнера.

Вывод: для последующего Backend предпочтительно использовать уникальное имя контейнера и явное удаление через `docker rm -f` с последующей проверкой.

Не следует полагаться только на `--rm` при управлении тайм-аутами: Backend должен иметь возможность адресно удалить контейнер и проверить результат.

**Ограничение:** проверка подтверждает удаление контейнера через Docker Engine. Она не является самостоятельным доказательством отсутствия любых возможных остаточных процессов внутри VM или на хосте.

### 6.15. Проверка отсутствующего Docker-образа

Команда:

```powershell
docker run --rm --network none `
    local-agent-image-that-does-not-exist `
    python --version

$LASTEXITCODE
```

Фактический результат:

- образ не найден локально;
- Docker попытался выполнить pull;
- pull завершился ошибкой доступа к образу;
- команда вернула ненулевой код.

Параметр --network none ограничивает сетевое взаимодействие самого контейнера. Загрузка образа выполняется Docker Engine до запуска контейнера и этим параметром не ограничивается. Поэтому для будущего Backend необходимо отдельно запрещать неявную загрузку образов с помощью --pull=never.

Вывод: последующий Backend должен предотвращать неявную загрузку образа во время выполнения задачи. Для этого следует использовать `--pull=never` или эквивалентную проверку наличия образа до запуска.

Сборка и загрузка образов должны быть отдельной операцией подготовки окружения, а не скрытым действием при запуске пользовательской команды.

### 6.16. Проверка недоступного Docker Engine

Для проверки недоступности Engine был создан временный Docker context с заведомо недоступным endpoint:

```powershell
docker context create local-agent-unavailable `
    --docker "host=tcp://127.0.0.1:1"

docker --context local-agent-unavailable info

$LASTEXITCODE

docker context rm local-agent-unavailable
```

Фактический результат:

- context успешно создан;
- запрос `docker info` завершился ошибкой подключения;
- код завершения равен 1;
- временный context удалён.

Вывод: недоступность Docker Engine должна распознаваться как инфраструктурная ошибка, отличающаяся от ненулевого кода завершения пользовательского процесса.

- ошибку подключения к Docker Engine;
- ошибку создания или запуска контейнера;
- ненулевой код завершения команды внутри контейнера;
- превышение тайм-аута;
- ошибку удаления контейнера;
- невозможность подтвердить удаление контейнера.


## 7. Ограничения исследования

В ходе исследования не проверялись или проверялись не полностью следующие сценарии:

- чтение файлов за пределами Workspace через Windows Junction;
- все возможные варианты обхода границ bind mount через символические ссылки;
- доступ к Docker socket по нестандартным путям;
- поведение контейнера при аварийном завершении Docker Desktop или виртуальной машины;
- гарантированное отсутствие остаточных процессов на хосте после аварии Docker Engine;
- переполнение stdout/stderr при длительном выполнении команды;
- read-only root filesystem;
- сравнение Hyper-V backend с WSL 2;
- Windows Containers.

Результаты исследования относятся к конкретной конфигурации Docker Desktop, Windows и Linux VM. Они не доказывают абсолютную безопасность контейнера и не заменяют анализ уязвимостей Docker Engine, гипервизора и ядра Linux.

## 8. Выводы для будущего ExecutionBackend

На основании проведённых проверок для будущего самостоятельного ExecutionBackend рекомендуется:

1. Использовать Docker Engine с Linux-контейнерами через проверенный Docker Desktop backend.
2. Перед выполнением проверять доступность Docker Engine.
3. Проверять наличие подготовленного образа и запрещать неявный pull через `--pull=never`.
4. Передавать контейнеру только выделенный Workspace через bind mount.
5. Не монтировать корень проекта, домашний каталог пользователя или диски хоста без отдельного обоснования.
6. Запускать контейнер от непривилегированного пользователя.
7. Отключать сеть, capabilities и получение дополнительных привилегий.
8. Ограничивать память, CPU и количество процессов.
9. Использовать уникальное имя контейнера для каждого выполнения.
10. При тайм-ауте явно удалять контейнер и проверять результат удаления.
11. Разделять инфраструктурные ошибки Docker и результат пользовательской команды.
12. Сохранять stdout, stderr, exit code, сведения о тайм-ауте и результат очистки.
13. Если Docker Engine недоступен во время очистки, сообщать о невозможности подтвердить удаление контейнера, а не считать очистку успешной.

Эти рекомендации являются результатами исследования, а не описанием уже реализованного функционала.

## 9. Границы воспроизводимости

Экспериментальные проверки выполнены на рабочем компьютере
с Windows 11 Pro и Docker Desktop, использующим Hyper-V backend.

Повторное воспроизведение полного набора проверок на домашнем
компьютере не входит в критерии завершения исследования.

Полученные результаты характеризуют проверенную конфигурацию
и не являются гарантией идентичного поведения на других
конфигурациях Windows и Docker Desktop.

Совместимость с домашней конфигурацией будет проверена
при практической реализации ExecutionBackend.

## 10. Очистка тестового окружения

После завершения исследования удалить временные контейнеры и тестовый образ:

```powershell
# Найти временные контейнеры исследования
$timeoutContainers = @(
    docker ps -a --filter "name=local-agent-timeout-" --format "{{.Names}}"
)

$timeoutContainers

# Удалить найденные контейнеры
if ($timeoutContainers.Count -gt 0) {
    docker rm -f $timeoutContainers
}

# Удалить тестовый образ
docker image rm local-agent-python-test:3.12
```
Удаление

```powershell
$testRoot = Join-Path $env:SystemDrive "local-agent-isolation-test"

# Сначала проверить содержимое
Test-Path $testRoot
Get-ChildItem -Force $testRoot

# Удалять только после проверки содержимого
Remove-Item -LiteralPath $testRoot -Recurse -Force
```

Перед удалением проверить, что указанный каталог содержит только временные материалы исследования.

Не удалять другие образы, контейнеры, Docker contexts и данные пользователя.

## 11. Итоговый статус исследования

| Часть работы | Статус |
|---|---|
| Исследование Docker Desktop с Hyper-V на рабочем компьютере | Выполнено |
| Проверка запуска Python 3.12 и полного набора тестов | Выполнено |
| Проверка Workspace, ограничений доступа и ресурсов | Выполнено в перечисленных сценариях |
| Проверка тайм-аутов и удаления контейнера | Выполнено в перечисленных сценариях |
| Документирование ограничений и рекомендаций | Выполнено |
| Воспроизведение на домашнем компьютере | Не входит в критерии завершения этапа 6.А |
| Реализация самостоятельного ExecutionBackend | Не входит в этап 6.А |
| Интеграция Docker с ToolExecutor и Orchestrator | Не входит в этап 6.А |
