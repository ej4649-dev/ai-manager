"""
AI Manager - Streamlit Dashboard

仕様書「機能4」「機能2」「機能3」の可視化を担当。
起動: streamlit run dashboard/app.py

DB (data/ai_manager.db) から直接読み込むだけの読み取り専用ダッシュボード。
書き込みは各 src/*.py の定期実行が担当する。
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.express as px
import streamlit as st

from src import db

st.set_page_config(page_title="AI Manager Dashboard", layout="wide")
st.title("📊 AI Manager Dashboard")
st.caption("TheVintageSalon / airstobu / GoldenMUGI 統合管理")

tab_overview, tab_fb, tab_ig, tab_reports = st.tabs(
    ["概要", "Facebook", "Instagram 広告", "レポート履歴"]
)

with tab_overview:
    today = date.today()
    latest_morning = db.latest_report("morning_brief", today.isoformat())
    st.subheader("今日の指示書")
    if latest_morning:
        st.markdown(latest_morning["content"])
    else:
        st.info("本日の朝の指示書はまだ生成されていません。`python -m src.morning_brief` を実行してください。")

with tab_fb:
    st.subheader("Facebook 日次レポート（直近7件）")
    end = today.isoformat()
    start = (today - timedelta(days=7)).isoformat()
    rows = db.reports_between("fb_daily", start, end)
    if rows:
        for r in reversed(rows):
            with st.expander(r["period_key"]):
                st.markdown(r["content"])
    else:
        st.info("Facebook レポートがまだありません。")

with tab_ig:
    st.subheader("Instagram 広告実績（直近30日）")
    start = (today - timedelta(days=30)).isoformat()
    metric_rows = [dict(r) for r in db.ig_metrics_range(start, today.isoformat())]
    if metric_rows:
        df = pd.DataFrame(metric_rows)
        daily = df.groupby("date", as_index=False).agg(
            spend=("spend", "sum"), conversions=("conversions", "sum"),
            impressions=("impressions", "sum"), clicks=("clicks", "sum"),
        )
        daily["cpa"] = daily.apply(
            lambda r: r["spend"] / r["conversions"] if r["conversions"] else None, axis=1
        )
        col1, col2 = st.columns(2)
        with col1:
            st.plotly_chart(px.line(daily, x="date", y="cpa", title="日別 CPA 推移", markers=True),
                             use_container_width=True)
        with col2:
            st.plotly_chart(px.bar(daily, x="date", y="spend", title="日別支出", color_discrete_sequence=["#6366f1"]),
                             use_container_width=True)
        st.dataframe(df.sort_values("date", ascending=False), use_container_width=True)
    else:
        st.info("Instagram 広告データがまだありません。")

with tab_reports:
    st.subheader("週次・月次レポート")
    report_type = st.selectbox("種類", ["weekly", "monthly", "note_draft"])
    rows = db.reports_between(report_type, "0000-00", "9999-99")
    if rows:
        for r in reversed(rows):
            with st.expander(f"{r['period_key']}（{r['created_at']}）"):
                st.markdown(r["content"])
    else:
        st.info("該当レポートはまだありません。")
