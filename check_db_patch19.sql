-- ПАТЧ №19 — Проверка состояния базы данных
-- Проверяем, что все поля и таблицы существуют

-- 1. Проверяем структуру таблицы deals
PRAGMA table_info(deals);

-- 2. Проверяем структуру таблицы deal_rounds
PRAGMA table_info(deal_rounds);

-- 3. Проверяем структуру таблицы system_settings
PRAGMA table_info(system_settings);

-- 4. Проверяем структуру таблицы ui_timer_messages
PRAGMA table_info(ui_timer_messages);

-- 5. Проверяем существующие сделки
SELECT id, deal_no, status, stage1_deadline_at, stage1_finished, is_paid FROM deals LIMIT 5;

-- 6. Проверяем существующие раунды
SELECT * FROM deal_rounds LIMIT 5;

-- 7. Проверяем настройки
SELECT * FROM system_settings LIMIT 1;

-- 8. Проверяем, что все таблицы существуют
SELECT name FROM sqlite_master WHERE type='table' AND name IN ('deals', 'deal_rounds', 'system_settings', 'ui_timer_messages');

-- 9. Проверяем индексы
SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_%';

-- 10. Проверяем, что все поля имеют правильные значения по умолчанию
SELECT 
    COUNT(*) as total_deals,
    SUM(CASE WHEN stage1_finished IS NULL THEN 1 ELSE 0 END) as deals_without_stage1_finished,
    SUM(CASE WHEN is_paid IS NULL THEN 1 ELSE 0 END) as deals_without_is_paid
FROM deals;
