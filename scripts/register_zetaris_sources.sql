-- =============================================================================
-- Rind: register the five customer-success systems in Zetaris.
--
-- Each system is its own Lightning database (they are separate systems in real
-- life: CRM, product analytics, help desk, billing, people directory). Each one
-- serves a JSON export over HTTPS, registered as a Lightning REST table, then
-- flattened by a SchemaStore view in the shared `rind` container.
--
-- {{DATA_BASE_URL}} is replaced by scripts/setup_zetaris.py with RIND_DATA_BASE_URL
-- from your .env (e.g. https://raw.githubusercontent.com/<you>/rind/main/data/sources).
-- If Zetaris does not parse raw.githubusercontent.com (served as text/plain), use the
-- jsDelivr mirror instead: https://cdn.jsdelivr.net/gh/<you>/rind@main/data/sources
--
-- Shape: top-level object with a `data` array of flat objects (the proven
-- "explode(<array_field>)" pattern from the Zetaris getting-started repo).
-- Run one statement at a time (setup_zetaris.py does this for you).
-- =============================================================================

CREATE LIGHTNING DATABASE RIND_CRM DESCRIBE BY "Rind demo CRM - accounts, health score, renewals";

CREATE LIGHTNING DATABASE RIND_USAGE DESCRIBE BY "Rind demo product analytics - weekly active users";

CREATE LIGHTNING DATABASE RIND_SUPPORT DESCRIBE BY "Rind demo help desk - support tickets";

CREATE LIGHTNING DATABASE RIND_BILLING DESCRIBE BY "Rind demo billing - invoices and payments";

CREATE LIGHTNING DATABASE RIND_CONTACTS DESCRIBE BY "Rind demo people directory - contacts and job changes";

CREATE SCHEMASTORE CONTAINER rind;

CREATE LIGHTNING REST TABLE crm_export FROM RIND_CRM REQUEST(
    endpoint "{{DATA_BASE_URL}}/crm_accounts.json",
    method "get",
    response_type "json",
    http_encoding "URLENCODED"
) HEADER (
) BODY ();

CREATE LIGHTNING REST TABLE usage_export FROM RIND_USAGE REQUEST(
    endpoint "{{DATA_BASE_URL}}/product_usage.json",
    method "get",
    response_type "json",
    http_encoding "URLENCODED"
) HEADER (
) BODY ();

CREATE LIGHTNING REST TABLE tickets_export FROM RIND_SUPPORT REQUEST(
    endpoint "{{DATA_BASE_URL}}/support_tickets.json",
    method "get",
    response_type "json",
    http_encoding "URLENCODED"
) HEADER (
) BODY ();

CREATE LIGHTNING REST TABLE invoices_export FROM RIND_BILLING REQUEST(
    endpoint "{{DATA_BASE_URL}}/billing_invoices.json",
    method "get",
    response_type "json",
    http_encoding "URLENCODED"
) HEADER (
) BODY ();

CREATE LIGHTNING REST TABLE contacts_export FROM RIND_CONTACTS REQUEST(
    endpoint "{{DATA_BASE_URL}}/contacts.json",
    method "get",
    response_type "json",
    http_encoding "URLENCODED"
) HEADER (
) BODY ();

CREATE SCHEMASTORE VIEW crm_accounts WITH CONTAINER rind AS
SELECT
    r.account_id, r.account_name, r.domain, r.billing_customer_no, r.industry, r.segment,
    CAST(r.arr_usd AS INT) AS arr_usd, CAST(r.seats_purchased AS INT) AS seats_purchased,
    r.renewal_date, r.csm_owner, CAST(r.health_score AS INT) AS health_score, r.health_color,
    r.health_updated_on, CAST(r.nps_last AS INT) AS nps_last, r.nps_date, r.last_csm_touch, r.csm_note
FROM rind_crm.crm_export
LATERAL VIEW explode(data) AS r;

CREATE SCHEMASTORE VIEW product_usage WITH CONTAINER rind AS
SELECT
    u.account_ref, u.week_start, CAST(u.active_users AS INT) AS active_users,
    CAST(u.seats_purchased AS INT) AS seats_purchased, CAST(u.sessions AS INT) AS sessions,
    CAST(u.workflows_run AS INT) AS workflows_run
FROM rind_usage.usage_export
LATERAL VIEW explode(data) AS u;

CREATE SCHEMASTORE VIEW support_tickets WITH CONTAINER rind AS
SELECT
    t.ticket_id, t.org_domain, t.opened_at, t.priority, t.status, t.subject,
    CAST(t.sentiment AS DOUBLE) AS sentiment, t.solved_at,
    CAST(t.first_response_hours AS DOUBLE) AS first_response_hours
FROM rind_support.tickets_export
LATERAL VIEW explode(data) AS t;

CREATE SCHEMASTORE VIEW billing_invoices WITH CONTAINER rind AS
SELECT
    i.invoice_id, i.customer_no, i.legal_name, i.issued_on, i.due_on,
    CAST(i.amount_usd AS INT) AS amount_usd, i.paid_on, i.status
FROM rind_billing.invoices_export
LATERAL VIEW explode(data) AS i;

CREATE SCHEMASTORE VIEW contacts WITH CONTAINER rind AS
SELECT
    c.contact_id, c.company_domain, c.full_name, c.title, c.role, c.status,
    c.changed_on, c.new_company, c.last_engaged_on
FROM rind_contacts.contacts_export
LATERAL VIEW explode(data) AS c;

-- Federated view: one query across CRM + billing + help desk, three different
-- systems with three different customer keys (account_id, customer_no, domain).
CREATE SCHEMASTORE VIEW account_360 WITH CONTAINER rind AS
SELECT
    a.account_id, a.account_name, a.arr_usd, a.renewal_date, a.health_score, a.health_color,
    b.overdue_invoices, s.open_high_tickets
FROM rind.crm_accounts a
LEFT JOIN (
    SELECT customer_no, SUM(CASE WHEN status = 'overdue' THEN 1 ELSE 0 END) AS overdue_invoices
    FROM rind.billing_invoices GROUP BY customer_no
) b ON a.billing_customer_no = b.customer_no
LEFT JOIN (
    SELECT org_domain, SUM(CASE WHEN status <> 'solved' AND priority IN ('high', 'urgent') THEN 1 ELSE 0 END) AS open_high_tickets
    FROM rind.support_tickets GROUP BY org_domain
) s ON a.domain = s.org_domain;

-- Speed: REST tables re-fetch on every query unless cached (session cache).
CACHE TABLE rind.product_usage;
