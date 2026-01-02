# Aurora Gadgets Customer Service Key Data Template

This template captures the canonical facts the pre-cleaner can stitch into customer emails. Each entry is a key/value pair that supports lookups by keyword, key code, or sender email. Update these values whenever policies change—the verification layer compares the cleaned draft against this table to ensure nothing hallucinated.

| Key | Value | Notes |
| --- | ----- | ----- |
| company_name | Aurora Gadgets | Brand name that should appear unchanged in cleaned drafts. |
| founded_year | 1990 | Required for regression coverage. |
| headquarters | Helsinki, Finland | City and country for location-related questions. |
| support_hours | Monday to Friday 09:00–17:00 EET | Publish as-is for time-sensitive escalations. |
| warranty_policy | Our warranty policy covers every Aurora device for two full years. | Inject when customers reference warranties. |
| return_policy | Customers may return unused products within 30 days for a full refund. | Keep wording precise; verification checks it verbatim. |
| shipping_time | Orders ship worldwide and arrive within 5–7 business days. | Mention when delivery windows are requested. |
| loyalty_program | Aurora Rewards grants points on every purchase and perks for loyal customers. | Included whenever loyalty perks are queried. |
| support_email | support@auroragadgets.example | Used when routing customers to direct contact. |
| premium_support | Business customers can opt into premium support with a four-hour SLA. | Referenced by enterprise accounts. |
| key_code_AG-445 | Our warranty policy covers every Aurora device for two full years. | Canonical payload when key code AG-445 appears in the email. |

> Keep this template updated whenever policies change; automated enrichment and verification derive their ground truth from these values. Add new rows for any additional key codes or sender-specific data you plan to confirm.
