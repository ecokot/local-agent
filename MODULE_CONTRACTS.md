# MODULE_CONTRACTS.md

> Технический справочник по архитектуре и публичным контрактам local-agent.
>
> **Проект:** `local-agent`
> **Репозиторий:** https://github.com/ecokot/local-agent
> **Основная ветка:** `main`
> **Коммит проверенной версии кода:** `04aff91`
> **Актуальность:** 2026-11-23
> **Среда:** Windows 11, Python 3.12+, `uv`, PowerShell, PyCharm
>
> Документ описывает код именно указанного коммита. Если ветка main изменилась после него, сверяйте затронутые контракты с актуальным исходником.
>
## 1. Назначение и правила чтения

Документ — первая точка входа в архитектуру local-agent для нового чата разработки. Он фиксирует фактические модули, типы, публичные интерфейсы, связи, процессы и обнаруженные ограничения реализации.

Источники инвентаризации:

- plan.MD — план и критерии этапов;
- PROJECT_STRUCTURE.txt — снимок структуры проекта;
- README.md — описание проекта;
- pyproject.toml — настройки проекта и зависимости;
- все Python-файлы под src/local_agent;
- все Python-тесты под test.

Документ не заменяет исходный код как источник истины для деталей реализации. Он позволяет понять, какой именно файл и контракт относятся к задаче, чтобы запрашивать только их.

### Уровни достоверности

- **Реализовано** — поведение непосредственно обеспечено кодом текущего коммита.
- **Частично реализовано** — есть отдельный модуль или контракт, но отсутствует необходимая интеграция либо часть сценария.
- **Целевое требование** — правило плана/архитектуры, которое нельзя считать реализованным только на основании этого документа.

Не считать наличие интерфейса доказательством наличия полноценной реализации. Например, Protocol Planner/Executor/Reviewer и тестовые заглушки существуют, но это ещё не означает, что к ним подключены реальные модели и инструменты.

## 2. Архитектура в целом

Проект разделяет принятие интеллектуальных решений и выполнение действий:

- Python-код управляет состоянием задачи, SQLite, подтверждениями, вызовами компонентов, записью событий и восстановлением.
- Модель должна анализировать задачу, строить план и предлагать изменения. Модель не должна самостоятельно обходить Python и выполнять локальные инструменты.
- Контекст для модели должен формироваться приложением явно.
- Персистентное состояние задачи и подтверждений хранится в SQLite.
- Подтверждение относится к конкретной задаче, типу действия и хешу параметров.

В текущем коде реализована часть этой архитектуры: управление задачами, подтверждение плана, отдельный журнал выполнений и отдельный механизм восстановления. Полный автономный цикл пока не собран.

### 2.1 Фактические связи компонентов

Текущая основная цепочка Orchestrator:

1. Orchestrator создаёт задачу через StateManager.
2. Planner формирует Plan.
3. Orchestrator сохраняет сериализованный план как параметры запроса подтверждения через ApprovalManager.
4. ApprovalManager хранит запрос и переводит задачу в WAITING_FOR_APPROVAL.
5. После решения пользователя Orchestrator получает Approval.
6. При одобрении Orchestrator передаёт Plan в Executor, затем результат в Reviewer.
7. Orchestrator переводит задачу в SUCCEEDED или FAILED.
8. При отклонении плана Orchestrator завершает задачу как CANCELLED.

Отдельные компоненты, которые пока не встроены в эту цепочку:

- ModelClient и ModelRouter не вызываются Orchestrator.
- ContextManager не используется Orchestrator или ModelClient автоматически.
- ExecutionRecorder не вызывается Orchestrator.
- RecoveryManager существует отдельно и должен быть вызван приложением при восстановлении; автоматического запуска из Orchestrator/UserInterface нет.
- StubPlanner, StubExecutor и StubReviewer — тестовые заглушки, а не реальные LLM-агенты или исполнитель инструментов.

## 3. Инвентаризация модулей

### 3.1 Пакеты и файлы инициализации

| Файл | Назначение и контракт |
|---|---|
| src/local_agent/__init__.py | Маркер пакета. Самостоятельного прикладного API не добавляет. |
| src/local_agent/models/__init__.py | Пакет моделей. Сами контракты определены в отдельных файлах models/*.py. |
| src/local_agent/execution/__init__.py | Пакет подсистемы записи выполнений. |

### 3.2 StateManager — состояние задач

**Файл:** src/local_agent/state_manager.py
**Тесты:** test/test_state_manager.py

Назначение: единственный компонент текущей реализации, отвечающий за создание задач, чтение состояния и контролируемые переходы между статусами.

Публичные типы:

- TaskStatus — StrEnum.
- StateManager — SQLite-репозиторий задач.

Статусы TaskStatus:

| Значение | Смысл |
|---|---|
| CREATED | Задача создана, работа ещё не начата. |
| RUNNING | Задача выполняется либо находится в рабочей фазе между подтверждениями. |
| WAITING_FOR_APPROVAL | Задача ожидает решения по одному или нескольким запросам подтверждения. |
| SUCCEEDED | Задача завершена успешно. |
| FAILED | Задача завершена с ошибкой. |
| CANCELLED | Задача отменена. |

Разрешённые переходы:

| Из | В |
|---|---|
| CREATED | RUNNING, WAITING_FOR_APPROVAL, CANCELLED |
| RUNNING | WAITING_FOR_APPROVAL, SUCCEEDED, FAILED, CANCELLED |
| WAITING_FOR_APPROVAL | RUNNING, CANCELLED |
| SUCCEEDED | Нет переходов |
| FAILED | Нет переходов |
| CANCELLED | Нет переходов |

Публичные методы:

- __init__(database_path: str | Path | None = None, *, connection: sqlite3.Connection | None = None) -> None
- create_task(description: str) -> str — создаёт запись со статусом CREATED и возвращает task_id.
- get_state(task_id: str) -> TaskStatus — возвращает текущий статус.
- transition(task_id: str, new_status: TaskStatus) -> None — проверяет допустимость перехода и выполняет его в собственной транзакции.
- transition_in_transaction(task_id: str, new_status: TaskStatus) -> None — выполняет проверенный переход внутри транзакции вызывающего кода; сам не должен использоваться как самостоятельная граница commit.
- get_task(task_id: str) -> Task — возвращает запись задачи.
- get_tasks() -> list[Task] — возвращает список задач.
- close() -> None; поддерживается контекстный менеджер __enter__/__exit__.

Данные: таблица задач SQLite; запись содержит идентификатор, описание и статус. Task — неизменяемое представление записи.

Инварианты:

- Переходы проверяются по ALLOWED_TRANSITIONS.
- Терминальные статусы SUCCEEDED, FAILED и CANCELLED не имеют исходящих переходов.
- Переход к тому же статусу не является допустимым переходом, если явно не предусмотрен таблицей.
- transition_in_transaction нужен для атомарного изменения состояния вместе с другой операцией в той же SQLite-транзакции.
- При передаче connection вызывающий код отвечает за совместное использование соединения и его жизненный цикл.

Ошибки: ValueError при недопустимом переходе или некорректном описании; KeyError для неизвестного task_id; возможны ошибки sqlite3 при операциях с БД.

### 3.3 ApprovalManager — запросы и решения о подтверждении

**Файл:** src/local_agent/approval_manager.py
**Модель:** src/local_agent/models/approval.py
**Тесты:** test/test_approval_manager.py, test/test_approval_model.py, test/test_plan_approval.py

Назначение: сохранять запросы подтверждения и фиксировать решение пользователя. Компонент не исполняет подтверждённое действие.

Публичные методы:

- __init__(database_path: str | Path | None = None, *, connection: sqlite3.Connection | None = None, state_manager: StateManager | None = None) -> None
- request_approval(task_id: str, action_type: str, parameters: dict) -> Approval
- get_approval(approval_id: str) -> Approval
- approve(approval_id: str) -> Approval
- reject(approval_id: str) -> Approval
- cancel(approval_id: str) -> Approval
- is_approved(task_id: str, action_type: str, parameters: dict) -> bool
- get_pending_approvals(task_id: str) -> list[Approval]
- close() -> None; поддерживается контекстный менеджер.

Внутренние методы: _create_table, _serialize_parameters, _row_to_approval, _decide. Они не являются публичным API для прикладных компонентов.

Идентичность запроса подтверждения определяется сочетанием task_id, action_type и parameters_hash. Параметры сериализуются в каноническое JSON-представление с сортировкой ключей, после чего вычисляется хеш. Поэтому перестановка ключей словаря не должна создавать другое подтверждаемое действие, а изменение значения параметра должно.

Статусы ApprovalStatus:

- PENDING — ожидает решения.
- APPROVED — одобрено.
- REJECTED — отклонено.
- CANCELLED — отменено.

Фактические правила:

- Повторный запрос с теми же task_id, action_type и параметрами может вернуть уже существующую запись, если её состояние допускает повторное использование.
- Отмена для той же комбинации действия и параметров является окончательной: повторное создание идентичного запроса блокируется.
- Одобрение привязано к точному набору параметров; изменение параметров требует другого запроса.
- is_approved проверяет совпадение задачи, типа действия, параметров и статуса APPROVED.
- ApprovalManager может использовать StateManager для атомарного обновления статуса задачи при принятии решения.
- Если остаются другие PENDING-запросы для задачи, задача продолжает ждать подтверждения.

Важное ограничение текущей реализации: общий путь _decide после решения последнего ожидающего запроса переводит задачу в RUNNING независимо от того, был запрос одобрен, отклонён или отменён. Это не эквивалентно завершению задачи как CANCELLED. Orchestrator отдельно завершает задачу при отклонении плана, но прямой вызов ApprovalManager.cancel сам по себе не гарантирует терминальный статус задачи. Учитывать это при изменении политики подтверждений.

Ошибки: KeyError для неизвестного approval_id; ValueError для недопустимого решения, повторного решения или запрещённого повторного запроса; возможны ошибки sqlite3.

### 3.4 Orchestrator — координация планирования и выполнения

**Файл:** src/local_agent/orchestrator.py
**Тесты:** test/test_orchestrator.py, test/test_plan_approval.py

Назначение: связывать создание задачи, Planner, подтверждение плана, Executor и Reviewer.

Конструктор принимает:

- state_manager: StateManager
- approval_manager: ApprovalManager
- planner: Planner
- executor: Executor
- reviewer: Reviewer

Публичные методы:

- prepare_task(description: str) -> Approval — создаёт задачу, получает план от Planner и создаёт запрос подтверждения на выполнение плана.
- execute_approved_plan(approval_id: str) — загружает одобренный план, передаёт его Executor, передаёт результат Reviewer и обновляет состояние задачи.
- reject_plan(approval_id: str) -> Approval — отклоняет запрос подтверждения плана и завершает задачу как CANCELLED.

Фактический тип запроса подтверждения плана — EXECUTE_PLAN. Параметры запроса содержат сериализованный Plan. Для исполнения план восстанавливается из сохранённых параметров, а не из произвольного текста чата.

Распределение решений:

- Planner отвечает за формирование Plan.
- Пользователь через ApprovalManager решает, можно ли выполнять план.
- Executor возвращает ExecutionResult.
- Reviewer возвращает ReviewResult.
- Orchestrator принимает решение о конечном статусе задачи на основании результата исполнения и ревью.

Ограничения:

- Orchestrator не вызывает ModelClient, ModelRouter или ContextManager.
- Orchestrator не вызывает ExecutionRecorder и не создаёт записи Execution.
- Orchestrator не вызывает RecoveryManager.
- Хотя Planner Protocol содержит revise_plan, текущий Orchestrator не реализует цикл пересмотра плана по замечаниям Reviewer.
- Текущий цикл не является полноценным coder → reviewer → coder loop.
- Executor получает Plan, но не Task.
- Вызов execute_approved_plan не имеет заявленного возвращаемого типа; результат работы фиксируется через состояние задачи и ошибки.
- Не считать наличие Reviewer доказательством независимого анализа кода: это определяется конкретной реализацией переданного объекта.

Ошибки: ошибки доступа к задаче/подтверждению, ошибки проверки статуса и валидации Plan, а также ошибки Planner/Executor/Reviewer. Ошибки этапов исполнения обрабатываются Orchestrator с обновлением статуса задачи там, где это предусмотрено текущей реализацией.

### 3.5 RecoveryManager — проверка и восстановление после перезапуска

**Файл:** src/local_agent/recovery_manager.py
**Тесты:** test/test_recovery_manager.py

Публичный тип: RecoveryError(Exception) — ошибка противоречивых данных восстановления.

Конструктор:

- __init__(state_manager: StateManager, execution_recorder: ExecutionRecorder, approval_manager: ApprovalManager) -> None

Публичный метод:

- recover() -> None

Внутренний метод:

- _validate_execution(execution, is_latest: bool) -> None

Фактический алгоритм:

1. Получает задачи из StateManager.
2. Для WAITING_FOR_APPROVAL проверяет, что есть хотя бы один PENDING-запрос. Если запросов нет — поднимает RecoveryError.
3. Для задач, не находящихся в RUNNING или WAITING_FOR_APPROVAL, ничего не делает.
4. Для RUNNING проверяет отсутствие PENDING-подтверждений.
5. Получает связанные Execution из ExecutionRecorder.
6. Если Execution отсутствуют, оставляет задачу без изменений. Это допустимо, поскольку Orchestrator пока не интегрирован с ExecutionRecorder.
7. Проверяет историю событий каждого Execution: ровно одно начальное событие, допустимые типы событий, согласованность терминального события со статусом.
8. Последний успешный Execution позволяет перевести задачу в SUCCEEDED.
9. Последний FAILED или RUNNING Execution не переводит задачу в FAILED: неудачная попытка сама по себе не означает окончательный провал задачи.

Ограничения:

- recover не запускает повторное выполнение.
- recover не возобновляет незавершённый код или внешний инструмент.
- recover не восстанавливает задачи в CREATED, FAILED, SUCCEEDED или CANCELLED.
- Для RUNNING без Execution восстановление намеренно ничего не делает.
- RecoveryManager не вызывается автоматически при запуске UserInterface или Orchestrator.

Ошибки: RecoveryError при несогласованной истории, неизвестных событиях, отсутствии ожидающего подтверждения или наличии PENDING-подтверждений у RUNNING-задачи. Ошибки перехода StateManager оборачиваются в RecoveryError.

### 3.6 ExecutionRecorder — журнал выполнений и трассировка

**Файлы:** src/local_agent/execution/recorder.py, src/local_agent/execution/models.py
**Тесты:** test/test_execution_recorder.py

Назначение: отдельно от состояния задачи хранить отдельные попытки выполнения и последовательность событий. Метаданные и события сохраняются в SQLite; трассы записываются в каталог trace_directory.

Конструктор:

- __init__(database_path: str | Path, trace_directory: str | Path) -> None

Публичные методы:

- start_execution(task_id: str) -> str — создаёт Execution в RUNNING и начальное событие; возвращает execution_id.
- record_event(execution_id: str, event_type: str, data: dict[str, Any] | None = None) -> ExecutionEvent — записывает событие.
- finish_execution(execution_id: str, result: dict[str, Any] | None = None) -> None — завершает Execution как SUCCEEDED и записывает EXECUTION_FINISHED.
- fail_execution(execution_id: str, error: str) -> None — завершает Execution как FAILED и записывает EXECUTION_FAILED.
- get_events(execution_id: str) -> list[ExecutionEvent]
- get_incomplete_executions() -> list[str]
- get_status(execution_id: str) -> str
- get_executions_for_task(task_id: str) -> list[Execution]
- close() -> None; поддерживается контекстный менеджер.

Внутренние методы: _initialize_database, _read_existing_event_ids, _finish, _write_pending_traces, _recover_traces, _trace_path, _sanitize.

Инварианты и поведение:

- ExecutionRecorder ведёт историю попыток, а не заменяет TaskStatus.
- Начальное событие — EXECUTION_STARTED.
- Терминальные события — EXECUTION_FINISHED и EXECUTION_FAILED.
- Порядок и согласованность событий дополнительно проверяются RecoveryManager.
- Запись событий в SQLite и запись trace-файлов — разные операции; для трасс предусмотрены механизмы отложенной записи и восстановления.
- event_type принимается как строка; не следует считать, что каждый вызов record_event автоматически ограничен ExecutionEventType.
- ExecutionRecorder не вызывает исполнитель и не управляет разрешениями.

Ошибки: ошибки SQLite, файловой системы и сериализации; ошибки неизвестного execution_id и недопустимых операций над завершённым выполнением. Специальная прикладная ошибка восстановления истории определена в RecoveryManager как RecoveryError.

### 3.6.1 ToolExecutor и инструменты

**Файлы:**
- src/local_agent/execution/tool_executor.py
- src/local_agent/execution/tools/list_directory.py
- src/local_agent/execution/tools/read_file.py
- src/local_agent/execution/tools/create_file.py
- src/local_agent/execution/tools/write_file.py
- src/local_agent/execution/tools/get_diff.py
- src/local_agent/execution/tools/run_command.py
- src/local_agent/execution/tools/run_tests.py
- src/local_agent/execution/workspace.py

**Тесты:** test/test_tool_executor.py и отдельные тесты инструментов.

Назначение: единая точка запуска инструментов. ToolExecutor связывает фиксированный набор инструментов с ApprovalManager и ExecutionRecorder.

Конструктор принимает:
- approval_manager: ApprovalManager
- экземпляры семи инструментов
- execution_recorder: ExecutionRecorder

Публичный метод:

- execute(task_id: str, tool_name: str, parameters: dict[str, Any]) -> ToolExecutionResult

Поддерживаемые имена инструментов:

| Инструмент | Требует подтверждения |
|---|---|
| list_directory | Нет |
| read_file | Нет |
| create_file | Да |
| write_file | Да |
| get_diff | Нет |
| run_command | Да |
| run_tests | Да |

Поведение:

- Имя инструмента проверяется по внутреннему фиксированному набору.
- Параметры проверяются по обязательным и необязательным ключам соответствующего инструмента.
- Для рискованных действий ToolExecutor запрашивает подтверждение через ApprovalManager.
- При PENDING возвращает WAITING_FOR_APPROVAL без запуска инструмента.
- После APPROVED дополнительно проверяет разрешение через is_approved() с теми же task_id, action_type и parameters.
- Запись выполнения через ExecutionRecorder начинается только после необходимых проверок.
- При успешном выполнении вызывается finish_execution().
- При перехваченной ошибке выполнения вызывается fail_execution().
- Если запись результата в ExecutionRecorder завершается ошибкой, ToolExecutor возвращает FAILED и сообщает об ошибке журналирования.

Инварианты и ограничения:

- Вызывающий код передаёт имя инструмента и параметры, но не может передать собственную политику разрешений или объект произвольного инструмента.
- ToolExecutor не заменяет ApprovalManager и ExecutionRecorder.
- ToolExecutor пока не интегрирован с Orchestrator.
- Наличие подтверждения для run_command не означает, что команда выполняется в изолированной песочнице.
- RunCommandTool не является полноценным sandbox: shell=False само по себе не ограничивает доступ команды к файловой системе за пределами рабочего пространства.
- Список перехватываемых исключений ограничен конкретными типами; обработка неожиданных исключений отмечена как технический долг.

### 3.6.2 Исследование изоляции выполнения

**Документ исследования:** `docs/EXECUTION_ISOLATION_RESEARCH.md`

Проведено практическое исследование Docker Desktop с Linux-контейнерами и Hyper-V backend на Windows 11.

Цель исследования — определить возможность безопасного выполнения Python-команд и тестов проекта в отдельном контейнере с ограниченным доступом к файловой системе и ресурсам.

На рабочем компьютере проверены:

- запуск Linux-контейнеров и Python 3.12;
- выполнение полного набора тестов проекта;
- доступ контейнера только к явно смонтированному Workspace в проверенных сценариях;
- чтение и запись файлов Workspace с сохранением изменений;
- запуск от непривилегированного пользователя;
- отключение Linux capabilities и включение `no-new-privileges`;
- отключение сети;
- отсутствие Docker socket по стандартным путям;
- ограничения CPU, памяти и количества процессов;
- проверенные сценарии выхода через символические ссылки и Windows Junction;
- завершение контейнера при тайм-ауте;
- обработка отсутствующего образа и недоступного Docker Engine.

**Статус:** исследование на рабочем компьютере проведено и задокументировано. Воспроизведение на домашнем компьютере не входит в критерии завершения этапа 6.А. Совместимость с домашней конфигурацией будет проверена при практической реализации ExecutionBackend.

**Архитектурная граница:**

- Docker выбран как кандидат для будущего ExecutionBackend.
- Самостоятельный ExecutionBackend пока не реализован.
- ToolExecutor, RunCommandTool и RunTestsTool пока не интегрированы с Docker.
- Результаты исследования не означают, что текущие инструменты обеспечивают контейнерную изоляцию.
- Исследование не доказывает абсолютную защиту хоста от всех способов выхода из контейнера.

Подробные команды, фактические результаты, ограничения и порядок воспроизведения приведены в `docs/EXECUTION_ISOLATION_RESEARCH.md`.

### 3.7 ModelClient — клиент модели

**Файл:** src/local_agent/model_client.py
**Тесты:** test/test_model_client.py

Типы ошибок:

- ModelClientError — базовая ошибка клиента.
- ModelConnectionError — ошибка соединения.
- ModelRequestError — ошибка отправки запроса/ответа API.
- ModelResponseError — некорректный или непригодный ответ модели.

Конструктор:

- __init__(base_url: str = "http://localhost:1235/v1", api_key: str = "lm-studio", timeout: float = 120.0) -> None

Методы:

- complete(model: str, messages: Sequence[dict[str, str]], temperature: float = 0.2) -> str
- close() -> None

Назначение: отправить сообщения в OpenAI-compatible Chat Completions API и вернуть текст ответа. Клиент не решает, какую роль или модель выбрать, не формирует контекст задачи и не выполняет локальные инструменты.

Ограничения:

- Значения endpoint, ключа и timeout по умолчанию соответствуют локальному OpenAI-compatible серверу.
- Наличие ModelClient не означает, что Orchestrator уже использует его.
- Ошибки транспортного уровня, API и содержимого ответа разделены типами исключений.

### 3.8 ModelRouter — выбор модели по роли

**Файл:** src/local_agent/model_router.py
**Тесты:** test/test_model_router.py

Конструктор:

- __init__(models: Mapping[str, str]) -> None

Метод:

- get_model(role: str) -> str

Назначение: вернуть имя модели по строковому ключу роли из переданного mapping.

Ограничения:

- Роли не закреплены отдельным Enum; набор ролей определяется конфигурацией вызывающего кода.
- Не выбирает модель автоматически по сложности задачи и не вызывает ModelClient.
- Не является агентным оркестратором.

Ошибки: KeyError для неизвестной роли.

### 3.9 ContextManager — явный контекст сообщений

**Файл:** src/local_agent/context_manager.py
**Тесты:** test/test_context_manager.py

Тип MessageRole — Literal с допустимыми значениями system, user, assistant.

Конструктор:

- __init__() -> None

Методы:

- add_message(role: MessageRole, content: str) -> None
- get_messages() -> list[dict[str, str]]
- clear() -> None

Назначение: хранить упорядоченный список сообщений для передачи модели. Контекст создаётся и изменяется приложением явно.

Ограничения:

- Не обращается к модели.
- Не загружает автоматически файлы проекта или историю задачи.
- Не связан с SQLite и не является долговременным хранилищем.
- Orchestrator не использует его автоматически.

### 3.10 Агентные Protocol и заглушки

**Файлы:** src/local_agent/agents/protocols.py, src/local_agent/agents/stubs.py
**Тесты:** test/test_agent_protocols.py, test/test_agent_stubs.py, test/test_agent_models.py

Контракты в protocols.py:

- Planner.create_plan(task: Task) -> Plan
- Planner.revise_plan(task: Task, previous_plan: Plan, feedback: str) -> Plan
- Executor.execute(plan: Plan) -> ExecutionResult
- Reviewer.review(plan: Plan, result: ExecutionResult) -> ReviewResult

Это typing.Protocol-контракты. Они описывают требуемый интерфейс объектов, передаваемых в Orchestrator, но не реализуют вызов модели.

Заглушки в stubs.py:

- StubPlanner — тестовая реализация Planner.
- StubExecutor — тестовая реализация Executor.
- StubReviewer — тестовая реализация Reviewer.
- get_stub_agents() -> tuple[Planner, Executor, Reviewer] — фабрика тестового набора.

Заглушки нужны для тестирования orchestration без сетевого вызова модели и без реального исполнения инструментов.

В проекте нет отдельного публичного Coder Protocol. Роль, отвечающая за исполнение плана, в текущем интерфейсе называется Executor. Не называть Executor полноценным Coder без отдельного изменения контракта и реализации.

### 3.11 UserInterface — CLI взаимодействие

**Файл:** src/local_agent/user_interface.py

**Тесты:** прямой отдельный тестовый файл для UserInterface в текущем наборе не обнаружен; поведение CLI следует проверять через тесты Orchestrator и при необходимости добавлять целевые тесты UI.

Конструктор:

- __init__(orchestrator: Orchestrator, approval_manager: ApprovalManager) -> None

Методы:

- run() -> None
- _run_task(description: str) -> None — внутренний обработчик задачи.

Назначение: пользовательский интерфейс поверх Orchestrator и ApprovalManager. UI не должен самостоятельно менять SQLite или исполнять план в обход этих компонентов.

Ограничения: это интерфейс взаимодействия, а не слой политик безопасности. Не считать, что сам факт наличия CLI обеспечивает подтверждение каждого отдельного инструмента.

## 4. Модели данных, типы и протоколы

### 4.1 Pydantic-модели

Pydantic-модели используют строгую конфигурацию: extra="forbid" и strict=True. Неизвестные поля запрещены, автоматическое приведение типов ограничено.

**PlanStep** — src/local_agent/models/plan.py

- description: str — обязательная строка, min_length=1.
- action: str — обязательная строка, min_length=1.
- parameters: dict[str, Any] — обязательный словарь параметров.

**Plan** — src/local_agent/models/plan.py

- summary: str — обязательная строка, min_length=1.
- steps: list[PlanStep] — обязательный непустой список.

**ExecutionResult** — src/local_agent/models/agent.py

- success: bool — обязательное поле.
- output: str = "".
- errors: list[str] — по умолчанию пустой список через default_factory.

**ReviewResult** — src/local_agent/models/agent.py

- approved: bool — обязательное поле.
- feedback: str = "".
- Модель содержит валидатор feedback_required_for_rejection: отклонение должно сопровождаться непустой обратной связью.

Для этих моделей не следует добавлять поля в сериализуемые структуры без изменения контракта и обновления тестов.

### 4.2 Dataclass-модели

**Task** — src/local_agent/models/task.py

Неизменяемый dataclass со slots:

- task_id: str
- description: str
- status: TaskStatus

TaskStatus импортируется для типизации из state_manager. Источник истины о статусе — SQLite/StateManager, а не экземпляр Task, сохранённый в памяти.

**Approval** — src/local_agent/models/approval.py

Неизменяемый dataclass со slots:

- approval_id: str
- task_id: str
- action_type: str
- parameters: dict[str, Any]
- parameters_hash: str
- status: ApprovalStatus
- created_at: datetime
- decided_at: datetime | None

При создании параметры копируются через deepcopy. frozen=True запрещает переназначение полей, но вложенный словарь сам по себе не становится глубоко неизменяемым.

**Execution** — src/local_agent/models/execution.py

Неизменяемый dataclass со slots:

- execution_id: str
- task_id: str
- status: ExecutionStatus
- started_at: str
- finished_at: str | None

**ExecutionEvent** — src/local_agent/execution/models.py

Неизменяемый dataclass со slots:

- execution_id: str
- event_type: str
- data: dict[str, Any]
- event_id: str
- timestamp: str

Методы:

- create(...) — создаёт событие с новым UUID и UTC timestamp.
- to_dict() -> dict[str, Any]
- from_dict(value: dict[str, Any]) -> ExecutionEvent

Данные события копируются при создании. event_type хранится как строка, хотя для стандартных событий есть ExecutionEventType.

**ToolExecutionResult** — src/local_agent/execution/models.py

Неизменяемый dataclass со slots:

- status: ToolExecutionStatus
- execution_id: str | None
- approval_id: str | None
- result: Any | None
- error: str | None

Используется ToolExecutor для возврата результата запуска инструмента, ожидания подтверждения или ошибки.

### 4.3 Enum

| Enum | Значения |
|---|---|
| TaskStatus | CREATED, RUNNING, WAITING_FOR_APPROVAL, SUCCEEDED, FAILED, CANCELLED |
| ApprovalStatus | PENDING, APPROVED, REJECTED, CANCELLED |
| ExecutionStatus | RUNNING, SUCCEEDED, FAILED |
| ExecutionEventType | EXECUTION_STARTED, EXECUTION_FINISHED, EXECUTION_FAILED |
| ToolExecutionStatus | WAITING_FOR_APPROVAL, SUCCEEDED, FAILED |

Не добавлять статусы по аналогии или по предполагаемой архитектуре. Например, PLANNING и WAITING_FOR_REVIEW в текущем TaskStatus отсутствуют.

### 4.4 Интерфейсы и отсутствующие контракты

- Planner, Executor и Reviewer определены как Protocol.
- Реальные LLM-адаптеры для этих Protocol в текущей структуре не обнаружены.
- Отдельного Coder Protocol нет.
- ToolExecutor реализован как конкретный исполнитель фиксированного набора семи инструментов.
- Отдельного универсального Tool Protocol для инструментов нет: ToolExecutor использует переданные экземпляры конкретных классов инструментов и их методы execute().
- ModelClient и ModelRouter — самостоятельные инфраструктурные классы, а не реализации Planner/Executor/Reviewer.
- MessageRole — Literal, а не Enum.

## 5. Основные процессы

### 5.1 Создание задачи и формирование плана

Реализованная часть:

1. Orchestrator вызывает StateManager.create_task(description).
2. Задача получает статус CREATED.
3. Orchestrator переводит её в RUNNING для этапа подготовки плана.
4. Planner.create_plan(task) возвращает Plan.
5. Orchestrator сериализует Plan в параметры запроса EXECUTE_PLAN.
6. ApprovalManager сохраняет запрос и переводит задачу в WAITING_FOR_APPROVAL.

План хранится как параметры Approval в SQLite. Отдельной таблицы планов в текущей реализации нет.

Если Planner завершился ошибкой, Orchestrator обрабатывает ошибку и не должен оставлять задачу как успешно подготовленную.

### 5.2 Согласование плана

Реализовано:

- пользовательское решение фиксируется через ApprovalManager;
- одобрение связано с task_id, action_type и хешем параметров;
- изменение параметров приводит к другому идентификатору действия;
- отклонение плана через Orchestrator.reject_plan завершает задачу как CANCELLED.

Не путать согласование плана с подтверждением каждого отдельного инструмента. Универсальный механизм разрешений на каждое файловое/системное действие ещё не представлен отдельным интегрированным исполнительным слоем.

### 5.3 Выполнение одобренного плана

Реализован базовый путь:

1. Orchestrator загружает Approval.
2. Проверяет, что запрос одобрен и относится к исполнению плана.
3. Восстанавливает Plan из сохранённых параметров.
4. Передаёт Plan в Executor.execute.
5. Передаёт ExecutionResult в Reviewer.review.
6. При успешном результате и одобрении Reviewer задача становится SUCCEEDED.
7. При неуспехе или отклонении Reviewer задача становится FAILED.

Не реализовано в текущем Orchestrator:

- цикл исправления замечаний;
- повторная генерация плана через Planner.revise_plan;
- отдельный Coder, который меняет файлы;
- реальное исполнение локальных инструментов;
- запись каждой попытки в ExecutionRecorder.

### 5.4 Состояния задачи

StateManager хранит TaskStatus в SQLite. Все переходы проходят через таблицу разрешённых переходов. ApprovalManager может обновлять состояние задачи в той же транзакции, что и решение по подтверждению, если использует совместное соединение/транзакционный путь.

Терминальные состояния: SUCCEEDED, FAILED, CANCELLED. Они не должны возвращаться в RUNNING.

### 5.5 История выполнений

ExecutionRecorder хранит отдельные Execution и ExecutionEvent. Это журнал попыток и событий, а не замена статуса задачи.

Важное различие:

- TaskStatus — итоговое состояние задачи.
- ExecutionStatus — состояние конкретной попытки выполнения.
- ExecutionEvent — отдельное событие внутри попытки.

### 5.6 Восстановление после перезапуска

RecoveryManager проверяет состояние задач, pending approvals и историю Execution. Он может завершить задачу как SUCCEEDED, если последняя попытка имеет согласованную историю и завершилась успешно.

Он не возобновляет фактическую работу инструмента и не запускает повторную попытку. Для RUNNING-задачи без Execution он ничего не меняет, поскольку Orchestrator пока не подключён к ExecutionRecorder.

### 5.7 Повторные попытки и ошибки

ExecutionRecorder поддерживает несколько Execution для одной задачи. RecoveryManager допускает FAILED-попытки перед более поздними попытками и проверяет, что неуспешная попытка не является автоматически доказательством провала всей задачи.

Автоматического retry-цикла в Orchestrator нет. Повторное исполнение, лимиты попыток, backoff и решение о повторе остаются незавершённой частью архитектуры.

## 6. Архитектурные правила: статус реализации

| Правило                                                     | Статус в текущем коде                                                                                                                                                                                                                               |
|-------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Python управляет состоянием задачи                          | Реализовано через StateManager.                                                                                                                                                                                                                     |
| SQLite — источник истины для задач                          | Реализовано через StateManager.                                                                                                                                                                                                                     |
| SQLite — источник истины для подтверждений                  | Реализовано через ApprovalManager.                                                                                                                                                                                                                  |
| Модель не должна выполнять инструменты в обход Python       | ToolExecutor предоставляет Python-точку запуска фиксированного набора инструментов. Интеграция с Orchestrator пока отсутствует.                                                                                                                     |
| Модель анализирует и предлагает план                        | Контракт Planner существует; текущая интеграция использует переданный объект Planner, реальные модели не подключены.                                                                                                                                |
| Контекст модели формируется приложением явно                | ContextManager реализует список сообщений, но пока не интегрирован с Orchestrator/ModelClient.                                                                                                                                                      |
| Подтверждение привязано к действию и параметрам             | Реализовано в ApprovalManager через action_type и parameters_hash.                                                                                                                                                                                  |
| Отмена идентичного действия окончательна                    | Реализована блокировка повторного запроса с теми же параметрами после CANCELLED.                                                                                                                                                                    |
| Рискованные действия требуют подтверждения Python           | Частично реализовано: ToolExecutor запрашивает подтверждение через ApprovalManager для create_file, write_file, run_command и run_tests. list_directory, read_file и get_diff выполняются без подтверждения. Интеграция с Orchestrator отсутствует. |
| История выполнений используется при восстановлении          | Реализованы ExecutionRecorder и RecoveryManager, но их автоматическая интеграция с Orchestrator отсутствует.                                                                                                                                        |
| Контекст и данные задачи не зависят от неявной истории чата | Целевое правило; ContextManager существует, но его сборка в единый контекст задачи ещё не интегрирована.                                                                                                                                            |
| Не добавлять менеджеры и абстракции без необходимости       | Действующее архитектурное правило проекта, не машинно проверяемый инвариант.                                                                                                                                                                        |
| Изоляция выполнения системных команд                        | Исследована возможность Docker/Hyper-V. Реализация ExecutionBackend и интеграция с инструментами отсутствуют. Домашнее воспроизведение исследования ожидает выполнения.                                                                             |

### 6.1 Известные разрывы интеграции

1. Orchestrator не использует ModelClient/ModelRouter/ContextManager.
2. Orchestrator не записывает Execution через ExecutionRecorder.
3. RecoveryManager не вызывается автоматически при старте.
4. ToolExecutor и семь инструментов реализованы как самостоятельный исполнительный слой, но пока не подключены к Orchestrator. RunCommandTool не обеспечивает полноценную изоляцию команд от файловой системы. Возможность контейнерной изоляции исследована отдельно (см. раздел 3.6.2 и `docs/EXECUTION_ISOLATION_RESEARCH.md`), однако ExecutionBackend пока не реализован.
5. Planner.revise_plan определён, но не участвует в цикле выполнения.
6. ApprovalManager при решении последнего pending-запроса переводит задачу в RUNNING независимо от исхода решения. Терминальный переход при отмене должен быть обеспечен вызывающим процессом; прямой cancel требует отдельного внимания.
7. Текущий набор статусов TaskStatus не содержит отдельных состояний для планирования, ревью или ожидания исправлений.

## 7. Карта тестов

| Тестовый файл                   | Что покрывает                                                                                               |
|---------------------------------|-------------------------------------------------------------------------------------------------------------|
| test/test_agent_models.py       | Валидация ExecutionResult и ReviewResult.                                                                   |
| test/test_agent_protocols.py    | Совместимость объектов с Protocol-контрактами агентов.                                                      |
| test/test_agent_stubs.py        | Поведение тестовых Planner/Executor/Reviewer.                                                               |
| test/test_approval_manager.py   | Создание запросов, решения, дедупликация, хеширование параметров и интеграция со статусом задачи.           |
| test/test_approval_model.py     | Модель Approval и копирование параметров.                                                                   |
| test/test_context_manager.py    | Добавление, получение и очистка сообщений.                                                                  |
| test/test_execution_recorder.py | Запись выполнений, событий и trace-файлов, восстановление/целостность журнала.                              |
| test/test_model_client.py       | Вызов локального OpenAI-compatible API и обработка ошибок клиента.                                          |
| test/test_model_router.py       | Выбор модели по роли и неизвестные роли.                                                                    |
| test/test_orchestrator.py       | Подготовка задачи, планирование, выполнение, ревью и обработка ошибок.                                      |
| test/test_plan_approval.py      | Согласование плана и запрет выполнения без одобрения.                                                       |
| test/test_plan_models.py        | Валидация Plan и PlanStep.                                                                                  |
| test/test_recovery_manager.py   | Проверка истории выполнений и восстановление состояния задач.                                               |
| test/test_state_manager.py      | Создание задач, чтение и допустимые/недопустимые переходы.                                                  |
| test/test_tool_executor.py      | Проверка диспетчеризации инструментов, валидации параметров, подтверждений и записи результатов выполнения. |

Тесты не заменяют проверку фактической интеграции. Наличие отдельных тестов ModelClient, ContextManager и ExecutionRecorder не доказывает, что эти компоненты вызываются из Orchestrator.

## 8. Что пока не реализовано как единая система

По состоянию на коммит инвентаризации нельзя считать завершёнными следующие функции:

- Полноценный автономный цикл с реальными Planner/Coder/Reviewer, работающими через ModelClient.
- Автоматическое построение контекста задачи из файлов и состояния проекта.
- Интеграция ToolExecutor с Orchestrator и включение инструментов в общий цикл выполнения задачи.
- Реализация полноценной изоляции системных команд и проверка границ безопасности рабочего пространства. Исследование Docker/Hyper-V проведено на рабочем компьютере, но ExecutionBackend ещё не реализован, а воспроизведение исследования на домашнем компьютере ожидает выполнения.
- Расширение и уточнение политик разрешений за пределами текущего фиксированного набора инструментов.
- Цикл coder → reviewer → coder с ограничением попыток и формальным условием выхода.
- Интеграция ExecutionRecorder в каждую попытку Orchestrator.
- Автоматический вызов RecoveryManager при запуске приложения.
- Продолжение прерванного выполнения после перезапуска.
- Автоматическая политика retry/backoff.
- Отдельная роль Coder как конкретный Protocol/реализация.
- Полное управление жизненным циклом локального рабочего пространства проекта.

Эти пункты являются описанием обнаруженных пробелов, а не предложением менять текущий план или начинать следующий этап без отдельного решения.

## 9. Инструкция для чатов разработки

Перед началом любой задачи:

1. Изучить plan.MD, PROJECT_STRUCTURE.txt и MODULE_CONTRACTS.md.
2. Не запрашивать полный исходный код проекта.
3. Использовать документацию для понимания архитектуры и публичных контрактов.
4. Если задача затрагивает конкретный модуль, запросить только его актуальный исходник и необходимые связанные тесты.
5. Если возникает ошибка, запросить конкретный traceback и относящиеся к нему файлы.
6. Не запрашивать исходники несвязанных модулей.
7. Если документации недостаточно, сначала определить конкретный пробел, а затем запросить только необходимые данные.
8. Если документация расходится с кодом, проверить код и исправить документацию.
9. При изменении публичных контрактов, ответственности или связей модулей обновлять MODULE_CONTRACTS.md.
10. При внутренних изменениях без влияния на контракты не переписывать документацию без необходимости.

Дополнительные правила:

- Не описывать целевое поведение как уже реализованное.
- Не считать заглушки реальными агентами.
- Не считать Protocol реализацией.
- Не считать независимые модули интегрированными только потому, что они существуют в одном пакете.
- Не менять plan.MD в рамках обычного обновления MODULE_CONTRACTS.md без отдельного согласования.
- Не начинать следующий этап разработки только на основании завершения документирования.
- После изменения контрактов сверять документ с исходником и связанными тестами.
- В отчёте по изменению документа указывать коммит, по которому проверялись контракты.

## 10. Обновление документа

Обновлять этот файл обязательно, если изменились:

- публичные классы, функции, методы, Protocol или их сигнатуры;
- поля, типы, ограничения или Enum моделей;
- переходы TaskStatus;
- правила подтверждения и идентификации действий;
- схема взаимодействия Orchestrator с менеджерами и агентами;
- формат истории выполнений или правила восстановления;
- ответственность модуля или его место в архитектуре.

Не требуется переписывать документ при изменении локальной реализации метода, если публичный контракт, ответственность и связи не изменились.

При обновлении даты и коммита указывать фактический commit SHA, относительно которого проверены исходники.

### Технический долг: обработка неожиданных исключений в ToolExecutor

**Когда:** на финальном этапе разработки проекта.

**Что проверить:** обработку исключений в `ToolExecutor.execute()`.

**Текущая ситуация:** для соблюдения Ruff BLE001 используется ограниченный список типов исключений. Неожиданное исключение другого типа может выйти за пределы `execute()`, оставив запись выполнения в состоянии `RUNNING`.

**Что сделать перед завершением разработки:**
- Пересмотреть стратегию обработки неожиданных исключений.
- Обеспечить корректное завершение или фиксацию неудачного выполнения в `ExecutionRecorder`.
- Сохранить соблюдение Ruff без отключения BLE001.
- Добавить тест на неожиданное исключение.
