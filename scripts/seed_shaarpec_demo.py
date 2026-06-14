#!/usr/bin/env python3
"""
SHAARPEC/Healthix Demo Seed Script

Seeds 50 demo patients with clinically realistic longitudinal trajectories
demonstrating the three PTIP use cases:

  1. HFrEF cohort — heart failure patients with medication escalation timelines,
     comorbidities (HTN, DM2, CKD, AFib), and lab trends
  2. Readmission risk — patients with index hospitalizations followed by ED visits
     and 30-day readmissions, across two fictional facilities (MountSinai, LIJ)
  3. Patient leakage — trajectories that cross facility boundaries, detectable
     only via cross-institutional graph traversal

All FHIR resources are POSTed to arno-fhir, which calls the ivg sidecar
(/fhir-event) to materialize pointer nodes and temporal edges in real time.

Usage:
  python scripts/seed_shaarpec_demo.py
  python scripts/seed_shaarpec_demo.py --arno-url http://dpgenai1:8094 \\
      --sidecar-url http://dpgenai1:8765 --patients 50
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Clinical vocabulary
# ---------------------------------------------------------------------------

# ICD-10 codes for the HFrEF / cardiac comorbidity cluster
CONDITIONS = {
    "HFrEF":          ("I50.20", "Heart failure with reduced ejection fraction, unspecified"),
    "HTN":            ("I10",    "Essential (primary) hypertension"),
    "DM2":            ("E11.9",  "Type 2 diabetes mellitus without complications"),
    "CKD3":           ("N18.3",  "Chronic kidney disease, stage 3"),
    "AFib":           ("I48.91", "Unspecified atrial fibrillation"),
    "CAD":            ("I25.10", "Atherosclerotic heart disease of native coronary artery"),
    "COPD":           ("J44.1",  "COPD with acute exacerbation"),
    "Obesity":        ("E66.09", "Other obesity due to excess calories"),
    "Anemia":         ("D64.9",  "Anemia, unspecified"),
    "Depression":     ("F32.9",  "Major depressive disorder, single episode, unspecified"),
}

# LOINC codes for labs relevant to HFrEF / readmission risk
LABS = {
    "BNP":         ("33762-6",  "BNP [Mass/volume] in Serum or Plasma",         "pg/mL",  (100, 5000)),
    "Creatinine":  ("2160-0",   "Creatinine [Mass/volume] in Serum or Plasma",  "mg/dL",  (0.6, 4.5)),
    "Sodium":      ("2951-2",   "Sodium [Moles/volume] in Serum or Plasma",     "mmol/L", (128, 145)),
    "Potassium":   ("6298-4",   "Potassium [Moles/volume] in Serum or Plasma",  "mmol/L", (3.0, 5.5)),
    "Hemoglobin":  ("718-7",    "Hemoglobin [Mass/volume] in Blood",            "g/dL",   (7.0, 14.0)),
    "EF":          ("10230-1",  "Left ventricular Ejection fraction",           "%",      (15, 45)),
    "NYHA":        ("88020-3",  "NYHA Heart failure classification",            "class",  (1, 4)),
}

# RxNorm-style medication codes for HF medication escalation
MEDICATIONS = {
    "Furosemide_20":    ("313988",  "Furosemide 20 MG Oral Tablet",         "furosemide",    "20mg"),
    "Furosemide_40":    ("313989",  "Furosemide 40 MG Oral Tablet",         "furosemide",    "40mg"),
    "Furosemide_80":    ("197900",  "Furosemide 80 MG Oral Tablet",         "furosemide",    "80mg"),
    "Lisinopril_5":     ("314077",  "Lisinopril 5 MG Oral Tablet",          "lisinopril",    "5mg"),
    "Lisinopril_10":    ("314076",  "Lisinopril 10 MG Oral Tablet",         "lisinopril",    "10mg"),
    "Lisinopril_20":    ("314073",  "Lisinopril 20 MG Oral Tablet",         "lisinopril",    "20mg"),
    "Carvedilol_6":     ("200031",  "Carvedilol 6.25 MG Oral Tablet",       "carvedilol",    "6.25mg"),
    "Carvedilol_12":    ("200032",  "Carvedilol 12.5 MG Oral Tablet",       "carvedilol",    "12.5mg"),
    "Carvedilol_25":    ("200033",  "Carvedilol 25 MG Oral Tablet",         "carvedilol",    "25mg"),
    "Spironolactone":   ("313096",  "Spironolactone 25 MG Oral Tablet",     "spironolactone","25mg"),
    "Sacubitril":       ("1656340", "Sacubitril-Valsartan 49/51 MG Tablet", "entresto",      "49/51mg"),
    "Empagliflozin":    ("2359264", "Empagliflozin 10 MG Oral Tablet",      "jardiance",     "10mg"),
    "Apixaban":         ("1599538", "Apixaban 5 MG Oral Tablet",            "apixaban",      "5mg"),
    "Metformin":        ("860975",  "Metformin 500 MG Oral Tablet",         "metformin",     "500mg"),
    "Amlodipine":       ("308135",  "Amlodipine 5 MG Oral Tablet",          "amlodipine",    "5mg"),
}

# Two fictional Healthix-connected facilities for cross-institutional leakage demo
FACILITIES = {
    "MSH":  {"name": "Mount Sinai Hospital",          "npi": "1234567890"},
    "LIJ":  {"name": "Long Island Jewish Medical Ctr","npi": "0987654321"},
    "NYP":  {"name": "NewYork-Presbyterian Hospital", "npi": "1122334455"},
    "SUNY": {"name": "SUNY Downstate Medical Center", "npi": "5544332211"},
}


# ---------------------------------------------------------------------------
# FHIR resource builders
# ---------------------------------------------------------------------------

def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def build_encounter(
    patient_id: str,
    start: datetime,
    end: datetime,
    class_code: str,           # "IMP"=inpatient, "EMER"=ED, "AMB"=ambulatory
    reason_codes: list[str],   # ICD-10 codes
    facility: str,
    discharge_disposition: Optional[str] = None,  # "01"=home, "03"=SNF, "30"=still in
) -> dict:
    fac = FACILITIES[facility]
    enc = {
        "resourceType": "Encounter",
        "status": "finished",
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": class_code,
        },
        "subject": {"reference": f"Patient/{patient_id}"},
        "period": {"start": _ts(start), "end": _ts(end)},
        "reasonCode": [
            {
                "coding": [{
                    "system": "http://hl7.org/fhir/sid/icd-10-cm",
                    "code": c,
                }]
            }
            for c in reason_codes
        ],
        "serviceProvider": {
            "identifier": {"system": "http://hl7.org/fhir/sid/us-npi", "value": fac["npi"]},
            "display": fac["name"],
        },
        "extension": [
            {"url": "http://shaarpec.com/fhir/ext/facility-code", "valueCode": facility}
        ],
    }
    if discharge_disposition:
        enc["hospitalization"] = {
            "dischargeDisposition": {
                "coding": [{
                    "system": "http://www.nubc.org/patient-discharge",
                    "code": discharge_disposition,
                }]
            }
        }
    return enc


def build_condition(patient_id: str, code_key: str, onset: datetime, encounter_ref: Optional[str] = None) -> dict:
    code, display = CONDITIONS[code_key]
    cond = {
        "resourceType": "Condition",
        "subject": {"reference": f"Patient/{patient_id}"},
        "code": {
            "coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": code, "display": display}],
            "text": display,
        },
        "onsetDateTime": _ts(onset),
        "recordedDate": _date(onset),
        "clinicalStatus": {
            "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]
        },
    }
    if encounter_ref:
        cond["encounter"] = {"reference": encounter_ref}
    return cond


def build_medication_request(
    patient_id: str,
    med_key: str,
    authored: datetime,
    status: str = "active",
    encounter_ref: Optional[str] = None,
) -> dict:
    rxnorm, display, generic, dose = MEDICATIONS[med_key]
    req = {
        "resourceType": "MedicationRequest",
        "status": status,
        "intent": "order",
        "subject": {"reference": f"Patient/{patient_id}"},
        "authoredOn": _ts(authored),
        "medicationCodeableConcept": {
            "coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": rxnorm, "display": display}],
            "text": f"{generic} {dose}",
        },
        "dosageInstruction": [{"text": f"{generic} {dose} by mouth daily"}],
    }
    if encounter_ref:
        req["encounter"] = {"reference": encounter_ref}
    return req


def build_observation(
    patient_id: str,
    lab_key: str,
    effective: datetime,
    value: float,
    encounter_ref: Optional[str] = None,
) -> dict:
    loinc, display, unit, _ = LABS[lab_key]
    obs = {
        "resourceType": "Observation",
        "status": "final",
        "subject": {"reference": f"Patient/{patient_id}"},
        "effectiveDateTime": _ts(effective),
        "code": {
            "coding": [{"system": "http://loinc.org", "code": loinc, "display": display}],
            "text": display,
        },
        "valueQuantity": {"value": round(value, 2), "unit": unit, "system": "http://unitsofmeasure.org"},
    }
    if encounter_ref:
        obs["encounter"] = {"reference": encounter_ref}
    return obs


def build_diagnostic_report(
    patient_id: str,
    effective: datetime,
    results: list[str],    # Observation references
    conclusion: str,
    encounter_ref: Optional[str] = None,
) -> dict:
    dr = {
        "resourceType": "DiagnosticReport",
        "status": "final",
        "subject": {"reference": f"Patient/{patient_id}"},
        "effectiveDateTime": _ts(effective),
        "code": {
            "coding": [{"system": "http://loinc.org", "code": "18748-4", "display": "Diagnostic imaging study"}],
            "text": "Echocardiogram",
        },
        "result": [{"reference": r} for r in results],
        "conclusion": conclusion,
    }
    if encounter_ref:
        dr["encounter"] = {"reference": encounter_ref}
    return dr


# ---------------------------------------------------------------------------
# FHIR POST helper
# ---------------------------------------------------------------------------

class ArnoClient:
    def __init__(self, base_url: str, timeout: int = 30):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["Content-Type"] = "application/fhir+json"

    def post(self, resource: dict) -> str:
        rtype = resource["resourceType"]
        resp = self.session.post(f"{self.base}/fhir/{rtype}", json=resource, timeout=self.timeout)
        if resp.status_code not in (200, 201):
            print(f"  WARN: POST {rtype} → {resp.status_code}: {resp.text[:120]}")
            return ""
        rid = resp.json().get("id", "")
        return f"{rtype}/{rid}"

    def health(self) -> bool:
        try:
            r = self.session.get(f"{self.base}/health", timeout=5)
            return r.status_code == 200
        except Exception:
            return False


class SidecarClient:
    """Direct ivg sidecar client — used when arno hook not configured."""
    def __init__(self, base_url: str, timeout: int = 10):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def send(
        self,
        resource_type: str,
        fhir_url: str,
        patient_ref: str,
        date: str,
        content: Optional[dict] = None,
    ) -> bool:
        try:
            payload = {
                "resourceType": resource_type,
                "id": fhir_url.split("/")[-1],
                "fhirUrl": fhir_url,
                "patientRef": patient_ref,
                "date": date,
            }
            # Pass content for lazy vectorization of embed-eligible types
            if content is not None:
                payload["content"] = content
            resp = self.session.post(
                f"{self.base}/fhir-event/",
                json=payload,
                timeout=self.timeout,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def health(self) -> bool:
        try:
            r = self.session.get(f"{self.base}/health", timeout=5)
            return r.status_code == 200
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Patient trajectory generators
# ---------------------------------------------------------------------------

def generate_hfref_patient(arno: ArnoClient, sidecar: SidecarClient, patient_id: str, rng: random.Random) -> int:
    """
    HFrEF patient with 18-month trajectory:
    - Index hospitalization with diagnosis + echo + BNP
    - Medication escalation over 6 months (Furosemide → ACEi → Beta-blocker → Sacubitril → SGLT2i)
    - Follow-up ambulatory visits with lab trends
    - One readmission (possibly at a different facility = leakage signal)
    """
    events = 0
    base_date = datetime(2023, 1, 1, tzinfo=timezone.utc) + timedelta(days=rng.randint(0, 180))
    primary_facility = rng.choice(["MSH", "LIJ"])
    secondary_facility = rng.choice([f for f in FACILITIES if f != primary_facility])

    patient_ref = f"Patient/{patient_id}"

    # --- Index hospitalization (day 0) ---
    enc_start = base_date
    enc_end = enc_start + timedelta(days=rng.randint(4, 9))
    enc_ref = arno.post(build_encounter(
        patient_id, enc_start, enc_end, "IMP",
        ["I50.20", "I10", "E11.9"],
        primary_facility, discharge_disposition="01",
    ))
    if enc_ref:
        events += 1
        sidecar.send("Encounter", enc_ref, patient_ref, _date(enc_start))

    # Conditions diagnosed at index — pass content for lazy vectorization
    for cond_key in ["HFrEF", "HTN", "DM2"] + (["AFib"] if rng.random() < 0.5 else []):
        cond_resource = build_condition(patient_id, cond_key, enc_start, enc_ref or None)
        ref = arno.post(cond_resource)
        if ref:
            events += 1
            sidecar.send("Condition", ref, patient_ref, _date(enc_start), content=cond_resource)

    # Index BNP + EF labs
    bnp_start = rng.uniform(800, 4000)
    ef_start = rng.uniform(15, 35)
    for lab_key, val in [("BNP", bnp_start), ("EF", ef_start), ("Creatinine", rng.uniform(1.2, 2.5)),
                          ("Sodium", rng.uniform(130, 140))]:
        ref = arno.post(build_observation(patient_id, lab_key, enc_start + timedelta(hours=6), val, enc_ref or None))
        if ref:
            events += 1
            sidecar.send("Observation", ref, patient_ref, _date(enc_start))

    # Echo / DiagnosticReport — pass conclusion for lazy vectorization
    dr_conclusion = f"EF {ef_start:.0f}%. Severely reduced systolic function. LV dilation."
    dr_resource = build_diagnostic_report(
        patient_id, enc_start + timedelta(days=1),
        [], dr_conclusion, enc_ref or None,
    )
    dr_ref = arno.post(dr_resource)
    if dr_ref:
        events += 1
        sidecar.send("DiagnosticReport", dr_ref, patient_ref,
                     _date(enc_start + timedelta(days=1)), content=dr_resource)

    # --- Medication escalation (months 0-6) ---
    med_schedule = [
        (0,  "Furosemide_20"),
        (7,  "Lisinopril_5"),
        (14, "Carvedilol_6"),
        (30, "Furosemide_40"),
        (45, "Lisinopril_10"),
        (60, "Carvedilol_12"),
        (90, "Spironolactone"),
        (120, "Lisinopril_20"),
        (150, "Carvedilol_25"),
        (180, "Sacubitril"),
        (210, "Empagliflozin"),
    ]
    if rng.random() < 0.5:
        med_schedule.append((240, "Apixaban"))  # AFib subset

    for day_offset, med_key in med_schedule:
        med_date = enc_end + timedelta(days=day_offset)
        ref = arno.post(build_medication_request(patient_id, med_key, med_date))
        if ref:
            events += 1
            sidecar.send("MedicationRequest", ref, patient_ref, _date(med_date))

    # --- Follow-up ambulatory visits (months 1, 2, 3, 6, 9, 12) ---
    for month in [1, 2, 3, 6, 9, 12]:
        visit_date = base_date + timedelta(days=30 * month)
        amb_ref = arno.post(build_encounter(
            patient_id, visit_date, visit_date + timedelta(hours=1),
            "AMB", ["I50.20"], primary_facility,
        ))
        if amb_ref:
            events += 1
            sidecar.send("Encounter", amb_ref, patient_ref, _date(visit_date))

        # Lab trends — BNP improves with treatment, EF may improve
        bnp_val = max(100, bnp_start * (1 - 0.07 * month) + rng.gauss(0, 50))
        ef_val = min(55, ef_start + 0.8 * month + rng.gauss(0, 2))
        for lab_key, val in [("BNP", bnp_val), ("Creatinine", rng.uniform(1.0, 2.8)),
                              ("Potassium", rng.uniform(3.5, 5.2))]:
            ref = arno.post(build_observation(patient_id, lab_key, visit_date + timedelta(hours=1), val, amb_ref or None))
            if ref:
                events += 1
                sidecar.send("Observation", ref, patient_ref, _date(visit_date))

    # --- Readmission (30-90 days post-discharge, possibly at different facility) ---
    if rng.random() < 0.6:  # 60% readmission rate for HFrEF demo
        readmit_days = rng.randint(12, 70)
        readmit_start = enc_end + timedelta(days=readmit_days)
        readmit_end = readmit_start + timedelta(days=rng.randint(2, 6))
        # Leakage signal: 40% of readmissions go to a different facility
        readmit_facility = secondary_facility if rng.random() < 0.4 else primary_facility
        readmit_ref = arno.post(build_encounter(
            patient_id, readmit_start, readmit_end, "IMP",
            ["I50.20", "J81.1"],  # HFrEF + acute pulmonary edema
            readmit_facility, discharge_disposition="01",
        ))
        if readmit_ref:
            events += 1
            sidecar.send("Encounter", readmit_ref, patient_ref, _date(readmit_start))

        # BNP spike at readmission
        ref = arno.post(build_observation(
            patient_id, "BNP", readmit_start + timedelta(hours=2),
            rng.uniform(1500, 5000), readmit_ref or None,
        ))
        if ref:
            events += 1
            sidecar.send("Observation", ref, patient_ref, _date(readmit_start))

    # --- ED visit before readmission (early warning signal) ---
    if rng.random() < 0.5:
        ed_days_before = rng.randint(3, 14)
        ed_date = enc_end + timedelta(days=ed_days_before)
        ed_ref = arno.post(build_encounter(
            patient_id, ed_date, ed_date + timedelta(hours=rng.randint(4, 18)),
            "EMER", ["I50.20", "R06.0"],  # HFrEF + dyspnea
            rng.choice(list(FACILITIES.keys())),
        ))
        if ed_ref:
            events += 1
            sidecar.send("Encounter", ed_ref, patient_ref, _date(ed_date))

    return events


def generate_leakage_patient(arno: ArnoClient, sidecar: SidecarClient, patient_id: str, rng: random.Random) -> int:
    """
    Patient leakage trajectory: care starts at one facility, transitions to
    a second (out-of-network), then returns — visible only via cross-institutional
    graph traversal.
    """
    events = 0
    base_date = datetime(2023, 6, 1, tzinfo=timezone.utc) + timedelta(days=rng.randint(0, 90))
    patient_ref = f"Patient/{patient_id}"

    facilities = rng.sample(list(FACILITIES.keys()), k=3)
    primary, secondary, tertiary = facilities

    # Initial primary care encounter
    t0 = base_date
    enc1_ref = arno.post(build_encounter(
        patient_id, t0, t0 + timedelta(hours=1), "AMB",
        ["I25.10", "I10"], primary,
    ))
    if enc1_ref:
        events += 1
        sidecar.send("Encounter", enc1_ref, patient_ref, _date(t0))

    for cond_key in ["CAD", "HTN"]:
        cond_resource = build_condition(patient_id, cond_key, t0, enc1_ref or None)
        ref = arno.post(cond_resource)
        if ref:
            events += 1
            sidecar.send("Condition", ref, patient_ref, _date(t0), content=cond_resource)

    # Hospitalization at secondary (specialist referral — leakage event)
    t1 = t0 + timedelta(days=rng.randint(30, 90))
    enc2_ref = arno.post(build_encounter(
        patient_id, t1, t1 + timedelta(days=rng.randint(2, 5)), "IMP",
        ["I25.10", "Z95.1"],  # CAD + coronary artery bypass status
        secondary, discharge_disposition="01",
    ))
    if enc2_ref:
        events += 1
        sidecar.send("Encounter", enc2_ref, patient_ref, _date(t1))

    # Cardiac cath lab observation at secondary
    ref = arno.post(build_observation(
        patient_id, "EF", t1 + timedelta(hours=12), rng.uniform(40, 65), enc2_ref or None,
    ))
    if ref:
        events += 1
        sidecar.send("Observation", ref, patient_ref, _date(t1))

    # Medication initiated at secondary
    for med_key in ["Carvedilol_6", "Amlodipine"]:
        ref = arno.post(build_medication_request(patient_id, med_key, t1 + timedelta(days=1), encounter_ref=enc2_ref or None))
        if ref:
            events += 1
            sidecar.send("MedicationRequest", ref, patient_ref, _date(t1 + timedelta(days=1)))

    # Follow-up BACK at primary — closing the leakage loop
    t2 = t1 + timedelta(days=rng.randint(14, 30)) + timedelta(days=int(enc2_ref and 3 or 0))
    enc3_ref = arno.post(build_encounter(
        patient_id, t2, t2 + timedelta(hours=1), "AMB",
        ["I25.10", "Z09"],  # CAD + follow-up
        primary,
    ))
    if enc3_ref:
        events += 1
        sidecar.send("Encounter", enc3_ref, patient_ref, _date(t2))

    # Tertiary ED visit (true leakage — unrelated emergency at out-of-network facility)
    if rng.random() < 0.6:
        t3 = t2 + timedelta(days=rng.randint(10, 60))
        enc4_ref = arno.post(build_encounter(
            patient_id, t3, t3 + timedelta(hours=rng.randint(3, 12)),
            "EMER", ["I21.9", "R07.9"],  # NSTEMI + chest pain
            tertiary,
        ))
        if enc4_ref:
            events += 1
            sidecar.send("Encounter", enc4_ref, patient_ref, _date(t3))

        for lab_key, val in [("Hemoglobin", rng.uniform(9, 13)), ("Creatinine", rng.uniform(1.1, 2.0))]:
            ref = arno.post(build_observation(patient_id, lab_key, t3 + timedelta(hours=1), val, enc4_ref or None))
            if ref:
                events += 1
                sidecar.send("Observation", ref, patient_ref, _date(t3))

    return events


def generate_vbc_patient(arno: ArnoClient, sidecar: SidecarClient, patient_id: str, rng: random.Random) -> int:
    """
    VBC risk stratification patient: multimorbid, high-utilization trajectory
    with multiple chronic conditions, frequent ambulatory visits, and rising
    complexity over 12 months.
    """
    events = 0
    base_date = datetime(2023, 3, 1, tzinfo=timezone.utc) + timedelta(days=rng.randint(0, 60))
    patient_ref = f"Patient/{patient_id}"
    facility = rng.choice(list(FACILITIES.keys()))

    # Establish chronic conditions — pass content for lazy vectorization
    condition_keys = rng.sample(list(CONDITIONS.keys()), k=rng.randint(4, 7))
    for i, cond_key in enumerate(condition_keys):
        onset = base_date - timedelta(days=rng.randint(30, 730))
        cond_resource = build_condition(patient_id, cond_key, onset)
        ref = arno.post(cond_resource)
        if ref:
            events += 1
            sidecar.send("Condition", ref, patient_ref, _date(onset), content=cond_resource)

    # High-frequency ambulatory visits (monthly for 12 months)
    for month in range(13):
        visit_date = base_date + timedelta(days=30 * month + rng.randint(-3, 3))
        reason = [CONDITIONS[rng.choice(condition_keys[:3])][0]]
        enc_ref = arno.post(build_encounter(
            patient_id, visit_date, visit_date + timedelta(hours=1), "AMB",
            reason, facility,
        ))
        if enc_ref:
            events += 1
            sidecar.send("Encounter", enc_ref, patient_ref, _date(visit_date))

        # Labs every other month
        if month % 2 == 0:
            for lab_key in ["BNP", "Creatinine", "Hemoglobin"]:
                _, _, _, (lo, hi) = LABS[lab_key]
                val = rng.uniform(lo, hi)
                # Rising trend = worsening risk
                val *= (1 + 0.03 * month)
                val = min(val, hi)
                ref = arno.post(build_observation(patient_id, lab_key, visit_date + timedelta(hours=1), val, enc_ref or None))
                if ref:
                    events += 1
                    sidecar.send("Observation", ref, patient_ref, _date(visit_date))

    # Medication burden (5-8 meds = high complexity signal)
    med_keys = rng.sample(list(MEDICATIONS.keys()), k=rng.randint(5, 8))
    for med_key in med_keys:
        med_date = base_date + timedelta(days=rng.randint(-60, 30))
        ref = arno.post(build_medication_request(patient_id, med_key, med_date))
        if ref:
            events += 1
            sidecar.send("MedicationRequest", ref, patient_ref, _date(med_date))

    # 1-2 hospitalizations in the year
    for _ in range(rng.randint(1, 2)):
        hosp_date = base_date + timedelta(days=rng.randint(60, 330))
        reason_codes = [CONDITIONS[k][0] for k in rng.sample(condition_keys, k=2)]
        enc_ref = arno.post(build_encounter(
            patient_id, hosp_date, hosp_date + timedelta(days=rng.randint(3, 7)),
            "IMP", reason_codes, facility, discharge_disposition="01",
        ))
        if enc_ref:
            events += 1
            sidecar.send("Encounter", enc_ref, patient_ref, _date(hosp_date))

    return events


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

COHORT_DISTRIBUTION = [
    ("hfref",   generate_hfref_patient,   0.40),  # 40% HFrEF cohort
    ("leakage", generate_leakage_patient, 0.30),  # 30% leakage pattern
    ("vbc",     generate_vbc_patient,     0.30),  # 30% VBC multimorbid
]


def select_cohort(rng: random.Random) -> tuple:
    r = rng.random()
    cumulative = 0.0
    for name, fn, prob in COHORT_DISTRIBUTION:
        cumulative += prob
        if r < cumulative:
            return name, fn
    return COHORT_DISTRIBUTION[-1][0], COHORT_DISTRIBUTION[-1][1]


def main():
    parser = argparse.ArgumentParser(description="Seed SHAARPEC demo data")
    parser.add_argument("--arno-url",    default="http://localhost:8094", help="arno-fhir base URL")
    parser.add_argument("--sidecar-url", default="http://localhost:8765", help="ivg sidecar (FastAPI) base URL")
    parser.add_argument("--patients",    type=int, default=50,            help="Number of patients to seed (default 50)")
    parser.add_argument("--seed",        type=int, default=42,            help="Random seed for reproducibility")
    parser.add_argument("--start-index", type=int, default=0,             help="Patient ID start index")
    parser.add_argument("--dry-run",     action="store_true",             help="Print plan without posting")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    arno = ArnoClient(args.arno_url)
    sidecar = SidecarClient(args.sidecar_url)

    # Health checks
    print(f"Checking arno-fhir at {args.arno_url} ...")
    if not arno.health():
        print(f"  ERROR: arno-fhir not reachable at {args.arno_url}")
        sys.exit(1)
    print(f"  ✓ arno-fhir healthy")

    print(f"Checking ivg sidecar at {args.sidecar_url} ...")
    if not sidecar.health():
        print(f"  WARN: sidecar not reachable at {args.sidecar_url} — temporal edges won't be written")
        print(f"        (start the ivg FastAPI server: uvicorn api.main:app --port 8765)")
        sidecar = None
    else:
        print(f"  ✓ ivg sidecar healthy")

    if args.dry_run:
        print(f"\nDRY RUN: would seed {args.patients} patients across 3 cohorts")
        for name, _, prob in COHORT_DISTRIBUTION:
            print(f"  {name}: {int(prob * args.patients)} patients")
        return

    # Null sidecar if unavailable
    if sidecar is None:
        class NullSidecar:
            def send(self, *a, **kw): return False
        sidecar = NullSidecar()

    print(f"\nSeeding {args.patients} patients (seed={args.seed}) ...")
    total_events = 0
    cohort_counts: dict[str, int] = {}

    for i in range(args.patients):
        patient_id = f"demo-pt-{args.start_index + i:06d}"
        cohort_name, generator = select_cohort(rng)
        cohort_counts[cohort_name] = cohort_counts.get(cohort_name, 0) + 1

        print(f"  [{i+1:3d}/{args.patients}] {patient_id}  cohort={cohort_name}", end="", flush=True)
        t0 = time.time()
        events = generator(arno, sidecar, patient_id, rng)
        elapsed = time.time() - t0
        total_events += events
        print(f"  → {events:3d} events  ({elapsed:.1f}s)")

    print(f"\n{'='*60}")
    print(f"Done: {args.patients} patients, {total_events} FHIR resources")
    print(f"Cohort breakdown: {cohort_counts}")
    print(f"\nDemo queries to try in Neo4j Browser (bolt://localhost:7687):")
    print()
    print("# Patient trajectory — graph view")
    print("MATCH (p:Patient)-[e]->(x)")
    print("RETURN p, e, x LIMIT 100")
    print()
    print("# HFrEF cohort — temporal window (index hospitalizations Q1 2023)")
    print("MATCH (p:Patient)-[e:ENCOUNTER]->(enc)")
    print("WHERE e.ts >= 1672531200 AND e.ts <= 1680307200")
    print("RETURN p.node_id, enc.node_id, e.ts ORDER BY e.ts LIMIT 25")
    print()
    print("# Medication escalation timeline for one patient")
    print("MATCH (p:Patient {node_id:'Patient/demo-pt-000000'})-[e:MEDICATIONREQUEST]->(med)")
    print("RETURN med.node_id, e.ts ORDER BY e.ts")
    print()
    print("# Cross-facility leakage — patients with encounters at 2+ facilities")
    print("MATCH (p:Patient)-[e:ENCOUNTER]->(enc)")
    print("WITH p, count(DISTINCT enc.node_id) as enc_count")
    print("WHERE enc_count >= 3")
    print("RETURN p.node_id, enc_count ORDER BY enc_count DESC LIMIT 10")
    print()
    print("# PPR patient trajectory (requires ivg Bolt server)")
    print("CALL ivg.ppr({startNodes: ['Patient/demo-pt-000000'], dampingFactor: 0.85})")
    print("YIELD nodeId, score RETURN nodeId, score ORDER BY score DESC LIMIT 20")


if __name__ == "__main__":
    main()
