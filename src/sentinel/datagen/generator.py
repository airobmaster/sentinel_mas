"""Synthetic AML dataset with planted typologies and ground truth (TDD §3.5).

Deterministic for a given seed. Every alerted customer is dedicated to one typology, so each
alert has an unambiguous expected disposition. Output has the same shape as
data/fixtures/cases.json, plus accounts, alerts, ground_truth and case_history.
"""

import random
from collections import defaultdict
from datetime import date, timedelta

from faker import Faker
from rapidfuzz import fuzz, utils

AS_OF = date(2026, 3, 31)
HISTORY_DAYS = 120
LOOKBACK_DAYS = 90
CASH_THRESHOLD = 10_000

ENTITIES = {"UK": ("en_GB", "GBP", "GB"), "ES": ("es_ES", "EUR", "ES")}
BRANCHES = {
    "UK": ["Leeds Central", "Bradford", "Wakefield", "Huddersfield", "Manchester Piccadilly",
           "Birmingham New Street", "Bristol Temple", "Glasgow Central"],
    "ES": ["Madrid Sol", "Barcelona Diagonal", "Valencia Centro", "Sevilla Triana", "Bilbao Abando"],
}
HIGH_RISK_COUNTRIES = ["IR", "KP", "MM", "SY", "AF"]
FOREIGN_COUNTRIES = ["FR", "DE", "IE", "NL", "PT", "IT"]
RETAIL_JOBS = [("Teacher", 2600), ("Nurse", 2400), ("Software engineer", 4600), ("Shop assistant", 1800),
               ("Electrician", 3000), ("Accountant", 3800), ("Delivery driver", 2000), ("Civil servant", 2900),
               ("Chef", 2200), ("Pharmacist", 3500)]
INDUSTRIES = [("electronics wholesale", False), ("software consultancy", False), ("construction", False),
              ("logistics", False), ("cafe", True), ("market stall", True), ("car wash", True),
              ("convenience store", True)]
MERCHANTS = ["Supermarket", "Fuel station", "Restaurant", "Online retailer", "Pharmacy", "Coffee shop"]
SANCTION_PROGRAMMES = ["Russia sanctions (export controls evasion)", "Counter-terrorism financing",
                       "Cyber-related sanctions", "Global human rights sanctions"]
PEP_POSITIONS = ["Member of Parliament", "Regional government minister", "Mayor", "Senior judge",
                 "Board member, state-owned enterprise"]
NOISE_HEADLINES = ["Local resident fined for speeding", "Shop owner fined over licensing breach",
                   "Driver banned after motorway incident", "Councillor criticised over parking dispute"]

# typology: (number of alerts, expected disposition, acceptable dispositions)
TYPOLOGIES = {
    "STRUCT": (12, "escalate", ["escalate"]),
    "PASSTHRU": (8, "escalate", ["escalate"]),
    "MULE": (6, "escalate", ["escalate"]),
    "HRJ": (8, "escalate", ["escalate", "request_info"]),
    "SANCT_TRUE": (4, "escalate", ["escalate"]),
    "PEP": (5, "escalate", ["escalate", "request_info"]),
    "BENIGN_CASH_BUSINESS": (10, "close", ["close", "request_info"]),
    "BENIGN_PROPERTY_SALE": (10, "close", ["close", "request_info"]),
    "BENIGN_BONUS": (10, "close", ["close", "request_info"]),
    "SANCT_NEAR": (12, "close", ["close"]),
    "MULE_RING": (6, "escalate", ["escalate"]),  # planted last (see generate), so earlier data is unchanged
}
BACKGROUND_CUSTOMERS = 220
SHARED_HOUSEHOLD_DEVICES = 12  # benign pairs of background customers sharing one device


class Generator:
    def __init__(self, seed: int = 42):
        self.seed = seed
        self.rng = random.Random(seed)
        self.fakers = {e: Faker(loc) for e, (loc, _, _) in ENTITIES.items()}
        for i, f in enumerate(self.fakers.values()):
            f.seed_instance(seed + i)
        self.d: dict = {k: [] for k in ("customers", "accounts", "crm_notes", "sanctions_list", "pep_list",
                                         "adverse_media", "transactions", "alerts", "case_history", "device_links")}
        self.d["ground_truth"] = {}
        self.counters: dict[str, int] = defaultdict(int)
        self.profile: dict[str, dict] = {}  # private generation details per customer

    # --- helpers -------------------------------------------------------------------------------
    def next_id(self, prefix: str, width: int) -> str:
        self.counters[prefix] += 1
        return f"{prefix}{self.counters[prefix]:0{width}d}"

    def day(self, back_min: int, back_max: int) -> date:
        return AS_OF - timedelta(days=self.rng.randint(back_min, back_max))

    def txn(self, cust: dict, d: date, direction: str, amount: float, channel: str, reference: str,
            counterparty: str | None = None, branch: str | None = None, country: str | None = None,
            counterparty_account: str | None = None) -> str:
        txn_id = self.next_id("TXN-G", 6)
        self.d["transactions"].append({
            "txn_id": txn_id, "account_id": cust["account_ids"][0], "date": d.isoformat(),
            "direction": direction, "amount": round(amount, 2), "currency": self.profile[cust["customer_id"]]["currency"],
            "channel": channel, "branch": branch, "counterparty": counterparty,
            "counterparty_country": country, "counterparty_account": counterparty_account, "reference": reference,
        })
        return txn_id

    def amount_of(self, txn_id: str) -> float:
        return next(t["amount"] for t in reversed(self.d["transactions"]) if t["txn_id"] == txn_id)

    def link_device(self, cust: dict, device_id: str, device_type: str) -> None:
        self.d["device_links"].append({"customer_id": cust["customer_id"], "device_id": device_id,
                                       "device_type": device_type})

    def person(self, entity: str = "UK") -> str:
        f = self.fakers[entity]
        return f"{f.first_name()} {f.last_name()}"

    def note(self, cust: dict, d: date, text: str) -> None:
        self.d["crm_notes"].append({"note_id": self.next_id("CRM-G", 5), "customer_id": cust["customer_id"],
                                    "date": d.isoformat(), "text": text})

    def article(self, d: date, headline: str, text: str) -> None:
        self.d["adverse_media"].append({"article_id": self.next_id("MED-G", 4), "date": d.isoformat(),
                                        "headline": headline, "text": text})

    def age(self, cust: dict) -> int:
        return (AS_OF - date.fromisoformat(cust["dob"])).days // 365

    # --- customers and normal activity ---------------------------------------------------------
    def new_customer(self, segment: str | None = None, risk: str | None = None, cash_intensive: bool | None = None,
                     entity: str | None = None, job: tuple | None = None, min_age: int = 21, max_age: int = 75,
                     monthly_credits: int | None = None) -> dict:
        rng = self.rng
        entity = entity or rng.choices(["UK", "ES"], [0.8, 0.2])[0]
        f = self.fakers[entity]
        _, currency, nationality = ENTITIES[entity]
        segment = segment or rng.choices(["retail", "business"], [0.8, 0.2])[0]
        prof = {"currency": currency, "entity": entity, "segment": segment}
        if segment == "retail":
            title, base = job or rng.choice(RETAIL_JOBS)
            prof |= {"salary": round(base * rng.uniform(0.85, 1.2), -1), "employer": f.company()}
            occupation = title
            purpose = "Personal current account for salary and household spending"
            expected_credits = round(prof["salary"] * 1.1, -2)
            expected_cash = rng.choice([0, 0, 100, 200, 300])
        else:
            options = [i for i in INDUSTRIES if cash_intensive is None or i[1] == cash_intensive]
            industry, cash = rng.choice(options)
            company = f.company()
            prof |= {"industry": industry, "cash": cash, "company": company, "home_branch": rng.choice(BRANCHES[entity])}
            occupation = f"Director, {company} ({industry})"
            purpose = "Business account for trade receipts and supplier payments"
            expected_credits = monthly_credits or rng.randrange(20_000, 80_001, 1000)
            expected_cash = round(expected_credits * rng.uniform(0.5, 0.8), -3) if cash else 0
            prof["monthly_credits"] = expected_credits
        cust = {
            "customer_id": self.next_id("CUST-G", 4), "legal_entity": entity, "name": self.person(entity),
            "dob": f.date_of_birth(minimum_age=min_age, maximum_age=max_age).isoformat(),
            "nationality": nationality if rng.random() < 0.9 else rng.choice(FOREIGN_COUNTRIES),
            "segment": segment, "risk_rating": risk or rng.choices(["low", "medium", "high"], [0.6, 0.3, 0.1])[0],
            "occupation": occupation, "business_purpose": purpose,
            "expected_monthly_credits": float(expected_credits), "expected_monthly_cash": float(expected_cash),
            "prior_alerts_12m": 0, "account_ids": [self.next_id("ACC-G", 4)],
        }
        self.profile[cust["customer_id"]] = prof
        self.d["customers"].append(cust)
        self.d["accounts"].append({"account_id": cust["account_ids"][0], "customer_id": cust["customer_id"],
                                   "legal_entity": entity, "currency": currency})
        self.normal_activity(cust)
        return cust

    def months(self):
        start = AS_OF - timedelta(days=HISTORY_DAYS - 1)
        y, m = start.year, start.month
        while (y, m) <= (AS_OF.year, AS_OF.month):
            yield y, m
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    def in_window(self, d: date) -> bool:
        return AS_OF - timedelta(days=HISTORY_DAYS) < d <= AS_OF

    def normal_activity(self, cust: dict) -> None:
        rng, prof = self.rng, self.profile[cust["customer_id"]]
        for y, m in self.months():
            if prof["segment"] == "retail":
                for d, args in [
                    (date(y, m, rng.randint(25, 28)), ("credit", prof["salary"], "transfer", "Salary", prof["employer"])),
                    (date(y, m, rng.randint(1, 3)), ("debit", round(prof["salary"] * 0.3, -1), "direct_debit",
                                                      "Rent", "Lettings Agency")),
                ]:
                    if self.in_window(d):
                        self.txn(cust, d, *args)
                if rng.random() < 0.5 and self.in_window(d := date(y, m, rng.randint(5, 20))):
                    self.txn(cust, d, "debit", rng.uniform(20, 300), "transfer", "Transfer",
                             self.person(prof["entity"]))
                if cust["expected_monthly_cash"] and self.in_window(d := date(y, m, rng.randint(1, 28))):
                    self.txn(cust, d, "credit", rng.uniform(50, cust["expected_monthly_cash"]), "cash",
                             "Cash deposit", branch=rng.choice(BRANCHES[prof["entity"]]))
                for _ in range(rng.randint(8, 14)):
                    if self.in_window(d := date(y, m, rng.randint(1, 28))):
                        self.txn(cust, d, "debit", rng.uniform(5, 150), "card", "Card purchase", rng.choice(MERCHANTS))
            else:
                monthly = prof["monthly_credits"]
                cash_share = cust["expected_monthly_cash"] / monthly if monthly else 0
                for _ in range(rng.randint(4, 8)):
                    if self.in_window(d := date(y, m, rng.randint(1, 28))):
                        self.txn(cust, d, "credit", monthly * (1 - cash_share) / 6 * rng.uniform(0.5, 1.5),
                                 "transfer", f"Invoice {rng.randint(1000, 9999)}", self.fakers[prof["entity"]].company())
                for _ in range(rng.randint(3, 6)):
                    if self.in_window(d := date(y, m, rng.randint(1, 28))):
                        self.txn(cust, d, "debit", monthly * 0.7 / 4.5 * rng.uniform(0.5, 1.5), "transfer",
                                 "Supplier payment", self.fakers[prof["entity"]].company())
                if self.in_window(d := date(y, m, 28)):
                    self.txn(cust, d, "debit", monthly * 0.2, "transfer", "Payroll", "Payroll bureau")
                if prof["cash"]:
                    for _ in range(12):  # takings banked about three times a week
                        if self.in_window(d := date(y, m, rng.randint(1, 28))):
                            self.txn(cust, d, "credit", cust["expected_monthly_cash"] / 12 * rng.uniform(0.4, 1.2),
                                     "cash", "Cash takings", branch=prof["home_branch"])

    # --- alerts --------------------------------------------------------------------------------
    def alert(self, typology: str, cust: dict, scenario: tuple[str, str], triggering: list[str],
              score: tuple[float, float], **flags) -> None:
        case_id = self.next_id("CASE-G", 4)
        expected, acceptable = TYPOLOGIES[typology][1:]
        self.d["alerts"].append({
            "case_id": case_id, "alert_id": self.next_id("TM-G", 5), "legal_entity": cust["legal_entity"],
            "customer_id": cust["customer_id"], "account_ids": cust["account_ids"], "scenario_code": scenario[0],
            "scenario_name": scenario[1], "score": round(self.rng.uniform(*score), 2),
            "triggered_at": f"{AS_OF.isoformat()}T06:00:00Z", "lookback_days": LOOKBACK_DAYS,
            "triggering_txn_ids": triggering, "schema_version": "1.0", **flags,
        })
        self.d["ground_truth"][case_id] = {"typology": typology, "expected": expected, "acceptable": acceptable}

    def noise_article(self, cust: dict) -> None:
        """Adverse media about a different person with the same name (different age and town)."""
        f = self.fakers[cust["legal_entity"]]
        other_age = self.age(cust) + self.rng.choice([-1, 1]) * self.rng.randint(15, 30)
        self.article(self.day(30, 400), self.rng.choice(NOISE_HEADLINES),
                     f"{cust['name']}, {max(other_age, 19)}, from {f.city()}, was fined after a hearing at the local court.")

    def plant_struct(self) -> None:
        c = self.new_customer(segment="retail", risk=self.rng.choice(["low", "medium"]))
        c["expected_monthly_cash"] = 200.0
        branches = BRANCHES[c["legal_entity"]]
        days = sorted(self.rng.sample(range(5, 45), self.rng.randint(4, 7)), reverse=True)
        ids = [self.txn(c, AS_OF - timedelta(days=d), "credit", self.rng.randrange(9000, 9951, 50), "cash",
                        "Cash deposit", branch=self.rng.choice(branches)) for d in days]
        if self.rng.random() < 0.5:
            total = sum(t["amount"] for t in self.d["transactions"][-len(ids):])
            self.txn(c, AS_OF - timedelta(days=max(days[-1] - 2, 1)), "debit", total * self.rng.uniform(0.7, 0.9),
                     "transfer", "Payment", f"ACC-EXT-{self.rng.randint(1000, 9999)}")
        self.alert("STRUCT", c, ("TM-STRUCT-01", "Cash deposits below reporting threshold"), ids, (0.7, 0.95))

    def plant_passthru(self) -> None:
        c = self.new_customer(segment=self.rng.choice(["retail", "business"]))
        ids = []
        for d in sorted(self.rng.sample(range(5, 80), self.rng.randint(3, 5)), reverse=True):
            amount = self.rng.uniform(15_000, 40_000)
            ids.append(self.txn(c, AS_OF - timedelta(days=d), "credit", amount, "transfer", "Transfer",
                                self.fakers["UK"].company()))
            ids.append(self.txn(c, AS_OF - timedelta(days=d - self.rng.randint(0, 2)), "debit",
                                amount * self.rng.uniform(0.9, 0.98), "transfer", "Payment",
                                self.fakers["UK"].company(), country=self.rng.choice([None, *FOREIGN_COUNTRIES])))
        self.alert("PASSTHRU", c, ("TM-PASSTHRU-01", "Rapid movement of funds through the account"), ids, (0.65, 0.9))

    def plant_mule(self) -> None:
        c = self.new_customer(segment="retail", risk="low", job=("Student", 900), min_age=18, max_age=24)
        start = self.rng.randint(15, 60)
        ids = [self.txn(c, AS_OF - timedelta(days=start - self.rng.randint(0, 9)), "credit",
                        self.rng.uniform(300, 1500), "transfer",
                        self.rng.choice(["Thanks", "Rent share", "Payment", "For you"]), self.person())
               for _ in range(self.rng.randint(8, 15))]
        total = sum(t["amount"] for t in self.d["transactions"][-len(ids):])
        beneficiary, country = self.fakers["UK"].company(), self.rng.choice([None, *FOREIGN_COUNTRIES])
        parts = self.rng.randint(1, 3)
        for i in range(parts):
            ids.append(self.txn(c, AS_OF - timedelta(days=start - 10 - i), "debit",
                                total * self.rng.uniform(0.9, 0.97) / parts, "transfer", "Payment", beneficiary,
                                country=country))
        self.alert("MULE", c, ("TM-MULE-01", "Many inbound payments from unrelated senders"), ids, (0.7, 0.95))

    def plant_hrj(self) -> None:
        c = self.new_customer(segment="retail", risk="low")
        ids = [self.txn(c, self.day(5, 80), "debit", self.rng.uniform(3000, 12_000), "transfer",
                        self.rng.choice(["Family support", "Payment", "Invoice"]), self.person(),
                        country=self.rng.choice(HIGH_RISK_COUNTRIES))
               for _ in range(self.rng.randint(3, 5))]
        self.alert("HRJ", c, ("TM-HRJ-01", "Transfers to a high-risk jurisdiction"), ids, (0.6, 0.85))

    def plant_sanct_true(self) -> None:
        c = self.new_customer(segment="business", cash_intensive=False)
        self.d["sanctions_list"].append({"list": "OFSI", "entry_id": self.next_id("OFSI-G", 4), "name": c["name"],
                                         "dob": c["dob"], "nationality": c["nationality"],
                                         "programme": self.rng.choice(SANCTION_PROGRAMMES)})
        company = self.profile[c["customer_id"]]["company"]
        self.article(self.day(20, 200), "Trader named in sanctions investigation",
                     f"{c['name']}, director of {company}, is among those named in an investigation "
                     "into breaches of financial sanctions.")
        for _ in range(2):
            self.txn(c, self.day(5, 80), "debit", self.rng.uniform(10_000, 30_000), "transfer", "Supplier payment",
                     self.fakers["UK"].company(), country=self.rng.choice(HIGH_RISK_COUNTRIES + FOREIGN_COUNTRIES))
        self.alert("SANCT_TRUE", c, ("TM-SCREEN-01", "Customer name screening hit"), [], (0.8, 0.99),
                   sanctions_indicator=True)

    def near_name(self, name: str) -> str:
        first, *rest = name.split()
        surname = rest[-1] if rest else first
        for _ in range(20):
            i = self.rng.randrange(1, len(surname))
            variant = self.rng.choice([surname[:i] + surname[i] + surname[i:],  # doubled letter
                                       surname + self.rng.choice(["s", "e", "son"]),
                                       surname[:i] + surname[i + 1:]])  # dropped letter
            candidate = " ".join([first, *rest[:-1], variant])
            if candidate != name and fuzz.token_sort_ratio(name, candidate, processor=utils.default_process) >= 88:
                return candidate
        return f"{name}s"

    def plant_sanct_near(self) -> None:
        c = self.new_customer()
        dob = date.fromisoformat(c["dob"]) - timedelta(days=365 * self.rng.randint(8, 30) + self.rng.randint(1, 300))
        self.d["sanctions_list"].append({"list": "OFSI", "entry_id": self.next_id("OFSI-G", 4),
                                         "name": self.near_name(c["name"]), "dob": dob.isoformat(),
                                         "nationality": self.rng.choice(FOREIGN_COUNTRIES + HIGH_RISK_COUNTRIES),
                                         "programme": self.rng.choice(SANCTION_PROGRAMMES)})
        self.alert("SANCT_NEAR", c, ("TM-SCREEN-01", "Customer name screening hit"), [], (0.5, 0.8),
                   sanctions_indicator=True)

    def plant_pep(self) -> None:
        c = self.new_customer(segment="retail", risk="high")
        position = self.rng.choice(PEP_POSITIONS)
        self.d["pep_list"].append({"list": "PEP", "entry_id": self.next_id("PEP-G", 4), "name": c["name"],
                                   "dob": c["dob"], "nationality": c["nationality"], "position": position})
        if self.rng.random() < 0.5:
            self.article(self.day(20, 200), "Questions raised over consultancy payments",
                         f"Opposition members have asked {c['name']}, {position.lower()}, to explain "
                         "consultancy fees received from a construction firm.")
        ids = [self.txn(c, self.day(5, 80), "credit", self.rng.uniform(20_000, 60_000), "transfer",
                        self.rng.choice(["Consultancy", "Gift", "Loan"]), self.fakers["UK"].company())
               for _ in range(self.rng.randint(2, 3))]
        self.alert("PEP", c, ("TM-HIGHVAL-01", "Large incoming credits compared with profile"), ids, (0.6, 0.9),
                   pep_indicator=True)

    def plant_cash_business(self) -> None:
        # Large enough that three or four ~9.5k deposits fit inside a normal month's takings.
        c = self.new_customer(segment="business", cash_intensive=True, risk=self.rng.choice(["low", "medium"]),
                              monthly_credits=self.rng.randrange(100_000, 140_001, 1000))
        prof = self.profile[c["customer_id"]]
        self.note(c, self.day(150, 700), f"Cash-intensive {prof['industry']}: takings are banked several times a "
                                         f"week at the {prof['home_branch']} branch.")
        # The large deposits replace ordinary takings from the same 30 days, so the month's cash
        # stays in line with the declared profile; only the deposit sizes look like structuring.
        recent = (AS_OF - timedelta(days=30)).isoformat()
        ordinary = [t for t in self.d["transactions"] if t["account_id"] == c["account_ids"][0]
                    and t["channel"] == "cash" and t["date"] > recent]
        ids = [self.txn(c, self.day(3, 30), "credit", self.rng.randrange(9000, 9951, 50), "cash", "Cash takings",
                        branch=prof["home_branch"]) for _ in range(self.rng.randint(3, 4))]
        to_remove, removed = sum(t["amount"] for t in self.d["transactions"][-len(ids):]), 0.0
        for t in ordinary:
            if removed >= to_remove:
                break
            self.d["transactions"].remove(t)
            removed += t["amount"]
        self.alert("BENIGN_CASH_BUSINESS", c, ("TM-STRUCT-01", "Cash deposits below reporting threshold"), ids,
                   (0.5, 0.75))

    def plant_property_sale(self) -> None:
        c = self.new_customer(segment="retail", risk="low")
        f = self.fakers[c["legal_entity"]]
        address, amount, when = f.street_address(), self.rng.randrange(150_000, 450_001, 500), self.rng.randint(10, 60)
        self.note(c, AS_OF - timedelta(days=when + self.rng.randint(20, 60)),
                  f"Customer advised they are selling their home at {address}; proceeds of about "
                  f"{amount:,} are expected on completion.")
        tid = self.txn(c, AS_OF - timedelta(days=when), "credit", amount, "transfer",
                       f"Completion - sale of {address}", f"{f.last_name()} & Co Solicitors LLP")
        if self.rng.random() < 0.4:
            self.noise_article(c)
        self.alert("BENIGN_PROPERTY_SALE", c, ("TM-HIGHVAL-01", "Large incoming credit compared with profile"),
                   [tid], (0.4, 0.7))

    def plant_bonus(self) -> None:
        c = self.new_customer(segment="retail", risk="low")
        prof = self.profile[c["customer_id"]]
        tid = self.txn(c, self.day(5, 30), "credit", prof["salary"] * self.rng.uniform(3, 6), "transfer",
                       "Annual bonus", prof["employer"])
        if self.rng.random() < 0.4:
            self.noise_article(c)
        self.alert("BENIGN_BONUS", c, ("TM-HIGHVAL-01", "Large incoming credit compared with profile"), [tid],
                   (0.4, 0.65))

    # --- lists, history and assembly -----------------------------------------------------------
    def filler_lists(self) -> None:
        for _ in range(40):
            self.d["sanctions_list"].append({
                "list": "OFSI", "entry_id": self.next_id("OFSI-G", 4), "name": self.person(self.rng.choice(["UK", "ES"])),
                "dob": self.fakers["UK"].date_of_birth(minimum_age=30, maximum_age=80).isoformat(),
                "nationality": self.rng.choice(FOREIGN_COUNTRIES + HIGH_RISK_COUNTRIES),
                "programme": self.rng.choice(SANCTION_PROGRAMMES)})
        for _ in range(30):
            self.d["pep_list"].append({
                "list": "PEP", "entry_id": self.next_id("PEP-G", 4), "name": self.person(self.rng.choice(["UK", "ES"])),
                "dob": self.fakers["UK"].date_of_birth(minimum_age=35, maximum_age=80).isoformat(),
                "nationality": self.rng.choice(["GB", "ES"]), "position": self.rng.choice(PEP_POSITIONS)})

    def case_history(self, customers: list[dict]) -> None:
        for c in customers:
            n = self.rng.choices([0, 1, 2], [0.85, 0.1, 0.05])[0]
            for _ in range(n):
                closed = self.day(20, 500)
                self.d["case_history"].append({"case_id": self.next_id("CASE-H", 4), "customer_id": c["customer_id"],
                                               "disposition": self.rng.choice(["close", "close", "escalate"]),
                                               "closed_at": closed.isoformat()})
                if (AS_OF - closed).days <= 365:
                    c["prior_alerts_12m"] += 1

    def plant_mule_ring(self) -> None:
        """5-9 young customers sharing devices: each receives small payments from unrelated people and
        forwards them to one aggregator member, who sends the total abroad."""
        rng = self.rng
        members = [self.new_customer(segment="retail", risk="low", entity="UK", job=("Student", 900),
                                     min_age=18, max_age=26) for _ in range(rng.randint(5, 9))]
        devices = [self.next_id("DEV-R", 3) for _ in range(rng.randint(1, 2))]
        for m in members:
            for device in devices:
                self.link_device(m, device, "mobile")
        aggregator, start = members[0], rng.randint(12, 40)
        forwarded, alerted = 0.0, rng.choice(members[1:])
        triggering: list[str] = []
        for m in members[1:]:
            credits = [self.txn(m, AS_OF - timedelta(days=start - rng.randint(0, 5)), "credit", rng.uniform(400, 1500),
                                "transfer", rng.choice(["Thanks", "Rent share", "Payment", "For you"]), self.person())
                       for _ in range(rng.randint(3, 6))]
            amount = sum(self.amount_of(t) for t in credits) * rng.uniform(0.9, 0.97)
            day = AS_OF - timedelta(days=start - 6)
            out = self.txn(m, day, "debit", amount, "transfer", "Payment", aggregator["name"],
                           counterparty_account=aggregator["account_ids"][0])
            self.txn(aggregator, day, "credit", amount, "transfer", "Payment", m["name"],
                     counterparty_account=m["account_ids"][0])
            forwarded += amount
            if m is alerted:
                triggering = [*credits, out]
        self.txn(aggregator, AS_OF - timedelta(days=start - 8), "debit", forwarded * 0.96, "transfer", "Invoice",
                 self.fakers["UK"].company(), country=rng.choice(FOREIGN_COUNTRIES + HIGH_RISK_COUNTRIES))
        self.alert("MULE_RING", alerted, ("TM-MULE-02", "Account sharing a device with other customers"),
                   triggering, (0.7, 0.95))

    def assign_devices(self) -> None:
        """Every customer without a planted device gets 1-2 of their own; a few households share one."""
        rng = random.Random(self.seed + 7)  # separate stream: device data never shifts other data
        linked = {link["customer_id"] for link in self.d["device_links"]}
        unlinked = [c for c in self.d["customers"] if c["customer_id"] not in linked]
        alerted = {a["customer_id"] for a in self.d["alerts"]}
        households = rng.sample([c for c in unlinked if c["customer_id"] not in alerted], SHARED_HOUSEHOLD_DEVICES * 2)
        for a, b in zip(households[::2], households[1::2]):
            device = self.next_id("DEV-H", 3)
            self.link_device(a, device, "tablet")
            self.link_device(b, device, "tablet")
        for c in unlinked:
            for _ in range(rng.choice([1, 1, 2])):
                self.link_device(c, self.next_id("DEV-G", 4), rng.choice(["mobile", "laptop"]))

    def generate(self) -> dict:
        planters = {
            "STRUCT": self.plant_struct, "PASSTHRU": self.plant_passthru, "MULE": self.plant_mule,
            "HRJ": self.plant_hrj, "SANCT_TRUE": self.plant_sanct_true, "PEP": self.plant_pep,
            "BENIGN_CASH_BUSINESS": self.plant_cash_business, "BENIGN_PROPERTY_SALE": self.plant_property_sale,
            "BENIGN_BONUS": self.plant_bonus, "SANCT_NEAR": self.plant_sanct_near,
        }
        for typology, (count, _, _) in TYPOLOGIES.items():
            for _ in range(count if typology in planters else 0):
                planters[typology]()
        background = [self.new_customer() for _ in range(BACKGROUND_CUSTOMERS)]
        for c in self.rng.sample(background, 15):
            self.noise_article(c)
        self.filler_lists()
        self.case_history(self.d["customers"])
        for _ in range(TYPOLOGIES["MULE_RING"][0]):  # after everything else: earlier data is unchanged
            self.plant_mule_ring()
        self.assign_devices()
        self.d["meta"] = {"seed": self.seed, "as_of": AS_OF.isoformat(),
                          "counts": {k: len(v) for k, v in self.d.items() if isinstance(v, list)}}
        return self.d


def generate(seed: int = 42) -> dict:
    return Generator(seed).generate()
