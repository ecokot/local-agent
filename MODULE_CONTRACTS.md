# MODULE_CONTRACTS.md

> Технический справочник по архитектуре и публичным контрактам local-agent.
>
> **Проект:** `local-agent`
> **Репозиторий:** https://github.com/ecokot/local-agent
> **Основная ветка:** `main`
> **Коммит проверенной версии кода:** `efb45cd`
> **Актуальность:** 2026-11-25
> **Среда:** Windows 11, Python 3.12+, `uv`, PowerShell, PyCharm
>
> Публичные контракты и связи модулей сверены с исходниками указанного коммита. Карта тестов описывает покрытие, присутствующее в этом коммите.
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

Поведение AI-ролей при планировании и реализации регулируется отдельными универсальными навыками [Project Planner](skills/project-planner/SKILL.md) и [Project Developer](skills/project-developer/SKILL.md). Эти навыки описывают рабочий процесс, не являются runtime-модулями и не заменяют архитектурные контракты, план или постановку конкретной задачи.

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

В текущем коде реализованы управление задачами, подтверждение плана и исполнительная цепочка через ToolExecutorAdapter и ToolExecutor. ExecutionRecorder записывает отдельные вызовы инструментов; RecoveryManager проверяет сохранённую историю. Полный автономный цикл с реальными Planner/Reviewer на базе моделей пока не собран.

### 2.1 Фактические связи компонентов

Основная цепочка выполнения одобренного плана:

`Orchestrator → ToolExecutorAdapter → ToolExecutor → инструменты`

Фактическая последовательность:

1. Orchestrator создаёт задачу через StateManager.
2. Planner формирует Plan.
3. Orchestrator сохраняет сериализованный Plan как параметры запроса PLAN_APPROVAL через ApprovalManager.
4. После одобрения плана Orchestrator вызывает переданный ему Executor. Для исполнения плана через инструменты этой ролью выступает ToolExecutorAdapter.
5. ToolExecutorAdapter последовательно передаёт каждый шаг в ToolExecutor: имя действия и параметры.
6. ToolExecutor проверяет инструмент и параметры, при необходимости создаёт запрос подтверждения отдельного действия через ApprovalManager, а после одобрения запускает инструмент.
7. ToolExecutor использует ExecutionRecorder для записи каждого фактического вызова инструмента.
8. ToolExecutorAdapter собирает результаты шагов и возвращает ExecutionResult. Если требуется подтверждение, он возвращает состояние ожидания и сохраняет текущий прогресс в памяти процесса.
9. После одобрения отдельного действия Orchestrator повторно вызывает Executor с тем же task_id и Plan; Adapter продолжает с ожидающего шага, не повторяя уже завершённые.
10. Только после полного успешного выполнения Orchestrator передаёт Reviewer исходный Plan и фактический ExecutionResult со step_results.
11. Orchestrator завершает задачу как SUCCEEDED или FAILED на основании результата Reviewer и исполнения.

При отклонении плана Orchestrator завершает задачу как CANCELLED. Отклонение/отмена отдельного действия обрабатывается исполнительным контуром и ApprovalManager.

Другие компоненты и ограничения интеграции:

- ModelClient и ModelRouter не вызываются Orchestrator.
- ContextManager не используется Orchestrator или ModelClient автоматически.
- ExecutionRecorder встроен в ToolExecutor, а не вызывается Orchestrator напрямую.
- RecoveryManager существует отдельно и должен быть вызван приложением при восстановлении; автоматического запуска из Orchestrator/UserInterface нет.
- StubPlanner, StubExecutor и StubReviewer остаются тестовыми заглушками. StubExecutor — не единственный вариант Executor: ToolExecutorAdapter является реализацией контракта Executor для выполнения плана через инструменты.
- Прогресс ToolExecutorAdapter хранится только в памяти процесса. Восстановление позиции и продолжение плана после перезапуска не реализованы. При этом approval отдельного Git-действия и stage receipt сохраняются в SQLite; повторный вызов ToolExecutor с тем же task_id/action/parameters может возобновить действие после перезапуска. При этом stage receipt LA-9G-01 хранится в SQLite execution events и доступна новому ToolExecutor после перезапуска.

## 3. Инвентаризация модулей

### 3.1 Пакеты и файлы инициализации

| Файл | Назначение и контракт |
|---|---|
| src/local_agent/__init__.py | Маркер пакета. Самостоятельного прикладного API не добавляет. |
| src/local_agent/models/__init__.py | Пакет моделей. Сами контракты определены в отдельных файлах models/*.py. |
| src/local_agent/execution/__init__.py | Публичный API подсистемы выполнения: ExecutionBackend, ExecutionResult, ExecutionBackendError, ExecutionRecorder и модели событий/результатов ToolExecutor. |
| src/local_agent/execution/tools/git.py | GitTool с read-only диагностикой и тремя фиксированными изменяющими операциями, защищёнными snapshot approval, per-repository lock и проверками pre/postconditions. |
| src/local_agent/execution/tools/_windows_runner.py | Внутренний Windows runner на Win32 API: запуск процесса в Job Object, ограниченный сбор stdout/stderr, timeout, cleanup и whitelist наследуемых handles. |

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
| CREATED | RUNNING, WAITING_FOR_APPROVAL, FAILED, CANCELLED |
| RUNNING | WAITING_FOR_APPROVAL, SUCCEEDED, FAILED, CANCELLED |
| WAITING_FOR_APPROVAL | RUNNING, FAILED, CANCELLED |
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

Обновление статуса задачи при решении последнего ожидающего запроса:

- Если остаются другие PENDING-запросы задачи, статус задачи остаётся WAITING_FOR_APPROVAL.
- После APPROVED последнего запроса задача переводится в RUNNING.
- После CANCELLED последнего запроса задача переводится в CANCELLED.
- После REJECTED запроса отдельного действия (action_type не равен PLAN_APPROVAL) задача переводится в FAILED.
- REJECTED для PLAN_APPROVAL оставляет задачу в RUNNING на выходе из транзакции ApprovalManager; Orchestrator затем переводит её в CANCELLED в reject_plan().

Ошибки: KeyError для неизвестного approval_id; ValueError для недопустимого решения, повторного решения или запрещённого повторного запроса; возможны ошибки sqlite3.

### 3.4 Orchestrator — координация планирования и выполнения

**Файл:** src/local_agent/orchestrator.py
**Тесты:** test/test_orchestrator.py, test/test_plan_approval.py

Назначение: связывать создание задачи, Planner, подтверждение плана, Executor и Reviewer. Orchestrator не исследует рабочие файлы, не формирует сообщения и не управляет контекстом модели.

Подготовка контекста находится на стороне приложения: приложение инициирует исследование рабочего проекта, формирует сообщения и наполняет ContextManager. Конкретная реализация Planner получает подготовленный контекст через собственный настроенный механизм. ContextManager предоставляет упорядоченные сообщения через get_messages(); Orchestrator не зависит от него напрямую. Публичный контракт Planner.create_plan(task) сохраняется.

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
Фактический тип запроса подтверждения плана — PLAN_APPROVAL. Параметры запроса содержат сериализованный Plan. Для исполнения план восстанавливается из сохранённых параметров, а не из произвольного текста чата.

Распределение решений:

- Planner отвечает за формирование Plan.
- Пользователь через ApprovalManager решает, можно ли выполнять план.
- Executor возвращает ExecutionResult.
- Reviewer возвращает ReviewResult.
- Orchestrator принимает решение о конечном статусе задачи на основании результата исполнения и ревью. SUCCEEDED устанавливается только при result.success == True и review.approved == True; неуспешный ExecutionResult всегда приводит к FAILED.

Ограничения:

- Orchestrator не вызывает ModelClient, ModelRouter или ContextManager.
- Orchestrator не вызывает ExecutionRecorder напрямую: запись каждого запуска инструмента выполняет ToolExecutor.
- Orchestrator не вызывает RecoveryManager.
- Хотя Planner Protocol содержит revise_plan, текущий Orchestrator не реализует цикл пересмотра плана по замечаниям Reviewer.
- Текущий цикл не является полноценным coder → reviewer → coder loop.
- Executor получает task_id и Plan.
- ToolExecutorAdapter хранит прогресс шагов только в памяти процесса; восстановление этого прогресса после перезапуска не реализовано.
- execute_approved_plan(approval_id: str) -> ReviewResult | ExecutionResult возвращает результат ожидания подтверждения/исполнения либо итог Reviewer.
- Не считать наличие Reviewer доказательством независимого анализа кода: это определяется конкретной реализацией переданного объекта.

Жизненный цикл и проверки: после создания задачи исключения Planner или создания запроса подтверждения переводят CREATED в FAILED. При выполнении допускается только подтверждение типа PLAN_APPROVAL со статусом APPROVED и задача в актуальном состоянии RUNNING. Проверка состояния выполняется до передачи плана Executor, поэтому терминальную задачу нельзя выполнить повторно. Если ToolExecutor обнаруживает действие, требующее подтверждения, ApprovalManager сохраняет запрос отдельного действия и задача переходит в WAITING_FOR_APPROVAL. При одобрении запроса задача возвращается в RUNNING; Orchestrator повторно вызывает Executor, а Adapter продолжает с ожидающего шага. При ожидании Reviewer не вызывается. Отклонение/отмена отдельного действия обрабатывается ApprovalManager и исполнительным контуром. Исключения Executor и Reviewer, неуспешный ExecutionResult и отклонение Reviewer завершают задачу как FAILED. Отклонение ожидающего подтверждения плана фиксируется ApprovalManager, после чего Orchestrator переводит задачу в CANCELLED. Несколько задач могут одновременно находиться в WAITING_FOR_APPROVAL; глобального ограничения на одну активную задачу нет.

Ошибки: ошибки доступа к задаче/подтверждению, ошибки проверки статуса и валидации Plan, а также ошибки Planner/Executor/Reviewer. Исключения этапов подготовки и выполнения повторно выбрасываются вызывающему коду после обновления состояния задачи.

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
- В текущей исполнительной цепочке ToolExecutor вызывает start_execution перед запуском инструмента, а затем finish_execution или fail_execution по результату. Поэтому журналирование встроено в каждый вызов инструмента, а не в Orchestrator целиком.

Ошибки: ошибки SQLite, файловой системы и сериализации; ошибки неизвестного execution_id и недопустимых операций над завершённым выполнением. Специальная прикладная ошибка восстановления истории определена в RecoveryManager как RecoveryError.

### 3.6.1 ToolExecutor и зарегистрированные инструменты

**Файлы:**
- src/local_agent/execution/tool_executor.py
- src/local_agent/execution/tools/list_directory.py
- src/local_agent/execution/tools/read_file.py
- src/local_agent/execution/tools/create_file.py
- src/local_agent/execution/tools/write_file.py
- src/local_agent/execution/tools/get_diff.py
- src/local_agent/execution/tools/run_command.py
- src/local_agent/execution/tools/run_tests.py
- src/local_agent/execution/tools/git.py
- src/local_agent/execution/workspace.py

**Тесты:** test/test_tool_executor.py, test/test_tool_executor_adapter.py, test/integration/test_git_mutations_windows.py и отдельные тесты инструментов.

Назначение: единая точка запуска фиксированного набора инструментов. ToolExecutor связывает переданные экземпляры инструментов с ApprovalManager и ExecutionRecorder.

Конструктор принимает:
- approval_manager: ApprovalManager
- экземпляры семи существующих инструментов;
- git_tool: GitTool — существующий экземпляр GitTool;
- execution_recorder: ExecutionRecorder.

ToolExecutor не создаёт GitTool самостоятельно. Создание GitTool и Workspace остаётся ответственностью composition root вызывающего приложения.

Публичный метод:

- execute(task_id: str, tool_name: str, parameters: dict[str, Any]) -> ToolExecutionResult

Поддерживаемые имена инструментов:

| Инструмент | Action type | Параметры | Требует подтверждения |
|---|---|---|---|
| list_directory | LIST_DIRECTORY | path, max_depth, max_entries (необязательные) | Нет |
| read_file | READ_FILE | path | Нет |
| create_file | CREATE_FILE | path, content | Да |
| write_file | WRITE_FILE | path, content | Да |
| get_diff | GET_DIFF | path, original_content | Нет |
| run_command | RUN_COMMAND | command | Да |
| run_tests | RUN_TESTS | нет | Да |
| git_status | GIT_STATUS | нет | Нет |
| git_branch | GIT_BRANCH | нет | Нет |
| git_head | GIT_HEAD | нет | Нет |
| git_branches | GIT_BRANCHES | нет | Нет |
| git_log | GIT_LOG | limit: int, необязательный, default 20, диапазон 1..100 | Нет |
| git_diff | GIT_DIFF | paths: list[str], необязательный, default [] | Нет |
| git_staged_diff | GIT_STAGED_DIFF | paths: list[str], необязательный, default [] | Нет |
| git_changed_files | GIT_CHANGED_FILES | нет | Нет |
| git_create_and_switch_branch | GIT_CREATE_AND_SWITCH_BRANCH | branch_name: str | Да |
| git_stage | GIT_STAGE | paths: list[str] | Да |
| git_commit | GIT_COMMIT | message: str, paths: list[str] | Да |

Read-only Git-действия отображаются на методы переданного GitTool: status, branch, head, branches, log, diff, staged_diff и changed_files. Изменяющие действия обрабатываются отдельным путём ToolExecutor: GitTool сначала нормализует параметры и снимает snapshot, ToolExecutor добавляет его в параметры ApprovalManager, а после APPROVED повторно снимает snapshot под per-repository lock. При изменении состояния создаётся новый PENDING approval; команда не запускается. Перед вызовом повторно проверяются preconditions и approval hash.

Поведение:

- Имя инструмента проверяется по внутреннему фиксированному набору.
- Параметры проверяются по обязательным и необязательным ключам соответствующего инструмента.
- Для рискованных действий ToolExecutor запрашивает подтверждение через ApprovalManager.
- При PENDING возвращает WAITING_FOR_APPROVAL без запуска инструмента.
- После APPROVED начинает запись выполнения через ExecutionRecorder, затем непосредственно перед вызовом инструмента повторно проверяет разрешение через is_approved() с теми же task_id, action_type и parameters.
- Read-only Git-действия не вызывают request_approval() или is_approved(); они выполняются через общий механизм ExecutionRecorder.
- `git_create_and_switch_branch`, `git_stage` и `git_commit` требуют отдельного approval; до него запускаются только read-only Git-команды. Snapshot входит в canonical parameters hash существующего ApprovalManager.
- Успешный результат `git_stage` вместе с внутренней stage receipt записывается в SQLite как ExecutionRecorder event; следующий `git_stage` или `git_commit` использует эту receipt для доказательства происхождения текущего index, в том числе после перезапуска ToolExecutor.
- Ошибки валидации параметров Git-действий фиксируются как неуспешные выполнения через ExecutionRecorder до вызова GitTool; некорректные параметры не доходят до инструмента.
- При успешном выполнении вызывается finish_execution().
- При перехваченной ошибке выполнения вызывается fail_execution().
- Если запись результата в ExecutionRecorder завершается ошибкой, ToolExecutor возвращает FAILED и сообщает об ошибке журналирования.

Инварианты и ограничения:

- Вызывающий код передаёт имя инструмента и параметры, но не может передать собственную политику разрешений или объект произвольного инструмента.
- ToolExecutor не заменяет ApprovalManager и ExecutionRecorder.
- ToolExecutor вызывается из ToolExecutorAdapter, который реализует контракт Executor и последовательно исполняет шаги Plan; Adapter не содержит Git-специфичной диспетчеризации.
- Для требующих подтверждения действий ToolExecutor создаёт запрос через ApprovalManager. При PENDING возвращает WAITING_FOR_APPROVAL; после одобрения повторный вызов ToolExecutor с теми же параметрами позволяет выполнить действие.
- run_command и run_tests требуют подтверждения. ToolExecutor повторно проверяет одобрение непосредственно перед вызовом соответствующего инструмента.
- Все три изменяющие Git-действия используют фиксированные аргументы; arbitrary Git commands, push/pull/fetch/merge/rebase/reset/clean/restore/stash и произвольные pathspec не предоставляются.
- RunCommandTool передаёт команду в ExecutionBackend; Backend запускает её в Docker-контейнере с явно смонтированным Workspace.
- RunTestsTool использует тот же путь исполнения через RunCommandTool и ExecutionBackend.
- ToolExecutor отвечает за диспетчеризацию, подтверждения и запись выполнения; ExecutionBackend отвечает за запуск команды в Docker и очистку контейнера.
- Docker-изоляция снижает доступ контейнера к хосту, но не является доказательством абсолютной защиты от всех способов выхода из контейнера.
- Список перехватываемых исключений ограничен конкретными типами; обработка неожиданных исключений отмечена как технический долг.

### 3.6.2 GitTool — read-only диагностика и подтверждённые мутации (подэтапы 9.Б и 9.Г)

**Файл:** `src/local_agent/execution/tools/git.py`  
**Внутренний runner:** `src/local_agent/execution/tools/_windows_runner.py`  
**Тесты:** `test/test_git_tool.py`, `test/test_windows_runner.py`, `test/integration/test_git_tool_windows.py`, `test/integration/test_git_mutations_windows.py`.

Назначение: читать состояние локального Git-репозитория и выполнять три строго ограниченные изменяющие операции в репозитории, корень которого совпадает с `Workspace.root`. GitTool запускает установленный `git.exe` непосредственно на Windows-хосте; Docker ExecutionBackend для этих операций не используется.

Основной контракт:

- `GitTool(workspace: Workspace, *, timeout_seconds: float = 15.0, max_output_size: int = MAX_OUTPUT_SIZE, runner: Runner | None = None)`.
- При создании проверяет тип Workspace, допустимость timeout/лимита вывода, наличие Git через `shutil.which("git")` и совпадение корня репозитория с `Workspace.root`.
- Read-only операции:
  - `status() -> GitStatus`;
  - `branch() -> str | None`;
  - `head() -> str`;
  - `branches() -> tuple[GitBranch, ...]`;
  - `log(limit: int = 20) -> tuple[GitCommit, ...]`;
  - `diff(paths: tuple[str, ...] | list[str] = ()) -> str`;
  - `staged_diff(paths: tuple[str, ...] | list[str] = ()) -> str`;
  - `changed_files() -> tuple[str, ...]`.
  - `prepare_mutation(operation, parameters, previous_stage_receipt=None) -> tuple[dict, dict]` — нормализация параметров, snapshot и проверка preconditions до approval;
  - `capture_mutation_snapshot(...)` — повторный snapshot без преждевременного отклонения изменившегося состояния;
  - `mutation_lock()` — контекст per-repository lock;
  - внутренний `_execute_approved_mutation(...)` — фиксированная команда и postcondition после проверки approval.
- `GitStatus` содержит raw porcelain v2, текущую ветку (или None для detached HEAD), HEAD и записи статуса.
- `GitBranch` содержит имя, commit SHA и признак текущей ветки.
- `GitCommit` содержит SHA, автора, дату и subject.
- `GitCommandResult` содержит operation, exit code, текстовые и байтовые stdout/stderr и признаки усечения. Это внутренняя модель результата вызова Git.
- Ошибки: `GitToolError` — базовая; `GitNotFoundError` — Git отсутствует; `GitRepositoryError` — Workspace не является корнем репозитория; `GitCommandError` — ошибка фиксированной Git-операции, содержит operation, exit_code и stderr.

Безопасность аргументов и вывода:

- Git запускается без shell; executable определяется через `shutil.which`.
- Аргументы формируются внутри GitTool; произвольная команда и произвольный shell-вызов не предоставляются.
- Перед каждой подкомандой передаётся `--literal-pathspecs`; пути отделяются через `--` и проверяются через `Workspace.resolve_path()`. Для snapshot status используется `--no-optional-locks`, `core.fsmonitor=false` применяется ко всем вызовам, чтобы не запускать внешний fsmonitor и не обновлять index до approval.
- Snapshot включает canonical root, branch/HEAD, NUL-safe porcelain v2, index file и staged tree fingerprints, staged/unstaged diff fingerprints, tracked/untracked/conflict entries, выбранные file fingerprints и состояние Git hooks.
- Пути для `git_stage`/`git_commit` — непустой уникальный список относительных файлов; абсолютные пути, traversal, NUL, wildcard и pathspec magic отклоняются.
- `git_create_and_switch_branch` допускается только при существующем HEAD, attached branch и полностью чистом состоянии; команда — `git switch -c`.
- `git_stage` допускается только с чистым исходным index или с index, совпадающим с receipt предыдущего успешного одобренного stage; команда — `git add -- <explicit paths>`. Внешние clean filters блокируются. Postcondition проверяет, что изменились только разрешённые index entries и файлы не изменились.
- `git_commit` требует receipt предыдущего stage, точного списка staged paths, отсутствия unstaged/untracked/conflict изменений и совпадения index fingerprint; postcondition проверяет новый HEAD, parent и точный commit tree. Все активные Git hooks и настроенный `core.hooksPath` блокируют branch/commit; commit signing отключён фиксированным аргументом. Автоматического rollback нет.
- Per-repository lock сериализует мутации одного репозитория внутри процесса local-agent. Внешние процессы этот lock не соблюдают; абсолютная атомарность между последней проверкой и Git syscall не гарантируется.
- Таймаут по умолчанию — 15 секунд, максимальный — 120 секунд.
- Максимальный сохраняемый вывод ограничен `MAX_OUTPUT_SIZE` (см. константу runner); stdout и stderr читаются параллельно, остаток вычитывается с отбрасыванием после достижения лимита.
- Timeout и ошибки запуска/runner преобразуются в типизированные ошибки GitTool.

Windows runner:

- Использует `CreateProcessW` с `CREATE_SUSPENDED`, затем `AssignProcessToJobObject`, и только после успешного назначения — `ResumeThread`.
- Job Object настроен с `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`; для завершения дерева процессов применяется `TerminateJobObject`.
- Для наследования handles используется `STARTUPINFOEXW` и `PROC_THREAD_ATTRIBUTE_HANDLE_LIST`; whitelist содержит только stdin, stdout write и stderr write handles.
- Reader threads обрабатывают stdout/stderr параллельно; timeout, отмена чтения и cleanup handles обрабатываются runner.
- `ProcessResult` содержит stdout/stderr bytes, exit_code, timed_out и признаки усечения потоков.

**Граница реализации:** read-only операции и их регистрация относятся к 9.Б/9.В; три изменяющие операции и их approval/snapshot flow добавлены в LA-9G-01 (9.Г). GitTool не предоставляет произвольных команд. ToolExecutorAdapter не содержит Git-специфичной логики.

#### RunCommandTool — выполнение системных команд

**Файл:** `src/local_agent/execution/tools/run_command.py`
**Unit-тесты:** `test/test_run_command.py`

Назначение: выполнить переданную команду через внедрённый `ExecutionBackend` в Workspace.

Конструктор:

- `__init__(backend: ExecutionBackend) -> None`

Публичный метод:

- `execute(command: list[str] | tuple[str, ...]) -> RunCommandResult`

Поведение:

- Проверяет, что команда является непустым списком или кортежем строк, исполняемый файл не пустой, аргументы не содержат NUL.
- Преобразует команду в tuple и передаёт её в `backend.execute(command)`.
- Преобразует `ExecutionResult` Backend в `RunCommandResult`.
- Преобразует `ExecutionBackendError` в `RunCommandError`, сохраняя исходную ошибку как причину.
- Отдельно ограничивает stdout и stderr размером 1 MiB каждый в UTF-8; при обрезке выставляет соответствующий флаг.
- Декодирует вывод с `errors="replace"` и нормализует `CRLF`/`CR` в `LF`.

Поля `RunCommandResult`:

- `command: tuple[str, ...]`
- `exit_code: int | None`
- `stdout: str`
- `stderr: str`
- `timed_out: bool`
- `stdout_truncated: bool`
- `stderr_truncated: bool`

RunCommandTool не запускает команды самостоятельно и не реализует отдельную песочницу: фактическое исполнение делегируется Backend.

#### RunTestsTool — запуск тестов проекта

**Файл:** `src/local_agent/execution/tools/run_tests.py`
**Unit-тесты:** `test/test_run_tests.py`

Назначение: запустить тесты проекта фиксированной командой через `RunCommandTool`.

Конструктор:

- `__init__(command_tool: RunCommandTool) -> None`

Публичный метод:

- `execute() -> RunTestsResult`

Фиксированная команда: `("uv", "run", "pytest", "-v")` (`TEST_COMMAND`).

Поведение:

- Не имеет собственного механизма запуска процессов или контейнеров.
- Вызывает `RunCommandTool.execute(TEST_COMMAND)`.
- Преобразует `RunCommandResult` в `RunTestsResult`, сохраняя команду, exit code, stdout, stderr, timeout и флаги обрезки вывода.
- `passed` вычисляется как `exit_code == 0 and not timed_out`.

Таким образом, RunTestsTool выполняет тесты через цепочку `RunTestsTool → RunCommandTool → ExecutionBackend → Docker`.

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

**Статус:** исследование Docker на рабочем компьютере проведено и задокументировано. Самостоятельный ExecutionBackend реализован и прошёл unit- и интеграционные тесты на текущей конфигурации Docker. Повторение всех ручных проверок исследования на других компьютерах не является критерием завершения этапа 6.Б.

**Архитектурная граница:**

- Docker выбран как среда изолированного выполнения.
- Самостоятельный ExecutionBackend реализован в src/local_agent/execution/backend.py.
- Backend применяет ограничения, исследованные на этапе 6.А.
- RunCommandTool интегрирован с ExecutionBackend и передаёт ему команды для выполнения в Docker с Workspace.
- RunTestsTool вызывает RunCommandTool с фиксированной командой pytest и использует тот же Docker-путь.
- ToolExecutor управляет разрешениями и вызывает инструменты; ExecutionBackend не принимает решений о пользовательских подтверждениях.
- ToolExecutor включён в исполнительную цепочку через ToolExecutorAdapter, реализующий контракт Executor.
- Исследование и интеграционные тесты не доказывают абсолютную защиту хоста от всех способов выхода из контейнера.

Подробные команды, фактические результаты, ограничения и порядок воспроизведения приведены в `docs/EXECUTION_ISOLATION_RESEARCH.md`.


### 3.6.3 ExecutionBackend — изолированное выполнение команд
**Файл:** src/local_agent/execution/backend.py
**Тесты:** test/test_execution_backend.py, test/integration/test_execution_backend_integration.py
**Документация:** docs/EXECUTION_BACKEND.md

Назначение: самостоятельный компонент выполнения команд в отдельных Docker-контейнерах с ограниченным доступом к Workspace и ресурсам.

Публичные типы:

- ExecutionBackend — выполняет команды в Docker.
- ExecutionResult — неизменяемый результат выполнения команды.
- ExecutionBackendError — ошибка запуска, проверки состояния или очистки контейнера.

Важно: этот ExecutionResult отличается от ExecutionResult из src/local_agent/models/agent.py. Первый описывает результат выполнения системной команды, второй — результат работы агента Executor.

Конструктор:

- __init__(workspace: Workspace, *, image: str = "local-agent-python-test:3.12", timeout_seconds: float = 60) -> None

Публичный метод:

- execute(command: Sequence[str]) -> ExecutionResult

Поля ExecutionResult:

- stdout: str
- stderr: str
- exit_code: int | None
- timed_out: bool

Фактическое поведение:

- Команда передаётся Docker CLI списком аргументов, без системной оболочки.
- Для каждой команды создаётся отдельный контейнер со случайным именем.
- Автоматическая загрузка образа запрещена через --pull=never.
- Workspace монтируется в /workspace.
- Рабочий каталог контейнера — /workspace.
- После выполнения Backend удаляет контейнер и проверяет его отсутствие.
- При превышении тайм-аута Backend пытается удалить контейнер, ожидает завершения Docker CLI и при необходимости принудительно завершает сам процесс Docker CLI.

Применяемые ограничения контейнера:

| Ограничение | Значение |
|---|---|
| Сеть | --network none |
| Память | 512m |
| CPU | 1 |
| Процессы | 64 |
| Linux capabilities | --cap-drop ALL |
| Дополнительная защита | no-new-privileges |
| Пользователь | 10000:10000 |
| Автоматическая загрузка образа | Запрещена |

Валидация:

- workspace должен быть экземпляром Workspace;
- image должен быть непустой строкой;
- timeout_seconds должен быть конечным положительным числом;
- command должен быть непустой последовательностью строк;
- строка, bytes, пустой исполняемый файл и аргументы с нулевым байтом отклоняются;
- путь Workspace с запятой не поддерживается.

Границы ответственности:

- Backend не управляет пользовательскими подтверждениями и разрешениями.
- Backend не записывает события через ExecutionRecorder.
- Backend не является диспетчером инструментов.
- RunCommandTool вызывает Backend для выполнения команды в Docker.
- RunTestsTool использует Backend через RunCommandTool; отдельного механизма исполнения тестов у него нет.
- Backend не управляет разрешениями, пользовательскими подтверждениями или ExecutionRecorder.
- Backend не управляет жизненным циклом Workspace.

Проверки:

- Unit-тесты проверяют формирование команды Docker, валидацию, обработку ошибок и тайм-аут.
- Интеграционные тесты проверяют фактическое выполнение в Docker, потоки вывода, код завершения, работу с Workspace, непривилегированного пользователя и отсутствие сети.
- Ручные проверки ограничений изоляции и поведения Docker зафиксированы отдельно в docs/EXECUTION_ISOLATION_RESEARCH.md.

**Статус:** самостоятельный ExecutionBackend реализован и используется RunCommandTool; RunTestsTool выполняет тесты через RunCommandTool и тот же Backend. ToolExecutor управляет разрешениями и вызывает инструменты. ToolExecutor подключён к Orchestrator через ToolExecutorAdapter.


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

Назначение: хранить упорядоченный список сообщений для передачи модели. Приложение инициирует исследование рабочего проекта, формирует сообщения и наполняет ContextManager. Конкретная реализация Planner получает подготовленный контекст через собственный настроенный механизм; ContextManager предоставляет сообщения в исходном порядке через get_messages().

Ограничения:

- Не обращается к модели.
- Не загружает автоматически файлы проекта или историю задачи.
- Не связан с SQLite и не является долговременным хранилищем.
- Orchestrator не использует его автоматически, не исследует файлы, не формирует сообщения и не управляет контекстом. Контракт Planner.create_plan(task) не меняется.

### 3.10 Агентные Protocol, адаптер и заглушки

**Файлы:** src/local_agent/agents/protocols.py, src/local_agent/agents/stubs.py, src/local_agent/agents/tool_executor_adapter.py
**Тесты:** test/test_agent_protocols.py, test/test_agent_stubs.py, test/test_agent_models.py, test/test_tool_executor_adapter.py

Контракты в protocols.py:

- Planner.create_plan(task: Task) -> Plan
- Planner.revise_plan(task: Task, previous_plan: Plan, feedback: str) -> Plan
- Executor.execute(task_id: str, plan: Plan) -> ExecutionResult
- Reviewer.review(plan: Plan, result: ExecutionResult) -> ReviewResult

Это typing.Protocol-контракты. Orchestrator получает реализации через внедрение зависимостей; сам Protocol не реализует вызов модели или инструментов.

ToolExecutorAdapter — реализация контракта Executor для исполнения шагов Plan через ToolExecutor.

Публичный контракт:

- __init__(tool_executor: ToolExecutor) -> None
- execute(task_id: str, plan: Plan) -> ExecutionResult

Поведение ToolExecutorAdapter:

- Проверяет task_id и последовательно обрабатывает Plan.steps.
- Для каждого шага вызывает ToolExecutor.execute(task_id, step.action, step.parameters).
- При успешном выполнении добавляет фактический результат и execution_id в step_results и переходит к следующему шагу.
- При WAITING_FOR_APPROVAL возвращает ExecutionResult с waiting_for_approval=True и pending_approval_id; текущий шаг остаётся ожидающим.
- После одобрения Orchestrator повторно вызывает Executor с тем же task_id и Plan. Adapter продолжает с сохранённого индекса, не повторяя ранее завершённые шаги.
- При ошибке инструмента завершает выполнение с ошибкой.
- Если переданный при продолжении Plan отличается от сохранённого, продолжение отклоняется.
- Прогресс и накопленные step_results хранятся в словаре Adapter в памяти процесса. После перезапуска процесса они теряются; механизм восстановления/перепостроения прогресса отсутствует.

Заглушки в stubs.py:

- StubPlanner — тестовая реализация Planner.
- StubExecutor — тестовая реализация Executor.
- StubReviewer — тестовая реализация Reviewer.
- get_stub_agents() -> tuple[Planner, Executor, Reviewer] — фабрика тестового набора.

Заглушки нужны для тестирования orchestration без сетевого вызова модели и без реального исполнения инструментов. StubExecutor — альтернативная тестовая реализация Executor, а не единственный доступный исполнитель.

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

1. Orchestrator вызывает StateManager.create_task(description); задача получает статус CREATED.
2. Planner.create_plan(task) формирует и возвращает Plan.
3. Orchestrator сериализует Plan в параметры запроса PLAN_APPROVAL.
4. ApprovalManager сохраняет запрос и переводит задачу в WAITING_FOR_APPROVAL.
5. После одобрения PLAN_APPROVAL задача переводится в RUNNING.

План хранится как параметры Approval в SQLite. Отдельной таблицы планов в текущей реализации нет.

Если Planner завершился ошибкой, Orchestrator обрабатывает ошибку и не должен оставлять задачу как успешно подготовленную.

### 5.2 Согласование плана

Реализовано:

- пользовательское решение фиксируется через ApprovalManager;
- одобрение связано с task_id, action_type и хешем параметров;
- изменение параметров приводит к другому идентификатору действия;
- отклонение плана через Orchestrator.reject_plan завершает задачу как CANCELLED.

Не путать согласование плана с подтверждением каждого отдельного инструмента. Политика подтверждений реализована для фиксированного набора поддерживаемых инструментов и применяется в интегрированном исполнительном контуре (раздел 5.3). Универсальная расширяемая система разрешений для произвольных файловых/системных действий пока не реализована.

### 5.3 Выполнение одобренного плана

Реализованный путь:

1. Orchestrator загружает Approval.
2. Проверяет, что запрос одобрен и относится к исполнению плана.
3. Восстанавливает Plan из сохранённых параметров.
4. Вызывает Executor.execute(task_id, plan). В исполнительной конфигурации эту роль выполняет ToolExecutorAdapter.
5. ToolExecutorAdapter последовательно передаёт шаги в ToolExecutor.execute(task_id, tool_name, parameters).
6. ToolExecutor проверяет имя инструмента и параметры. Для действий с обязательным подтверждением создаёт запрос через ApprovalManager.
7. Если ToolExecutor возвращает WAITING_FOR_APPROVAL, Adapter возвращает Orchestrator ExecutionResult с waiting_for_approval=True и pending_approval_id. Задача остаётся в WAITING_FOR_APPROVAL, Reviewer не вызывается.
8. После одобрения отдельного действия Orchestrator повторно вызывает Executor с тем же task_id и Plan. Adapter продолжает с ожидающего шага, используя сохранённый in-memory прогресс.
9. ToolExecutor записывает вызовы инструментов через ExecutionRecorder: начало и успешное/неуспешное завершение.
10. После успешного выполнения всех шагов Adapter возвращает ExecutionResult с фактическими результатами в step_results.
11. Orchestrator передаёт полный ExecutionResult в Reviewer.review(plan, result).
12. При одобрении Reviewer задача становится SUCCEEDED; при отклонении Reviewer или неуспешном исполнении задача становится FAILED.

Ограничения текущей реализации:

- прогресс ToolExecutorAdapter хранится только в памяти процесса и не восстанавливается после перезапуска;
- цикл исправления замечаний и повторная генерация плана через Planner.revise_plan не реализованы;
- отдельный Coder Protocol/реализация отсутствуют;
- реальные Planner/Reviewer на базе ModelClient не подключены.

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
|-------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|| Python управляет состоянием задачи                          | Реализовано через StateManager.                                                                                                                                                                                                                     |
| SQLite — источник истины для задач                          | Реализовано через StateManager.                                                                                                                                                                                                                     |
| SQLite — источник истины для подтверждений                  | Реализовано через ApprovalManager.                                                                                                                                                                                                                  |
| Модель не должна выполнять инструменты в обход Python       | Реализовано: Orchestrator передаёт Plan в ToolExecutorAdapter, который вызывает ToolExecutor — единую Python-точку запуска фиксированного набора инструментов.                                                                                         |
| Модель анализирует и предлагает план                        | Контракт Planner существует; текущая интеграция использует переданный объект Planner, реальные модели не подключены.                                                                                                                                |
| Контекст модели формируется приложением явно                | ContextManager реализует список сообщений, но пока не интегрирован с Orchestrator/ModelClient.                                                                                                                                                      |
| Подтверждение привязано к действию и параметрам             | Реализовано в ApprovalManager через action_type и parameters_hash.                                                                                                                                                                                  |
| Отмена идентичного действия окончательна                    | Реализована блокировка повторного запроса с теми же параметрами после CANCELLED.                                                                                                                                                                    |
| Рискованные действия требуют подтверждения Python           | Реализовано в исполнительной цепочке: ToolExecutorAdapter → ToolExecutor → ApprovalManager. create_file, write_file, run_command и run_tests требуют подтверждения; list_directory, read_file, get_diff и все восемь git_* действий выполняются без него.                 |
| История выполнений используется при восстановлении          | ToolExecutor записывает каждый вызов инструмента через ExecutionRecorder; RecoveryManager отдельно проверяет сохранённую историю. Автоматический запуск RecoveryManager при старте не подключён.                                                     |
| Контекст и данные задачи не зависят от неявной истории чата | Целевое правило; ContextManager существует, но его сборка в единый контекст задачи ещё не интегрирована.                                                                                                                                            |
| Не добавлять менеджеры и абстракции без необходимости       | Действующее архитектурное правило проекта, не машинно проверяемый инвариант.                                                                                                                                                                        |
| Изоляция выполнения системных команд                        | RunCommandTool выполняет команды через ExecutionBackend в Docker с Workspace; RunTestsTool использует тот же путь через RunCommandTool. Docker integration tests проверяют фактическую работу Backend; сквозные тесты инструментов должны быть отражены после их добавления. |

### 6.1 Известные разрывы интеграции

1. Orchestrator не использует ModelClient/ModelRouter/ContextManager.
2. RecoveryManager не вызывается автоматически при старте.
3. ToolExecutorAdapter хранит прогресс выполнения только в памяти процесса; после перезапуска продолжить с ожидающего шага нельзя.
4. Planner.revise_plan определён, но не участвует в цикле выполнения.
5. Текущий цикл не включает автоматическое исправление замечаний Reviewer и повторный запуск Coder.
6. Текущий набор статусов TaskStatus не содержит отдельных состояний для планирования, ревью или ожидания исправлений.

## 7. Карта тестов

| Тестовый файл                                          | Что покрывает                                                                                                                    |
|--------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------|
| test/test_agent_models.py                              | Валидация ExecutionResult и ReviewResult.                                                                                        |
| test/test_agent_protocols.py                           | Совместимость объектов с Protocol-контрактами агентов.                                                                           |
| test/test_agent_stubs.py                               | Поведение тестовых Planner/Executor/Reviewer.                                                                                    |
| test/test_approval_manager.py                          | Создание запросов, решения, дедупликация, хеширование параметров и интеграция со статусом задачи.                                |
| test/test_approval_model.py                            | Модель Approval и копирование параметров.                                                                                        |
| test/test_context_manager.py                           | Добавление, получение и очистка сообщений.                                                                                       |
| test/test_execution_recorder.py                        | Запись выполнений, событий и trace-файлов, восстановление/целостность журнала.                                                   |
| test/test_model_client.py                              | Вызов локального OpenAI-compatible API и обработка ошибок клиента.                                                               |
| test/test_model_router.py                              | Выбор модели по роли и неизвестные роли.                                                                                         |
| test/test_orchestrator.py                              | Подготовка задачи, планирование, выполнение, ревью и обработка ошибок.                                                           |
| test/test_plan_approval.py                             | Согласование плана и запрет выполнения без одобрения.                                                                            |
| test/test_plan_models.py                               | Валидация Plan и PlanStep.                                                                                                       |
| test/test_recovery_manager.py                          | Проверка истории выполнений и восстановление состояния задач.                                                                    |
| test/test_state_manager.py                             | Создание задач, чтение и допустимые/недопустимые переходы.                                                                       |
| test/test_tool_executor.py                             | Проверка диспетчеризации инструментов, валидации параметров, подтверждений и записи результатов выполнения.                      |
| test/test_git_tool.py | Unit-тесты GitTool: проверка Workspace/repository, восьми read-only операций, параметров, результатов и ошибок. |
| test/test_windows_runner.py | Unit-тесты Win32 runner: порядок запуска, Job Object, handle whitelist, reader lifecycle, timeout, ошибки API и cleanup. |
| test/integration/test_git_tool_windows.py | Windows integration tests GitTool: реальные Git-репозитории, linked worktree, read-only snapshot и проверка отсутствия наследования постороннего handle. |
| test/integration/test_git_mutations_windows.py | Windows integration tests трёх подтверждаемых Git-операций, changed snapshot, cancellation, SQLite receipt restart, фильтров, hooks, точного stage scope и commit postconditions. |
| test/integration/test_git_mutations_windows.py | Windows integration tests трёх подтверждаемых Git-операций, changed snapshot, cancellation, точного stage scope и commit postconditions. |
| test/test_run_command.py                                | Unit-тесты RunCommandTool: передача команды Backend, преобразование результата, ограничение вывода и обработка ошибок Backend.   |
| test/test_run_tests.py                                   | Unit-тесты RunTestsTool: фиксированная команда pytest, преобразование результата и обработка ошибок RunCommandTool.              |
| test/test_execution_backend.py                         | Unit-тесты ExecutionBackend: формирование команды Docker, валидация параметров, обработка ошибок, тайм-аут и очистка контейнера. |
| test/integration/test_execution_backend_integration.py | Docker integration tests: выполнение команд через ExecutionBackend, stdout/stderr, exit code, Workspace, UID/GID, ограничения контейнера, очистка контейнера, а также сквозная проверка RunCommandTool и RunTestsTool через реальный Docker Backend. |

Тесты не заменяют проверку фактической интеграции. GitTool остаётся самостоятельным read-only компонентом, зарегистрированным в ToolExecutor и доступным через общий маршрут ToolExecutorAdapter; Git-специфичной логики в Adapter нет. ModelClient и ContextManager остаются вне автоматической цепочки Orchestrator. ExecutionRecorder, напротив, вызывается ToolExecutor при каждом фактическом запуске инструмента; это не означает, что Orchestrator управляет журналом напрямую.

## 8. Что пока не реализовано как единая система

По состоянию на коммит инвентаризации нельзя считать завершёнными следующие функции:

- Полноценный автономный цикл с реальными Planner/Coder/Reviewer, работающими через ModelClient.
- Автоматическое построение контекста задачи из файлов и состояния проекта.
- Расширение и уточнение политик разрешений за пределами текущего фиксированного набора инструментов.
- Цикл coder → reviewer → coder с ограничением попыток и формальным условием выхода.
- Автоматический вызов RecoveryManager при запуске приложения.
- Восстановление прогресса ToolExecutorAdapter и продолжение прерванного плана после перезапуска.
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