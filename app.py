import streamlit as st
import pandas as pd
from datetime import date

st.set_page_config(page_title="أداة السيولة", page_icon="💧", layout="wide")

# ----------------------------------------------------------------------
# دوال أساسية
# ----------------------------------------------------------------------
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
    try:
        ohlc = client.option_snapshot_ohlc(
            symbol=symbol, expiration=exp_str, strike="*", right="both", strike_range=strike_range,
        )
    except Exception:
        ohlc = None
    try:
        greeks = client.option_snapshot_greeks_all(
            symbol=symbol, expiration=exp_str, strike="*", right="both", strike_range=strike_range,
        )
    except Exception:
        greeks = None

    volume_map = {}
    if ohlc is not None and len(ohlc) > 0:
        for _, r in ohlc.iterrows():
            key = (round(float(r["strike"]), 2), str(r["right"]).lower()[0])
            volume_map[key] = int(r.get("volume", 0) or 0)

    delta_map = {}
    if greeks is not None and len(greeks) > 0:
        for _, r in greeks.iterrows():
            key = (round(float(r["strike"]), 2), str(r["right"]).lower()[0])
            d = r.get("delta")
            delta_map[key] = float(d) if d is not None else None

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
            "volume": volume_map.get((strike, right[0]), 0),
            "delta": delta_map.get((strike, right[0])),
        }
    strikes = sorted(set(list(calls.keys()) + list(puts.keys())))
    return strikes, calls, puts


def format_arabic_date(d):
    """يحول التاريخ إلى صيغة يوم-شهر-سنة بالميلادي"""
    months = {
        1: "يناير", 2: "فبراير", 3: "مارس", 4: "أبريل", 5: "مايو", 6: "يونيو",
        7: "يوليو", 8: "أغسطس", 9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر",
    }
    if hasattr(d, "day"):
        return f"{d.day} {months.get(d.month, d.month)} {d.year}"
    return str(d)


def rank_contracts(client, symbol, right, max_price=300, min_price=5, top_n=4):
    """
    يرجّع أفضل top_n عقود مرتبة حسب:
    1. السعر ما يتجاوز max_price (بالدولار للعقد الواحد)
    2. أعلى سيولة (Volume) وأضيق سبريد
    3. من بينهم، أقوى Delta (يتفاعل أكثر مع حركة السهم)
    """
    expirations = get_expirations(client, symbol)
    if not expirations:
        return []
    expiration = expirations[0]
    result = build_chain(client, symbol, expiration)
    if result is None:
        return []

    strikes, calls, puts = result
    bucket = calls if right == "CALL" else puts

    candidates = []
    for k, data in bucket.items():
        ask = data.get("ask")
        bid = data.get("bid")
        volume = data.get("volume") or 0
        delta = data.get("delta")
        if ask is None or bid is None or ask <= 0:
            continue
        contract_price = ask * 100
        if contract_price > max_price or contract_price < min_price:
            continue
        spread = ask - bid
        spread_pct = (spread / ask) if ask > 0 else 1
        liquidity_score = volume / (spread_pct + 0.01)
        candidates.append({
            "Strike": k, "Ask": ask, "Bid": bid, "Volume": volume,
            "Delta": delta, "السعر": round(contract_price, 2),
            "liquidity_score": liquidity_score, "expiration": expiration,
        })

    if not candidates:
        return []

    cand_df = pd.DataFrame(candidates).sort_values("liquidity_score", ascending=False)
    top_pool = cand_df.head(top_n * 2)

    if top_pool["Delta"].notna().any():
        top_pool = top_pool.reindex(
            top_pool["Delta"].abs().sort_values(ascending=False).index
        )

    return top_pool.head(top_n).to_dict("records")


def render_contract_cards(contracts, right_label):
    if not contracts:
        st.warning("ما فيه عقود مناسبة حالياً ضمن معايير السعر والسيولة")
        return
    for i, c in enumerate(contracts, start=1):
        delta_txt = f"{c['Delta']:.2f}" if c["Delta"] is not None else "غير متاح"
        exp_txt = format_arabic_date(c["expiration"])
        with st.container(border=True):
            st.markdown(f"**#{i} — Strike {c['Strike']:.2f}**")
            st.caption(f"تاريخ الانتهاء: {exp_txt}")
            m1, m2, m3 = st.columns(3)
            m1.metric("سعر العقد", f"${c['السعر']:.2f}")
            m2.metric("السيولة (Volume)", f"{int(c['Volume']):,}")
            m3.metric("Delta", delta_txt)


# ----------------------------------------------------------------------
# تسجيل الدخول
# ----------------------------------------------------------------------
if "client" not in st.session_state:
    st.session_state.client = None
if "watchlist" not in st.session_state:
    st.session_state.watchlist = []

st.title("💧 أداة السيولة - عقود وأسهم")

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
        add_clicked = st.button("+ إضافة", use_container_width=True)

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
                    if st.button("🗑 حذف", key=f"del_{sym}"):
                        st.session_state.watchlist.remove(sym)
                        st.rerun()

                with st.spinner(f"جاري تحليل أفضل العقود لـ {sym} ..."):
                    call_contracts = rank_contracts(client, sym, "CALL")
                    put_contracts = rank_contracts(client, sym, "PUT")

                call_col, put_col = st.columns(2)
                with call_col:
                    st.markdown("### CALL 🟢")
                    render_contract_cards(call_contracts, "CALL")
                with put_col:
                    st.markdown("### PUT 🔴")
                    render_contract_cards(put_contracts, "PUT")
    else:
        st.info("لسا ما أضفت أي شركة. اكتب رمز السهم فوق واضغط + إضافة.")

    st.markdown("---")
    if st.button("تسجيل خروج"):
        st.session_state.client = None
        st.rerun()
