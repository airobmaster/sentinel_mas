---
doc_id: AML-UK
title: UK AML Investigation Procedures
version: "3.2"
legal_entity: UK
---

# UK AML Investigation Procedures

Synthetic procedures for the UK legal entity in the Sentinel proof of concept.

## 4.1 Alert triage and investigation lanes
Every transaction-monitoring alert is investigated in a fast lane or a full lane. The full lane is mandatory when the customer is rated high risk, the alert carries a sanctions or PEP indicator, the alerted amount is 50,000 GBP or more, the customer had two or more alerts in the last twelve months, the scenario is network-type, or the alert covers more than one customer. All other alerts use the fast lane.

## 4.2 Evidence standard
Every statement in a case rationale must reference the record it relies on (transaction, customer profile, list entry, article, note or policy section). Statements that a check found nothing must reference the check. Unreferenced statements are not accepted at quality assurance.

## 4.3 Cash deposits below the reporting threshold
The cash reporting threshold is 10,000 GBP. A series of three or more cash deposits between 9,000 and 9,999 GBP within thirty days indicates possible structuring and must be compared with the customer's declared monthly cash. Deposits made at several branches increase the concern. Where the declared cash activity of a cash-intensive business covers the deposits and they are made at the customer's usual branch, the investigator may close the alert as a known business pattern. Otherwise the alert is escalated.

## 4.4 Rapid movement of funds
Credits of 5,000 GBP or more that are paid away within forty-eight hours, in two or more instances, must be escalated unless documented transactions (for example a property completion) explain them.

## 4.5 Mule activity and shared devices
Customers who share a device with other customers under investigation, or who belong to a community with a mule score of 0.6 or higher, must be investigated in the full lane, and the connected customers must be listed in the rationale. Confirmed mule activity is escalated and the connected accounts are referred for review.

## 4.6 Sanctions and PEP screening
A sanctions match is a true match only when the date of birth matches and the nationality is consistent. True matches are escalated immediately and the account is restricted by the sanctions team. Name-only matches with a different date of birth are discounted and recorded with the reason. PEP customers with credits that their declared income does not explain are escalated; if the explanation is plausible but undocumented, a request for information is raised.

## 4.7 Requests for information
A request for information is used when activity may be legitimate but a material fact (source of funds, business purpose or counterparty relationship) is undocumented. The request must never reveal or hint at suspicion (tipping-off prohibition).

## 4.8 Dispositions
The investigator closes, escalates or requests information. Escalated cases are passed to the Money Laundering Reporting Officer, who decides whether to file a suspicious activity report. Agents and automated tools may recommend a disposition but may never close, escalate or file.
