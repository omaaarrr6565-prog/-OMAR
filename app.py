import streamlit as st
import pandas as pd
from datetime import date

st.set_page_config(page_title="أداة السيولة", page_icon="💧", layout="wide")


# ---------------------------------------------------------------------------
# دوال أساسية
# ---------------------------------------------------------------------------
def get_spot_price(client, symbol):
    try:
        df = client.stock_snapshot_quote(symbol=[symbol])
        if df is not None and len(df) > 0:
            row = df.iloc[0]
            bid, ask = row.get("bid"), row.get("ask")
            if bid and ask and bid > 0 and ask > 0:
                return round((bid + ask) / 2, 2)
    except Exception:
        pass
    try:
        df = client.index_snapshot_price(symbol=[symbol])
        if df is not None and len(df) > 0:
            price = df.iloc[0].get("price")
            if price:
                return round(float(price), 2)
    except Exception:
        pass
    return None


def get_expirations(client, symbol):
    df = client.option_list_expirations(symbol=symbol)
    if isinstance(df, pd.DataFrame):
        date_col = None
        for c in df.columns:
            if "exp" in c.lower() or "date" in c.lower():
                date_col = c
                break
        if date_col is None:
            date_col = df.columns[-1]
        raw = df[date_col].tolist()
    else:
        raw = list(df)
    today = date.today()
    parsed = sorted(set(pd.to_datetime(str(x)).date() for x in raw))
    future = [d for d in parsed if d >= today]
    return future if future else parsed


def build_chain(client, symbol, expiration, strike_range=15):
    exp_str = expiration.strftime("%Y-%m-%d") if hasattr(expiration, "strftime") else str(expiration)
    quotes = client.option_snapshot_quote(
        symbol=symbol, expiration=exp_str, strike="*", right="both", strike_range=strike_range,
    )
    try:
        oi = client.option_snapshot_open_interest(
            symbol=symbol, expiration=exp_str, strike="*", right="both", strike_range=strike_range,
        )
    except Exception:
        oi = None
    if quotes is None or len(quotes) == 0:
        return None
    oi_map = {}
    if oi is not None and len(oi) > 0:
        for _, r in oi.iterrows():
            key = (round(float(r["strike"]), 2), str(r["right"]).lower()[0])
            oi_map[key] = int(r.get("open_interest", 0) or 0)
    calls, puts = {}, {}
    for _, r in quotes.iterrows():
        strike = round(float(r["strike"]), 2)
        right = str(r["right"]).lower()
        bucket = calls if right.startswith("c") else puts
        bucket[strike] = {
            "bid": r.get("bid"), "ask": r.get("ask"),
            "oi": oi_map.get((strike, right[0]), 0),
        }
    strikes = sorted(set(list(calls.keys()) + list(puts.keys())))
    return strikes, calls, puts


def get_symbol_table(client, symbol, top_n=8):
    spot = get_spot_price(client, symbol)
    expirations = get_expirations(client, symbol)
    if not expirations:
        return None, None, None
    expiration = expirations[0]
    result = build_chain(client, symbol, expiration)
    if result is None:
        return spot, expiration, None

    strikes, calls, puts = result
    rows = []
    for k in strikes:
        c = calls.get(k, {})
        p = puts.get(k, {})
        rows.append({"Strike": k, "النوع": "CALL", "OI": c.get("oi") or 0, "Bid": c.get("bid"), "Ask": c.get("ask")})
        rows.append({"Strike": k, "النوع": "PUT", "OI": p.get("oi") or 0, "Bid": p.get("bid"), "Ask": p.get("ask")})

    df = pd.DataFrame(rows).sort_values("OI", ascending=False).head(top_n).reset_index(drop=True)
    df.index = df.index + 1
    return spot, expiration, df


# ---------------------------------------------------------------------------
# تسجيل الدخول
# ---------------------------------------------------------------------------
if "client" not in st.session_state:
    st.session_state.client = None
if "watchlist" not in st.session_state:
    st.session_state.watchlist = []

st.title("💧 أداة السيولة — عقود وأسهم")

if st.session_state.client is None:
    st.subheader("تسجيل الدخول (Theta Data)")
    with st.form("login_form"):
        email = st.text_input("الإيميل")
        password = st.text_input("كلمة المرور", type="password")
        submitted = st.form_submit_button("تسجيل الدخول")

    if submitted:
        try:
            from thetadata import ThetaClient
            client = ThetaClient(email=email, password=password, dataframe_type="pandas")
            st.session_state.client = client
            st.success("تم تسجيل الدخول بنجاح")
            st.rerun()
        except Exception as e:
            st.error(f"فشل تسجيل الدخول: {e}")

else:
    client = st.session_state.client

    col1, col2 = st.columns([3, 1])
    with col1:
        new_symbol = st.text_input("اكتب اسم السهم أو المؤشر", placeholder="مثال: AAPL")
    with col2:
        st.write("")
        st.write("")
        add_clicked = st.button("➕ إضافة", use_container_width=True)

    if add_clicked and new_symbol.strip():
        sym = new_symbol.strip().upper()
        if sym not in st.session_state.watchlist:
            st.session_state.watchlist.append(sym)

    if st.session_state.watchlist:
        st.markdown("---")
        cols = st.columns([5, 1])
        with cols[1]:
            if st.button("🔄 تحديث الكل"):
                st.rerun()

        for sym in st.session_state.watchlist:
            with st.container(border=True):
                top_row = st.columns([4, 1])
                with top_row[0]:
                    st.subheader(sym)
                with top_row[1]:
                    if st.button("🗑️ حذف", key=f"del_{sym}"):
                        st.session_state.watchlist.remove(sym)
                        st.rerun()

                with st.spinner(f"جاري جلب بيانات {sym} ..."):
                    spot, expiration, df = get_symbol_table(client, sym)

                if df is None and expiration is None:
                    st.error("ما فيه عقود متاحة لهذا الرمز")
                    continue
                if df is None:
                    st.warning("ما فيه بيانات متاحة الحين (السوق مسكر)")
                    continue

                spot_txt = f"{spot:,.2f}" if spot else "غير متاح"
                st.caption(f"السعر الحالي: **{spot_txt}**  |  تاريخ الانتهاء: **{expiration}**")

                st.dataframe(
                    df.style.background_gradient(subset=["OI"], cmap="Greens"),
                    use_container_width=True,
                )
    else:
        st.info("لسا ما أضفت أي شركة. اكتب رمز السهم فوق واضغط '➕ إضافة'.")

    st.markdown("---")
    if st.button("تسجيل خروج"):
        st.session_state.client = None
        st.rerun()
