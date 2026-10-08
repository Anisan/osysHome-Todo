# Todo - Техническая документация

## 1. Общая информация

`Todo` предоставляет MCP-интерфейс для работы со списками задач, самими задачами, настройками уведомлений и тестированием Python-хуков.

Capabilities доступны через:

- `action=capabilities`;
- ресурс `osys://plugin/Todo`.

## 2. MCP Collections

| ID | binding_mode | writable | has_code | Фильтры `list_entities` | Описание |
| :--- | :--- | :---: | :---: | :--- | :--- |
| `lists` | `none` | yes | no | - | Списки задач |
| `tasks` | `none` | yes | no | `list_id`, `query`, `has_started`, `completed_only` | Задачи |
| `hooks` | `none` | no | yes | - | Виртуальная коллекция для проверки и dry-run Python-кода |

## 3. Схемы сущностей

### 3.1. `lists`

Основные поля:

- `id` (readOnly);
- `title` (required);
- `tags`;
- `sort_order`;
- `created`, `updated` (readOnly).

### 3.2. `tasks`

Основные поля:

- `id` (readOnly);
- `list_id`;
- `title` (required);
- `notes`, `tags`;
- `started`, `finished`, `completed`;
- `all_day`;
- `priority` (`0..3`);
- `settings` (object);
- `created`, `updated` (readOnly).

Поддерживается legacy-поле `complited`, которое нормализуется в `completed`.

### 3.3. `tasks.settings`

Поля настроек задачи:

- `reminder_enabled` (bool);
- `reminder_offset_minutes` (int, default `15`);
- `reminder_code` (string);
- `start_code` (string);
- `finish_code` (string);
- `notified` (bool);
- `recurrence_cron` (string) — выражение cron для повтора; пустая строка = без повтора.

Формат `recurrence_cron`: стандартное cron `m h dom mon dow` (как в Scheduler). При сохранении задачи строка проверяется через `validate_cron_expression`.

### 3.4. `hooks` (виртуальная схема)

Поля:

- `code` (required);
- `task_id`;
- `event`: `reminder`, `start`, `finish`, `create`, `delete`, `notified`, `completed`;
- `task` (override snapshot для dry-run).

## 4. Конфигурация плагина

`action=config_schema` возвращает JSON Schema конфигурации `Todo`.

Ключевые поля для уведомлений:

- `default_reminder_code`;
- `default_start_code`;
- `default_finish_code`;
- `code_on_create`;
- `code_on_delete`;
- `code_on_notified`;
- `code_on_completed`.

## 5. Операции `invoke`

| operation | Параметры | Назначение |
| :--- | :--- | :--- |
| `complete_task` | `task_id` | Переключить выполнение задачи |
| `reorder_lists` | `ids` или `order` | Изменить порядок списков |
| `mark_notified` | `task_id` | Отметить задачу как уведомленную |
| `sync_schedules` | `task_id` (optional) | Пересобрать scheduler-задачи |
| `run_task_event` | `task_id`, `event`, `force?` | Запустить hook-событие задачи |
| `resolve_task_hook` | `task_id`, `event` | Определить итоговый код хука и источник |
| `get_notification_settings` | - | Получить текущие plugin-level настройки |
| `save_notification_settings` | `settings` | Сохранить plugin-level настройки |

## 6. Code actions для коллекции `hooks`

### 6.1. Валидация кода

`validate_entity_code` с `collection=hooks` проверяет Python-код хука.

### 6.2. Dry-run

`run_entity_dry` с `collection=hooks` выполняет код в тестовом контексте.

Особенности:

- `context.task_id` обязателен;
- поддерживается `context.event`;
- допускается `context.task` как override объекта задачи.

Контекст переменных в коде:

- `task`;
- `task_id`;
- `event`;
- `params`;
- `logger`.

## 7. Семантика дат и статусов

- `started` — время начала задачи;
- `finished` — время окончания;
- `completed` — время фактического завершения.

`complete_task` синхронно выставляет или очищает `completed` и `finished`.

При **отметке выполненной** (переход в completed):

1. Снимаются Scheduler-задачи плагина для этой записи (`Todo.task.<id>.*`).
2. Выполняется instant-хук `completed` (код `code_on_completed` в конфиге плагина).
3. Если в `settings.recurrence_cron` задано непустое выражение и у задачи есть `started`:
   - текущая запись остаётся с заполненным `completed`;
   - создаётся **новая** задача-копия (тот же список, текст, приоритет, доступ, `settings` с `notified=false`);
   - `started` / `finished` копии — следующий слот cron (от «сейчас» в часовом поясе сервера, как Scheduler) плюс прежняя длительность `finished - started` (если `finished` был задан до complete; иначе — правила all_day / +30 мин для timed);
   - для копии вызываются `sync_task_schedules` и хук `create` (если есть даты).

При снятии отметки выполнения повторная копия **не** создаётся.

Если `started` отсутствует, задача трактуется как заметка:

- не получает scheduler-задачи;
- scheduled-hooks не запускаются (если не принудить через `force=true` для ручного вызова события).

У задач с заполненным `completed` Scheduler-джобы reminder/start/finish **не** создаются.

## 7.1. Scheduler и напоминания

Имена одноразовых задач Scheduler: `Todo.task.<task_id>.<event>`, где `event` — `reminder`, `start` или `finish`.

Код джоба вызывает `callPluginFunction("Todo", "run_task_event", ...)`. Метод плагина `run_task_event` выполняется внутри Flask `app_context`, чтобы ORM и БД работали из потока Scheduler.

Приоритет кода хука: поле в `tasks.settings` → значение по умолчанию в конфиге плагина → событие пропускается, если код пустой.

## 8. Примеры MCP запросов

### 8.1. Создать список

```json
{
  "plugin": "Todo",
  "action": "upsert_entity",
  "args": {
    "collection": "lists",
    "payload": {
      "title": "Работа",
      "tags": "office"
    }
  }
}
```

### 8.2. Создать задачу с напоминанием

```json
{
  "plugin": "Todo",
  "action": "upsert_entity",
  "args": {
    "collection": "tasks",
    "payload": {
      "list_id": 1,
      "title": "Подготовить отчет",
      "started": "2026-07-10 10:00:00",
      "priority": 2,
      "settings": {
        "reminder_enabled": true,
        "reminder_offset_minutes": 30
      }
    }
  }
}
```

### 8.2.1. Повторяющаяся задача (cron)

```json
{
  "plugin": "Todo",
  "action": "upsert_entity",
  "args": {
    "collection": "tasks",
    "payload": {
      "list_id": 1,
      "title": "Еженедельный обзор",
      "started": "2026-07-07 09:00:00",
      "finished": "2026-07-07 10:00:00",
      "settings": {
        "recurrence_cron": "0 9 * * 1"
      }
    }
  }
}
```

После `complete_task` для такой задачи появится новая запись со следующим понедельником в 09:00 (следующий слот cron от момента завершения).

### 8.3. Получить задачи списка

```json
{
  "plugin": "Todo",
  "action": "list_entities",
  "args": {
    "collection": "tasks",
    "list_id": 1,
    "completed_only": false,
    "limit": 100
  }
}
```

### 8.4. Запустить событие вручную

```json
{
  "plugin": "Todo",
  "action": "invoke",
  "args": {
    "operation": "run_task_event",
    "params": {
      "task_id": 5,
      "event": "start"
    }
  }
}
```

### 8.5. Проверить код hooks

```json
{
  "plugin": "Todo",
  "action": "validate_entity_code",
  "args": {
    "collection": "hooks",
    "code": "logger.info(task['title'])"
  }
}
```

## 9. MCP resources

- `osys://plugin/Todo` - capabilities и operation schemas;
- `osys://plugin/Todo/schema/lists` - JSON Schema списка;
- `osys://plugin/Todo/schema/tasks` - JSON Schema задачи;
- `osys://plugin/Todo/schema/hooks` - JSON Schema виртуальной коллекции hooks.
