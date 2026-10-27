-- =============================================================================
-- Rind: data quality rules for the Unified Semantic Layer (USL).
--
-- One SchemaStore view per rule, all in container `rind`, all named dq_*.
-- Each view returns one row: the rule, how many rows it checked, and how many broke it.
-- A rule passes when violations = 0.
-- Run them with:  python scripts/zetaris_usl_report.py   (creates the views, then queries each one)
--
-- Why these rules: Rind joins five systems that each use a different customer key
-- (CRM account_id, usage account_ref, billing customer_no, help-desk org_domain,
-- directory company_domain). If a key stops matching, an account silently loses
-- its evidence and could look healthy. These rules catch that before the agents run.
--
-- Each rule reads each source view at most once: Zetaris REST tables flattened with
-- LATERAL VIEW explode cannot appear twice in one Spark plan.
-- =============================================================================

CREATE SCHEMASTORE VIEW dq_invoice_customer_in_crm WITH CONTAINER rind AS
SELECT 'invoice_customer_in_crm' AS rule_name,
       'Every invoice belongs to a CRM account (billing customer_no = CRM billing_customer_no)' AS rule_text,
       COUNT(*) AS checked,
       SUM(CASE WHEN a.account_id IS NULL THEN 1 ELSE 0 END) AS violations
FROM rind.billing_invoices i
LEFT JOIN rind.crm_accounts a ON a.billing_customer_no = i.customer_no;

CREATE SCHEMASTORE VIEW dq_ticket_domain_in_crm WITH CONTAINER rind AS
SELECT 'ticket_domain_in_crm' AS rule_name,
       'Every support ticket belongs to a CRM account (help-desk org_domain = CRM domain)' AS rule_text,
       COUNT(*) AS checked,
       SUM(CASE WHEN a.account_id IS NULL THEN 1 ELSE 0 END) AS violations
FROM rind.support_tickets t
LEFT JOIN rind.crm_accounts a ON a.domain = t.org_domain;

CREATE SCHEMASTORE VIEW dq_usage_ref_in_crm WITH CONTAINER rind AS
SELECT 'usage_ref_in_crm' AS rule_name,
       'Every usage row belongs to a CRM account (usage account_ref acc1047 = CRM ACC-1047)' AS rule_text,
       COUNT(*) AS checked,
       SUM(CASE WHEN a.account_id IS NULL THEN 1 ELSE 0 END) AS violations
FROM rind.product_usage u
LEFT JOIN rind.crm_accounts a ON CONCAT('acc', SUBSTRING(a.account_id, 5)) = u.account_ref;

CREATE SCHEMASTORE VIEW dq_contact_domain_in_crm WITH CONTAINER rind AS
SELECT 'contact_domain_in_crm' AS rule_name,
       'Every key contact belongs to a CRM account (directory company_domain = CRM domain)' AS rule_text,
       COUNT(*) AS checked,
       SUM(CASE WHEN a.account_id IS NULL THEN 1 ELSE 0 END) AS violations
FROM rind.contacts c
LEFT JOIN rind.crm_accounts a ON a.domain = c.company_domain;

CREATE SCHEMASTORE VIEW dq_health_score_in_range WITH CONTAINER rind AS
SELECT 'health_score_in_range' AS rule_name,
       'CRM health score is present and between 0 and 100' AS rule_text,
       COUNT(*) AS checked,
       SUM(CASE WHEN health_score IS NULL OR health_score < 0 OR health_score > 100 THEN 1 ELSE 0 END) AS violations
FROM rind.crm_accounts;

CREATE SCHEMASTORE VIEW dq_invoice_due_after_issue WITH CONTAINER rind AS
SELECT 'invoice_due_after_issue' AS rule_name,
       'No invoice is due before it was issued' AS rule_text,
       COUNT(*) AS checked,
       SUM(CASE WHEN due_on < issued_on THEN 1 ELSE 0 END) AS violations
FROM rind.billing_invoices;

CREATE SCHEMASTORE VIEW dq_overdue_invoice_unpaid WITH CONTAINER rind AS
SELECT 'overdue_invoice_unpaid' AS rule_name,
       'An invoice marked overdue has no payment date' AS rule_text,
       COUNT(*) AS checked,
       SUM(CASE WHEN status = 'overdue' AND COALESCE(CAST(paid_on AS STRING), '') NOT IN ('', 'null') THEN 1 ELSE 0 END) AS violations
FROM rind.billing_invoices;

CREATE SCHEMASTORE VIEW dq_usage_within_seats WITH CONTAINER rind AS
SELECT 'usage_within_seats' AS rule_name,
       'Weekly active users never exceed seats purchased' AS rule_text,
       COUNT(*) AS checked,
       SUM(CASE WHEN active_users > seats_purchased THEN 1 ELSE 0 END) AS violations
FROM rind.product_usage;
