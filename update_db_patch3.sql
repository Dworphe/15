-- ПАТЧ №3 — Этап 2: выбор карты, резервы USDT, оплата админом и подтверждение трейдером
-- Выполнить на существующей БД для добавления новых полей

-- Добавляем новые поля в таблицу deals для Этапов A и B
ALTER TABLE deals ADD COLUMN pay_mins INTEGER DEFAULT 20;
ALTER TABLE deals ADD COLUMN confirm_mins INTEGER DEFAULT 10;
ALTER TABLE deals ADD COLUMN confirm_deadline_at TIMESTAMP NULL;

-- pay_deadline_at уже есть; если нет — добавь:
-- ALTER TABLE deals ADD COLUMN pay_deadline_at TIMESTAMP NULL;

-- Обновляем существующие записи (если есть)
UPDATE deals SET pay_mins = 20 WHERE pay_mins IS NULL;
UPDATE deals SET confirm_mins = 10 WHERE confirm_mins IS NULL;
