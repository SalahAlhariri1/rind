# Zetaris Unified Semantic Layer report

Engine: **local DuckDB copy (sample mode)** · generated 2026-10-27T06:28:27+00:00 by `scripts/zetaris_usl_report.py`. Every result below was produced by running the SQL shown; nothing is typed in by hand.

USL definition (DDL): `scripts/register_zetaris_sources.sql` (5 Lightning databases, 5 REST tables, 5 SchemaStore views and the federated `rind.account_360` view, all in container `rind`) and `scripts/zetaris_quality_rules.sql` (the `rind.dq_*` data quality views, one per rule).

**Summary:** Sources: 5/5 · exploratory queries: 15/15 · USL queries: 3/3 · data quality rules passed: 8/8 · hand-coded match: 60/60

## 1. Sources onboarded and verified

| System | USL view | Rows | Status |
|---|---|---|---|
| CRM | `rind.crm_accounts` | 60 | PASS |
| Product usage | `rind.product_usage` | 1920 | PASS |
| Support desk | `rind.support_tickets` | 205 | PASS |
| Billing | `rind.billing_invoices` | 240 | PASS |
| Contacts | `rind.contacts` | 182 | PASS |

## 2. Exploratory questions, 3 per source

### CRM

**Q: How is the book of business split by CRM health colour?**

```sql
SELECT health_color, COUNT(*) AS accounts, SUM(arr_usd) AS arr_usd FROM rind.crm_accounts GROUP BY health_color ORDER BY accounts DESC
```

| health_color | accounts | arr_usd |
|---|---|---|
| green | 44 | 2713300 |
| amber | 11 | 561900 |
| red | 5 | 171700 |

**Q: Which green accounts renew in the next 90 days?**

```sql
SELECT account_name, health_score, renewal_date, arr_usd FROM rind.crm_accounts WHERE health_color = 'green' AND renewal_date <= '2027-01-17' ORDER BY renewal_date
```

| account_name | health_score | renewal_date | arr_usd |
|---|---|---|---|
| Maplecrest Logistics | 80 | 2026-11-22 | 170900 |
| Clearwater Fabrication | 80 | 2026-11-29 | 18900 |
| Eastvale Power | 89 | 2026-12-02 | 59300 |
| Oakhaven Labs | 86 | 2026-12-07 | 7100 |
| Hollowell Power | 77 | 2026-12-15 | 24400 |
| Juniperhill Hotels | 83 | 2026-12-22 | 232000 |
| Larkspur Analytics | 88 | 2027-01-05 | 23900 |
| Bluegate Academy | 72 | 2027-01-07 | 86600 |

_…2 more rows_

**Q: How old are the health scores the CSM is relying on?**

```sql
SELECT MIN(health_updated_on) AS oldest_score, MAX(health_updated_on) AS newest_score, COUNT(*) AS accounts FROM rind.crm_accounts
```

| oldest_score | newest_score | accounts |
|---|---|---|
| 2026-09-09 | 2026-09-25 | 60 |

### Product usage

**Q: How much usage history is there?**

```sql
SELECT COUNT(DISTINCT account_ref) AS accounts, COUNT(DISTINCT week_start) AS weeks, MIN(week_start) AS first_week, MAX(week_start) AS last_week FROM rind.product_usage
```

| accounts | weeks | first_week | last_week |
|---|---|---|---|
| 60 | 32 | 2025-06-30 | 2026-10-12 |

**Q: Total weekly active users across all customers, last 6 weeks**

```sql
SELECT week_start, SUM(active_users) AS active_users FROM rind.product_usage GROUP BY week_start ORDER BY week_start DESC LIMIT 6
```

| week_start | active_users |
|---|---|
| 2026-10-12 | 6947 |
| 2026-10-05 | 6869 |
| 2026-09-28 | 6974 |
| 2026-09-21 | 7035 |
| 2026-09-14 | 7139 |
| 2026-09-07 | 7287 |

**Q: Which customers use the fewest of the seats they pay for (latest week)?**

```sql
SELECT account_ref, week_start, active_users, seats_purchased, utilisation_pct FROM (SELECT account_ref, week_start, active_users, seats_purchased, ROUND(100.0 * active_users / seats_purchased, 1) AS utilisation_pct, DENSE_RANK() OVER (ORDER BY week_start DESC) AS week_rank FROM rind.product_usage) t WHERE week_rank = 1 ORDER BY utilisation_pct ASC LIMIT 5
```

| account_ref | week_start | active_users | seats_purchased | utilisation_pct |
|---|---|---|---|---|
| acc1044 | 2026-10-12 | 10 | 45 | 22.2 |
| acc1059 | 2026-10-12 | 14 | 57 | 24.6 |
| acc1011 | 2026-10-12 | 44 | 169 | 26.0 |
| acc1053 | 2026-10-12 | 22 | 76 | 28.9 |
| acc1036 | 2026-10-12 | 7 | 24 | 29.2 |

### Support desk

**Q: Ticket volume by priority and status**

```sql
SELECT priority, status, COUNT(*) AS tickets FROM rind.support_tickets GROUP BY priority, status ORDER BY tickets DESC
```

| priority | status | tickets |
|---|---|---|
| normal | solved | 115 |
| low | solved | 64 |
| urgent | open | 14 |
| high | open | 4 |
| urgent | solved | 3 |
| urgent | pending | 2 |
| high | pending | 2 |
| normal | pending | 1 |

**Q: Who has the most open high/urgent tickets?**

```sql
SELECT org_domain, COUNT(*) AS open_high_or_urgent FROM rind.support_tickets WHERE status <> 'solved' AND priority IN ('high', 'urgent') GROUP BY org_domain ORDER BY open_high_or_urgent DESC LIMIT 5
```

| org_domain | open_high_or_urgent |
|---|---|
| maplecrestlogistics.example | 4 |
| windwardlogistics.example | 4 |
| marigoldlogistics.example | 3 |
| larkspuranalytics.example | 3 |
| elmsteadanalytics.example | 3 |

**Q: How do sentiment and first response time vary by priority?**

```sql
SELECT priority, ROUND(AVG(sentiment), 2) AS avg_sentiment, ROUND(AVG(first_response_hours), 1) AS avg_first_response_hours, COUNT(*) AS tickets FROM rind.support_tickets GROUP BY priority ORDER BY avg_sentiment
```

| priority | avg_sentiment | avg_first_response_hours | tickets |
|---|---|---|---|
| high | -0.67 | 21.8 | 6 |
| urgent | -0.65 | 21.6 | 19 |
| low | 0.46 | 3.4 | 64 |
| normal | 0.46 | 4.4 | 116 |

### Billing

**Q: Invoices by status**

```sql
SELECT status, COUNT(*) AS invoices, SUM(amount_usd) AS amount_usd FROM rind.billing_invoices GROUP BY status ORDER BY invoices DESC
```

| status | invoices | amount_usd |
|---|---|---|
| paid | 231 | 3277200 |
| overdue | 9 | 169700 |

**Q: Largest overdue invoices**

```sql
SELECT invoice_id, legal_name, amount_usd, due_on FROM rind.billing_invoices WHERE status = 'overdue' ORDER BY amount_usd DESC LIMIT 5
```

| invoice_id | legal_name | amount_usd | due_on |
|---|---|---|---|
| INV-90188 | Maplecrest Logistics, LLC | 42725 | 2026-08-30 |
| INV-90072 | Foxhollow Retail, Corp. | 38275 | 2026-09-08 |
| INV-90232 | Bluegate Academy, Ltd. | 21650 | 2026-09-05 |
| INV-90044 | Cinderfield Growers, Ltd. | 16050 | 2026-08-31 |
| INV-90096 | Ambergate Freight, LLC | 15875 | 2026-09-02 |

**Q: Which customers habitually pay after the due date?**

```sql
SELECT customer_no, MAX(legal_name) AS legal_name, COUNT(*) AS paid_invoices, SUM(CASE WHEN paid_on > due_on THEN 1 ELSE 0 END) AS paid_late FROM rind.billing_invoices WHERE status = 'paid' GROUP BY customer_no ORDER BY paid_late DESC LIMIT 5
```

| customer_no | legal_name | paid_invoices | paid_late |
|---|---|---|---|
| C-84946 | Silverlake Insurance, Corp. | 3 | 3 |
| C-58717 | Ambergate Freight, LLC | 3 | 3 |
| C-87060 | Foxhollow Retail, Corp. | 3 | 3 |
| C-66207 | Mossbank College, Corp. | 4 | 0 |
| C-68183 | Cinderfield Growers, Ltd. | 3 | 0 |

### Contacts

**Q: Key contacts by role and status**

```sql
SELECT role, status, COUNT(*) AS contacts FROM rind.contacts GROUP BY role, status ORDER BY contacts DESC
```

| role | status | contacts |
|---|---|---|
| exec_sponsor | active | 60 |
| admin | active | 60 |
| champion | active | 52 |
| champion | left_company | 10 |

**Q: Which champions left, and where did they go?**

```sql
SELECT company_domain, full_name, title, changed_on, new_company FROM rind.contacts WHERE role = 'champion' AND status = 'left_company' ORDER BY changed_on DESC
```

| company_domain | full_name | title | changed_on | new_company |
|---|---|---|---|---|
| saltmarshdental.example | Anya Osei | VP Operations | 2026-10-06 | Elmstead Partners |
| bluegateacademy.example | Owen Holt | Head of Revenue Ops | 2026-10-05 | Westmarch Holdings |
| bramblewoodsystems.example | Ryan Duarte | IT Director | 2026-10-02 | Elmstead Group |
| tidewaterfarm.example | Malik Iyer | VP Operations | 2026-09-30 | Juniperhill Holdings |
| corvidfarm.example | Owen Lindqvist | Head of Revenue Ops | 2026-09-28 | Ashcombe Group |
| cinderfieldgrowers.example | Hugo Nakamura | Head of Revenue Ops | 2026-09-27 | Cedarline Holdings |
| clearwaterfabrication.example | Ines Osei | IT Director | 2026-09-13 | Highmoor Group |
| eastvalepower.example | Nadia Sandoval | Head of Revenue Ops | 2026-09-13 | Cedarline Holdings |

_…2 more rows_

**Q: Which customers have no active champion?**

```sql
SELECT company_domain, SUM(CASE WHEN role = 'champion' AND status = 'active' THEN 1 ELSE 0 END) AS active_champions FROM rind.contacts GROUP BY company_domain HAVING SUM(CASE WHEN role = 'champion' AND status = 'active' THEN 1 ELSE 0 END) = 0
```

| company_domain | active_champions |
|---|---|
| cinderfieldgrowers.example | 0 |
| bramblewoodsystems.example | 0 |
| clearwaterfabrication.example | 0 |
| eastvalepower.example | 0 |
| tidewaterfarm.example | 0 |
| copperlinehaulage.example | 0 |
| windwardlogistics.example | 0 |
| bluegateacademy.example | 0 |

## 3. The USL in action

These questions cross systems that use different customer keys (CRM `account_id`, billing `customer_no`, help-desk `org_domain`, directory `company_domain`). The USL joins them, so each answer is one query.

**Q: Watermelon candidates: green in the CRM, but red in billing or support (one query, three systems, three keys)**

```sql
SELECT account_name, health_score, arr_usd, overdue_invoices, open_high_tickets FROM rind.account_360 WHERE health_color = 'green' AND (overdue_invoices > 0 OR open_high_tickets > 0) ORDER BY arr_usd DESC
```

| account_name | health_score | arr_usd | overdue_invoices | open_high_tickets |
|---|---|---|---|---|
| Maplecrest Logistics | 80 | 170900 | 1 | 4 |
| Foxhollow Retail | 89 | 153100 | 1 | 0 |
| Bluegate Academy | 72 | 86600 | 1 | 0 |
| Ambergate Freight | 73 | 63500 | 1 | 0 |
| Eastvale Power | 89 | 59300 | 1 | 0 |
| Silverlake Insurance | 87 | 41000 | 1 | 0 |
| Larkspur Analytics | 88 | 23900 | 0 | 3 |
| Clearwater Fabrication | 80 | 18900 | 0 | 3 |

_…2 more rows_

**Q: Revenue exposure: how many accounts of each CRM colour have a red signal elsewhere?**

```sql
SELECT health_color, COUNT(*) AS accounts, SUM(arr_usd) AS arr_usd, SUM(CASE WHEN overdue_invoices > 0 OR open_high_tickets > 0 THEN 1 ELSE 0 END) AS with_red_signal_elsewhere FROM rind.account_360 GROUP BY health_color ORDER BY accounts DESC
```

| health_color | accounts | arr_usd | with_red_signal_elsewhere |
|---|---|---|---|
| green | 44 | 2713300 | 10 |
| amber | 11 | 561900 | 0 |
| red | 5 | 171700 | 4 |

**Q: Four systems in one query: green accounts whose champion has left**

```sql
SELECT a.account_name, a.health_score, c.full_name AS departed_champion, c.new_company, x.overdue_invoices, x.open_high_tickets FROM rind.crm_accounts a JOIN rind.contacts c ON c.company_domain = a.domain JOIN rind.account_360 x ON x.account_id = a.account_id WHERE a.health_color = 'green' AND c.role = 'champion' AND c.status = 'left_company' ORDER BY a.arr_usd DESC
```

| account_name | health_score | departed_champion | new_company | overdue_invoices | open_high_tickets |
|---|---|---|---|---|---|
| Corvid Farm Supply | 87 | Owen Lindqvist | Ashcombe Group | 0 | 0 |
| Bluegate Academy | 72 | Owen Holt | Westmarch Holdings | 1 | 0 |
| Eastvale Power | 89 | Nadia Sandoval | Cedarline Holdings | 1 | 0 |
| Saltmarsh Dental Group | 87 | Anya Osei | Elmstead Partners | 0 | 0 |
| Tidewater Farm Supply | 74 | Malik Iyer | Juniperhill Holdings | 0 | 0 |
| Clearwater Fabrication | 80 | Ines Osei | Highmoor Group | 0 | 3 |
| Windward Logistics | 86 | Grace Abara | Cedarline Partners | 0 | 4 |

## 4. Data quality rules

Defined in the USL as one SchemaStore view per rule, `rind.dq_*` (`scripts/zetaris_quality_rules.sql`). Each view was created and then queried in this run.

| rule_name | rule_text | checked | violations | result | ran_as |
|---|---|---|---|---|---|
| invoice_customer_in_crm | Every invoice belongs to a CRM account (billing customer_no = CRM billing_customer_no) | 240 | 0 | PASS | view `rind.dq_invoice_customer_in_crm` |
| ticket_domain_in_crm | Every support ticket belongs to a CRM account (help-desk org_domain = CRM domain) | 205 | 0 | PASS | view `rind.dq_ticket_domain_in_crm` |
| usage_ref_in_crm | Every usage row belongs to a CRM account (usage account_ref acc1047 = CRM ACC-1047) | 1920 | 0 | PASS | view `rind.dq_usage_ref_in_crm` |
| contact_domain_in_crm | Every key contact belongs to a CRM account (directory company_domain = CRM domain) | 182 | 0 | PASS | view `rind.dq_contact_domain_in_crm` |
| health_score_in_range | CRM health score is present and between 0 and 100 | 60 | 0 | PASS | view `rind.dq_health_score_in_range` |
| invoice_due_after_issue | No invoice is due before it was issued | 240 | 0 | PASS | view `rind.dq_invoice_due_after_issue` |
| overdue_invoice_unpaid | An invoice marked overdue has no payment date | 240 | 0 | PASS | view `rind.dq_overdue_invoice_unpaid` |
| usage_within_seats | Weekly active users never exceed seats purchased | 1920 | 0 | PASS | view `rind.dq_usage_within_seats` |

## 5. Bonus: hand-coded approach vs the USL

`handcoded_account_360()` in this script rebuilds `rind.account_360` without a semantic layer: it loads three exports, maps three different customer keys by hand, aggregates and left-joins in Python.

| | Hand-coded Python | Zetaris USL view |
|---|---|---|
| Lines of code | 23 | 13 (SQL) |
| Accounts with identical results | 60 of 60 | (reference) |
| Reusable by other queries and tools | No: only this script can use it | Yes: any SQL client, the agent, the data quality rules |
| Picks up new rows | Re-download and re-run | Next query reads the source |

