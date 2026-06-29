import streamlit as st
import requests
import pandas as pd
import os
import re
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="DOL EFAST2 Prospect Finder",
    page_icon="🔍",
    layout="wide",
)

# ── Brand colors ──────────────────────────────────────────────────────────────
PRIMARY       = "#003594"
PRIMARY_GREEN = "#1e7e34"
ACCENT        = "#AE9142"

st.markdown(f"""
<style>
    [data-testid="stSidebar"] {{
        background-color: {PRIMARY}10;
        border-right: 3px solid {PRIMARY};
    }}
    .stButton > button {{
        background-color: {PRIMARY};
        color: white;
        border: none;
        font-weight: 600;
    }}
    .stButton > button:hover {{
        background-color: {ACCENT};
        color: white;
    }}
    [data-testid="metric-container"] {{
        background: {PRIMARY}0d;
        border: 1px solid {PRIMARY}33;
        border-radius: 8px;
        padding: 10px;
    }}
    .header-bar {{
        background: linear-gradient(90deg, {PRIMARY}, {ACCENT});
        color: white;
        padding: 14px 20px;
        border-radius: 8px;
        margin-bottom: 20px;
    }}
</style>
""", unsafe_allow_html=True)

st.markdown(f"""
<div class="header-bar">
    <h2 style="margin:0">🔍 DOL EFAST2 Self-Funded Employer Finder</h2>
    <p style="margin:4px 0 0 0;opacity:0.85;font-size:0.9rem">
        Predicts self-funded likelihood from Form 5500 filings for Apollo/Bombora prioritization
    </p>
</div>
""", unsafe_allow_html=True)

# ── Constants ─────────────────────────────────────────────────────────────────
EFAST2_URL = "https://efts.dol.gov/EFTS-Public/search"
# Only fields the EFAST2 search API actually returns reliably.
# FUNDING_*_IND fields are NOT available on the search index — only in bulk CSVs.
FIELDS = ",".join([
    "SPONSOR_DFE_NAME",
    "SPONSOR_DFE_LOC_CITY",
    "SPONSOR_DFE_LOC_STATE_CD",
    "TOT_PARTCP_BOY_CNT",
    "PLAN_NAME",
    "PLAN_YR_BEGIN_DT",
])
US_STATES = [
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
    "VA","WA","WV","WI","WY","DC",
]

TARGET_TYPES = {"Self-Funded", "Likely Level-Funded"}

# ── Session with retry logic ──────────────────────────────────────────────────
def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    return session

@st.cache_data(ttl=60, show_spinner=False)
def check_connectivity() -> bool:
    try:
        r = requests.get(f"{EFAST2_URL}?query=test&from=0&size=1", timeout=5)
        return r.status_code < 500
    except Exception:
        return False

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"<h3 style='color:{PRIMARY}'>Search Filters</h3>", unsafe_allow_html=True)

    selected_states = st.multiselect(
        "States", options=US_STATES, default=["MI", "IN", "OH", "IL"],
    )

    col_a, col_b = st.columns(2)
    with col_a:
        # Denise: 150+ is where the self-funded thesis gets strong.
        # We allow lower for "Smaller — Keep but Deprioritize" tier.
        min_participants = st.number_input("Min Participants", min_value=0, value=150, step=10)
    with col_b:
        max_participants = st.number_input("Max Participants", min_value=0, value=500, step=10)

    plan_year = st.selectbox("Plan Year", options=[2024, 2023, 2022, 2021], index=0)
    keyword   = st.text_input("Company Name Keyword (optional)", placeholder="e.g. Manufacturing")

    st.markdown("---")
    search_clicked = st.button("🔎 Search EFAST2", use_container_width=True)

    st.markdown("---")
    st.markdown(f"""
    <small style='color:{PRIMARY}88'>
    Source: DOL EFAST2 / Form 5500 search index.<br>
    Per Denise: <strong>150+ employees</strong> = strong self-funded thesis.<br>
    Funding indicator fields are NOT in the search API — likelihood is predicted
    from plan name + size.
    </small>
    """, unsafe_allow_html=True)


# ── API query ─────────────────────────────────────────────────────────────────
def build_state_query(state: str, keyword: str, year: int) -> str:
    parts = [
        f'SPONSOR_DFE_LOC_STATE_CD:"{state}"',
        f"PLAN_YR_BEGIN_DT:[{year}-01-01 TO {year}-12-31]",
    ]
    if keyword and keyword.strip():
        parts.append(f'SPONSOR_DFE_NAME:*{keyword.strip()}*')
    return " AND ".join(parts)


def fetch_one_state(session: requests.Session, state: str, keyword: str, year: int) -> list[dict]:
    query = build_state_query(state, keyword, year)
    url = (
        f"{EFAST2_URL}"
        f"?query={requests.utils.quote(query)}"
        f"&from=0&size=200"
        f"&_source={FIELDS}"
    )
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        st.warning(f"Could not fetch data for {state}: {e}")
        return []

    hits = resp.json().get("hits", {}).get("hits", [])
    rows = []
    for hit in hits:
        src = hit.get("_source", {})
        rows.append({
            "Company":      src.get("SPONSOR_DFE_NAME", ""),
            "City":         src.get("SPONSOR_DFE_LOC_CITY", ""),
            "State":        src.get("SPONSOR_DFE_LOC_STATE_CD", ""),
            "Participants": src.get("TOT_PARTCP_BOY_CNT"),
            "Plan Name":    src.get("PLAN_NAME", ""),
            "Plan Year":    (src.get("PLAN_YR_BEGIN_DT") or "")[:4],
        })
    return rows


def fetch_all_states(states: list[str], keyword: str, year: int) -> pd.DataFrame:
    session = make_session()
    all_rows: list[dict] = []
    progress = st.progress(0, text="Starting search…")
    for i, state in enumerate(states):
        progress.progress(i / len(states), text=f"Fetching {state}… ({i+1}/{len(states)})")
        all_rows.extend(fetch_one_state(session, state, keyword, year))
    progress.progress(1.0, text="Done!")
    progress.empty()
    return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# PREDICTION LOGIC
# ─────────────────────────────────────────────────────────────────────────────

# Keyword groups
STRONG_SELF_FUNDED_KW = [
    "SELF FUND", "SELF-FUND", "SELF INSURED", "SELF-INSURED",
    "STOP LOSS", "STOP-LOSS",
    "ASO", "ADMINISTRATIVE SERVICES ONLY",
    "MEWA", "MULTIPLE EMPLOYER WELFARE",
]
STRONG_LEVEL_FUNDED_KW = [
    "LEVEL FUND", "LEVEL-FUND", "LEVEL-FUNDED",
    "MINIMUM PREMIUM",
    "PARTIALLY SELF", "PARTIAL SELF",
    "AETNA FUNDING ADVANTAGE", "ALLSAVERS", "ALL SAVERS",
    "SUREST", "SURESTBYUNITEDHEALTHCARE",
]
STRONG_FULLY_INSURED_KW = [
    "FULLY INSURED",
    "GROUP INSURANCE POLICY",
    "GROUP HEALTH INSURANCE",
]
CARRIER_NAMES = [
    "BLUE CROSS", "BCBS", "BLUE SHIELD",
    "AETNA", "CIGNA", "HUMANA", "ANTHEM",
    "UNITEDHEALTHCARE", "UNITED HEALTHCARE", "UHC",
    "KAISER", "PRIORITY HEALTH",
]
GENERIC_ERISA_TERMS = [
    "WELFARE BENEFIT PLAN",
    "EMPLOYEE BENEFIT PLAN",
    "HEALTH AND WELFARE", "HEALTH & WELFARE",
    "MEDICAL PLAN", "HEALTH PLAN",
]
SKIP_WORDS = {"THE", "A", "AN", "OF", "AND", "&", "INC", "INC.", "LLC",
              "CO", "CO.", "CORP", "CORP.", "COMPANY", "GROUP", "HOLDINGS"}


def _first_significant_word(name: str) -> str:
    """Return the first non-skipword token from a company name, uppercased."""
    if not name:
        return ""
    for tok in re.split(r"[\s,\.]+", name.upper()):
        tok = tok.strip()
        if tok and tok not in SKIP_WORDS:
            return tok
    return ""


def _safe_int(val):
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def predict_funding_type(plan_name: str, company_name: str, participants):
    """
    Predict (funding_type, confidence) from soft signals.

    Per Denise: 150+ employees is the strong-thesis inflection point. We never
    return "Unknown" — always a best guess so Apollo/Bombora can prioritize.
    """
    pn = (plan_name or "").upper()
    p  = _safe_int(participants)

    # ── Step 1 — strong PLAN_NAME keyword signals ────────────────────────────
    if any(kw in pn for kw in STRONG_SELF_FUNDED_KW):
        return ("Self-Funded", "High")
    if any(kw in pn for kw in STRONG_LEVEL_FUNDED_KW):
        return ("Likely Level-Funded", "High")
    if any(kw in pn for kw in STRONG_FULLY_INSURED_KW):
        return ("Fully Insured", "High")

    # ── Step 2 — carrier-only plan names → fully insured (Medium) ────────────
    company_token = _first_significant_word(company_name)
    has_carrier   = any(c in pn for c in CARRIER_NAMES)
    company_in_pn = bool(company_token and company_token in pn)
    if has_carrier and not company_in_pn:
        return ("Fully Insured", "Medium")

    # ── Step 3 — plan name contains company name + ERISA terms ───────────────
    if company_in_pn and any(t in pn for t in GENERIC_ERISA_TERMS):
        if p is None:
            return ("Likely Level-Funded", "Low")
        if p >= 500: return ("Self-Funded", "High")
        if p >= 250: return ("Self-Funded", "Medium")
        if p >= 150: return ("Self-Funded", "Medium")   # Denise's strong zone
        if p >= 100: return ("Likely Level-Funded", "Medium")
        return ("Likely Level-Funded", "Low")

    # Also catch plain "GROUP HEALTH PLAN" without "INSURANCE" — same scale
    if "GROUP HEALTH PLAN" in pn and "INSURANCE" not in pn:
        if p is None:
            return ("Likely Level-Funded", "Low")
        if p >= 500: return ("Self-Funded", "High")
        if p >= 250: return ("Self-Funded", "Medium")
        if p >= 150: return ("Self-Funded", "Medium")
        if p >= 100: return ("Likely Level-Funded", "Medium")
        return ("Likely Level-Funded", "Low")

    # ── Step 4 — pure size-based fallback ────────────────────────────────────
    if p is None:
        return ("Likely Level-Funded", "Low")
    if p >= 1000: return ("Self-Funded", "Medium")
    if p >= 500:  return ("Self-Funded", "Medium")
    if p >= 250:  return ("Self-Funded", "Low")
    if p >= 150:  return ("Likely Level-Funded", "Low")
    if p >= 100:  return ("Likely Level-Funded", "Low")
    return ("Fully Insured", "Low")


# Likelihood lookup table — combines Funding Type + Confidence into one label
LIKELIHOOD_MAP = {
    ("Self-Funded",         "High"):   "Very High",
    ("Self-Funded",         "Medium"): "High",
    ("Self-Funded",         "Low"):    "Medium-High",
    ("Likely Level-Funded", "High"):   "High",
    ("Likely Level-Funded", "Medium"): "Medium",
    ("Likely Level-Funded", "Low"):    "Medium-Low",
    ("Fully Insured",       "High"):   "Very Low",
    ("Fully Insured",       "Medium"): "Low",
    ("Fully Insured",       "Low"):    "Low",
}

# Used both for sort order (lower index = better) and rank lookups
LIKELIHOOD_ORDER = [
    "Very High", "High", "Medium-High", "Medium",
    "Medium-Low", "Low", "Very Low",
]

STRONG_LIKELIHOODS = {"Very High", "High", "Medium-High"}


def likelihood_for(funding_type: str, confidence: str) -> str:
    return LIKELIHOOD_MAP.get((funding_type, confidence), "Low")


def priority_tier_for(participants, likelihood: str) -> str:
    """Per Denise: 150+ is the inflection point — smaller still kept but deprioritized."""
    p = _safe_int(participants)
    if p is None:
        return "Smaller — Keep but Deprioritize"
    if p >= 150:
        if likelihood in STRONG_LIKELIHOODS:                return "Strong"
        if likelihood == "Medium":                          return "Watch"
        return "Deprioritize"
    return "Smaller — Keep but Deprioritize"


# ── Indicator-based logic (CSV fallback only) ─────────────────────────────────
def _is_set(val):
    try:
        return float(val) == 1.0
    except (TypeError, ValueError):
        return False


def funding_from_indicators(row) -> tuple[str, str]:
    """Use real Form 5500 indicator columns when available (CSV path only)."""
    gen_asset = _is_set(row.get("FUNDING_GEN_ASSET_IND"))
    trust     = _is_set(row.get("FUNDING_TRUST_IND"))
    insurance = _is_set(row.get("FUNDING_INSURANCE_IND"))
    plan_name = str(row.get("PLAN_NAME") or row.get("Plan Name") or "").upper()
    has_level_signal = any(kw in plan_name for kw in STRONG_LEVEL_FUNDED_KW)

    if gen_asset and insurance:           return ("Likely Level-Funded", "High")
    if gen_asset or trust:                return ("Self-Funded",         "High")
    if insurance and has_level_signal:    return ("Likely Level-Funded", "High")
    if insurance:                         return ("Fully Insured",       "High")
    # Fall through to predictor if indicators are blank
    return predict_funding_type(plan_name, row.get("Company", ""), row.get("Participants"))


def has_indicator_columns(df: pd.DataFrame) -> bool:
    return any(c in df.columns for c in
               ("FUNDING_INSURANCE_IND", "FUNDING_GEN_ASSET_IND", "FUNDING_TRUST_IND"))


# ── Derived column builder ────────────────────────────────────────────────────
def add_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Adds: Funding Type, Confidence, Self-Funded Likelihood, Priority Tier, Target."""
    use_indicators = has_indicator_columns(df)

    def _predict(row):
        if use_indicators:
            return funding_from_indicators(row)
        return predict_funding_type(
            row.get("Plan Name", ""), row.get("Company", ""), row.get("Participants"),
        )

    preds = df.apply(_predict, axis=1, result_type="expand")
    df["Funding Type"] = preds[0]
    df["Confidence"]   = preds[1]
    df["Self-Funded Likelihood"] = df.apply(
        lambda r: likelihood_for(r["Funding Type"], r["Confidence"]), axis=1,
    )
    df["Priority Tier"] = df.apply(
        lambda r: priority_tier_for(r["Participants"], r["Self-Funded Likelihood"]), axis=1,
    )
    df["Target"] = df["Funding Type"].isin(TARGET_TYPES)
    return df


def process_df(raw_df: pd.DataFrame, min_p: int, max_p: int) -> pd.DataFrame:
    raw_df["Participants"] = pd.to_numeric(raw_df["Participants"], errors="coerce")
    filtered = raw_df[
        (raw_df["Participants"] >= min_p) &
        (raw_df["Participants"] <= max_p)
    ].copy()
    return add_derived_columns(filtered)


# ── Styling ───────────────────────────────────────────────────────────────────
def style_funding(val):
    return {
        "Self-Funded":         "color: #27ae60; font-weight:700",
        "Likely Level-Funded": "color: #1a6fa8; font-weight:700",
        "Fully Insured":       "color: #c0392b",
    }.get(val, "color:#7f8c8d")


def style_target(val):
    if val is True:
        return "background-color: #eafaf1; color: #27ae60; font-weight:700"
    return ""


def style_priority(val):
    return {
        "Strong":                          f"color:{PRIMARY};font-weight:700",
        "Watch":                           "color:#7f8c8d",
        "Deprioritize":                    "color:#bfc4d6",
        "Smaller — Keep but Deprioritize": "color:#bfc4d6;font-style:italic",
    }.get(val, "color:#bfc4d6")


def style_likelihood(val):
    return {
        "Very High":   f"color:{PRIMARY_GREEN};font-weight:700",
        "High":        "color:#27ae60;font-weight:600",
        "Medium-High": f"color:{PRIMARY};font-weight:600",
        "Medium":      f"color:{PRIMARY}88",
        "Medium-Low":  "color:#7f8c8d",
        "Low":         "color:#bfc4d6",
        "Very Low":    f"color:{ACCENT}88",
    }.get(val, "color:#bfc4d6")


# ── Results UI ────────────────────────────────────────────────────────────────
def show_results(filtered: pd.DataFrame) -> None:
    # Default sort: best prospects on top
    filtered = filtered.copy()
    filtered["_lh_rank"] = filtered["Self-Funded Likelihood"].map(
        {l: i for i, l in enumerate(LIKELIHOOD_ORDER)}
    ).fillna(999)
    filtered = filtered.sort_values(
        by=["_lh_rank", "Participants"], ascending=[True, False],
    ).drop(columns=["_lh_rank"])

    # All counts use the pattern int((bool_series).sum()) — each .sum() happens
    # on a Series and produces a numpy scalar before int() casts it.
    total       = len(filtered)
    cnt_self    = int((filtered["Funding Type"]  == "Self-Funded").sum())
    cnt_level   = int((filtered["Funding Type"]  == "Likely Level-Funded").sum())
    cnt_insured = int((filtered["Funding Type"]  == "Fully Insured").sum())
    cnt_strong  = int((filtered["Priority Tier"] == "Strong").sum())

    mask_smaller    = filtered["Priority Tier"] == "Smaller — Keep but Deprioritize"
    mask_mediumplus = filtered["Self-Funded Likelihood"].isin(
        {"Very High", "High", "Medium-High", "Medium"}
    )
    cnt_smaller_watch = int((mask_smaller & mask_mediumplus).sum())

    def metric_card(label, count, color):
        return f"""
        <div style="background:{color}18;border:1.5px solid {color}55;border-radius:8px;
                    padding:14px 10px;text-align:center;">
            <div style="font-size:2rem;font-weight:700;color:{color};line-height:1.1">{count}</div>
            <div style="font-size:0.78rem;color:{color};margin-top:4px;font-weight:600">{label}</div>
        </div>"""

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.markdown(metric_card("Total Results",       total,             PRIMARY),       unsafe_allow_html=True)
    c2.markdown(metric_card("Self-Funded",         cnt_self,          "#27ae60"),     unsafe_allow_html=True)
    c3.markdown(metric_card("Likely Level-Funded", cnt_level,         "#1a6fa8"),     unsafe_allow_html=True)
    c4.markdown(metric_card("Fully Insured",       cnt_insured,       "#c0392b"),     unsafe_allow_html=True)
    c5.markdown(metric_card("Strong Targets",      cnt_strong,        PRIMARY_GREEN), unsafe_allow_html=True)
    c6.markdown(metric_card("Smaller Watch",       cnt_smaller_watch, ACCENT),        unsafe_allow_html=True)
    st.markdown("<div style='margin-bottom:12px'></div>", unsafe_allow_html=True)

    want_cols = [
        "Company", "City", "State", "Participants",
        "Priority Tier", "Self-Funded Likelihood",
        "Funding Type", "Confidence", "Plan Year",
    ]
    display_cols = [c for c in want_cols if c in filtered.columns]
    display_df = filtered[display_cols].reset_index(drop=True)

    styled = (
        display_df.style
        .map(style_funding,    subset=["Funding Type"])
        .map(style_priority,   subset=["Priority Tier"])
        .map(style_likelihood, subset=["Self-Funded Likelihood"])
        .format({"Participants": "{:,.0f}"})
    )
    st.dataframe(styled, use_container_width=True, height=500)

    st.caption(
        "⚠️ Self-Funded Likelihood is **PREDICTED** from plan name and participant "
        "count — the EFAST2 search API doesn't expose the underlying funding indicators. "
        "Per Denise: **150+ employees** is where the self-funded thesis gets strong. "
        "Use this as a prioritization hint for Apollo/Bombora, not a verified flag. "
        "Confirm before outreach."
    )

    st.markdown("---")
    b1, b2 = st.columns(2)
    with b1:
        if st.button("💾 Export to CSV", use_container_width=True):
            out_path = os.path.join(os.getcwd(), "efast2_prospects.csv")
            display_df.to_csv(out_path, index=False)
            st.success(f"Saved → `{out_path}`")
    with b2:
        if st.button("📋 Copy Company Names", use_container_width=True):
            names_path = os.path.join(os.getcwd(), "company_names.txt")
            companies = filtered["Company"].dropna().unique().tolist()
            with open(names_path, "w", encoding="utf-8") as f:
                f.write("\n".join(companies))
            st.success(f"Saved {len(companies)} names → `{names_path}`\nReady to paste into Apollo.")


# ── CSV fallback ──────────────────────────────────────────────────────────────
def show_csv_fallback(min_p: int, max_p: int, states: list[str], kw: str) -> None:
    st.markdown(f"""
    <div style="background:#fff8e1;border:1px solid {ACCENT};border-radius:8px;padding:16px;margin-bottom:16px;">
        <h4 style="color:{ACCENT};margin-top:0">📥 Manual Download Fallback</h4>
        <p>The DOL EFAST2 API is unreachable from this network. You can download the annual
        Form 5500 dataset directly from the DOL and upload it here. CSV uploads include the
        real funding indicator columns, which get used preferentially over the predictor.</p>
        <ol>
            <li>Go to
                <a href="https://www.dol.gov/agencies/ebsa/employers-and-advisers/plan-administration-and-compliance/reporting-and-filing/form-5500/5500-datasets"
                   target="_blank" style="color:{PRIMARY}">
                   DOL Form 5500 Datasets
                </a>
            </li>
            <li>Download the <strong>Form 5500</strong> CSV for the plan year you need.</li>
            <li>Upload it using the file picker below.</li>
        </ol>
    </div>
    """, unsafe_allow_html=True)

    uploaded = st.file_uploader(
        "Upload DOL Form 5500 CSV", type=["csv"],
        help="Upload the CSV downloaded from the DOL 5500 Datasets page.",
    )
    if uploaded is None:
        return

    with st.spinner("Reading CSV…"):
        try:
            csv_df = pd.read_csv(uploaded, low_memory=False, encoding='latin-1')
        except Exception as e:
            st.error(f"Could not parse file: {e}")
            return

    col_map = {
        "SPONSOR_DFE_NAME":      "Company",
        "SPONS_DFE_LOC_US_CITY": "City",
        "SPONS_DFE_LOC_US_STATE":"State",
        "TOT_PARTCP_BOY_CNT":    "Participants",
        "PLAN_NAME":             "Plan Name",
        "PLAN_YR_BEGIN_DT":      "Plan Year",
    }
    csv_df = csv_df.rename(columns={k: v for k, v in col_map.items() if k in csv_df.columns})

    missing = {"Company", "State", "Participants"} - set(csv_df.columns)
    if missing:
        st.error(
            f"Uploaded file is missing expected columns: {missing}. "
            "Make sure you uploaded a Form 5500 dataset CSV."
        )
        return

    if "TYPE_WELFARE_BNFT_CODE" in csv_df.columns:
        csv_df = csv_df[csv_df["TYPE_WELFARE_BNFT_CODE"].notna() &
                        (csv_df["TYPE_WELFARE_BNFT_CODE"].astype(str).str.strip() != "")]

    if "Plan Year" in csv_df.columns:
        csv_df["Plan Year"] = csv_df["Plan Year"].astype(str).str[:4]

    if states:
        csv_df = csv_df[csv_df["State"].isin(states)]
    if kw and kw.strip():
        csv_df = csv_df[csv_df["Company"].str.contains(kw.strip(), case=False, na=False)]

    csv_df["Participants"] = pd.to_numeric(csv_df["Participants"], errors="coerce")
    filtered = csv_df[
        (csv_df["Participants"] >= min_p) &
        (csv_df["Participants"] <= max_p)
    ].copy()
    filtered = add_derived_columns(filtered)

    if filtered.empty:
        st.info("No matching records in the uploaded file.")
        return
    show_results(filtered)


# ── Main ──────────────────────────────────────────────────────────────────────
if search_clicked:
    if not selected_states:
        st.warning("Please select at least one state.")
        st.stop()

    api_ok = check_connectivity()
    if not api_ok:
        st.error(
            "⚠️ Cannot reach **efts.dol.gov**. "
            "This is usually a network/firewall issue. "
            "Use the manual CSV fallback below."
        )
        show_csv_fallback(min_participants, max_participants, selected_states, keyword)
        st.stop()

    raw_df = fetch_all_states(selected_states, keyword, plan_year)
    if raw_df.empty:
        st.info("No results returned from the API. Try broadening your filters.")
        st.stop()

    filtered = process_df(raw_df, min_participants, max_participants)
    if filtered.empty:
        st.info(
            f"No plans found with {min_participants:,}–{max_participants:,} participants. "
            "Try adjusting the range."
        )
        st.stop()

    show_results(filtered)

else:
    api_ok = check_connectivity()
    if not api_ok:
        st.warning(
            "⚠️ **efts.dol.gov** is currently unreachable. "
            "You can still upload a DOL Form 5500 CSV below to filter locally."
        )
        show_csv_fallback(min_participants, max_participants, selected_states, keyword)
    else:
        st.markdown(f"""
        <div style="text-align:center; padding:60px 20px; color:{PRIMARY}88;">
            <h3>Configure your filters in the sidebar and click <strong>Search EFAST2</strong></h3>
            <p>Predicts self-funded likelihood from Form 5500 plan names for Apollo/Bombora prioritization</p>
        </div>
        """, unsafe_allow_html=True)
