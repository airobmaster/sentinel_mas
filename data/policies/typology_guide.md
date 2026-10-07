---
doc_id: TYP-GUIDE
title: AML Typology Guide
version: "1.4"
legal_entity: ALL
---

# AML Typology Guide

Synthetic guidance for the Sentinel proof of concept. Each section defines one typology, the red flags that indicate it, the facts that can explain it away, and the usual disposition. Investigators and agents cite this guide by section.

## 1 STRUCT — Structuring (smurfing)
**Definition.** Splitting cash into several deposits that each stay just below the cash reporting threshold, so that no single deposit is reported.
**Red flags.** Three or more cash deposits within about 10% below the threshold inside a short window; deposits made at several different branches; cash far above the customer's declared cash activity; funds moved out soon after the deposits.
**Mitigating facts.** A documented cash-intensive business whose declared monthly cash covers the deposits, with deposits made at its usual branch; a one-off, documented cash sale.
**Usual disposition.** Escalate (reason STRUCTURING_CONFIRMED) unless the profile and documentation explain both the amounts and the pattern.

## 2 PASSTHRU — Pass-through / rapid movement of funds
**Definition.** Funds that arrive and leave again within one or two days, leaving a low balance, so the account acts as a conduit.
**Red flags.** Two or more large credits each followed within 48 hours by debits of 80–100% of the amount; counterparties with no link to the customer's stated business; outgoing payments abroad.
**Mitigating facts.** Documented property or vehicle purchases; transfers between the customer's own accounts; a business model that genuinely settles on behalf of clients.
**Usual disposition.** Escalate (reason PASS_THROUGH).

## 3 MULE_NETWORK — Money mule activity and mule rings
**Definition.** Accounts used to receive and forward funds for others, often recruited in groups that share devices, addresses or contact details.
**Red flags.** Many small credits from unrelated senders followed by consolidated outflows (fan-in / fan-out); several customers sharing the same device; a tightly connected community with a high mule score; young customers with little income history.
**Mitigating facts.** Shared household devices with consistent, low-value family activity; documented rent sharing among a small, stable group.
**Usual disposition.** Escalate (reason MULE_NETWORK), and review the connected customers.

## 4 HRJ — High-risk jurisdictions
**Definition.** Payments to or from countries on the high-risk or sanctioned-jurisdiction list that the customer's profile does not explain.
**Red flags.** Repeated transfers to high-risk countries by a customer with no declared ties; round amounts; vague references.
**Mitigating facts.** Documented family support or trade with a stated, legitimate counterparty.
**Usual disposition.** Escalate, or request information (reason SOURCE_OF_FUNDS or COUNTERPARTY_RELATIONSHIP) when the purpose may be legitimate but is undocumented.

## 5 SANCTIONS — Sanctions screening matches
**Definition.** A customer or counterparty whose identifiers match a sanctions list entry.
**Red flags.** Name match supported by the same date of birth and a consistent nationality; adverse media linking the person to sanctions breaches.
**Mitigating facts.** A name-only match where date of birth or nationality differs: this is a false match and must be recorded as discounted with the reason.
**Usual disposition.** A true match is always escalated (reason SANCTIONS_TRUE_MATCH). A discounted false match with no other red flags is closed (reason FP_SCREENING_FALSE_MATCH).

## 6 PEP — Politically exposed persons
**Definition.** A customer who holds or held a prominent public function, or a close associate.
**Red flags.** Large or unusual credits that the declared income does not explain; payments labelled as gifts, loans or consultancy; adverse media about the person's conduct.
**Mitigating facts.** Documented source of funds and wealth that covers the activity.
**Usual disposition.** Escalate (reason PEP_UNEXPLAINED) when unexplained; request information (reason SOURCE_OF_FUNDS) when the explanation is plausible but undocumented.

## 7 BENIGN — Explained activity
**Definition.** Activity that looks unusual against the profile but is explained by documented facts.
**Examples.** Bonus payments from the customer's own employer; property sale proceeds paid by a solicitor and noted in advance by the customer; cash takings of a declared cash-intensive business banked at its usual branch.
**Usual disposition.** Close (reason FP_EXPLAINED_ACTIVITY or FP_KNOWN_BUSINESS_PATTERN). Request information only if a material fact is still undocumented.
