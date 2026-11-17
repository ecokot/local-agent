@'
# Local Agent

Локальный Python-агент для автоматизации работы с программными проектами.

Проект находится в активной разработке.

## Технологии

- Python 3.12+
- OpenAI-compatible API
- Pydantic
- uv
- pytest
- Ruff

## Текущая структура

- `context_manager.py` — управление контекстом сообщений.
- `model_client.py` — взаимодействие с языковой моделью.
- `model_router.py` — выбор модели по роли.
- `state_manager.py` — управление состояниями задач.
- `test/` — автоматические тесты.

## Установка

Требуется Python 3.12+ и [uv](https://docs.astral.sh/uv/).

```powershell
uv sync