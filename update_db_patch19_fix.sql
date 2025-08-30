-- ПАТЧ №19 — Исправление для корректного перехода на второй этап
-- Обновление базы данных для поддержки новых полей

-- 1. Добавляем новые поля в таблицу deals (если их нет)
ALTER TABLE deals ADD COLUMN stage1_deadline_at DATETIME;
ALTER TABLE deals ADD COLUMN stage1_finished BOOLEAN DEFAULT 0 NOT NULL;
ALTER TABLE deals ADD COLUMN is_paid BOOLEAN DEFAULT 0 NOT NULL;

-- 2. Добавляем новые поля в таблицу deal_rounds (если их нет)
ALTER TABLE deal_rounds ADD COLUMN is_active BOOLEAN DEFAULT 0 NOT NULL;

-- 3. Добавляем новые поля в таблицу system_settings (если их нет)
ALTER TABLE system_settings ADD COLUMN stage1_secs_default INTEGER DEFAULT 60;
ALTER TABLE system_settings ADD COLUMN pay_mins_default INTEGER DEFAULT 20;

-- 4. Создаем таблицу для UI таймеров (если её нет)
CREATE TABLE IF NOT EXISTS ui_timer_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id BIGINT NOT NULL,
    deal_id INTEGER NOT NULL,
    kind VARCHAR(16) NOT NULL DEFAULT 'buy',
    message_id BIGINT NOT NULL,
    UNIQUE(chat_id, deal_id, kind)
);

-- 5. Создаем индексы для новых полей
CREATE INDEX IF NOT EXISTS ix_deals_stage1_deadline ON deals(stage1_deadline_at);
CREATE INDEX IF NOT EXISTS ix_deals_stage1_finished ON deals(stage1_finished);
CREATE INDEX IF NOT EXISTS ix_deals_is_paid ON deals(is_paid);
CREATE INDEX IF NOT EXISTS ix_deal_rounds_is_active ON deal_rounds(is_active);

-- 6. Обновляем существующие записи
UPDATE deals SET stage1_finished = 0 WHERE stage1_finished IS NULL;
UPDATE deals SET is_paid = 0 WHERE is_paid IS NULL;
UPDATE deal_rounds SET is_active = 0 WHERE is_active IS NULL;

-- 7. Обновляем настройки по умолчанию
UPDATE system_settings SET stage1_secs_default = 60 WHERE stage1_secs_default IS NULL;
UPDATE system_settings SET pay_mins_default = 20 WHERE pay_mins_default IS NULL;

-- 8. Проверяем, что все поля добавлены корректно
PRAGMA table_info(deals);
PRAGMA table_info(deal_rounds);
PRAGMA table_info(system_settings);
PRAGMA table_info(ui_timer_messages);

-- 9. Проверяем существующие сделки
SELECT id, deal_no, status, stage1_deadline_at, stage1_finished, is_paid FROM deals LIMIT 5;

-- 10. Проверяем существующие раунды
SELECT * FROM deal_rounds LIMIT 5;

-- 11. Проверяем настройки
SELECT * FROM system_settings LIMIT 1;
