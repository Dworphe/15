-- ПАТЧ №2 — Этап 1: инфраструктура и аукцион до определения победителя
-- Выполнить на существующей БД для добавления новых полей

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

CREATE INDEX IF NOT EXISTS ix_deal_rounds_deal_id ON deal_rounds(deal_id);
CREATE INDEX IF NOT EXISTS ix_deal_rounds_round_number ON deal_rounds(round_number);

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

CREATE INDEX IF NOT EXISTS ix_bids_deal_round_user ON bids(deal_id, round_number, user_id);
CREATE INDEX IF NOT EXISTS ix_bids_deal_round_pct ON bids(deal_id, round_number, pct);

-- Создаем уникальные ограничения для ставок
CREATE UNIQUE INDEX IF NOT EXISTS uq_bids_deal_round_user ON bids(deal_id, round_number, user_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_bids_deal_round_pct ON bids(deal_id, round_number, pct);

-- Создаем индекс для сделок по типу и статусу
CREATE INDEX IF NOT EXISTS ix_deals_type_status ON deals(deal_type, status);

-- Обновляем существующие записи (если есть)
UPDATE deals SET deal_type = 'SELL' WHERE deal_type IS NULL;
UPDATE users SET balance_usdt = 0.00 WHERE balance_usdt IS NULL;
UPDATE users SET reserved_usdt = 0.00 WHERE reserved_usdt IS NULL;
