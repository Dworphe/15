# 🚀 ПАТЧ №19 — Полный фикс многоэтапной «Покупки»

## 📋 Описание

Полный фикс многоэтапной системы покупки с:
- ✅ Единым планировщиком (singleton)
- ✅ UTC-aware датами
- ✅ Одним сообщением-таймером на chat+deal
- ✅ Правильными этапами и переходами
- ✅ Детерминированными job-id

## 🔧 Что изменено

### 1. Единый планировщик (singleton)
- **Файл:** `app/scheduler.py`
- Заменен на singleton с SQLAlchemyJobStore для SQLite
- Все job-id теперь детерминированные

### 2. Время — только UTC-aware
- **Файл:** `app/utils/dt.py` (уже обновлен)
- Все даты теперь с tzinfo в UTC
- Функции `now_tz()`, `to_aware_utc()`, `seconds_left()`, `fmt_mmss()`

### 3. UI таймеры
- **Файл:** `app/services/ui_timers.py` (новый)
- Одно сообщение-таймер на chat+deal
- Автоматическое пересоздание при ошибках

### 4. Апдейтер таймера
- **Файл:** `app/workers/timers.py` (полностью заменен)
- Детерминированные job-id: `timer:buy:{deal_id}:{chat_id}`
- Без дублей, с правильной логикой этапов

### 5. Сервис потока покупки
- **Файл:** `app/services/buy_flow.py` (новый)
- Управление этапами: Stage1 → Round1 → RoundN → Payment
- Автоматические переходы и планирование

### 6. Модели данных
- **Файл:** `app/db/models.py`
- Добавлены поля: `stage1_deadline_at`, `stage1_finished`, `is_paid`
- Добавлено поле: `is_active` в DealRound
- Добавлены поля: `stage1_secs_default`, `pay_mins_default` в SystemSettings

### 7. Настройки
- **Файл:** `app/services/settings.py`
- Поддержка новых полей настроек
- Кэширование обновлено

### 8. Главный файл
- **Файл:** `app/main.py`
- Использует `init_scheduler()` вместо `get_scheduler(db_url)`

### 9. Админ-мастер
- **Файл:** `app/handlers/admin_deals.py`
- Автоматический запуск Этапа 1 после создания сделки
- Исправлена ошибка в шаге комментария

## 📊 Структура этапов

```
Этап 1 (Stage1): Принятие участия
├── Длительность: stage1_secs_default (60 сек)
├── Статус: OPEN
├── Результат: участники принимают/отклоняют
└── Переход: → Round1 или CANCELLED

Раунд 1 (Round1): Основные ставки
├── Длительность: round_secs_default (20 сек)
├── Статус: BIDDING
├── Результат: определение победителя
└── Переход: → Payment или Round2 (тай-брейк)

Раунд N (RoundN): Тай-брейк
├── Длительность: round_secs_default (20 сек)
├── Максимум: max_tie_rounds_default (5)
├── Результат: определение победителя
└── Переход: → Payment или CANCELLED

Этап оплаты (Payment): Оплата победителем
├── Длительность: pay_mins_default (20 мин)
├── Статус: WAIT_PAYMENT
├── Результат: подтверждение оплаты
└── Переход: → DONE или DISPUTE
```

## 🔄 Job-ID структура

```
Этап 1: deal:{deal_id}:stage1
Раунд n: deal:{deal_id}:round:{n}
Оплата: deal:{deal_id}:pay
UI-таймер: timer:buy:{deal_id}:{chat_id}
```

## 📝 Инструкции по применению

### 1. Обновление базы данных
```bash
# Применить SQL-скрипт
sqlite3 data/app.db < update_db_patch19.sql
```

### 2. Проверка импортов
```bash
python -c "from app.scheduler import get_scheduler, init_scheduler; print('✅ Планировщик')"
python -c "from app.services.buy_flow import start_stage1_for_deal; print('✅ buy_flow')"
python -c "from app.workers.timers import ensure_update_timer; print('✅ Таймеры')"
```

### 3. Запуск бота
```bash
python -m app.main --check  # проверка окружения
python -m app.main          # запуск
```

## 🧪 Тестирование

### 1. Создание сделки
- Команда: `/deals`
- Кнопка: "➕ Покупка (мастер)"
- Пройти все 11 шагов
- Проверить: создалась запись в БД

### 2. Проверка Этапа 1
- В логах должно появиться: `Added timer job timer:buy:{id}:{chat_id}`
- Должна добавиться job: `deal:{id}:stage1`
- Таймер должен показывать: "⏳ Осталось: MM:SS"

### 3. Проверка перехода к Раунду 1
- По окончании Этапа 1 должна создаться запись в `deal_rounds`
- Должна добавиться job: `deal:{id}:round:1`
- Статус должен измениться на `BIDDING`

### 4. Проверка таймера
- Таймер должен обновляться каждые 5 секунд
- Показывать правильное время до дедлайна
- Не создавать дубли сообщений

## 🚨 Важные моменты

### 1. Без Redis
- Используется только SQLite
- APScheduler с SQLAlchemyJobStore

### 2. UTC время
- Все даты в UTC с tzinfo
- Никаких `datetime.utcnow()` или `datetime.now()`

### 3. Один таймер
- Одно сообщение-таймер на chat+deal
- Автоматическое пересоздание при ошибках

### 4. Детерминированные ID
- Все job-id формируются по шаблону
- Никаких случайных UUID

## 🔍 Отладка

### Логи планировщика
```python
# В коде
log.info("Added timer job %s", job_id)
log.info("Deal %s cancelled: no participants", deal_id)
```

### Проверка job в планировщике
```python
scheduler = get_scheduler()
jobs = scheduler.get_jobs()
for job in jobs:
    print(f"{job.id}: {job.func_name} -> {job.next_run_time}")
```

### Проверка БД
```sql
-- Проверить новые поля
SELECT id, deal_no, stage1_deadline_at, stage1_finished, is_paid FROM deals LIMIT 5;

-- Проверить раунды
SELECT * FROM deal_rounds WHERE is_active = 1;

-- Проверить таймеры
SELECT * FROM ui_timer_messages;
```

## ✅ Результат

После применения патча:
1. **Многоэтапная покупка** работает корректно
2. **Таймеры** показывают правильное время
3. **Переходы между этапами** автоматические
4. **Job-id** детерминированные и без дублей
5. **Время** везде в UTC
6. **Один таймер** на chat+deal

## 🆘 Если что-то не работает

1. Проверить логи на ошибки импорта
2. Убедиться, что БД обновлена
3. Проверить, что все файлы созданы/обновлены
4. Убедиться, что планировщик запущен
5. Проверить job в планировщике: `scheduler.get_jobs()`
