"""
Synthetic MPLADS Dataset Generator v2.
Generates a realistic, highly connected long-format dataset covering Lok Sabha and Rajya Sabha works
across all Indian states, with full vendor links, expenditure vouchers, completion dates, and MP allocation limits.

Improvements over v1 (better statistical realism / correlations):
  1. Sanction amounts are drawn from a category-specific triangular distribution instead of one
     flat uniform range for every category (roads cost more than bar-association grants, etc).
  2. Number of works per MP is correlated with the MP's performance profile (low/avg/high), so
     total fund utilization emerges from the data instead of being force-set per work.
  3. Work status is now correlated with elapsed time since sanction AND the MP's profile (a
     "high" profile MP has a shorter expected completion time and lower stall probability than
     a "low" profile MP) -- in v1, status was independent of any date, which is unrealistic.
  4. Fixed a v1 bug where a "Works Completed" record could be emitted even when work_status was
     not "Work Completed" (70% of the time, regardless of actual status). Now "Works Completed"
     records only exist for works whose status is actually "Work Completed".
  5. Disbursed fraction of the sanctioned amount is now a function of both status and profile
     (low-profile completed works often show incomplete disbursement; high-profile completed
     works cluster near/slightly-over 100% reflecting cost escalation).
  6. Number of expenditure installments scales with sanction amount (a 40 lakh work is paid in
     more tranches than a 1 lakh work), and installment dates are spread between sanction and
     completion/today instead of being independently random.
  7. Vendors are now correlated with work category (solar vendor does solar work, water-tech
     vendor does water work, etc.) instead of being picked uniformly at random from one pooled list.
  8. Cost outliers are now category-relative multipliers (e.g. 2-3.5x a category's own typical
     cost) instead of one flat absolute value applied identically to every category.
"""

import random
import csv
from datetime import datetime, timedelta

random.seed(42)

REFERENCE_DATE = datetime(2026, 9, 1)  # "today" for the synthetic feed

STATES_AND_CONSTITUENCIES = {
    "Uttar Pradesh": ["Varanasi", "Lucknow", "Gorakhpur", "Amethi", "Agra", "Kanpur", "Prayagraj", "Meerut"],
    "Maharashtra": ["Nagpur", "Mumbai South", "Pune", "Nashik", "Thane", "Baramati", "Aurangabad", "Kolhapur"],
    "West Bengal": ["Kolkata Uttar", "Jadavpur", "Asansol", "Darjeeling", "Diamond Harbour", "Murshidabad"],
    "Tamil Nadu": ["Chennai South", "Coimbatore", "Madurai", "Thanjavur", "Salem", "Tiruchirappalli"],
    "Madhya Pradesh": ["Bhopal", "Indore", "Gwalior", "Jabalpur", "Ujjain", "Chhindwara"],
    "Bihar": ["Patna Sahib", "Gaya", "Muzaffarpur", "Bhagalpur", "Darbhanga", "Nalanda"],
    "Rajasthan": ["Jaipur", "Jodhpur", "Udaipur", "Kota", "Bikaner", "Ajmer"],
    "Karnataka": ["Bangalore South", "Mysore", "Hubli-Dharwad", "Mangalore", "Belgaum"],
    "Gujarat": ["Gandhinagar", "Surat", "Vadodara", "Rajkot", "Ahmedabad East"],
    "Odisha": ["Bhubaneswar", "Cuttack", "Puri", "Sambalpur", "Berhampur"],
    "Kerala": ["Thiruvananthapuram", "Ernakulam", "Kozhikode", "Thrissur", "Palakkad"],
    "Andhra Pradesh": ["Visakhapatnam", "Vijayawada", "Guntur", "Tirupati", "Anantapur"],
    "Telangana": ["Hyderabad", "Secunderabad", "Karimnagar", "Warangal", "Nizamabad"],
    "Punjab": ["Amritsar", "Ludhiana", "Jalandhar", "Patiala", "Gurdaspur"],
    "Haryana": ["Gurugram", "Faridabad", "Ambala", "Hisar", "Rohtak"],
    "Assam": ["Guwahati", "Dibrugarh", "Silchar", "Jorhat", "Tezpur"],
    "Jharkhand": ["Ranchi", "Jamshedpur", "Dhanbad", "Hazaribagh"],
    "Chhattisgarh": ["Raipur", "Bilaspur", "Durg", "Korba"],
    "Delhi": ["New Delhi", "South Delhi", "East Delhi", "Chandni Chowk"],
}

MP_NAMES_LOK_SABHA = [
    ("Shri Narendra Modi", "Uttar Pradesh", "Varanasi"),
    ("Rajnath Singh", "Uttar Pradesh", "Lucknow"),
    ("Nitin Gadkari", "Maharashtra", "Nagpur"),
    ("Piyush Goyal", "Maharashtra", "Mumbai South"),
    ("Abhishek Banerjee", "West Bengal", "Diamond Harbour"),
    ("K. Annamalai", "Tamil Nadu", "Coimbatore"),
    ("Shivraj Singh Chouhan", "Madhya Pradesh", "Vidisha"),
    ("Jyotiraditya Scindia", "Madhya Pradesh", "Gwalior"),
    ("Tejasvi Surya", "Karnataka", "Bangalore South"),
    ("Shashi Tharoor", "Kerala", "Thiruvananthapuram"),
    ("Gajendra Singh Shekhawat", "Rajasthan", "Jodhpur"),
    ("Anurag Thakur", "Himachal Pradesh", "Hamirpur"),
    ("Manohar Lal Khattar", "Haryana", "Karnal"),
    ("Sarbananda Sonowal", "Assam", "Dibrugarh"),
    ("Dharmendra Pradhan", "Odisha", "Sambalpur"),
    ("Supriya Sule", "Maharashtra", "Baramati"),
    ("Mahua Moitra", "West Bengal", "Krishnanagar"),
    ("Kanimozhi Karunanidhi", "Tamil Nadu", "Thoothukkudi"),
    ("Asaduddin Owaisi", "Telangana", "Hyderabad"),
    ("Shri Chirag Paswan", "Bihar", "Hajipur"),
]

for state, constituencies in STATES_AND_CONSTITUENCIES.items():
    for constituency in constituencies:
        name = f"Shri {constituency.replace(' ', '')} Representative"
        if not any(m[2] == constituency for m in MP_NAMES_LOK_SABHA):
            MP_NAMES_LOK_SABHA.append((name, state, constituency))

MP_NAMES_RAJYA_SABHA = [
    ("Shri Javed Ali Khan", "Uttar Pradesh", "Sitting Rajya Sabha"),
    ("Smt. Nirmala Sitharaman", "Karnataka", "Sitting Rajya Sabha"),
    ("Dr. S. Jaishankar", "Gujarat", "Sitting Rajya Sabha"),
    ("Shri Mallikarjun Kharge", "Karnataka", "Sitting Rajya Sabha"),
    ("Shri J. P. Nadda", "Himachal Pradesh", "Sitting Rajya Sabha"),
    ("Shri Ashwini Vaishnaw", "Odisha", "Sitting Rajya Sabha"),
    ("Shri Surendra Singh Nagar", "Uttar Pradesh", "Sitting Rajya Sabha"),
    ("Dr. Abhishek Manu Singhvi", "Telangana", "Sitting Rajya Sabha"),
    ("Shri Derek O'Brien", "West Bengal", "Sitting Rajya Sabha"),
    ("Shri Sanjay Raut", "Maharashtra", "Sitting Rajya Sabha"),
]

for state in STATES_AND_CONSTITUENCIES.keys():
    name = f"Shri {state.replace(' ', '')} Nominee RS"
    if not any(m[1] == state for m in MP_NAMES_RAJYA_SABHA):
        MP_NAMES_RAJYA_SABHA.append((name, state, "Sitting Rajya Sabha"))

WORK_CATEGORIES = [
    "Normal/Others",
    "Roads, Bridges and Culverts",
    "Drinking Water & Sanitation",
    "Education & School Infrastructure",
    "Public Health & Community Centers",
    "Electrification & Solar Power",
    "Trust and Society",
    "Bar and Associations",
    "Repair and Renovation",
]

WORK_TYPES_BY_CAT = {
    "Normal/Others": ["Construction of Community Hall", "Installation of High Mast Solar Lights", "Development of Park"],
    "Roads, Bridges and Culverts": ["Construction of CC Road", "Repair of PWD Connecting Road", "Construction of Small Culvert"],
    "Drinking Water & Sanitation": ["Installation of Community RO Drinking Water Plant", "Deep Tube Well Drilling", "Public Toilet Block Construction"],
    "Education & School Infrastructure": ["Construction of Additional Classrooms in Government School", "Computer Lab Setup in High School", "School Boundary Wall Construction"],
    "Public Health & Community Centers": ["Supply of Ambulance to Sub-District Hospital", "Community Health Sub-Center Ward Expansion"],
    "Electrification & Solar Power": ["Supply & Installation of Solar Street Lights", "Transformer Installation in Rural Village"],
    "Trust and Society": ["Grant for NGO Vocational Training Center", "Community Center Renovation for Private Trust"],
    "Bar and Associations": ["E-Library Extension in District Bar Association Building", "Furniture Purchase for Advocates Association"],
    "Repair and Renovation": ["Renovation of Old Primary School Building", "Repair of Village Drinking Water Pipeline"],
}

# (low, mode, high) rupee amounts per category -- triangular distribution instead of one
# flat uniform range shared by every category. Roads/health skew larger and longer-tailed;
# trust/bar grants skew small.
CATEGORY_AMOUNT_PARAMS = {
    "Normal/Others": (150000, 500000, 2000000),
    "Roads, Bridges and Culverts": (300000, 1200000, 5000000),
    "Drinking Water & Sanitation": (100000, 400000, 1500000),
    "Education & School Infrastructure": (200000, 900000, 3500000),
    "Public Health & Community Centers": (300000, 1200000, 4500000),
    "Electrification & Solar Power": (150000, 600000, 2200000),
    "Trust and Society": (100000, 300000, 900000),
    "Bar and Associations": (80000, 250000, 700000),
    "Repair and Renovation": (100000, 450000, 1800000),
}

VENDORS = [
    "A TO Z ASSOCIATES", "M/S INFRASTRUCTURE BUILDERS", "SRI BALAJI ENTERPRISES",
    "NEW TECH SOLAR SOLUTIONS", "RAMA CONSTRUCTIONS", "ROYAL SUPPLIERS & CONTRACTORS",
    "SHREE GANESH TRADERS", "NATIONAL INFRA PROJECTS", "MODERN WATER TECH",
    "JAI HO CONTRACTORS", "BHARAT WORKS & CO", "HINDUSTAN ENTERPRISES",
]

# Vendors correlated with category -- a solar vendor doesn't build roads.
CATEGORY_VENDORS = {
    "Roads, Bridges and Culverts": ["RAMA CONSTRUCTIONS", "NATIONAL INFRA PROJECTS", "M/S INFRASTRUCTURE BUILDERS", "BHARAT WORKS & CO"],
    "Drinking Water & Sanitation": ["MODERN WATER TECH", "SHREE GANESH TRADERS", "JAI HO CONTRACTORS"],
    "Electrification & Solar Power": ["NEW TECH SOLAR SOLUTIONS", "ROYAL SUPPLIERS & CONTRACTORS"],
    "Education & School Infrastructure": ["SRI BALAJI ENTERPRISES", "HINDUSTAN ENTERPRISES", "A TO Z ASSOCIATES"],
    "Public Health & Community Centers": ["NATIONAL INFRA PROJECTS", "HINDUSTAN ENTERPRISES"],
    "Trust and Society": ["A TO Z ASSOCIATES", "SHREE GANESH TRADERS"],
    "Bar and Associations": ["A TO Z ASSOCIATES", "ROYAL SUPPLIERS & CONTRACTORS"],
    "Repair and Renovation": ["JAI HO CONTRACTORS", "BHARAT WORKS & CO", "RAMA CONSTRUCTIONS"],
    "Normal/Others": VENDORS,
}

# (low, high, mode) days-to-completion, by MP profile -- drives the status/date correlation.
DURATION_PARAMS = {
    "high": (60, 180, 100),
    "avg": (120, 330, 200),
    "low": (200, 600, 300),
}

# Number of works generated per MP, by profile -- higher profile -> more works -> naturally
# higher cumulative fund utilization, without hard-coding a utilization number per work.
WORK_COUNT_RANGE = {
    "low": (3, 8),
    "avg": (6, 14),
    "high": (10, 22),
}

LONG_COLUMNS = [
    "record_type", "source_file", "source_sr_no", "state", "constituency",
    "mp_name", "house", "ida", "work_id", "work_category", "work_type",
    "work_description", "recommended_date", "sanction_date", "completion_date",
    "expenditure_date", "consent_date", "recommended_amount", "sanction_amount",
    "amount_disbursed", "fund_disbursed_amount", "consent_amount",
    "allocated_amount", "work_status", "payment_status", "vendor_name",
    "calamity_type", "calamity_name", "image_marker",
]


def random_date_between(start, end):
    if end <= start:
        return start
    delta = end - start
    return start + timedelta(days=random.randint(0, delta.days))


def n_installments_for_amount(amount):
    if amount < 300000:
        return 1
    elif amount < 1200000:
        return 2
    elif amount < 3000000:
        return 3
    else:
        return 4


def generate_records():
    records = []
    sr_no = 1

    all_mps = [(m[0], m[1], m[2], "Lok Sabha") for m in MP_NAMES_LOK_SABHA] + \
              [(m[0], m[1], m[2], "Rajya Sabha") for m in MP_NAMES_RAJYA_SABHA]

    work_counter = 0

    for mp_name, state, constituency, house in all_mps:
        # ~20% Low/Lagging, ~50% Average/Moderate, ~30% High/Efficient
        r = random.random()
        if r < 0.20:
            profile = "low"
        elif r < 0.70:
            profile = "avg"
        else:
            profile = "high"

        allocated_amount = 50000000.0  # statutory ₹5 Cr limit, same for every MP

        records.append({
            "record_type": "MP Allocated Limit",
            "source_file": "mplads_synthetic_feed.csv",
            "source_sr_no": sr_no,
            "state": state,
            "constituency": constituency,
            "mp_name": mp_name,
            "house": house,
            "allocated_amount": allocated_amount,
            "recommended_date": "2024-04-01",
        })
        sr_no += 1

        lo, hi = WORK_COUNT_RANGE[profile]
        num_works = random.randint(lo, hi)
        dur_lo, dur_hi, dur_mode = DURATION_PARAMS[profile]
        stall_chance = {"high": 0.05, "avg": 0.15, "low": 0.35}[profile]

        for _ in range(num_works):
            work_counter += 1
            i = work_counter

            category = random.choice(WORK_CATEGORIES)
            wtype = random.choice(WORK_TYPES_BY_CAT[category])
            work_id = f"WS/MP{random.randint(100, 999)}/2025-2026/{100000 + i}"
            ida = f"{state.upper()} DISTRICT MAGISTRATE IDA"

            cat_lo, cat_mode, cat_hi = CATEGORY_AMOUNT_PARAMS[category]
            sanc_amount = random.triangular(cat_lo, cat_hi, cat_mode)
            # category-relative cost outlier (2-3.5x that category's own typical cost),
            # instead of one flat absolute value applied to every category alike.
            if random.random() < 0.04:
                sanc_amount *= random.uniform(2.0, 3.5)
            sanc_amount = round(sanc_amount, -3)

            rec_date = random_date_between(datetime(2024, 1, 1), REFERENCE_DATE - timedelta(days=5))
            sanc_date = rec_date + timedelta(days=random.randint(10, 45))
            sanc_date = min(sanc_date, REFERENCE_DATE)

            expected_duration = max(20, random.triangular(dur_lo, dur_hi, dur_mode))
            elapsed = max(0, (REFERENCE_DATE - sanc_date).days)
            progress_ratio = elapsed / expected_duration

            # --- status correlated with elapsed time + MP profile ---
            if progress_ratio < 0.3:
                status = random.choices(
                    ["Sanction", "Physical Inspection", "Vendor Identification", "Time Estimation"],
                    weights=[0.4, 0.25, 0.2, 0.15],
                )[0]
            elif progress_ratio < 0.95:
                status = "Work in Progress"
            else:
                status = "Work in Progress" if random.random() < stall_chance else "Work Completed"

            is_completed = status == "Work Completed"

            # --- disbursed fraction correlated with status + profile ---
            if status in ("Sanction", "Physical Inspection", "Vendor Identification", "Time Estimation"):
                disb_fraction = random.uniform(0.0, 0.2)
            elif status == "Work in Progress":
                disb_fraction = min(0.95, max(0.05, progress_ratio * random.uniform(0.7, 1.0)))
            else:  # Work Completed
                if profile == "low":
                    disb_fraction = random.uniform(0.65, 0.95)
                elif profile == "avg":
                    disb_fraction = random.uniform(0.85, 1.02)
                else:
                    disb_fraction = random.uniform(0.95, 1.08)

            total_disbursed = round(sanc_amount * disb_fraction, -2)

            desc = f"{wtype} at {constituency} village ward {random.randint(1, 20)}"
            if i % 30 == 0:
                desc = "Installation of High Mast Solar Lights at Gram Panchayat Center"

            records.append({
                "record_type": "Works Recommended",
                "source_file": "mplads_synthetic_feed.csv",
                "source_sr_no": sr_no,
                "state": state, "constituency": constituency, "mp_name": mp_name, "house": house,
                "ida": ida, "work_id": work_id, "work_category": category, "work_type": wtype,
                "work_description": desc,
                "recommended_date": rec_date.strftime("%Y-%m-%d"),
                "recommended_amount": sanc_amount,
            })
            sr_no += 1

            records.append({
                "record_type": "Works Sanctioned",
                "source_file": "mplads_synthetic_feed.csv",
                "source_sr_no": sr_no,
                "state": state, "constituency": constituency, "mp_name": mp_name, "house": house,
                "ida": ida, "work_id": work_id, "work_category": category, "work_type": wtype,
                "work_description": desc,
                "sanction_date": sanc_date.strftime("%Y-%m-%d"),
                "sanction_amount": sanc_amount,
                "work_status": status,
            })
            sr_no += 1

            comp_date = None
            if is_completed:
                comp_date = sanc_date + timedelta(days=int(expected_duration * random.uniform(0.85, 1.15)))
                comp_date = min(comp_date, REFERENCE_DATE)
                has_image = random.choice(["Yes", "Yes", "Yes", "No"])
                records.append({
                    "record_type": "Works Completed",
                    "source_file": "mplads_synthetic_feed.csv",
                    "source_sr_no": sr_no,
                    "state": state, "constituency": constituency, "mp_name": mp_name, "house": house,
                    "ida": ida, "work_id": work_id, "work_category": category, "work_type": wtype,
                    "work_description": desc,
                    "completion_date": comp_date.strftime("%Y-%m-%d"),
                    "amount_disbursed": total_disbursed,
                    "image_marker": has_image,
                })
                sr_no += 1

            # --- expenditure installments: count scales with amount, dates spread realistically ---
            if total_disbursed > 0:
                n_pay = n_installments_for_amount(sanc_amount)
                window_end = comp_date if comp_date else REFERENCE_DATE
                window_end = max(window_end, sanc_date + timedelta(days=5))
                cat_vendor_pool = CATEGORY_VENDORS.get(category, VENDORS)
                vendor = random.choice(cat_vendor_pool)
                p_status = "Payment Released" if is_completed else "Payment In-Progress"

                remaining = total_disbursed
                for p in range(n_pay):
                    is_last = p == n_pay - 1
                    p_amount = round(total_disbursed / n_pay, -2) if not is_last else remaining
                    remaining -= p_amount
                    # spread installment dates roughly evenly across the sanction->completion/today window
                    slot_start = sanc_date + timedelta(days=int((window_end - sanc_date).days * p / n_pay))
                    slot_end = sanc_date + timedelta(days=int((window_end - sanc_date).days * (p + 1) / n_pay))
                    exp_date = random_date_between(slot_start, max(slot_start, slot_end))

                    p_vendor = vendor if (i % 33 != 0) else None  # ~3% missing-vendor data-quality anomaly

                    records.append({
                        "record_type": "Expenditure on Completed & On-going Works",
                        "source_file": "mplads_synthetic_feed.csv",
                        "source_sr_no": sr_no,
                        "state": state, "constituency": constituency, "mp_name": mp_name, "house": house,
                        "ida": ida, "work_id": work_id, "work_type": wtype, "work_description": desc,
                        "expenditure_date": exp_date.strftime("%Y-%m-%d"),
                        "fund_disbursed_amount": p_amount,
                        "payment_status": p_status,
                        "vendor_name": p_vendor,
                    })
                    sr_no += 1

    return records


if __name__ == "__main__":
    recs = generate_records()
    filepath = "data/mplads_raw_sample.csv"
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LONG_COLUMNS)
        writer.writeheader()
        for r in recs:
            full_r = {col: r.get(col, "") for col in LONG_COLUMNS}
            writer.writerow(full_r)
    print(f"Generated {len(recs)} synthetic records in {filepath}")
