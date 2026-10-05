-- Sentinel synthetic data schemas (TDD §3.3). Recreated on every `sentinel data load`.
-- The LangGraph checkpoint tables (schema `sentinel`) are not touched.

DROP SCHEMA IF EXISTS core, crm, lake, screening, cases CASCADE;
CREATE SCHEMA core;
CREATE SCHEMA crm;
CREATE SCHEMA lake;
CREATE SCHEMA screening;
CREATE SCHEMA cases;

CREATE TABLE core.customers (
    customer_id              text PRIMARY KEY,
    legal_entity             text NOT NULL,
    name                     text NOT NULL,           -- PII
    dob                      date,                    -- PII
    nationality              text,
    segment                  text NOT NULL,
    risk_rating              text NOT NULL,
    occupation               text,
    business_purpose         text,
    expected_monthly_credits numeric(14, 2),
    expected_monthly_cash    numeric(14, 2),
    prior_alerts_12m         int NOT NULL DEFAULT 0
);

CREATE TABLE core.accounts (
    account_id   text PRIMARY KEY,
    customer_id  text NOT NULL REFERENCES core.customers,
    legal_entity text NOT NULL,
    currency     text NOT NULL
);

CREATE TABLE crm.notes (
    note_id     text PRIMARY KEY,
    customer_id text NOT NULL REFERENCES core.customers,
    created_at  date NOT NULL,
    text        text NOT NULL
);

CREATE TABLE lake.transactions (
    txn_id               text PRIMARY KEY,
    account_id           text NOT NULL REFERENCES core.accounts,
    txn_date             date NOT NULL,
    direction            text NOT NULL CHECK (direction IN ('credit', 'debit')),
    amount               numeric(14, 2) NOT NULL,
    currency             text NOT NULL,
    channel              text NOT NULL,
    branch               text,
    counterparty         text,
    counterparty_country text,
    reference            text
);
CREATE INDEX transactions_account_date ON lake.transactions (account_id, txn_date);

CREATE TABLE screening.sanctions_list (
    entry_id    text PRIMARY KEY,
    list_name   text NOT NULL,
    name        text NOT NULL,
    dob         date,
    nationality text,
    programme   text
);

CREATE TABLE screening.pep_list (
    entry_id    text PRIMARY KEY,
    list_name   text NOT NULL,
    name        text NOT NULL,
    dob         date,
    nationality text,
    position    text
);

CREATE TABLE screening.adverse_media (
    article_id   text PRIMARY KEY,
    published_at date NOT NULL,
    headline     text NOT NULL,
    text         text NOT NULL
);

CREATE TABLE cases.alerts (
    case_id      text PRIMARY KEY,
    legal_entity text NOT NULL,
    customer_id  text NOT NULL REFERENCES core.customers,
    alert        jsonb NOT NULL,
    expected     jsonb,                                  -- ground truth for evaluation; never shown to agents
    status       text NOT NULL DEFAULT 'new'
);

CREATE TABLE cases.history (
    case_id     text PRIMARY KEY,
    customer_id text NOT NULL REFERENCES core.customers,
    disposition text NOT NULL,
    closed_at   date NOT NULL
);
