import streamlit as st
import pandas as pd
import streamlit_authenticator as stauth

st.set_page_config(page_title="Merchant Portal", layout="wide")

# =========================
# Auth (from Secrets)
# =========================
# In Streamlit Cloud → App → Settings → Secrets (example):
# COOKIE_KEY = "replace_with_random_secret"
#
# [users."M001"]
# name = "Merchant A"
# email = "a@example.com"
# password_hash = "$2b$12$D9ZTRh3DUXkdIibFhes.7eKWQLTc.cdJJwtOsFTLbBuxkanfkkbJm"  # for M001@123
# merchant_id = "M001 - Merchant A"
#
# [users."M002"]
# name = "Merchant B"
# email = "b@example.com"
# password_hash = "$2b$12$rGu5g8HsKr.0dqCUh1ksLOTQs6AttYRV/xWC/k99Ru3x5hRFJMU8O"  # for M002@123
# merchant_id = "M002 - Merchant B"

users_cfg = st.secrets.get("users", {})
cookie_key = st.secrets.get("COOKIE_KEY", "change-me")

# Build credentials for NEW constructor
creds = {"usernames": {}}
for uname, u in users_cfg.items():
    creds["usernames"][uname] = {
        "name": u["name"],
        "email": u["email"],
        "password": u["password_hash"],  # bcrypt hash only
    }

# NEW authenticator API (>= 0.4.x)
authenticator = stauth.Authenticate(
    credentials=creds,
    cookie_name="merchant_portal",
    key=cookie_key,
    cookie_expiry_days=7,
)

# Render login (NEW API) — do NOT unpack return values
authenticator.login(location="main")

# Pull results from session_state
auth_status = st.session_state.get("authentication_status")
name = st.session_state.get("name")
username = st.session_state.get("username")

if auth_status is False:
    st.error("Invalid credentials")
    st.stop()
elif auth_status is None:
    st.info("Please log in.")
    st.stop()

# Logged-in UI
authenticator.logout(location="sidebar")
st.sidebar.write(f"Hello, **{name}**")
# ===== Robust merchant mapping (case-insensitive)
users_cfg = st.secrets.get("users", {})
username = st.session_state.get("username")

def get_user_record(cfg: dict, uname: str):
    if uname in cfg:
        return cfg[uname]
    # case-insensitive fallback
    uname_cf = str(uname).casefold()
    for k, v in cfg.items():
        if str(k).casefold() == uname_cf:
            return v
    return None

merchant_rec = get_user_record(users_cfg, username)
if not merchant_rec or "merchant_id" not in merchant_rec:
    st.error(
        "Merchant mapping not found for this user. "
        f"Got username='{username}'. Available users: {list(users_cfg.keys())}. "
        "Check App → Settings → Secrets."
    )
    st.stop()

merchant_id = merchant_rec["merchant_id"]


# =========================
# Load transactions CSV
# =========================
@st.cache_data(ttl=60)
def load_transactions():
    # Try root, then /data (supports either placement)
    for p in ("sample_merchant_transactions.csv", "data/sample_merchant_transactions.csv"):
        try:
            df = pd.read_csv(p)
            df["__path__"] = p
            return df
        except Exception:
            pass
    raise FileNotFoundError(
        "CSV not found. Place it at repo root as 'sample_merchant_transactions.csv' "
        "or under 'data/' as 'data/sample_merchant_transactions.csv'."
    )

tx = load_transactions()

# Validate required columns from your schema
needed = {"Merchant Number - Business Name", "Transaction Date", "Settle Amount"}
missing = needed - set(tx.columns)
if missing:
    st.error(f"Missing required column(s) in CSV: {', '.join(sorted(missing))}")
    st.stop()

# Parse/clean
tx["Merchant Number - Business Name"] = tx["Merchant Number - Business Name"].astype(str).str.strip()
tx["Transaction Date"] = pd.to_datetime(tx["Transaction Date"], errors="coerce")
tx["Settle Amount"] = pd.to_numeric(tx["Settle Amount"], errors="coerce")

# Filter to this merchant (Secrets merchant_id must match CSV col exactly)
merchant_tx = tx[tx["Merchant Number - Business Name"] == merchant_id].copy()
if merchant_tx.empty:
    st.warning(
        f"No transactions found for merchant: {merchant_id}\n"
        "Check that the Secrets 'merchant_id' matches the CSV values in "
        "'Merchant Number - Business Name' (exact case & spaces)."
    )
    st.stop()

# =========================
# Aggregate to daily metrics
# revenue = sum(Settle Amount)
# orders  = count rows
# aov     = revenue / orders
# =========================
daily = (
    merchant_tx
    .groupby(merchant_tx["Transaction Date"].dt.date, dropna=False)
    .agg(revenue=("Settle Amount", "sum"),
         orders=("Settle Amount", "count"))
    .reset_index()
    .rename(columns={"Transaction Date": "date"})
)

# Normalize date type and order rows
daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
daily = daily.dropna(subset=["date"]).sort_values("date")
daily["aov"] = (daily["revenue"] / daily["orders"]).where(daily["orders"] > 0)

# =========================
# Filters
# =========================
min_date = daily["date"].min().date()
max_date = daily["date"].max().date()
start_date, end_date = st.sidebar.date_input(
    "Date range",
    value=(min_date, max_date),
    min_value=min_date,
    max_value=max_date,
)
daily = daily[(daily["date"].dt.date >= start_date) & (daily["date"].dt.date <= end_date)]

# =========================
# KPIs + Charts
# =========================
st.title("📊 Merchant Dashboard")
st.caption(f"Merchant: **{merchant_id}**  |  Source: `{merchant_tx['__path__'].iat[0]}`")

total_rev = float(daily["revenue"].sum()) if not daily.empty else 0.0
total_orders = int(daily["orders"].sum()) if not daily.empty else 0
aov_latest = daily["aov"].iloc[-1] if not daily.empty else None

k1, k2, k3 = st.columns(3)
k1.metric("Total Revenue (Settle)", f"R {total_rev:,.0f}")
k2.metric("Total Orders", f"{total_orders:,}")
k3.metric("Latest AOV", f"R {aov_latest:,.2f}" if pd.notnull(aov_latest) else "—")

st.subheader("Trends")
if not daily.empty:
    st.line_chart(daily.set_index("date")[["revenue", "orders"]])
    st.subheader("Average Order Value (AOV)")
    st.bar_chart(daily.set_index("date")[["aov"]])

st.subheader("Daily Aggregated Rows")
st.dataframe(daily.reset_index(drop=True))
