-- ПАТЧ 23: поля привязки сообщения и дедлайн для таймера
-- Добавляем поля для безопасного countdown с привязкой к UI

-- Поле для ID чата админа
ALTER TABLE trades ADD COLUMN IF NOT EXISTS admin_chat_id INTEGER;

-- Поле для ID сообщения для редактирования
ALTER TABLE trades ADD COLUMN IF NOT EXISTS admin_message_id INTEGER;

-- Поле для дедлайна таймера (ISO8601 UTC)
ALTER TABLE trades ADD COLUMN IF NOT EXISTS countdown_until TEXT;

-- Создаем индексы для быстрого поиска активных таймеров
CREATE INDEX IF NOT EXISTS idx_trades_countdown_active 
ON trades(admin_chat_id, admin_message_id, countdown_until) 
WHERE admin_chat_id IS NOT NULL 
  AND admin_message_id IS NOT NULL 
  AND countdown_until IS NOT NULL;
