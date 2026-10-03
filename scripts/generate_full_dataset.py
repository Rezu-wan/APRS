"""Generate the full project-wide synthetic dataset (SEED=42, reproducible).

Writes a self-contained CSV dataset under data/dataset/ — separate from the
Stage-1 ML training set (data/transactions.csv) and from the live demo seed
(scripts/seed_demo.py). Nothing in api/ reads these files; loading them into
the database later is a separate, explicit step.

Everything is 100% synthetic. Identities use example.com emails, generated
name combinations and fake phone numbers. No real person's data exists here.

Vocabularies mirror the live application exactly:
- transaction states: api/core/state_machine.py
- payment event types / sources / outcomes: api/core/payment_lifecycle.py
- anomaly taxonomy: api/services/anomaly_rules.py (9 values)
- twin event types: TRANSACTION_CREATED, PAYMENT_PROCESSING, PAYMENT_FAILED,
  PAYMENT_SUCCEEDED, PAYMENT_STALLED, ML_RISK_ASSESSED, RECOVERY_CHECKED,
  RECOVERY_ELIGIBILITY_ASSESSED, RECOVERY_APPROVED, RECOVERY_STARTED,
  RECOVERY_EXECUTED, RECOVERY_VERIFIED, RECOVERY_BLOCKED, LIMIT_RELEASED,
  MANUAL_REVIEW, RECOVERY_REJECTED, ANOMALY_CLASSIFIED,
  ROOT_CAUSE_IDENTIFIED, LATE_SETTLEMENT
- demo scenarios: api/services/demo_scenarios.py (S1..S6 semantics)

Usage:
    python -m scripts.generate_full_dataset            # full size, seed 42
    python -m scripts.generate_full_dataset --scale 0.5 --seed 42
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 42

# ---------------------------------------------------------------- time window
END = datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc)   # dataset "as of"
WINDOW_DAYS = 365                                            # 12 months of data
START = END - timedelta(days=WINDOW_DAYS)


def fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S+00:00")


def parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S%z")


def write_csv(path: Path, header: list[str], rows: list[dict]) -> int:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="raise")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return len(rows)


def sha(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def wpick(rng: random.Random, table: dict) -> object:
    """Pick a key from {value: weight}."""
    r = rng.random() * sum(table.values())
    acc = 0.0
    k = next(iter(table))
    for key, w in table.items():
        acc += w
        if r < acc:
            return key
    return k


def jdump(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


# =============================================================== static pools
FIRST_NAMES = [
    "Arif", "Nusrat", "Tanvir", "Sadia", "Rafiul", "Mehjabin", "Shakib", "Farhana",
    "Imran", "Tasnim", "Rakib", "Sumaiya", "Nabil", "Rumana", "Sabbir", "Maliha",
    "James", "Elena", "Marcus", "Priya", "Daniel", "Aiko", "Omar", "Ingrid",
    "Kwame", "Mei", "Ravi", "Fatima", "Lucas", "Amara", "Jonas", "Leila",
    "Peter", "Zara", "Hiro", "Carmen", "Ibrahim", "Anika", "Viktor", "Nadia",
]
LAST_NAMES = [
    "Hossain", "Rahman", "Chowdhury", "Akter", "Islam", "Khan", "Sarker", "Alam",
    "Haque", "Mahmud", "Sultana", "Karim", "Ahmed", "Bhuiyan", "Talukder", "Noor",
    "Whitfield", "Okafor", "Petrov", "Sharma", "Lindqvist", "Nakamura", "Haddad",
    "Moreau", "Adeyemi", "Costa", "Novak", "Yilmaz", "Ferreira", "Bergman",
    "Mustafi", "Delgado", "Kowalski", "Ibrahimov", "Tanaka", "Mwangi", "Rossi",
    "Andersen", "Silva", "Diallo",
]
COUNTRIES = {  # country: (weight, currency, phone_prefix)
    "BD": (82, "BDT", "+8801"), "US": (4, "USD", "+1202"),
    "GB": (3, "GBP", "+44"), "MY": (3, "MYR", "+60"),
    "SG": (3, "SGD", "+65"), "IN": (2, "INR", "+91"),
    "AE": (3, "AED", "+971"),
}
CURRENCY_FX = {"BDT": 1.0, "USD": 0.0084, "GBP": 0.0066, "MYR": 0.039,
               "SGD": 0.011, "INR": 0.72, "AED": 0.031}

MERCHANT_CATALOG = [  # (name, category, home_country, risk_tier, weight)
    ("Dhaka Fresh Mart", "grocery", "BD", "LOW", 90), ("GreenLine Utilities", "utility", "BD", "LOW", 70),
    ("PadmaFoods Online", "food", "BD", "LOW", 85), ("BongoBazar", "ecommerce", "BD", "LOW", 88),
    ("Meghna Telecom", "telecom", "BD", "LOW", 95), ("Rideshare Nikunj", "transport", "BD", "LOW", 80),
    ("CityCare Hospital", "health", "BD", "LOW", 25), ("EduPath Academy", "education", "BD", "LOW", 30),
    ("CinePlex Star", "entertainment", "BD", "LOW", 45), ("KartPay Digital", "ecommerce", "BD", "LOW", 60),
    ("Shopnodhara Fashion", "retail", "BD", "LOW", 50), ("QuickBite Delivery", "food", "BD", "LOW", 75),
    ("Jamuna Electronics", "electronics", "BD", "LOW", 35), ("Agni Gas & Fuel", "utility", "BD", "LOW", 40),
    ("MetroMart Superstore", "grocery", "BD", "LOW", 65), ("BDOptics", "retail", "BD", "LOW", 15),
    ("SurmaPharma", "health", "BD", "LOW", 30), ("Rupayan Property", "housing", "BD", "MEDIUM", 8),
    ("FalconAir BD", "travel", "BD", "MEDIUM", 12), ("Bengal Tours", "travel", "BD", "LOW", 10),
    ("NagadX Reload", "telecom", "BD", "MEDIUM", 55), ("TurboGame Zone", "gaming", "BD", "HIGH", 6),
    ("CoinBridge Exchange", "crypto", "XX", "HIGH", 3), ("LuckyWin Casino", "gaming", "XX", "HIGH", 2),
    ("GlobalSoft Licenses", "software", "US", "LOW", 18), ("NorthStream Media", "entertainment", "US", "LOW", 22),
    ("VegaCloud Hosting", "software", "US", "LOW", 12), ("MapleBooks Store", "ecommerce", "US", "LOW", 14),
    ("LibertyGadgets", "electronics", "US", "MEDIUM", 9), ("SunriseFare Travel", "travel", "US", "MEDIUM", 7),
    ("ThamesEnergy UK", "utility", "GB", "LOW", 10), ("CrownMarket UK", "grocery", "GB", "LOW", 12),
    ("KL Tech Bazaar", "electronics", "MY", "LOW", 11), ("TropicFare MY", "travel", "MY", "LOW", 8),
    ("LionCity Eats", "food", "SG", "LOW", 9), ("MerlionTelco", "telecom", "SG", "LOW", 10),
    ("DelhiMart India", "ecommerce", "IN", "LOW", 12), ("BharatBill Pay", "utility", "IN", "LOW", 14),
    ("GulfMart AE", "retail", "AE", "MEDIUM", 8), ("DuneGold Traders", "luxury", "AE", "HIGH", 4),
]

EMPLOYERS = [
    "Octson Textiles Ltd", "BengalSoftware Ltd", "DeltaApparels PLC", "Meghna Engineering",
    "Shomoy Media House", "Karnaphuli Foods", "VerdantAgro Ltd", "NovaHealth Services",
    "Trident Logistics", "BlueOrchid Hotels", "PrimeBank Operations", "CrescentEd Group",
    "SolarisEnergy BD", "Riverstone Consulting", "AtlasFabrics Ltd", "Helios Telecom",
    "Meridian Pharma", "QuantumRetail BD", "EverestShipping Ltd", "ZenithInsurance",
]

DEVICE_MODELS = [
    ("Android 14", "Pixel Vista X"), ("Android 13", "NovaPhone 12"), ("Android 14", "OrbitNote 9"),
    ("Android 12", "ZenLite 5"), ("iOS 17", "iSphere 15"), ("iOS 16", "iSphere 13"),
    ("iOS 17", "iSphere Mini"), ("Android 13", "TigerTab 8"), ("Windows 11", "FusionBook Pro"),
    ("macOS 14", "PearBook Air"),
]
CHANNELS_BY_TYPE = {
    "purchase": {"mobile_app": 50, "pos": 35, "web": 15},
    "bill_payment": {"mobile_app": 60, "web": 25, "agent": 15},
    "transfer": {"mobile_app": 55, "web": 40, "agent": 5},
    "salary_deposit": {"web": 60, "mobile_app": 40},
    "refund": {"web": 50, "mobile_app": 50},
    "withdrawal": {"atm": 85, "agent": 15},
}
FAILURE_REASONS_W = {"Timeout": 45, "Gateway Error": 20, "Network Drop": 15,
                     "Merchant Disconnect": 12, "Insufficient Balance": 8}

ARCHETYPES = {  # archetype: weight — drives every behavioral pattern
    "salaried": 24, "shopper": 30, "biller": 14, "p2p": 11, "merchant_owner": 3,
    "dormant": 7, "failure_prone": 6, "night_owl": 4, "velocity_mule": 3,
    "traveler": 3, "amount_spiker": 3,
}
BASE_FAIL_RATE = {
    "salaried": 0.06, "shopper": 0.07, "biller": 0.06, "p2p": 0.07,
    "merchant_owner": 0.05, "dormant": 0.08, "failure_prone": 0.30,
    "night_owl": 0.10, "velocity_mule": 0.12, "traveler": 0.09,
    "amount_spiker": 0.08,
}
TX_COUNT_RANGE = {  # transactions over the 12-month window
    "salaried": (14, 26), "shopper": (16, 34), "biller": (10, 22), "p2p": (8, 26),
    "merchant_owner": (30, 70), "dormant": (2, 7), "failure_prone": (14, 30),
    "night_owl": (18, 44), "velocity_mule": (22, 50), "traveler": (12, 28),
    "amount_spiker": (10, 24),
}
SEGMENT_BY_ARCH = {
    "salaried": "retail", "shopper": "retail", "biller": "retail", "p2p": "retail",
    "merchant_owner": "business", "dormant": "retail", "failure_prone": "retail",
    "night_owl": "premium", "velocity_mule": "business", "traveler": "premium",
    "amount_spiker": "premium",
}
TX_TYPES_BY_ARCH = {
    "salaried": {"purchase": 45, "bill_payment": 25, "transfer": 12, "withdrawal": 10, "refund": 5, "salary_deposit": 3},
    "shopper": {"purchase": 70, "bill_payment": 12, "transfer": 8, "refund": 7, "withdrawal": 3},
    "biller": {"bill_payment": 60, "purchase": 20, "transfer": 10, "withdrawal": 7, "refund": 3},
    "p2p": {"transfer": 65, "purchase": 20, "bill_payment": 10, "withdrawal": 5},
    "merchant_owner": {"purchase": 40, "transfer": 30, "bill_payment": 15, "salary_deposit": 10, "refund": 5},
    "dormant": {"purchase": 55, "bill_payment": 25, "withdrawal": 15, "transfer": 5},
    "failure_prone": {"purchase": 50, "bill_payment": 25, "transfer": 12, "withdrawal": 8, "refund": 5},
    "night_owl": {"purchase": 55, "transfer": 25, "bill_payment": 10, "withdrawal": 7, "refund": 3},
    "velocity_mule": {"transfer": 60, "purchase": 20, "withdrawal": 15, "bill_payment": 5},
    "traveler": {"purchase": 50, "transfer": 20, "bill_payment": 15, "withdrawal": 10, "refund": 5},
    "amount_spiker": {"purchase": 60, "transfer": 18, "bill_payment": 12, "withdrawal": 6, "refund": 4},
}
AMOUNT_BASE = {  # median BDT-equivalent per type
    "purchase": 900, "bill_payment": 1400, "transfer": 6500, "salary_deposit": 82000,
    "refund": 1800, "withdrawal": 4000,
}
CREDIT_TYPES = ("salary_deposit", "refund")

BLOCK_REASONS = {
    "DOUBLE_DEDUCTION": "multiple customer debit confirmations require manual financial review",
    "DUPLICATE_TRANSACTION": "same provider reference found on another transaction",
    "SUCCESSFUL_BUT_UNCONFIRMED": "settlement confirmed; funds moved — manual reconciliation required",
    "NONE": "no failure to recover",
    "FALSE_COMPLAINT": "payment completed successfully — evidence contradicts reported failure",
    "SUSPICIOUS": "unusual retry/attempt pattern without a determinable payment outcome",
    "INCOMPLETE": "insufficient payment evidence",
    "UNKNOWN": "late settlement changed the evidence",
}

EVENT_INFO = {  # mirrors api/core/payment_lifecycle.py EVENT_TYPE_INFO
    "CUSTOMER_DEBIT_CONFIRMED": {"source": "BANK", "outcome": "CONFIRMED"},
    "CUSTOMER_DEBIT_FAILED": {"source": "BANK", "outcome": "FAILED"},
    "GATEWAY_REQUEST_SENT": {"source": "GATEWAY", "outcome": "OBSERVED"},
    "GATEWAY_RESPONSE_RECEIVED": {"source": "GATEWAY", "outcome": "CONFIRMED"},
    "GATEWAY_TIMEOUT": {"source": "GATEWAY", "outcome": "TIMEOUT"},
    "GATEWAY_ERROR": {"source": "GATEWAY", "outcome": "ERROR"},
    "MERCHANT_CONFIRMATION_REQUESTED": {"source": "MERCHANT", "outcome": "OBSERVED"},
    "MERCHANT_CONFIRMATION_RECEIVED": {"source": "MERCHANT", "outcome": "CONFIRMED"},
    "MERCHANT_CONFIRMATION_TIMEOUT": {"source": "MERCHANT", "outcome": "TIMEOUT"},
    "MERCHANT_ERROR": {"source": "MERCHANT", "outcome": "ERROR"},
    "SETTLEMENT_REQUESTED": {"source": "SETTLEMENT", "outcome": "OBSERVED"},
    "SETTLEMENT_CONFIRMED": {"source": "SETTLEMENT", "outcome": "CONFIRMED"},
    "SETTLEMENT_FAILED": {"source": "SETTLEMENT", "outcome": "FAILED"},
    "SETTLEMENT_NOT_CONFIRMED": {"source": "SETTLEMENT", "outcome": "NOT_CONFIRMED"},
}
HAPPY_PATH = ["CUSTOMER_DEBIT_CONFIRMED", "GATEWAY_REQUEST_SENT", "GATEWAY_RESPONSE_RECEIVED",
              "MERCHANT_CONFIRMATION_REQUESTED", "MERCHANT_CONFIRMATION_RECEIVED",
              "SETTLEMENT_REQUESTED", "SETTLEMENT_CONFIRMED"]
STAGE_LATENCY = {"BANK": (150, 900), "GATEWAY": (200, 6000),
                 "MERCHANT": (300, 2500), "SETTLEMENT": (800, 4000)}


def _bdt(amount, currency) -> float:
    """Convert an amount in `currency` to BDT-equivalent."""
    return float(amount) / CURRENCY_FX[currency]


# ================================================================ generation
class Dataset:
    def __init__(self, seed: int, scale: float, out_dir: Path):
        self.seed = seed
        self.scale = scale
        self.out = out_dir
        self.rng_c = random.Random(seed * 1000 + 1)   # customers/accounts
        self.rng_t = random.Random(seed * 1000 + 2)   # transactions
        self.rng_e = random.Random(seed * 1000 + 3)   # events
        self.rng_a = random.Random(seed * 1000 + 4)   # assessments/recovery
        self.customers: list[dict] = []
        self.accounts: list[dict] = []
        self.merchants: list[dict] = []
        self.devices: list[dict] = []
        self.transactions: list[dict] = []
        self.twin: list[dict] = []
        self.pay_events: list[dict] = []
        self.assessments: list[dict] = []
        self.model_assessments: list[dict] = []
        self.behaviors: list[dict] = []
        self.edges: list[dict] = []
        self.recoveries: list[dict] = []
        self.merchant_by_id: dict[str, dict] = {}
        self.cust_devices: dict[str, list[str]] = {}
        self.cust_by_id: dict[str, dict] = {}
        self.mule_groups: list[tuple[str, str]] = []
        self._late_set: set[int] = set()
        self._shared_refs: dict[int, list[int]] = {}
        self._event_counters = {"ev": 0, "pev": 0}

    # ------------------------------------------------------------- identity
    def gen_merchants(self):
        for name, cat, ctry, tier, weight in MERCHANT_CATALOG:
            mid = f"MER-{len(self.merchants) + 1:04d}"
            m = {
                "merchant_id": mid, "name": name, "category": cat, "country": ctry,
                "risk_tier": tier, "traffic_weight": weight,
                "created_at": fmt(START - timedelta(days=self.rng_t.randint(200, 900))),
            }
            self.merchants.append(m)
            self.merchant_by_id[mid] = m

    def gen_devices(self, shared_count: int = 18):
        for i in range(700):
            os_k, model = self.rng_c.choice(DEVICE_MODELS)
            self.devices.append({
                "device_id": f"DEV-{i + 1:05d}", "os": os_k, "model": model,
                "trusted": "true" if self.rng_c.random() < 0.93 else "false",
                "first_seen": fmt(START - timedelta(days=self.rng_c.randint(30, 500))),
            })
        for j in range(shared_count):  # deliberately shared devices (mule signal)
            os_k, model = self.rng_c.choice(DEVICE_MODELS)
            self.devices.append({
                "device_id": f"DEV-{701 + j:05d}", "os": os_k, "model": model,
                "trusted": "false",
                "first_seen": fmt(START - timedelta(days=self.rng_c.randint(30, 300))),
            })

    def gen_customers_accounts(self):
        rng = self.rng_c
        n_customers = max(1, round(600 * self.scale))
        shared_devices = [d["device_id"] for d in self.devices[-18:]]
        mule_groups: list[tuple[str, str]] = []
        for i in range(n_customers):
            country = wpick(rng, {c: w[0] for c, w in COUNTRIES.items()})
            arch = wpick(rng, ARCHETYPES)
            seg = SEGMENT_BY_ARCH[arch]
            risk = wpick(rng, {
                "LOW": 60 if arch in ("salaried", "shopper", "biller", "dormant", "p2p") else 30,
                "MEDIUM": 30,
                "HIGH": 10 if arch in ("velocity_mule", "night_owl", "amount_spiker") else 5,
            })
            first = rng.choice(FIRST_NAMES)
            last = rng.choice(LAST_NAMES)
            n = rng.randint(10, 9999)
            created = START + timedelta(days=rng.randint(0, 300), hours=rng.randint(0, 23),
                                        minutes=rng.randint(0, 59))
            self.customers.append({
                "customer_id": f"CUST-{i + 1:06d}",
                "full_name": f"{first} {last}",
                "email": f"{first.lower()}.{last.lower()}{n}@example.com",
                "phone": f"{COUNTRIES[country][2]}{rng.randint(30000000, 99999999)}",
                "country": country, "created_at": fmt(created),
                "status": wpick(rng, {"active": 88, "dormant": 8, "suspended": 3, "closed": 1}),
                "segment": seg, "risk_profile": risk, "archetype": arch,
            })
            n_acc = rng.choices([1, 2, 3], weights=[62, 30, 8])[0] \
                if arch != "merchant_owner" else rng.choices([2, 3], weights=[55, 45])[0]
            for a in range(n_acc):
                cur = COUNTRIES[country][1]
                atype = wpick(rng, {"savings": 40, "checking": 30, "wallet": 22,
                                    "merchant_settlement": 5 if seg == "business" else 0,
                                    "fixed_deposit": 3})
                base_balance = {"retail": 25_000, "premium": 350_000, "business": 900_000}[seg]
                if atype == "fixed_deposit":
                    base_balance *= 4
                if atype == "wallet":
                    base_balance *= 0.2
                balance = round(rng.lognormvariate(math.log(max(base_balance, 1)), 0.9), 2)
                self.accounts.append({
                    "account_id": f"ACCT-{len(self.accounts) + 1:06d}",
                    "customer_id": f"CUST-{i + 1:06d}",
                    "account_type": atype, "currency": cur,
                    "balance": balance,
                    "opening_balance": round(balance * rng.uniform(0.5, 0.95), 2),
                    "status": wpick(rng, {"active": 90, "dormant": 7, "frozen": 2, "closed": 1}),
                    "created_at": fmt(created + timedelta(days=rng.randint(0, 20),
                                                          hours=rng.randint(0, 23))),
                    "last_activity_at": "",
                    "primary": "true" if a == 0 else "false",
                })
            if arch == "velocity_mule" and rng.random() < 0.8:
                mule_groups.append((f"CUST-{i + 1:06d}", rng.choice(shared_devices)))
        for dev in shared_devices:  # relationship noise: ordinary customers share too
            for _ in range(rng.randint(1, 2)):
                c = rng.choice(self.customers)
                if c["risk_profile"] != "HIGH":
                    mule_groups.append((c["customer_id"], dev))
        self.mule_groups = mule_groups
        self.cust_by_id = {c["customer_id"]: c for c in self.customers}

    # ---------------------------------------------------------- transactions
    def gen_transactions(self):
        rng = self.rng_t
        target = round(10_500 * self.scale)
        accs_by_cust: dict[str, list[dict]] = {}
        for a in self.accounts:
            accs_by_cust.setdefault(a["customer_id"], []).append(a)
        merchant_weights = {m["merchant_id"]: m["traffic_weight"] for m in self.merchants}

        plan: list[tuple[dict, int]] = []
        for c in self.customers:
            lo, hi = TX_COUNT_RANGE[c["archetype"]]
            plan.append((c, rng.randint(lo, hi)))
        total = sum(n for _, n in plan)
        idx = 0
        while total > target:                       # deterministic trim
            c, n = plan[idx % len(plan)]
            if n > 2:
                plan[idx % len(plan)] = (c, n - 1)
                total -= 1
            idx += 1
        while total < target - 500:                 # deterministic grow (rare)
            c, n = plan[idx % len(plan)]
            plan[idx % len(plan)] = (c, n + 1)
            total += 1
            idx += 1

        # personal devices: first 682 devices round-robin, 1-2 per customer
        cust_devices: dict[str, list[str]] = {c["customer_id"]: [] for c in self.customers}
        personal = [d["device_id"] for d in self.devices[:-18]]
        for i, dev in enumerate(personal):
            cid = self.customers[i % len(self.customers)]["customer_id"]
            if len(cust_devices[cid]) < 2:
                cust_devices[cid].append(dev)
        for cid, dev in self.mule_groups:
            if dev not in cust_devices[cid]:
                cust_devices[cid].append(dev)
        self.cust_devices = cust_devices

        def hour_for(arch: str) -> int:
            if arch == "night_owl":
                return rng.choice([0, 1, 1, 2, 2, 3, 3, 4, 23])
            r = rng.random()
            if r < 0.45:
                return rng.randint(9, 12)
            if r < 0.75:
                return rng.randint(13, 17)
            if r < 0.95:
                return rng.randint(18, 22)
            return rng.choice([6, 7, 8, 23])

        n_cust = len(self.customers)
        txn_no = 0
        for c, n in plan:
            cid = c["customer_id"]
            arch = c["archetype"]
            my_accs = accs_by_cust[cid]
            created = parse_ts(c["created_at"])
            t0 = max(created + timedelta(days=1), START)
            span_days = max((END - t0).days, 5)
            stamps = sorted(t0 + timedelta(seconds=rng.uniform(0, span_days * 86400))
                            for _ in range(n))
            fail_storm = 0
            for ts in stamps:
                if ts >= END:
                    break
                if arch == "night_owl":
                    ts = ts.replace(hour=hour_for(arch))
                ttype = wpick(rng, TX_TYPES_BY_ARCH[arch])
                if ttype == "salary_deposit":
                    try:
                        ts = ts.replace(day=rng.randint(1, 5), hour=rng.randint(8, 11))
                    except ValueError:
                        pass
                channel = wpick(rng, CHANNELS_BY_TYPE[ttype])
                acc = rng.choice(my_accs)
                currency = acc["currency"]
                fx = CURRENCY_FX[currency]
                med = AMOUNT_BASE[ttype] * (3.5 if c["segment"] == "business"
                                            else 2.2 if c["segment"] == "premium" else 1.0)
                if arch == "amount_spiker" and (END - ts).days < 40 and rng.random() < 0.5:
                    med *= rng.uniform(6, 12)          # sudden spending change
                amount = round(rng.lognormvariate(math.log(med), 0.85) * fx, 2)

                # merchant / counterparty
                mer = ""
                counterparty = ""
                if ttype in ("purchase", "refund", "bill_payment"):
                    if arch == "traveler" and ttype == "purchase" and rng.random() < 0.45:
                        mer = rng.choice([m["merchant_id"] for m in self.merchants
                                          if m["country"] != c["country"]])
                    else:
                        mer = wpick(rng, merchant_weights)
                elif ttype == "salary_deposit":
                    counterparty = rng.choice(EMPLOYERS)
                elif ttype == "transfer" and rng.random() < 0.4:
                    counterparty = f"CUST-{rng.randint(1, n_cust):06d}"

                direction = "credit" if ttype in CREDIT_TYPES else "debit"
                if arch == "velocity_mule" and ttype == "transfer" and rng.random() < 0.4:
                    direction = "credit"               # mule fan-in representation
                    counterparty = f"CUST-{rng.randint(1, n_cust):06d}"

                device = ""
                if channel in ("mobile_app", "web", "pos") and cust_devices[cid]:
                    device = rng.choice(cust_devices[cid]) if rng.random() < 0.85 \
                        else rng.choice([d["device_id"] for d in self.devices])

                # raw attributes
                latency = int(rng.lognormvariate(7.6, 0.35))
                netq = wpick(rng, {"Good": 72 if arch != "failure_prone" else 45,
                                   "Fair": 20,
                                   "Poor": 8 if arch != "failure_prone" else 25})
                retries = rng.choices([0, 1, 2, 3, 4],
                                      weights=[62, 22, 10, 4, 2] if arch != "failure_prone"
                                      else [30, 30, 22, 12, 6])[0]
                if arch == "velocity_mule":
                    retries = max(retries, rng.choices([0, 1, 2], weights=[40, 40, 20])[0])
                prev_fails = min(6, int(rng.expovariate(1.0 if arch != "failure_prone" else 2.2)))

                # outcome
                p_fail = BASE_FAIL_RATE[arch]
                if fail_storm > 0:
                    p_fail = 0.85
                    fail_storm -= 1
                elif arch == "failure_prone" and rng.random() < 0.12:
                    fail_storm = rng.randint(2, 5)
                r = rng.random()
                outcome = "FAILED" if r < p_fail else \
                    "STALLED" if r < p_fail + 0.045 else "SUCCESS"

                failure_reason = ""
                if outcome == "FAILED":
                    failure_reason = wpick(rng, FAILURE_REASONS_W)
                    if netq == "Poor" and rng.random() < 0.4:
                        failure_reason = "Network Drop"
                    if ttype == "withdrawal" and rng.random() < 0.3:
                        failure_reason = "Insufficient Balance"
                    latency = int(latency * rng.uniform(1.3, 2.2))

                txn_no += 1
                self.transactions.append({
                    "transaction_id": f"TXN-{txn_no:07d}",
                    "customer_id": cid, "account_id": acc["account_id"],
                    "merchant_id": mer, "counterparty": counterparty,
                    "direction": direction, "amount": amount, "currency": currency,
                    "transaction_type": ttype, "channel": channel,
                    "country": c["country"], "device_id": device,
                    "timestamp": fmt(ts),
                    "gateway_latency_ms": latency, "retry_count": retries,
                    "network_quality": netq, "previous_failures": prev_fails,
                    "account_age_days": max(0, (ts - created).days),
                    "failure_reason": failure_reason,
                    "current_state": "", "scenario": "",
                    "risk_score": "", "risk_level": "", "risk_decision": "",
                    "failure_prediction": "", "failure_probability": "",
                    "safe_to_release_probability": "", "safe_to_release": "",
                    "_outcome": outcome, "_ts": ts,
                })

    # --------------------------------------- states, risk, anomaly classes
    def plan_outcomes(self):
        rng = self.rng_t
        failed_idx = [i for i, t in enumerate(self.transactions)
                      if t["_outcome"] == "FAILED"]
        rng.shuffle(failed_idx)
        n = len(failed_idx)
        cut1, cut2, cut3, cut4 = (round(n * f) for f in (0.06, 0.14, 0.20, 0.21))
        dup_debit = set(failed_idx[:cut1])
        sparse = set(failed_idx[cut1:cut2])
        unconfirmed = set(failed_idx[cut2:cut3])
        false_complaint = set(failed_idx[cut3:cut4])
        shared_ref_members = set(failed_idx[cut4:cut4 + 24])
        shared_refs: dict[int, list[int]] = {}
        for k, i in enumerate(sorted(shared_ref_members)):
            shared_refs.setdefault(k % 8, []).append(i)
        special = dup_debit | sparse | unconfirmed | false_complaint | shared_ref_members

        multi_user_devs = {d for d, cnt in Counter(d for _, d in self.mule_groups).items()
                           if cnt > 1}

        for i, t in enumerate(self.transactions):
            outcome = t["_outcome"]
            ts = t["_ts"]
            created = self.cust_by_id[t["customer_id"]]["created_at"]
            home = self.cust_by_id[t["customer_id"]]["country"]

            suspicious = (t["device_id"] in multi_user_devs
                          or (0 <= ts.hour <= 5 and _bdt(t["amount"], t["currency"]) > 50_000)
                          or (t["country"] != home))

            # ---- deterministic risk score from real attributes
            mer_tier = self.merchant_by_id[t["merchant_id"]]["risk_tier"] \
                if t["merchant_id"] else "LOW"
            amount_outlier = _bdt(t["amount"], t["currency"]) > \
                4 * AMOUNT_BASE.get(t["transaction_type"], 1000)
            score = 0.03
            score += 0.040 * min(t["previous_failures"], 5)
            score += 0.16 * min(max((t["gateway_latency_ms"] - 1500) / 6000, 0), 1)
            score += 0.08 * min(t["retry_count"], 4) / 2
            score += 0.12 if t["network_quality"] == "Poor" \
                else 0.04 if t["network_quality"] == "Fair" else 0
            score += 0.24 if t["device_id"] in multi_user_devs else 0
            score += 0.15 if 0 <= ts.hour <= 5 else 0
            score += 0.14 if t["country"] != home else 0
            score += 0.18 if mer_tier == "HIGH" else 0
            score += 0.18 if amount_outlier else 0
            score += 0.06 if t["account_age_days"] < 30 else 0
            score = round(min(max(score + rng.uniform(-0.03, 0.05), 0.01), 0.99), 3)
            t["risk_score"] = score
            t["risk_level"] = "LOW" if score < 0.30 else "MEDIUM" if score < 0.55 \
                else "HIGH" if score < 0.80 else "CRITICAL"
            t["risk_decision"] = "ALLOW" if score < 0.55 else \
                "REVIEW" if score < 0.80 else "BLOCK"

            safe_p = round(min(max(0.99 - score * 0.85 + rng.uniform(-0.03, 0.02),
                                   0.02), 0.99), 3)
            t["safe_to_release_probability"] = safe_p
            t["safe_to_release"] = "true" if (safe_p >= 0.90 and score < 0.5) else "false"

            if outcome in ("FAILED", "STALLED"):
                if outcome == "FAILED" and rng.random() < 0.8:
                    fp = t["failure_reason"]
                else:
                    fp = wpick(rng, FAILURE_REASONS_W)
                    if outcome == "STALLED":
                        fp = "Timeout"
                t["failure_prediction"] = fp
                t["failure_probability"] = round(rng.uniform(0.55, 0.97), 3)

            # ---- final state + twin chain
            chain: list[tuple[str, str, str]] = []
            if outcome == "FAILED":
                chain = [("TRANSACTION_CREATED", "", "INITIATED"),
                         ("PAYMENT_PROCESSING", "INITIATED", "PROCESSING"),
                         ("PAYMENT_FAILED", "PROCESSING", "FAILED"),
                         ("ML_RISK_ASSESSED", "FAILED", "RISK_ASSESSED"),
                         ("RECOVERY_CHECKED", "RISK_ASSESSED", "RECOVERY_PENDING")]
                eligible = (safe_p >= 0.90 and t["risk_level"] in ("LOW", "MEDIUM")
                            and _bdt(t["amount"], t["currency"]) <= 1500
                            and t["previous_failures"] <= 3)
                cap_block = (_bdt(t["amount"], t["currency"]) > 1500
                             or t["previous_failures"] > 3)
                if i in special:
                    final = "MANUAL_REVIEW"
                elif eligible and rng.random() < 0.08:
                    # settlement race candidate: gate will block on late evidence
                    final = "MANUAL_REVIEW"
                    self._late_set.add(i)
                elif eligible:
                    final = "LIMIT_RELEASED"
                elif cap_block:
                    final = "RECOVERY_REJECTED"
                else:
                    final = "MANUAL_REVIEW"
                chain.append((final, "RECOVERY_PENDING", final))
            elif outcome == "STALLED":
                chain = [("TRANSACTION_CREATED", "", "INITIATED"),
                         ("PAYMENT_PROCESSING", "INITIATED", "PROCESSING"),
                         ("PAYMENT_STALLED", "PROCESSING", "STALLED")]
                if rng.random() < 0.45:                # resolves via retry
                    chain += [("PAYMENT_PROCESSING", "STALLED", "PROCESSING"),
                              ("PAYMENT_SUCCEEDED", "PROCESSING", "SUCCESS")]
                    t["failure_reason"] = ""
                    final = "SUCCESS"
                else:
                    t["failure_reason"] = "Timeout"    # resting stall
                    final = "STALLED"
            else:
                chain = [("TRANSACTION_CREATED", "", "INITIATED"),
                         ("PAYMENT_PROCESSING", "INITIATED", "PROCESSING"),
                         ("PAYMENT_SUCCEEDED", "PROCESSING", "SUCCESS")]
                final = "SUCCESS"
            t["current_state"] = final
            t["_chain"] = chain

        self._dup_debit = dup_debit
        self._sparse = sparse
        self._unconfirmed = unconfirmed
        self._false_complaint = false_complaint
        self._shared_refs = shared_refs

    # ---------------------------------- twin timeline + payment events
    def build_events_and_assessments(self):
        rng_e, rng_a = self.rng_e, self.rng_a
        ev = self._event_counters["ev"]
        pev_n = self._event_counters["pev"]

        def new_twin(tx_id, when, etype, prev, new, reason, meta=None, ml=None):
            nonlocal ev
            ev += 1
            row = {"event_id": f"EVT-{ev:07d}", "transaction_id": tx_id,
                   "timestamp": fmt(when), "event_type": etype,
                   "previous_state": prev, "new_state": new,
                   "failure_prediction": "", "risk_score": "",
                   "safe_to_release_probability": "", "safe_to_release": "",
                   "reason": reason or "", "metadata": jdump(meta) if meta else ""}
            if ml:
                row["failure_prediction"] = ml[0]
                row["risk_score"] = ml[1]
                row["safe_to_release_probability"] = ml[2]
                row["safe_to_release"] = ml[3]
            self.twin.append(row)
            return when

        def new_pev(tx_id, when, etype, ref, late_minutes=0, dup=False, attempt=None):
            nonlocal pev_n
            info = EVENT_INFO[etype]
            lo, hi = STAGE_LATENCY.get(info["source"], (200, 3000))
            ts = when + timedelta(minutes=late_minutes) if late_minutes else when
            pev_n += 1
            self.pay_events.append({
                "event_id": f"PEV-{pev_n:07d}", "transaction_id": tx_id,
                "provider_event_id": f"PEV-{pev_n:07d}{'r' if dup else ''}"
                                     f"-{rng_e.randint(10**8, 10**9 - 1)}",
                "event_type": etype, "source": info["source"],
                "status": info["outcome"], "event_timestamp": fmt(ts),
                "reference_id": ref, "latency_ms": rng_e.randint(lo, hi),
                "metadata": jdump({"attempt": attempt}) if attempt else "",
                "correlation_id": tx_id, "causation_id": "", "schema_version": "1"})
            return ts

        for i, t in enumerate(self.transactions):
            tx_id = t["transaction_id"]
            t0 = t["_ts"]
            ref = f"REF-{sha(tx_id)[:8].upper()}"
            reason = t["failure_reason"]
            is_failed = t["_outcome"] == "FAILED"
            is_stalled = t["_outcome"] == "STALLED"

            # ---------------------------------------------- twin timeline
            when = t0
            for k, (etype, prev, new) in enumerate(t["_chain"]):
                when += timedelta(seconds=rng_e.uniform(1.5, 9 if k < 3 else 4))
                ml = None
                if etype == "ML_RISK_ASSESSED":
                    ml = (t["failure_prediction"], t["risk_score"],
                          t["safe_to_release_probability"], t["safe_to_release"])
                rsn = {"TRANSACTION_CREATED": "transaction registered",
                       "PAYMENT_FAILED": f"gateway reported: {reason}",
                       "PAYMENT_STALLED": "no gateway response within SLA",
                       "ML_RISK_ASSESSED": "ML risk assessment completed",
                       "RECOVERY_CHECKED": "awaiting recovery decision"}.get(etype)
                if etype in ("LIMIT_RELEASED", "MANUAL_REVIEW", "RECOVERY_REJECTED"):
                    rsn = f"decision: {etype}"
                meta = {"policy": {"min_safe_probability": 0.90, "max_amount": 1500,
                                   "max_previous_failures": 3}} \
                    if etype == "RECOVERY_CHECKED" else None
                when = new_twin(tx_id, when, etype, prev, new, rsn, meta=meta, ml=ml)
            assess_at = when + timedelta(seconds=rng_e.uniform(2, 6))

            # ---------------------------------------------- payment events
            pev_ts = t0 + timedelta(seconds=rng_e.uniform(1, 4))
            anomaly = "NONE"
            root_cause, conf = "", ""

            if t["current_state"] == "SUCCESS" and not is_stalled:
                style = rng_e.random()
                if style < 0.82:                       # fully settled
                    for e in HAPPY_PATH:
                        pev_ts = new_pev(tx_id, pev_ts, e, ref)
                        pev_ts += timedelta(seconds=rng_e.uniform(0.5, 8))
                elif style < 0.92:                     # settled late (or pending)
                    for e in HAPPY_PATH[:6]:
                        pev_ts = new_pev(tx_id, pev_ts, e, ref)
                        pev_ts += timedelta(seconds=rng_e.uniform(0.5, 8))
                    if rng_e.random() < 0.5:
                        lag = rng_e.randint(180, 2880)
                        new_pev(tx_id, pev_ts, "SETTLEMENT_CONFIRMED", ref,
                                late_minutes=lag)
                        new_twin(tx_id, pev_ts + timedelta(minutes=lag),
                                 "LATE_SETTLEMENT", "SUCCESS", "SUCCESS",
                                 "settlement confirmed after a delay", {"late": True})
                else:                                  # settled w/o merchant confirmation
                    for e in HAPPY_PATH[:3]:
                        pev_ts = new_pev(tx_id, pev_ts, e, ref)
                        pev_ts += timedelta(seconds=rng_e.uniform(0.5, 8))
                    new_pev(tx_id, pev_ts + timedelta(seconds=4), "SETTLEMENT_REQUESTED", ref)
                    new_pev(tx_id, pev_ts + timedelta(seconds=9), "SETTLEMENT_CONFIRMED", ref)

            elif is_failed:
                if i in self._dup_debit:
                    anomaly = "DOUBLE_DEDUCTION"
                    pev_ts = new_pev(tx_id, pev_ts, "CUSTOMER_DEBIT_CONFIRMED", ref)
                    pev_ts = new_pev(tx_id, pev_ts + timedelta(seconds=90),
                                     "CUSTOMER_DEBIT_CONFIRMED", f"{ref}-B", attempt=2)
                    new_pev(tx_id, pev_ts + timedelta(seconds=4), "GATEWAY_REQUEST_SENT", ref)
                    new_pev(tx_id, pev_ts + timedelta(seconds=40), "GATEWAY_TIMEOUT", ref)
                elif i in self._sparse:
                    anomaly = "INCOMPLETE"
                    new_pev(tx_id, pev_ts, "CUSTOMER_DEBIT_CONFIRMED", ref)
                elif i in self._unconfirmed:
                    anomaly = "SUCCESSFUL_BUT_UNCONFIRMED"
                    root_cause, conf = "SETTLEMENT_FAILURE", 0.71
                    for e in HAPPY_PATH[:4]:
                        pev_ts = new_pev(tx_id, pev_ts + timedelta(seconds=3), e, ref)
                    new_pev(tx_id, pev_ts + timedelta(seconds=4), "SETTLEMENT_REQUESTED", ref)
                    new_pev(tx_id, pev_ts + timedelta(seconds=9), "SETTLEMENT_CONFIRMED", ref)
                elif i in {x for v in self._shared_refs.values() for x in v}:
                    anomaly = "DUPLICATE_TRANSACTION"
                    root_cause, conf = "GATEWAY_TIMEOUT", 0.43
                    pev_ts = new_pev(tx_id, pev_ts, "CUSTOMER_DEBIT_CONFIRMED", ref)
                    new_pev(tx_id, pev_ts + timedelta(seconds=3), "GATEWAY_REQUEST_SENT", ref)
                    new_pev(tx_id, pev_ts + timedelta(seconds=45), "GATEWAY_TIMEOUT", ref)
                elif i in self._false_complaint:
                    anomaly = "FALSE_COMPLAINT"        # full success chain + complaint
                    for e in HAPPY_PATH:
                        pev_ts = new_pev(tx_id, pev_ts + timedelta(seconds=3), e, ref)
                elif i in self._late_set:
                    anomaly = "GENUINE_FAILURE"        # race: resolved below
                    root_cause, conf = "MERCHANT_TIMEOUT", 0.71
                    pev_ts = new_pev(tx_id, pev_ts, "CUSTOMER_DEBIT_CONFIRMED", ref)
                    for e in ("GATEWAY_REQUEST_SENT", "GATEWAY_RESPONSE_RECEIVED",
                              "MERCHANT_CONFIRMATION_REQUESTED"):
                        pev_ts = new_pev(tx_id, pev_ts + timedelta(seconds=3), e, ref)
                    new_pev(tx_id, pev_ts + timedelta(seconds=30),
                            "MERCHANT_CONFIRMATION_TIMEOUT", ref)
                    settle_at = assess_at + timedelta(minutes=rng_e.randint(8, 90))
                    new_pev(tx_id, settle_at, "SETTLEMENT_CONFIRMED", ref)
                    new_twin(tx_id, settle_at + timedelta(seconds=1), "LATE_SETTLEMENT",
                             t["current_state"], t["current_state"],
                             "settlement confirmed after risk assessment", {"late": True})
                    anomaly = "UNKNOWN"
                else:
                    style = rng_e.random()
                    if style < 0.34:
                        anomaly = "GENUINE_FAILURE"
                        root_cause, conf = "MERCHANT_TIMEOUT", 0.71
                        pev_ts = new_pev(tx_id, pev_ts, "CUSTOMER_DEBIT_CONFIRMED", ref)
                        for e in ("GATEWAY_REQUEST_SENT", "GATEWAY_RESPONSE_RECEIVED",
                                  "MERCHANT_CONFIRMATION_REQUESTED"):
                            pev_ts = new_pev(tx_id, pev_ts + timedelta(seconds=3), e, ref)
                        new_pev(tx_id, pev_ts + timedelta(seconds=30),
                                "MERCHANT_CONFIRMATION_TIMEOUT", ref)
                    elif style < 0.58:
                        anomaly = "GENUINE_FAILURE"
                        root_cause, conf = "GATEWAY_TIMEOUT", 0.43
                        pev_ts = new_pev(tx_id, pev_ts, "CUSTOMER_DEBIT_CONFIRMED", ref)
                        new_pev(tx_id, pev_ts + timedelta(seconds=3), "GATEWAY_REQUEST_SENT", ref)
                        new_pev(tx_id, pev_ts + timedelta(seconds=45), "GATEWAY_TIMEOUT", ref)
                    elif style < 0.72:
                        anomaly = "GENUINE_FAILURE"
                        root_cause, conf = "GATEWAY_ERROR", 0.57
                        pev_ts = new_pev(tx_id, pev_ts, "CUSTOMER_DEBIT_CONFIRMED", ref)
                        new_pev(tx_id, pev_ts + timedelta(seconds=3), "GATEWAY_REQUEST_SENT", ref)
                        new_pev(tx_id, pev_ts + timedelta(seconds=18), "GATEWAY_ERROR", ref)
                    elif style < 0.84:
                        anomaly = "GENUINE_FAILURE"
                        root_cause, conf = "MERCHANT_ERROR", 0.57
                        new_pev(tx_id, pev_ts, "CUSTOMER_DEBIT_FAILED", ref, attempt=1)
                        pev_ts = new_pev(tx_id, pev_ts + timedelta(seconds=60),
                                         "CUSTOMER_DEBIT_CONFIRMED", ref, attempt=2)
                        for e in ("GATEWAY_REQUEST_SENT", "GATEWAY_RESPONSE_RECEIVED",
                                  "MERCHANT_CONFIRMATION_REQUESTED"):
                            pev_ts = new_pev(tx_id, pev_ts + timedelta(seconds=3), e, ref)
                        new_pev(tx_id, pev_ts + timedelta(seconds=12), "MERCHANT_ERROR", ref)
                    elif style < 0.94:
                        anomaly = "GENUINE_FAILURE"
                        root_cause, conf = "SETTLEMENT_FAILURE", 0.71
                        for e in HAPPY_PATH[:5]:
                            pev_ts = new_pev(tx_id, pev_ts + timedelta(seconds=3), e, ref)
                        new_pev(tx_id, pev_ts + timedelta(seconds=8),
                                wpick(rng_e, {"SETTLEMENT_FAILED": 60,
                                              "SETTLEMENT_NOT_CONFIRMED": 40}), ref)
                    else:
                        anomaly = "GENUINE_FAILURE"
                        root_cause, conf = "GATEWAY_TIMEOUT", 0.43
                        pev_ts = new_pev(tx_id, pev_ts, "CUSTOMER_DEBIT_CONFIRMED", ref)
                        new_pev(tx_id, pev_ts + timedelta(seconds=3), "GATEWAY_REQUEST_SENT", ref)
                        gt = new_pev(tx_id, pev_ts + timedelta(seconds=45), "GATEWAY_TIMEOUT", ref)
                        new_pev(tx_id, gt + timedelta(seconds=2), "GATEWAY_TIMEOUT", ref, dup=True)

            elif is_stalled:
                if rng_e.random() < 0.35:
                    anomaly = "INCOMPLETE"
                    new_pev(tx_id, pev_ts, "CUSTOMER_DEBIT_CONFIRMED", ref)
                else:
                    anomaly = "SUSPICIOUS" if t["risk_score"] >= 0.5 else "GENUINE_FAILURE"
                    pev_ts = new_pev(tx_id, pev_ts, "CUSTOMER_DEBIT_CONFIRMED", ref)
                    new_pev(tx_id, pev_ts + timedelta(seconds=3), "GATEWAY_REQUEST_SENT", ref)

            # redelivery noise on ~3% of evented transactions — duplicates the
            # CURRENT transaction's last event so per-tx chronology holds
            if self.pay_events and rng_e.random() < 0.03:
                src = self.pay_events[-1]
                if src["transaction_id"] == tx_id:
                    new_pev(tx_id,
                            parse_ts(src["event_timestamp"])
                            + timedelta(seconds=rng_e.uniform(1, 6)),
                            src["event_type"], src["reference_id"], dup=True)

            # ---------------------------------------------- anomaly observation
            if anomaly != "NONE":
                rules = []
                if anomaly == "GENUINE_FAILURE":
                    rules = [{"rule_id": "R3", "name": "genuine gateway failure"}]
                elif anomaly == "DOUBLE_DEDUCTION":
                    rules = [{"rule_id": "R4", "name": "multiple debit confirmations"}]
                elif anomaly == "DUPLICATE_TRANSACTION":
                    rules = [{"rule_id": "R5", "name": "provider reference shared"}]
                elif anomaly == "SUSPICIOUS":
                    rules = [{"rule_id": "R8", "name": "unusual pattern, outcome unknown"}]
                new_twin(tx_id, assess_at, "ANOMALY_CLASSIFIED", t["current_state"],
                         t["current_state"], f"classified {anomaly}",
                         {"anomaly_type": anomaly, "risk_level": t["risk_level"],
                          "risk_score": t["risk_score"],
                          "rule_ids": [r["rule_id"] for r in rules],
                          "rule_version": "1", "model_version": "synthetic-v1"})
            if root_cause:
                new_twin(tx_id, assess_at + timedelta(seconds=rng_e.uniform(1, 4)),
                         "ROOT_CAUSE_IDENTIFIED", t["current_state"], t["current_state"],
                         f"root cause: {root_cause}",
                         {"root_cause": root_cause, "confidence": conf})

            # ---------------------------------------------- risk assessment row
            # every transaction is scored (authorization-time risk check);
            # successes classify NONE while the decision carries the scrutiny
            if True:
                level = "UNKNOWN" if (anomaly == "INCOMPLETE" and t["risk_level"] == "LOW"
                                      and is_stalled) else t["risk_level"]
                recovery_candidate = (anomaly == "GENUINE_FAILURE"
                                      and t["risk_level"] in ("LOW", "MEDIUM"))
                ev_items = []
                if t["retry_count"] >= 3:
                    ev_items.append({"code": "HIGH_RETRY",
                                     "description": f"{t['retry_count']} retries",
                                     "severity": "MEDIUM"})
                if t["gateway_latency_ms"] > 4500:
                    ev_items.append({"code": "HIGH_LATENCY",
                                     "description": f"{t['gateway_latency_ms']} ms gateway latency",
                                     "severity": "MEDIUM"})
                if t["previous_failures"] >= 3:
                    ev_items.append({"code": "REPEATED_ATTEMPTS",
                                     "description": f"{t['previous_failures']} prior failures",
                                     "severity": "HIGH"})
                if not ev_items:
                    ev_items.append({"code": "BASELINE",
                                     "description": "no elevated attributes",
                                     "severity": "LOW"})
                assess_id = f"RSA-{len(self.assessments) + 1:07d}"
                self.assessments.append({
                    "assessment_id": assess_id, "transaction_id": tx_id,
                    "evidence_fingerprint": sha(tx_id + anomaly + t["risk_level"]
                                                + str(t["risk_score"])),
                    "anomaly_type": anomaly, "risk_level": level,
                    "risk_score": t["risk_score"],
                    "ml_anomaly_score": round(min(max(
                        t["risk_score"] + rng_a.uniform(-0.15, 0.15), 0.01), 0.99), 3),
                    "deterministic_risk_score": t["risk_score"],
                    "recovery_candidate": "true" if recovery_candidate else "false",
                    "recovery_block_reason": "" if recovery_candidate
                    else BLOCK_REASONS.get(anomaly, ""),
                    "reconstruction_root_cause": root_cause,
                    "reconstruction_confidence": conf,
                    "customer_reported_failure":
                        "true" if i in self._false_complaint else "false",
                    "evidence": jdump(ev_items), "triggered_rules": jdump(rules),
                    "model_version": "synthetic-v1", "rule_version": "1",
                    "risk_factors": jdump({
                        "latency": t["gateway_latency_ms"], "retries": t["retry_count"],
                        "network": t["network_quality"],
                        "prior_failures": t["previous_failures"],
                        "unusual_hour": 0 <= t["_ts"].hour <= 5,
                        "foreign": t["country"] != self.cust_by_id[t["customer_id"]]["country"]}),
                    "decision": t["risk_decision"],
                    "model_name": "anomaly-scenario-classifier",
                    "assessed_at": fmt(assess_at), "created_at": fmt(assess_at)})

                if t["failure_prediction"]:
                    self.model_assessments.append({
                        "assessment_id": f"MA-{len(self.model_assessments) + 1:06d}",
                        "transaction_id": tx_id, "model_name": "failure-classifier",
                        "model_version": "xgb-v2.1",
                        "assessment": t["failure_prediction"],
                        "confidence": t["failure_probability"],
                        "factors": jdump({"latency_ms": t["gateway_latency_ms"],
                                          "retries": t["retry_count"],
                                          "network": t["network_quality"]}),
                        "assessed_at": fmt(assess_at - timedelta(seconds=rng_a.uniform(1, 3)))})
                self.model_assessments.append({
                    "assessment_id": f"MA-{len(self.model_assessments) + 1:06d}",
                    "transaction_id": tx_id,
                    "model_name": "anomaly-scenario-classifier",
                    "model_version": "synthetic-v1", "assessment": anomaly,
                    "confidence": round(rng_a.uniform(0.55, 0.97), 3),
                    "factors": jdump({"risk_level": t["risk_level"],
                                      "root_cause": root_cause or "N/A"}),
                    "assessed_at": fmt(assess_at)})

        self._event_counters = {"ev": ev, "pev": pev_n}

    # ------------------------------------------------------- recovery cases
    def build_recoveries(self):
        rng = self.rng_a
        as_by_tx = {a["transaction_id"]: a for a in self.assessments}
        for t in self.transactions:
            if t["scenario"]:
                continue                       # scenarios add their own below
            a = as_by_tx.get(t["transaction_id"])
            if not a or a["anomaly_type"] == "NONE":
                continue
            if t["current_state"] == "STALLED":
                continue                       # stalled: no recovery attempted
            anomaly = a["anomaly_type"]
            completed = t["current_state"] == "LIMIT_RELEASED"
            rejected = t["current_state"] == "RECOVERY_REJECTED"
            manual = t["current_state"] == "MANUAL_REVIEW"
            t0 = parse_ts(t["timestamp"])
            start = t0 + timedelta(minutes=rng.randint(2, 240))
            reason = {
                "GENUINE_FAILURE": "single debit confirmation; settlement not confirmed",
                "DOUBLE_DEDUCTION": "multiple customer debit confirmations",
                "DUPLICATE_TRANSACTION": "provider reference shared with another transaction",
                "INCOMPLETE": "insufficient payment evidence",
                "SUCCESSFUL_BUT_UNCONFIRMED": "settlement confirmed without merchant confirmation",
                "FALSE_COMPLAINT": "evidence contradicts reported failure",
                "SUSPICIOUS": "unusual pattern; manual review",
                "UNKNOWN": "late settlement changed the evidence"}.get(anomaly, "")
            attempts = 0
            released, prov_ref = "", ""
            blocked = ""
            if completed:
                status, decision = "COMPLETED", "RELEASE_LIMIT"
                result, verif = "RECOVERED", "VERIFIED"
                released = round(_bdt(t["amount"], t["currency"]), 2)
                prov_ref = f"REL-{sha(t['transaction_id'] + 'rel')[:8].upper()}"
                attempts = 1
                end_at = start + timedelta(minutes=rng.randint(1, 20))
                verified_at = end_at + timedelta(seconds=rng.randint(2, 30))
            elif anomaly == "UNKNOWN":         # settlement race — gate veto
                status, decision = "BLOCKED", "NO_ACTION"
                result, verif = "NOT_EXECUTED", "NOT_VERIFIED"
                blocked = "NEW_SUCCESSFUL_SETTLEMENT"
                end_at, verified_at = start, ""
            elif rejected:
                status, decision = "BLOCKED", "NO_ACTION"
                result, verif = "NOT_EXECUTED", "NOT_VERIFIED"
                blocked = ("amount above auto-release cap"
                           if _bdt(t["amount"], t["currency"]) > 1500
                           else "previous failures above cap")
                end_at, verified_at = start, ""
            elif anomaly in ("DOUBLE_DEDUCTION", "INCOMPLETE"):
                status, decision = "BLOCKED", "NO_ACTION"
                result, verif = "NOT_EXECUTED", "NOT_VERIFIED"
                blocked = ("DOUBLE_DEDUCTION" if anomaly == "DOUBLE_DEDUCTION"
                           else "INSUFFICIENT_EVIDENCE")
                end_at, verified_at = start, ""
            elif manual:
                q = rng.random()
                if q < 0.45:
                    status, decision = "PENDING", "MANUAL_REVIEW"
                    result, verif = "AWAITING_REVIEW", "NOT_VERIFIED"
                elif q < 0.75:
                    status, decision = "COMPLETED", "MANUAL_REVIEW"
                    result, verif = "RESOLVED_MANUALLY", "REVIEWED"
                else:
                    status, decision = "BLOCKED", "NO_ACTION"
                    result, verif = "NOT_EXECUTED", "NOT_VERIFIED"
                end_at, verified_at = "", ""
            else:
                status, decision = "BLOCKED", "NO_ACTION"
                result, verif = "NOT_EXECUTED", "NOT_VERIFIED"
                blocked = BLOCK_REASONS.get(anomaly, "policy block")
                end_at, verified_at = start, ""

            self.recoveries.append({
                "recovery_id": f"RCV-{len(self.recoveries) + 1:06d}",
                "transaction_id": t["transaction_id"],
                "recovery_required": "true" if anomaly == "GENUINE_FAILURE" else "false",
                "recovery_reason": reason, "recovery_status": status,
                "recovery_attempts": attempts, "recovery_decision": decision,
                "recovery_result": result, "verification_status": verif,
                "requested_amount": released or round(_bdt(t["amount"], t["currency"]), 2),
                "released_amount": released, "currency": "BDT",
                "blocked_reason": blocked, "provider": "mock" if prov_ref else "",
                "provider_reference": prov_ref, "policy_version": "autonomous-v1",
                "risk_assessment_id": a["assessment_id"],
                "idempotency_key": sha(f"{t['transaction_id']}|{decision}|autonomous-v1|"
                                       f"{a['evidence_fingerprint']}"),
                "created_at": fmt(start),
                "completed_at": fmt(end_at) if end_at else "",
                "verified_at": fmt(verified_at) if verified_at else ""})

            # twin observations for the recovery lifecycle
            base = t["current_state"]
            when = start
            for etype, rsn in (("RECOVERY_ELIGIBILITY_ASSESSED", "eligibility assessed"),
                               ("RECOVERY_APPROVED", "policy approved release")):
                self.twin.append({
                    "event_id": f"EVT-{self._event_counters['ev'] + 1:07d}",
                    "transaction_id": t["transaction_id"], "timestamp": fmt(when),
                    "event_type": etype, "previous_state": base, "new_state": base,
                    "failure_prediction": "", "risk_score": t["risk_score"],
                    "safe_to_release_probability": t["safe_to_release_probability"],
                    "safe_to_release": t["safe_to_release"], "reason": rsn,
                    "metadata": ""})
                self._event_counters["ev"] += 1
                when += timedelta(seconds=rng.uniform(1, 4))
            if status == "COMPLETED" and decision == "RELEASE_LIMIT":
                for etype, rsn in (("RECOVERY_STARTED", "sandbox execution started"),
                                   ("RECOVERY_EXECUTED", "provider executed release"),
                                   ("RECOVERY_VERIFIED", "verification passed")):
                    when += timedelta(seconds=rng.uniform(1, 5))
                    self.twin.append({
                        "event_id": f"EVT-{self._event_counters['ev'] + 1:07d}",
                        "transaction_id": t["transaction_id"], "timestamp": fmt(when),
                        "event_type": etype, "previous_state": base, "new_state": base,
                        "failure_prediction": "", "risk_score": "",
                        "safe_to_release_probability": "", "safe_to_release": "",
                        "reason": rsn,
                        "metadata": jdump({"provider_reference": prov_ref})
                        if etype == "RECOVERY_EXECUTED" else ""})
                    self._event_counters["ev"] += 1
        self.twin.sort(key=lambda e: (e["transaction_id"], e["timestamp"]))

    # --------------------------------------------- behavior + relationships
    def build_behavior_and_edges(self):
        tx_by_cust: dict[str, list[dict]] = {}
        for t in self.transactions:
            tx_by_cust.setdefault(t["customer_id"], []).append(t)
        for t in self.transactions:
            t["_dt"] = parse_ts(t["timestamp"])

        def level(v, lo, hi):
            return "LOW" if v < lo else "MEDIUM" if v < hi else "HIGH"

        for c in self.customers:
            cid = c["customer_id"]
            txs = tx_by_cust.get(cid, [])
            days_active = max((END - parse_ts(c["created_at"])).days, 1)
            per_day = len(txs) / days_active
            hours = sorted(t["_dt"] for t in txs)
            max_hourly = 0
            for i, h in enumerate(hours):
                win = [x for x in hours[i:i + 12] if (x - h).total_seconds() <= 3600]
                max_hourly = max(max_hourly, len(win))
            bursts = sum(1 for i in range(len(hours) - 3)
                         if (hours[i + 3] - hours[i]).total_seconds() <= 900)
            recent = [t for t in txs if (END - t["_dt"]).days <= 30]
            prior = [t for t in txs if 30 < (END - t["_dt"]).days <= 90]
            r_avg = sum(float(t["amount"]) for t in recent) / len(recent) if recent else 0
            p_avg = sum(float(t["amount"]) for t in prior) / len(prior) if prior else r_avg
            change = (r_avg / p_avg) if p_avg else 0
            night_share = (sum(1 for h in hours if h.hour <= 5) / len(hours)) if hours else 0
            home = c["country"]
            foreign_share = (sum(1 for t in txs if t["country"] != home) / len(txs)) \
                if txs else 0
            fail_rate = (sum(1 for t in txs if t["failure_reason"]) / len(txs)) \
                if txs else 0
            last_seen = max(((END - h).days for h in hours), default=days_active)
            signals = [
                ("transaction_frequency", round(per_day, 4), "tx/day",
                 level(per_day, 0.02, 0.15)),
                ("transaction_velocity", max_hourly, "max tx in 60 min",
                 level(max_hourly, 2, 5)),
                ("amount_deviation", round(abs(change - 1) if change else 0, 3),
                 "|last30/prior90 - 1|", level(abs(change - 1) if change else 0, 0.3, 0.8)),
                ("unusual_timing", round(night_share, 3), "share 00-05 UTC",
                 level(night_share, 0.05, 0.25)),
                ("unusual_location", round(foreign_share, 3), "share foreign-country tx",
                 level(foreign_share, 0.05, 0.3)),
                ("failed_transaction_rate", round(fail_rate, 3), "failed/all",
                 level(fail_rate, 0.1, 0.3)),
                ("spending_pattern_change", round(change, 3) if change else "",
                 "last30 avg / prior90 avg",
                 level(abs(change - 1) if change else 0, 0.4, 1.0)),
                ("transaction_burst", bursts, "bursts (4 tx/15 min)", level(bursts, 1, 3)),
                ("account_age", days_active, "days",
                 "LOW" if days_active > 180 else "MEDIUM"),
                ("recent_activity", last_seen, "days since last tx",
                 "LOW" if last_seen <= 30 else "MEDIUM" if last_seen <= 90 else "HIGH"),
            ]
            for name, val, unit, lvl in signals:
                self.behaviors.append({
                    "behavior_id": f"BHV-{len(self.behaviors) + 1:06d}",
                    "customer_id": cid, "signal_name": name, "signal_value": val,
                    "unit": unit, "signal_level": lvl,
                    "baseline_source": "own_history_12m", "window": "12 months",
                    "computed_at": fmt(END)})

        # ------------------------------------------------ relationship edges
        def edge(f_t, f_id, t_t, t_id, rel, weight=1, first="", last=""):
            self.edges.append({
                "edge_id": f"EDGE-{len(self.edges) + 1:06d}",
                "from_type": f_t, "from_id": f_id, "to_type": t_t, "to_id": t_id,
                "relation": rel, "weight": weight,
                "first_seen": first or fmt(START), "last_seen": last or fmt(END)})

        tx_by_acc: dict[str, list[dict]] = {}
        for t in self.transactions:
            tx_by_acc.setdefault(t["account_id"], []).append(t)
        for a in self.accounts:
            ts = [t["_dt"] for t in tx_by_acc.get(a["account_id"], [])]
            edge("ACCOUNT", a["account_id"], "USER", a["customer_id"], "OWNED_BY",
                 len(ts), a["created_at"], fmt(max(ts)) if ts else a["created_at"])

        dev_users: dict[str, set] = {}
        for cid, devs in self.cust_devices.items():
            for d in devs:
                dev_users.setdefault(d, set()).add(cid)
        dev_by_id = {d["device_id"]: d for d in self.devices}
        for d, users in dev_users.items():
            for u in sorted(users):
                edge("DEVICE", d, "USER", u, "USED_BY", 1,
                     dev_by_id[d]["first_seen"], fmt(END))
            ul = sorted(users)
            for i in range(len(ul)):
                for j in range(i + 1, len(ul)):
                    edge("USER", ul[i], "USER", ul[j], "SHARED_DEVICE", 1,
                         dev_by_id[d]["first_seen"], fmt(END))

        txs_by_merchant: dict[str, list[dict]] = {}
        for t in self.transactions:
            edge("USER", t["customer_id"], "TRANSACTION", t["transaction_id"], "OWNED_BY")
            if t["merchant_id"]:
                edge("TRANSACTION", t["transaction_id"], "MERCHANT", t["merchant_id"],
                     "PROCESSED_BY")
                txs_by_merchant.setdefault(t["merchant_id"], []).append(t)
            if t["device_id"]:
                edge("TRANSACTION", t["transaction_id"], "DEVICE", t["device_id"],
                     "FROM_DEVICE")
            if t["counterparty"].startswith("CUST-"):
                edge("USER", t["customer_id"], "USER", t["counterparty"], "TRANSFER_TO")
        for mer, txs in txs_by_merchant.items():   # capped to keep file sane
            for i in range(0, min(len(txs) - 1, 8)):
                edge("TRANSACTION", txs[i]["transaction_id"], "TRANSACTION",
                     txs[i + 1]["transaction_id"], "SAME_MERCHANT")
        for ref, idxs in self._shared_refs.items():
            ids = [self.transactions[i]["transaction_id"] for i in idxs]
            for x in ids:
                edge("TRANSACTION", x, "REFERENCE", f"REF-DUP-{ref}", "REFERENCES")
            for i in range(len(ids) - 1):
                edge("TRANSACTION", ids[i], "TRANSACTION", ids[i + 1], "SAME_REFERENCE")
        seen_src = set()
        for p in self.pay_events:
            key = (p["transaction_id"], p["source"])
            if key not in seen_src:
                seen_src.add(key)
                edge("TRANSACTION", p["transaction_id"], "GATEWAY", p["source"],
                     "VIA_SOURCE")

    # ------------------------------------------------------------- scenarios
    def add_scenarios(self):
        """S1..S6 as first-class rows using the SAME structures as everything
        else (api/services/demo_scenarios.py semantics)."""
        rng = random.Random(self.seed * 1000 + 9)
        base_day = END - timedelta(days=14)
        cust_ids = [c["customer_id"] for c in self.customers]

        def twin(tx, when, etype, prev, new, reason, meta=""):
            self._event_counters["ev"] += 1
            self.twin.append({
                "event_id": f"EVT-{self._event_counters['ev']:07d}",
                "transaction_id": tx, "timestamp": fmt(when), "event_type": etype,
                "previous_state": prev, "new_state": new, "failure_prediction": "",
                "risk_score": "", "safe_to_release_probability": "",
                "safe_to_release": "", "reason": reason, "metadata": meta})

        def pev(tx, when, etype, ref, meta=""):
            self._event_counters["pev"] += 1
            info = EVENT_INFO[etype]
            self.pay_events.append({
                "event_id": f"PEV-{self._event_counters['pev']:07d}",
                "transaction_id": tx,
                "provider_event_id": f"PEV-SCN-{self._event_counters['pev']:07d}",
                "event_type": etype, "source": info["source"],
                "status": info["outcome"], "event_timestamp": fmt(when),
                "reference_id": ref, "latency_ms": rng.randint(200, 4000),
                "metadata": meta, "correlation_id": tx, "causation_id": "",
                "schema_version": "1"})

        for key in ("S1", "S2", "S3", "S4", "S5", "S6"):
            cust = cust_ids[(int(key[1]) * 83) % len(cust_ids)]
            cust_obj = self.cust_by_id[cust]
            my_acc = next(a for a in self.accounts if a["customer_id"] == cust)
            t0 = base_day + timedelta(days=int(key[1]) - 1, hours=rng.randint(9, 20),
                                      minutes=rng.randint(0, 59))
            created = parse_ts(cust_obj["created_at"])
            kind = {"S1": "merchant_timeout", "S2": "double_deduction",
                    "S3": "success", "S4": "single_debit",
                    "S5": "merchant_timeout_race", "S6": "merchant_timeout"}[key]
            amount = {"S1": 1250.00, "S2": 890.00, "S3": 640.00,
                      "S4": 450.00, "S5": 1499.00, "S6": 1750.00}[key]
            tx_id = f"DEMO-{key}"
            ref = f"REF-{key}-DEMO"
            t = {
                "transaction_id": tx_id, "customer_id": cust,
                "account_id": my_acc["account_id"],
                "merchant_id": self.merchants[0]["merchant_id"], "counterparty": "",
                "direction": "debit", "amount": amount, "currency": my_acc["currency"],
                "transaction_type": "purchase", "channel": "mobile_app",
                "country": cust_obj["country"],
                "device_id": (self.cust_devices.get(cust) or [""])[0],
                "timestamp": fmt(t0), "gateway_latency_ms": 4800, "retry_count": 2,
                "network_quality": "Fair", "previous_failures": 1,
                "account_age_days": max(0, (t0 - created).days),
                "failure_reason": "Timeout", "current_state": "", "scenario": key,
                "risk_score": 0.81 if kind == "double_deduction" else 0.12,
                "risk_level": "CRITICAL" if kind == "double_deduction" else "LOW",
                "risk_decision": "BLOCK" if kind == "double_deduction" else "ALLOW",
                "failure_prediction": "Timeout", "failure_probability": 0.9,
                "safe_to_release_probability":
                    0.11 if kind == "double_deduction" else 0.94,
                "safe_to_release":
                    "false" if kind == "double_deduction" else "true",
                "_outcome": "FAILED", "_ts": t0,
            }
            when = t0
            if kind == "success":
                t["failure_reason"] = ""
                t["current_state"] = "SUCCESS"
                t["risk_score"], t["risk_level"], t["risk_decision"] = 0.03, "LOW", "ALLOW"
                t["safe_to_release_probability"], t["safe_to_release"] = 0.99, "true"
                for et, pv, nw in (("TRANSACTION_CREATED", "", "INITIATED"),
                                   ("PAYMENT_PROCESSING", "INITIATED", "PROCESSING"),
                                   ("PAYMENT_SUCCEEDED", "PROCESSING", "SUCCESS")):
                    when += timedelta(seconds=4)
                    twin(tx_id, when, et, pv, nw,
                         "transaction registered" if et == "TRANSACTION_CREATED" else "")
                w2 = t0 + timedelta(seconds=2)
                for e in HAPPY_PATH:
                    w2 += timedelta(seconds=4)
                    pev(tx_id, w2, e, ref)
            elif kind == "single_debit":
                t["current_state"] = "MANUAL_REVIEW"
                t["risk_level"] = "UNKNOWN"
                chain = (("TRANSACTION_CREATED", "", "INITIATED"),
                         ("PAYMENT_PROCESSING", "INITIATED", "PROCESSING"),
                         ("PAYMENT_FAILED", "PROCESSING", "FAILED"),
                         ("ML_RISK_ASSESSED", "FAILED", "RISK_ASSESSED"),
                         ("RECOVERY_CHECKED", "RISK_ASSESSED", "RECOVERY_PENDING"),
                         ("MANUAL_REVIEW", "RECOVERY_PENDING", "MANUAL_REVIEW"))
                for et, pv, nw in chain:
                    when += timedelta(seconds=4)
                    twin(tx_id, when, et, pv, nw, "")
                pev(tx_id, t0 + timedelta(seconds=2), "CUSTOMER_DEBIT_CONFIRMED", ref)
            elif kind == "double_deduction":
                t["current_state"] = "MANUAL_REVIEW"
                chain = (("TRANSACTION_CREATED", "", "INITIATED"),
                         ("PAYMENT_PROCESSING", "INITIATED", "PROCESSING"),
                         ("PAYMENT_FAILED", "PROCESSING", "FAILED"),
                         ("ML_RISK_ASSESSED", "FAILED", "RISK_ASSESSED"),
                         ("RECOVERY_CHECKED", "RISK_ASSESSED", "RECOVERY_PENDING"),
                         ("MANUAL_REVIEW", "RECOVERY_PENDING", "MANUAL_REVIEW"))
                for et, pv, nw in chain:
                    when += timedelta(seconds=4)
                    twin(tx_id, when, et, pv, nw, "")
                pev(tx_id, t0 + timedelta(seconds=2), "CUSTOMER_DEBIT_CONFIRMED", ref)
                pev(tx_id, t0 + timedelta(seconds=95), "CUSTOMER_DEBIT_CONFIRMED",
                    ref + "-B", jdump({"attempt": 2}))
                pev(tx_id, t0 + timedelta(seconds=100), "GATEWAY_REQUEST_SENT", ref)
                pev(tx_id, t0 + timedelta(seconds=140), "GATEWAY_TIMEOUT", ref)
            else:  # merchant_timeout / merchant_timeout_race
                race = kind == "merchant_timeout_race"
                t["current_state"] = "MANUAL_REVIEW" if race else "LIMIT_RELEASED"
                if race:
                    t["risk_level"] = "UNKNOWN"
                chain = (("TRANSACTION_CREATED", "", "INITIATED"),
                         ("PAYMENT_PROCESSING", "INITIATED", "PROCESSING"),
                         ("PAYMENT_FAILED", "PROCESSING", "FAILED"),
                         ("ML_RISK_ASSESSED", "FAILED", "RISK_ASSESSED"),
                         ("RECOVERY_CHECKED", "RISK_ASSESSED", "RECOVERY_PENDING"),
                         ("MANUAL_REVIEW" if race else "LIMIT_RELEASED",
                          "RECOVERY_PENDING",
                          "MANUAL_REVIEW" if race else "LIMIT_RELEASED"))
                for et, pv, nw in chain:
                    when += timedelta(seconds=4)
                    twin(tx_id, when, et, pv, nw, "")
                assess_at = t0 + timedelta(seconds=24)
                w2 = t0 + timedelta(seconds=2)
                for e in ("CUSTOMER_DEBIT_CONFIRMED", "GATEWAY_REQUEST_SENT",
                          "GATEWAY_RESPONSE_RECEIVED", "MERCHANT_CONFIRMATION_REQUESTED"):
                    w2 += timedelta(seconds=4)
                    pev(tx_id, w2, e, ref)
                pev(tx_id, t0 + timedelta(seconds=45), "MERCHANT_CONFIRMATION_TIMEOUT", ref)
                if race:
                    pev(tx_id, assess_at + timedelta(minutes=12), "SETTLEMENT_CONFIRMED", ref)
                    twin(tx_id, assess_at + timedelta(minutes=12, seconds=1),
                         "LATE_SETTLEMENT", t["current_state"], t["current_state"],
                         "settlement confirmed after risk assessment", jdump({"late": True}))

            # assessment row
            anomaly = {"double_deduction": "DOUBLE_DEDUCTION", "success": "NONE",
                       "single_debit": "INCOMPLETE",
                       "merchant_timeout_race": "UNKNOWN"}.get(kind, "GENUINE_FAILURE")
            lvl = {"double_deduction": "CRITICAL", "success": "LOW",
                   "single_debit": "UNKNOWN", "merchant_timeout_race": "UNKNOWN"}.get(kind, "LOW")
            self.assessments.append({
                "assessment_id": f"RSA-{len(self.assessments) + 1:07d}",
                "transaction_id": tx_id, "evidence_fingerprint": sha(tx_id + anomaly),
                "anomaly_type": anomaly, "risk_level": lvl,
                "risk_score": t["risk_score"],
                "ml_anomaly_score": round(min(t["risk_score"] + 0.08, 0.99), 3),
                "deterministic_risk_score": t["risk_score"],
                "recovery_candidate": "true" if anomaly == "GENUINE_FAILURE" else "false",
                "recovery_block_reason": "" if anomaly == "GENUINE_FAILURE"
                else BLOCK_REASONS.get(anomaly, ""),
                "reconstruction_root_cause": "MERCHANT_TIMEOUT" if "timeout" in kind else "",
                "reconstruction_confidence": 0.71 if "timeout" in kind else "",
                "customer_reported_failure": "false",
                "evidence": jdump([{"code": "SCENARIO",
                                    "description": f"pinned demo scenario {key}",
                                    "severity": "LOW"}]),
                "triggered_rules": jdump([{"rule_id": "R3",
                                           "name": "genuine gateway failure"}]
                                         if anomaly == "GENUINE_FAILURE" else []),
                "model_version": "synthetic-v1", "rule_version": "1",
                "risk_factors": jdump({"latency": t["gateway_latency_ms"],
                                       "retries": t["retry_count"]}),
                "decision": t["risk_decision"],
                "model_name": "anomaly-scenario-classifier",
                "assessed_at": fmt(t0 + timedelta(seconds=24)),
                "created_at": fmt(t0 + timedelta(seconds=24))})

            # recovery row
            if kind != "merchant_timeout" or True:   # every scenario has a record
                released = amount if t["current_state"] == "LIMIT_RELEASED" else ""
                prov = f"REL-{sha(tx_id)[:8].upper()}" if released else ""
                blocked = "" if released else {
                    "double_deduction": "DOUBLE_DEDUCTION", "success": "ALREADY_SUCCESS",
                    "single_debit": "INSUFFICIENT_EVIDENCE",
                    "merchant_timeout_race": "NEW_SUCCESSFUL_SETTLEMENT"}.get(kind, "")
                self.recoveries.append({
                    "recovery_id": f"RCV-{len(self.recoveries) + 1:06d}",
                    "transaction_id": tx_id,
                    "recovery_required": "true" if "timeout" in kind else "false",
                    "recovery_reason": "genuine failure; held limit released in sandbox"
                    if released else (blocked.lower().replace("_", " ")
                                      + " — autonomous recovery refused"),
                    "recovery_status": "COMPLETED" if released else "BLOCKED",
                    "recovery_attempts": 2 if key == "S6" else (1 if released else 0),
                    "recovery_decision": "RELEASE_LIMIT" if released else "NO_ACTION",
                    "recovery_result": "RECOVERED" if released else "NOT_EXECUTED",
                    "verification_status": "VERIFIED" if released else "NOT_VERIFIED",
                    "requested_amount": amount, "released_amount": released,
                    "currency": my_acc["currency"], "blocked_reason": blocked,
                    "provider": "mock" if prov else "", "provider_reference": prov,
                    "policy_version": "autonomous-v1",
                    "risk_assessment_id": self.assessments[-1]["assessment_id"],
                    "idempotency_key": sha(f"{tx_id}|RELEASE_LIMIT|autonomous-v1|{key}"),
                    "created_at": fmt(t0 + timedelta(seconds=30)),
                    "completed_at": fmt(t0 + timedelta(seconds=60)) if released
                    else fmt(t0 + timedelta(seconds=30)),
                    "verified_at": fmt(t0 + timedelta(seconds=90)) if released else ""})
                if released:
                    w3 = t0 + timedelta(seconds=34)
                    base = t["current_state"]
                    for et, rsn in (("RECOVERY_ELIGIBILITY_ASSESSED", "eligibility assessed"),
                                    ("RECOVERY_APPROVED", "policy approved release"),
                                    ("RECOVERY_STARTED", "sandbox execution started"),
                                    ("RECOVERY_EXECUTED", "provider executed release"),
                                    ("RECOVERY_VERIFIED", "verification passed")):
                        w3 += timedelta(seconds=4)
                        twin(tx_id, w3, et, base, base, rsn,
                             jdump({"provider_reference": prov})
                             if et == "RECOVERY_EXECUTED" else "")
                else:
                    w3 = t0 + timedelta(seconds=34)
                    twin(tx_id, w3, "RECOVERY_ELIGIBILITY_ASSESSED", t["current_state"],
                         t["current_state"], "eligibility assessed")
                    w3 += timedelta(seconds=4)
                    twin(tx_id, w3, "RECOVERY_BLOCKED", t["current_state"],
                         t["current_state"], f"blocked: {blocked}")
            self.transactions.append(t)
        self.twin.sort(key=lambda e: (e["transaction_id"], e["timestamp"]))

    # ------------------------------------------------------------- finalize
    def finalize_accounts(self):
        net: dict[str, float] = {a["account_id"]: float(a["opening_balance"])
                                 for a in self.accounts}
        last: dict[str, str] = {a["account_id"]: a["created_at"] for a in self.accounts}
        for t in sorted(self.transactions, key=lambda x: x["timestamp"]):
            if not t["account_id"]:
                continue
            amt = float(t["amount"]) if t["currency"] == \
                next(a["currency"] for a in self.accounts
                     if a["account_id"] == t["account_id"]) \
                else float(t["amount"]) * 100  # defensive: shouldn't happen
            net[t["account_id"]] += amt if t["direction"] == "credit" else -amt
            if t["timestamp"] > last[t["account_id"]]:
                last[t["account_id"]] = t["timestamp"]
        for a in self.accounts:
            a["balance"] = round(max(net[a["account_id"]], 0.0), 2)
            a["last_activity_at"] = last[a["account_id"]]

    # ----------------------------------------------------------------- write
    def write_all(self) -> dict:
        self.out.mkdir(parents=True, exist_ok=True)
        P = lambda name: self.out / name
        counts = {}
        counts["customers.csv"] = write_csv(P("customers.csv"), [
            "customer_id", "full_name", "email", "phone", "country", "created_at",
            "status", "segment", "risk_profile", "archetype"], self.customers)
        counts["accounts.csv"] = write_csv(P("accounts.csv"), [
            "account_id", "customer_id", "account_type", "currency", "balance",
            "opening_balance", "status", "created_at", "last_activity_at", "primary"],
            self.accounts)
        counts["merchants.csv"] = write_csv(P("merchants.csv"), [
            "merchant_id", "name", "category", "country", "risk_tier",
            "traffic_weight", "created_at"], self.merchants)
        counts["devices.csv"] = write_csv(P("devices.csv"), [
            "device_id", "os", "model", "trusted", "first_seen"], self.devices)
        counts["transactions.csv"] = write_csv(P("transactions.csv"), [
            "transaction_id", "customer_id", "account_id", "merchant_id", "counterparty",
            "direction", "amount", "currency", "transaction_type", "channel", "country",
            "device_id", "timestamp", "gateway_latency_ms", "retry_count",
            "network_quality", "previous_failures", "account_age_days", "failure_reason",
            "current_state", "scenario", "risk_score", "risk_level", "risk_decision",
            "failure_prediction", "failure_probability",
            "safe_to_release_probability", "safe_to_release"],
            [{k: v for k, v in t.items() if not k.startswith("_")}
             for t in self.transactions])
        counts["digital_twin_events.csv"] = write_csv(P("digital_twin_events.csv"), [
            "event_id", "transaction_id", "timestamp", "event_type", "previous_state",
            "new_state", "failure_prediction", "risk_score",
            "safe_to_release_probability", "safe_to_release", "reason", "metadata"],
            self.twin)
        counts["payment_events.csv"] = write_csv(P("payment_events.csv"), [
            "event_id", "transaction_id", "provider_event_id", "event_type", "source",
            "status", "event_timestamp", "reference_id", "latency_ms", "metadata",
            "correlation_id", "causation_id", "schema_version"], self.pay_events)
        counts["risk_assessments.csv"] = write_csv(P("risk_assessments.csv"), [
            "assessment_id", "transaction_id", "evidence_fingerprint", "anomaly_type",
            "risk_level", "risk_score", "ml_anomaly_score", "deterministic_risk_score",
            "recovery_candidate", "recovery_block_reason", "reconstruction_root_cause",
            "reconstruction_confidence", "customer_reported_failure", "evidence",
            "triggered_rules", "model_version", "rule_version", "risk_factors",
            "decision", "model_name", "assessed_at", "created_at"], self.assessments)
        counts["model_assessments.csv"] = write_csv(P("model_assessments.csv"), [
            "assessment_id", "transaction_id", "model_name", "model_version",
            "assessment", "confidence", "factors", "assessed_at"],
            self.model_assessments)
        counts["behavior_signals.csv"] = write_csv(P("behavior_signals.csv"), [
            "behavior_id", "customer_id", "signal_name", "signal_value", "unit",
            "signal_level", "baseline_source", "window", "computed_at"], self.behaviors)
        counts["relationship_edges.csv"] = write_csv(P("relationship_edges.csv"), [
            "edge_id", "from_type", "from_id", "to_type", "to_id", "relation",
            "weight", "first_seen", "last_seen"], self.edges)
        counts["recovery_cases.csv"] = write_csv(P("recovery_cases.csv"), [
            "recovery_id", "transaction_id", "recovery_required", "recovery_reason",
            "recovery_status", "recovery_attempts", "recovery_decision",
            "recovery_result", "verification_status", "requested_amount",
            "released_amount", "currency", "blocked_reason", "provider",
            "provider_reference", "policy_version", "risk_assessment_id",
            "idempotency_key", "created_at", "completed_at", "verified_at"],
            self.recoveries)
        return counts


# ================================================================ validation
def validate(d: Dataset) -> list[str]:
    errs: list[str] = []
    cust_ids = {c["customer_id"] for c in d.customers}
    acc_ids = {a["account_id"] for a in d.accounts}
    mer_ids = {m["merchant_id"] for m in d.merchants}
    dev_ids = {x["device_id"] for x in d.devices}
    tx_ids = [t["transaction_id"] for t in d.transactions]
    tx_set = set(tx_ids)

    def uniq(rows, key, label, iterable=False):
        seen = set()
        src = rows if iterable else (r[key] for r in rows)
        for k in src:
            if k in seen:
                errs.append(f"duplicate {label}: {k}")
            seen.add(k)

    uniq(d.customers, "customer_id", "customer")
    uniq(d.accounts, "account_id", "account")
    uniq(d.devices, "device_id", "device")
    uniq(tx_ids, "", "transaction_id", iterable=True)
    uniq(d.twin, "event_id", "twin event")
    uniq(d.pay_events, "event_id", "payment event")
    uniq(d.pay_events, "provider_event_id", "provider_event_id")
    uniq(d.assessments, "assessment_id", "assessment")
    uniq(d.model_assessments, "assessment_id", "model assessment")
    uniq(d.behaviors, "behavior_id", "behavior")
    uniq(d.edges, "edge_id", "edge")
    uniq(d.recoveries, "recovery_id", "recovery")
    uniq(d.recoveries, "transaction_id", "recovery per transaction")

    acc_owner = {a["account_id"]: a["customer_id"] for a in d.accounts}
    for t in d.transactions:
        if t["customer_id"] not in cust_ids:
            errs.append(f"orphan tx customer {t['customer_id']}")
        if t["account_id"] and t["account_id"] not in acc_ids:
            errs.append(f"orphan tx account {t['account_id']}")
        elif t["account_id"] and acc_owner[t["account_id"]] != t["customer_id"]:
            errs.append(f"tx account not owned by customer: {t['transaction_id']}")
        if t["merchant_id"] and t["merchant_id"] not in mer_ids:
            errs.append(f"orphan tx merchant {t['merchant_id']}")
        if t["device_id"] and t["device_id"] not in dev_ids:
            errs.append(f"orphan tx device {t['device_id']}")
        if not (START <= parse_ts(t["timestamp"]) <= END):
            errs.append(f"tx out of window: {t['transaction_id']}")

    tx_by_id = {t["transaction_id"]: t for t in d.transactions}
    for e in d.twin:
        if e["transaction_id"] not in tx_set:
            errs.append(f"orphan twin event tx {e['transaction_id']}")
    for p in d.pay_events:
        if p["transaction_id"] not in tx_set:
            errs.append(f"orphan payment event tx {p['transaction_id']}")
        if p["event_type"] not in EVENT_INFO:
            errs.append(f"bad payment event type {p['event_type']}")
        elif EVENT_INFO[p["event_type"]]["outcome"] != p["status"]:
            errs.append(f"payment event status mismatch {p['event_id']}")
    for a in d.assessments + d.model_assessments + d.recoveries:
        if a["transaction_id"] not in tx_set:
            errs.append(f"orphan assessment/recovery tx {a['transaction_id']}")

    twin_by_tx: dict[str, list] = defaultdict(list)
    for e in d.twin:
        twin_by_tx[e["transaction_id"]].append(e)
    for tx, evs in twin_by_tx.items():
        times = [parse_ts(e["timestamp"]) for e in evs]
        if times != sorted(times):
            errs.append(f"twin not chronological: {tx}")
        if parse_ts(tx_by_id[tx]["timestamp"]) > times[0]:
            errs.append(f"twin starts before tx: {tx}")
    pev_by_tx: dict[str, list] = defaultdict(list)
    for p in d.pay_events:
        pev_by_tx[p["transaction_id"]].append(p)
    for tx, evs in pev_by_tx.items():
        times = [parse_ts(e["event_timestamp"]) for e in evs]
        if times != sorted(times):
            errs.append(f"payment events not chronological: {tx}")

    for r in d.recoveries:
        t = tx_by_id[r["transaction_id"]]
        if r["verification_status"] == "VERIFIED":
            if not r["released_amount"] or not r["provider_reference"]:
                errs.append(f"VERIFIED without release: {r['recovery_id']}")
            if t["current_state"] != "LIMIT_RELEASED":
                errs.append(f"VERIFIED but tx not LIMIT_RELEASED: {r['transaction_id']}")
        if r["recovery_status"] == "BLOCKED" and r["provider_reference"]:
            errs.append(f"BLOCKED but provider called: {r['recovery_id']}")
        if r["transaction_id"] == "DEMO-S5" and t["current_state"] == "LIMIT_RELEASED":
            errs.append("S5 must never be released")
    s2 = [e for e in d.pay_events if e["transaction_id"] == "DEMO-S2"
          and e["event_type"] == "CUSTOMER_DEBIT_CONFIRMED"]
    if len(s2) != 2:
        errs.append("S2 must have exactly 2 debit confirmations")
    s6 = next((r for r in d.recoveries if r["transaction_id"] == "DEMO-S6"), None)
    if s6 and (s6["verification_status"] != "VERIFIED" or s6["recovery_attempts"] != 2):
        errs.append("S6 must be VERIFIED with 2 recorded attempts (replay)")

    scores = [float(t["risk_score"]) for t in d.transactions if t["risk_score"] != ""]
    if len(set(scores)) < 500:
        errs.append(f"risk scores not diverse: {len(set(scores))} distinct")
    repeat = Counter(scores).most_common(1)[0]
    if repeat[1] > 0.025 * len(scores):
        errs.append(f"risk value flooding: {repeat[0]} appears on "
                    f"{repeat[1]} of {len(scores)} transactions")

    minimums = {
        "customers": (len(d.customers), 500), "accounts": (len(d.accounts), 700),
        "transactions": (len(d.transactions), 10_000),
        "twin_events": (len(d.twin), 20_000),
        "risk_assessments": (len(d.assessments), 5_000),
        "behavior_signals": (len(d.behaviors), 3_000),
        "relationship_edges": (len(d.edges), 3_000),
        "payment_events": (len(d.pay_events), 1_000),
        "recovery_cases": (len(d.recoveries), 100),
    }
    for label, (got, need) in minimums.items():
        if got < need:
            errs.append(f"below minimum {label}: {got} < {need}")
    return errs


# ==================================================================== main
def write_readme(out: Path, counts: dict, seed: int) -> None:
    lines = [
        "# Full synthetic dataset (data/dataset/)",
        "",
        "100% SYNTHETIC test data. Clearly-fake identities (example.com emails,",
        "generated name combinations, fake phone numbers). No real persons,",
        "accounts or payments exist here.",
        "",
        f"Regenerate: `python -m scripts.generate_full_dataset` (seed {seed}).",
        "Re-running produces byte-identical data files (fixed seed).",
        "",
        "| File | Rows | Contents |",
        "|---|---|---|",
    ]
    desc = {
        "customers.csv": "customers: identity, country, segment, risk profile, archetype",
        "accounts.csv": "accounts per customer; balances reconciled against transactions",
        "merchants.csv": "merchant catalog with category + risk tier",
        "devices.csv": "device registry; last 18 are deliberately shared (mule signal)",
        "transactions.csv": "transactions with ML attributes, state, risk score/level/decision",
        "digital_twin_events.csv": "append-only Digital Twin timeline (state hops + observations)",
        "payment_events.csv": "14-type payment lifecycle evidence (Stage 6 shape)",
        "risk_assessments.csv": "Stage 7-shape assessments: anomaly taxonomy, evidence, rules",
        "model_assessments.csv": "per-model records (name/version/confidence/factors)",
        "behavior_signals.csv": "10 signals per customer, computed from their own history",
        "relationship_edges.csv": "USER/ACCOUNT/DEVICE/MERCHANT/GATEWAY/REFERENCE edges",
        "recovery_cases.csv": "Stage 8-shape recovery records (idempotency keys included)",
    }
    for f, n in sorted(counts.items()):
        lines.append(f"| `{f}` | {n:,} | {desc.get(f, '')} |")
    lines += [
        "",
        "## S1-S6 scenarios",
        "",
        "`transactions.csv` rows with `scenario` S1..S6 mirror",
        "`api/services/demo_scenarios.py` semantics using the same structures as",
        "every other transaction (S1/S6 auto-recovered VERIFIED — S6 carries 2",
        "recorded attempts for the replay story; S2 double deduction BLOCKED",
        "with provider never called; S3 already-success; S4 insufficient",
        "evidence; S5 late-settlement race — never released).",
        "",
        "## Conventions",
        "",
        "- All timestamps UTC, `YYYY-MM-DD HH:MM:SS+00:00`.",
        "- `risk_score` is derived from transaction attributes (latency, retries,",
        "  network, hour, geography, shared device) — not random noise.",
        "- Empty string = not applicable / not observed (matches Stage 6's",
        "  \"not observed is derived, never stored\" honesty rule).",
        "- P2P transfers are represented as a single row on the sender's account",
        "  (counterparty customer in `counterparty`); mule fan-in is represented",
        "  as credit rows on the mule's account. There is no double-entry ledger.",
        "- Not loaded into the database automatically; a future loader can map",
        "  these files 1:1 onto existing tables without schema changes.",
    ]
    (out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the full synthetic dataset")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--scale", type=float, default=1.0,
                    help="shrink/grow target counts (1.0 = full size)")
    ap.add_argument("--out-dir", default="data/dataset")
    args = ap.parse_args()

    d = Dataset(args.seed, args.scale, Path(args.out_dir))
    print("[1/9] merchants + devices")
    d.gen_merchants()
    d.gen_devices()
    print("[2/9] customers + accounts")
    d.gen_customers_accounts()
    print(f"[3/9] transactions (target ~{round(10_500 * args.scale):,})")
    d.gen_transactions()
    print("[4/9] states, risk scores, anomaly classes")
    d.plan_outcomes()
    print("[5/9] twin timeline + payment events + assessments")
    d.build_events_and_assessments()
    print("[6/9] recovery cases")
    d.build_recoveries()
    print("[7/9] behavior signals + relationship edges")
    d.build_behavior_and_edges()
    print("[8/9] scenario records S1-S6")
    d.add_scenarios()
    d.finalize_accounts()
    print("[9/9] validating + writing")
    errs = validate(d)
    counts = d.write_all()

    manifest = {
        "seed": args.seed,
        "scale": args.scale,
        "generated_at": fmt(datetime.now(timezone.utc)),
        "window": {"start": fmt(START), "end": fmt(END)},
        "counts": counts,
        "synthetic": True,
        "note": "100% synthetic data — no real persons, accounts, or payments.",
        "vocabulary_alignment": {
            "states": "api/core/state_machine.py",
            "payment_event_types": len(EVENT_INFO),
            "anomaly_taxonomy": "api/services/anomaly_rules.py (9 values)",
            "scenarios": "api/services/demo_scenarios.py S1-S6",
        },
    }
    (d.out / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                         encoding="utf-8")
    write_readme(d.out, counts, args.seed)

    print("\n=== dataset summary ===")
    for f, n in sorted(counts.items()):
        print(f"  {f:28s} {n:>7,}")
    scores = [float(t["risk_score"]) for t in d.transactions if t["risk_score"] != ""]
    buckets = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
    for s in scores:
        buckets["LOW" if s < 0.25 else "MEDIUM" if s < 0.5
                else "HIGH" if s < 0.75 else "CRITICAL"] += 1
    print(f"  risk levels:        {buckets}")
    print(f"  distinct scores:    {len(set(scores)):,}")
    anomalies: dict[str, int] = {}
    for a in d.assessments:
        anomalies[a["anomaly_type"]] = anomalies.get(a["anomaly_type"], 0) + 1
    print(f"  anomaly mix:        {dict(sorted(anomalies.items(), key=lambda x: -x[1]))}")
    states: dict[str, int] = {}
    for t in d.transactions:
        states[t["current_state"]] = states.get(t["current_state"], 0) + 1
    print(f"  tx states:          {dict(sorted(states.items(), key=lambda x: -x[1]))}")
    print(f"\noutput: {d.out.resolve()}")
    if errs:
        print(f"\nVALIDATION FAILED ({len(errs)} problems):")
        for e in errs[:20]:
            print(f"  - {e}")
        return 1
    print("VALIDATION PASSED — FK integrity, chronology, uniqueness, "
          "state coherence, minimums, S1-S6 properties all OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
