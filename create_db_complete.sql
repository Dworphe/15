-- Полное создание базы данных для ПАТЧЕЙ №2 и №3
-- Выполнить для создания новой БД с нуля

-- Системные настройки
CREATE TABLE system_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rub_per_usdt NUMERIC(18, 6) DEFAULT 100.00 NOT NULL,
    service_fee_default_pct NUMERIC(6, 2) DEFAULT 1.00 NOT NULL,
    service_fee_min_pct NUMERIC(6, 2) DEFAULT 0.00 NOT NULL,
    service_fee_max_pct NUMERIC(6, 2) DEFAULT 3.00 NOT NULL,
    service_fee_overridable BOOLEAN DEFAULT 1 NOT NULL,
    round_secs_default INTEGER DEFAULT 20 NOT NULL,
    max_tie_rounds_default INTEGER DEFAULT 5 NOT NULL,
    updated_at DATETIME NOT NULL,
    updated_by INTEGER
);

-- Аудит
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_tg_id INTEGER,
    actor_role VARCHAR(32),
    action VARCHAR(64) NOT NULL,
    entity VARCHAR(64) NOT NULL,
    "before" TEXT,
    "after" TEXT,
    created_at DATETIME NOT NULL
);

-- Пользователи
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER UNIQUE,
    role VARCHAR(8) DEFAULT 'trader' NOT NULL,
    is_active BOOLEAN DEFAULT 0 NOT NULL,
    is_online BOOLEAN DEFAULT 0 NOT NULL,
    rep INTEGER DEFAULT 0 NOT NULL,
    balance_rub NUMERIC(18, 2) DEFAULT 0.00 NOT NULL,
    balance_usdt NUMERIC(18, 2) DEFAULT 0.00 NOT NULL,
    reserved_usdt NUMERIC(18, 2) DEFAULT 0.00 NOT NULL,
    created_at DATETIME NOT NULL
);

CREATE INDEX ix_users_tg_id ON users(tg_id);

-- Профили трейдеров
CREATE TABLE trader_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id VARCHAR(64) UNIQUE NOT NULL,
    nickname VARCHAR(64) NOT NULL,
    full_name VARCHAR(128) NOT NULL,
    phone VARCHAR(32) NOT NULL,
    referral_code VARCHAR(64),
    user_id INTEGER,
    created_at DATETIME NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX ix_trader_profiles_external_id ON trader_profiles(external_id);

-- Карты трейдеров
CREATE TABLE trader_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    bank VARCHAR(16) DEFAULT 'TBANK' NOT NULL,
    bank_other_name VARCHAR(64),
    card_number VARCHAR(32) NOT NULL,
    holder_name VARCHAR(128) NOT NULL,
    has_sbp BOOLEAN DEFAULT 0 NOT NULL,
    sbp_number VARCHAR(64),
    sbp_fullname VARCHAR(128),
    created_at DATETIME NOT NULL,
    FOREIGN KEY(profile_id) REFERENCES trader_profiles(id)
);

CREATE INDEX ix_trader_cards_profile_id ON trader_cards(profile_id);

-- Токены доступа
CREATE TABLE access_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    token VARCHAR(24) UNIQUE NOT NULL,
    created_at DATETIME NOT NULL,
    consumed_at DATETIME,
    FOREIGN KEY(profile_id) REFERENCES trader_profiles(id)
);

CREATE INDEX ix_access_tokens_profile_id ON access_tokens(profile_id);
CREATE INDEX ix_access_tokens_token ON access_tokens(token);

-- Сделки
CREATE TABLE deals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_no VARCHAR(32) UNIQUE NOT NULL,
    deal_type VARCHAR(10) DEFAULT 'SELL' NOT NULL,
    amount_rub NUMERIC(18, 2) NOT NULL,
    amount_usdt_snapshot NUMERIC(18, 2) NOT NULL,
    base_pct NUMERIC(6, 2) NOT NULL,
    service_fee_pct NUMERIC(6, 2) NOT NULL,
    winner_card_id INTEGER,
    winner_card_bank VARCHAR(64),
    winner_card_mask VARCHAR(32),
    winner_card_holder VARCHAR(128),
    audience_type VARCHAR(32) NOT NULL,
    audience_filter VARCHAR(256),
    comment VARCHAR(1000),
    warning_enabled BOOLEAN DEFAULT 0 NOT NULL,
    round_secs INTEGER DEFAULT 20 NOT NULL,
    max_tie_rounds INTEGER DEFAULT 5 NOT NULL,
    pay_mins INTEGER DEFAULT 20 NOT NULL,
    pay_deadline_at DATETIME,
    confirm_mins INTEGER DEFAULT 10 NOT NULL,
    confirm_deadline_at DATETIME,
    status VARCHAR(19) DEFAULT 'DRAFT' NOT NULL,
    winner_user_id INTEGER,
    winner_bid_pct NUMERIC(6, 2),
    created_at DATETIME NOT NULL,
    FOREIGN KEY(winner_user_id) REFERENCES users(id),
    FOREIGN KEY(winner_card_id) REFERENCES trader_cards(id)
);

CREATE INDEX ix_deals_deal_no ON deals(deal_no);
CREATE INDEX ix_deals_status ON deals(status);
CREATE INDEX ix_deals_type_status ON deals(deal_type, status);

-- Участники сделок
CREATE TABLE deal_participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    accepted BOOLEAN DEFAULT 0 NOT NULL,
    created_at DATETIME NOT NULL,
    FOREIGN KEY(deal_id) REFERENCES deals(id),
    FOREIGN KEY(user_id) REFERENCES users(id),
    UNIQUE(deal_id, user_id)
);

CREATE INDEX ix_deal_participants_deal_id ON deal_participants(deal_id);
CREATE INDEX ix_deal_participants_user_id ON deal_participants(user_id);

-- Раунды аукциона
CREATE TABLE deal_rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id INTEGER NOT NULL,
    round_number INTEGER NOT NULL,
    started_at DATETIME NOT NULL,
    deadline_at DATETIME NOT NULL,
    FOREIGN KEY(deal_id) REFERENCES deals(id)
);

CREATE INDEX ix_deal_rounds_deal_id ON deal_rounds(deal_id);
CREATE INDEX ix_deal_rounds_round_number ON deal_rounds(round_number);

-- Ставки
CREATE TABLE bids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    round_number INTEGER NOT NULL,
    pct NUMERIC(6, 2) NOT NULL,
    created_at DATETIME NOT NULL,
    FOREIGN KEY(deal_id) REFERENCES deals(id),
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX ix_bids_deal_id ON bids(deal_id);
CREATE INDEX ix_bids_round_number ON bids(round_number);
CREATE INDEX ix_bids_user_id ON bids(user_id);
CREATE INDEX ix_bids_deal_round_user ON bids(deal_id, round_number, user_id);
CREATE INDEX ix_bids_deal_round_pct ON bids(deal_id, round_number, pct);

-- Уникальные ограничения для ставок
CREATE UNIQUE INDEX uq_bids_deal_round_user ON bids(deal_id, round_number, user_id);

-- Вставляем базовые настройки
INSERT INTO system_settings (rub_per_usdt, service_fee_default_pct, service_fee_min_pct, service_fee_max_pct, service_fee_overridable, round_secs_default, max_tie_rounds_default, updated_at) 
VALUES (100.00, 1.00, 0.00, 3.00, 1, 20, 5, datetime('now'));

-- Создаем тестового админа (tg_id нужно заменить на реальный)
INSERT INTO users (tg_id, role, is_active, is_online, rep, balance_rub, balance_usdt, reserved_usdt, created_at)
VALUES (123456789, 'admin', 1, 1, 100, 10000.00, 1000.00, 0.00, datetime('now'));
