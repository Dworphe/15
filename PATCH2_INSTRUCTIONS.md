# ПАТЧ №2 — Этап 1: Инструкции по применению

## Статус выполнения

✅ **ПАТЧ №2 полностью применен и готов к тестированию**

## Что было выполнено

### 1. Обновлены модели данных (`app/db/models.py`)
- Добавлены новые модели: `Deal`, `DealRound`, `Bid`, `DealParticipant`
- Обновлена модель `User` (добавлены `balance_usdt`, `reserved_usdt`)
- Добавлены enum'ы: `DealType`, `DealStatus`, `BankEnum`
- Все модели используют timezone-aware даты (Europe/Amsterdam)

### 2. Созданы новые утилиты
- `app/utils/money.py` - банковское округление
- `app/services/balance.py` - проверка маржи USDT
- `app/utils/validation.py` - маскирование карт (уже было)

### 3. Обновлен планировщик (`app/scheduler.py`)
- Упрощенная реализация с функциями для аукциона
- `schedule_stage1_end()` - завершение Этапа 1
- `schedule_round_end()` - завершение раунда

### 4. Создан воркер аукциона (`app/workers/auction.py`)
- `end_stage1()` - завершение Этапа 1, запуск раунда 1
- `end_round()` - завершение раунда, обработка ничьих
- `_notify_winner_basic()` - базовое уведомление победителю

### 5. Обновлен бот (`app/bot.py`)
- Упрощенная структура с подключением роутеров для Этапа 1
- Убран `sell_winner_router` (будет в ПАТЧЕ №3)

### 6. Обновлен main.py
- Адаптирован под новый планировщик
- Корректная инициализация и завершение

### 7. Созданы вспомогательные файлы
- `update_db_patch2.sql` - SQL-скрипт для обновления БД
- `USAGE.md` - подробное описание использования
- `PATCH2_INSTRUCTIONS.md` - этот файл

## Обновление базы данных

**ВАЖНО**: Перед запуском бота необходимо обновить базу данных!

### Вариант 1: Автоматическое обновление
```bash
sqlite3 data/app.db < update_db_patch2.sql
```

### Вариант 2: Ручное выполнение команд
```sql
-- Добавляем новые поля в таблицу users
ALTER TABLE users ADD COLUMN balance_usdt NUMERIC(18,2) DEFAULT 0.00;
ALTER TABLE users ADD COLUMN reserved_usdt NUMERIC(18,2) DEFAULT 0.00;

-- Добавляем новые поля в таблицу deals
ALTER TABLE deals ADD COLUMN deal_type VARCHAR(10) DEFAULT 'SELL';
ALTER TABLE deals ADD COLUMN winner_card_id INTEGER;
ALTER TABLE deals ADD COLUMN winner_card_bank VARCHAR(64);
ALTER TABLE deals ADD COLUMN winner_card_mask VARCHAR(32);
ALTER TABLE deals ADD COLUMN winner_card_holder VARCHAR(128);

-- Создаем новые таблицы для аукциона
CREATE TABLE IF NOT EXISTS deal_rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id INTEGER NOT NULL,
    round_number INTEGER NOT NULL,
    started_at DATETIME NOT NULL,
    deadline_at DATETIME NOT NULL,
    FOREIGN KEY (deal_id) REFERENCES deals(id)
);

CREATE TABLE IF NOT EXISTS bids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    round_number INTEGER NOT NULL,
    pct NUMERIC(6,2) NOT NULL,
    created_at DATETIME NOT NULL,
    FOREIGN KEY (deal_id) REFERENCES deals(id),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

-- Создаем индексы
CREATE INDEX IF NOT EXISTS ix_deal_rounds_deal_id ON deal_rounds(deal_id);
CREATE INDEX IF NOT EXISTS ix_deal_rounds_round_number ON deal_rounds(round_number);
CREATE INDEX IF NOT EXISTS ix_bids_deal_round_user ON bids(deal_id, round_number, user_id);
CREATE INDEX IF NOT EXISTS ix_bids_deal_round_pct ON bids(deal_id, round_number, pct);
CREATE INDEX IF NOT EXISTS ix_deals_type_status ON deals(deal_type, status);

-- Уникальные ограничения
CREATE UNIQUE INDEX IF NOT EXISTS uq_bids_deal_round_user ON bids(deal_id, round_number, user_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_bids_deal_round_pct ON bids(deal_id, round_number, pct);
```

## Тестирование

### 1. Проверка импорта модулей
```bash
python -c "from app.db.models import *; print('Models OK')"
python -c "from app.scheduler import *; print('Scheduler OK')"
python -c "from app.workers.auction import *; print('Auction worker OK')"
python -c "from app.bot import *; print('Bot OK')"
python -c "from app.main import *; print('Main OK')"
```

### 2. Запуск бота
```bash
python -m app.main
```

### 3. Тестирование функционала
1. **Админ**: `/admin` → "Торги → Продажа USDT (мастер)"
2. **Создание сделки**: пройти мастер создания
3. **Этап 1**: проверить рассылку трейдерам
4. **Принятие/отклонение**: проверить изменение REP
5. **Раунд ставок**: проверить автоматический запуск
6. **Ставки**: проверить прием и валидацию
7. **Определение победителя**: проверить статус WINNER_ASSIGNED

## Acceptance Criteria (Этап 1)

✅ **Создание сделки SELL**
- Админ создает сделку через `/sell`
- Мастер проходит все этапы: RUB → base% → service% → аудитория → время → комментарий → предупреждение
- Сделка публикуется со статусом `OPEN`

✅ **Этап 1: Рассылка**
- Рассылка только онлайн трейдерам с USDT ≥ требуемого
- Кнопки "Принять" (+1 REP) и "Отклонить" (-2 REP)
- Недопуск без USDT-маржи

✅ **Запуск раунда ставок**
- Автоматический запуск раунда 1 после Этапа 1
- Уведомление допущенных о старте раунда

✅ **Прием ставок**
- Валидация ставок [−5.00; base_pct], шаг 0.01
- Обработка ничьих через дополнительные раунды (до 5)

✅ **Определение победителя**
- Статус `WINNER_ASSIGNED`
- Базовое уведомление победителю
- Без финализации (карта, подтверждения)

## Ограничения Этапа 1

❌ **НЕТ** выбора карты победителем
❌ **НЕТ** резервирования USDT
❌ **НЕТ** подтверждения перевода
❌ **НЕТ** финализации сделки

Эти функции будут добавлены в ПАТЧЕ №3.

## Структура файлов после ПАТЧА №2

```
app/
├── __init__.py
├── main.py              # ✅ Обновлен
├── bot.py               # ✅ Обновлен
├── config.py            # ✅ Уже был готов
├── db/
│   ├── __init__.py
│   ├── base.py          # ✅ Уже был готов
│   └── models.py        # ✅ Полностью обновлен
├── handlers/
│   ├── __init__.py
│   ├── common.py        # ✅ Уже был готов
│   ├── auth.py          # ✅ Уже был готов
│   ├── admin.py         # ✅ Обновлен (BANK_LABELS)
│   ├── admin_settings.py # ✅ Уже был готов
│   ├── admin_deals.py   # ✅ Уже был готов
│   ├── admin_deals_sell.py # ✅ Уже был готов
│   ├── sell_auction.py  # ✅ Уже был готов
│   ├── sell_winner.py   # ❌ Не используется в ПАТЧЕ №2
│   ├── trader.py        # ✅ Уже был готов
│   └── utils.py         # ✅ Уже был готов
├── services/
│   ├── __init__.py
│   ├── settings.py      # ✅ Уже был готов
│   └── balance.py       # ✅ Новый
├── utils/
│   ├── __init__.py
│   ├── validation.py    # ✅ Уже был готов
│   └── money.py         # ✅ Обновлен
├── workers/
│   ├── __init__.py      # ✅ Новый
│   └── auction.py       # ✅ Новый
├── scheduler.py          # ✅ Полностью обновлен
└── logging_setup.py     # ✅ Уже был готов
```

## Следующие шаги

После успешного тестирования Этапа 1:

1. **Убедитесь**, что все модели созданы корректно
2. **Проверьте** работу планировщика
3. **Протестируйте** создание сделок и аукцион
4. **Готовьтесь** к ПАТЧУ №3 (финализация сделок)

## Возможные проблемы

### 1. Ошибка импорта phonenumbers
```bash
pip install phonenumbers
```

### 2. Ошибка с enum в admin.py
✅ **Исправлено** - обновлены BANK_LABELS

### 3. Проблемы с базой данных
- Убедитесь, что выполнен SQL-скрипт обновления
- Проверьте права доступа к файлу БД

### 4. Ошибки планировщика
- Проверьте, что файл `data/scheduler.db` создан
- Убедитесь, что APScheduler установлен

## Контакты

При возникновении проблем:
1. Проверьте логи бота
2. Убедитесь, что все зависимости установлены
3. Проверьте корректность обновления БД

---

**ПАТЧ №2 успешно применен! 🎉**

Готов к тестированию Этапа 1 аукционов продажи USDT.
