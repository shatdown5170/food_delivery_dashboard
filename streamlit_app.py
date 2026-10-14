import sqlite3

import pandas as pd
import plotly.express as px
import streamlit as st

ACCENT, GRAY = "#1F3A5F", "#BDBDBD"  
px.defaults.color_discrete_map = {
     "Android": ACCENT, "iOS": GRAY,
        "push": ACCENT, "organic": "#8C8C8C", "paid_ad": GRAY, "referral": "#DADADA", 
        }
px.defaults.color_discrete_sequence = [GRAY]

st.set_page_config(page_title="사용자 행동 탐색 대시보드", layout="wide")
st.title("사용자 행동 탐색 대시보드")
st.caption("주별 세션 퍼널 | 플랫폼 · 유입 경로별 | 2025.07 ~ 2026.06")

# ------------------------------------------------------------
# 0. 필요한 데이터만 추출 — 업로드된 파일 확인 및 메모리 DB 구축
# ------------------------------------------------------------
DATA_DIR = "data"                        # 원본 CSV를 넣어 둔 폴더
NEEDED = {                        # 이번 대시보드에 필요한 테이블과 컬럼
    "app_events": [
        "event_id",
        "event_datetime",
        "customer_id",
        "session_id",
        "event_name",
        "platform",
        "traffic_source",
    ],
}

uploaded_file = st.session_state.get("upload_file", None)
uploaded_bytes = uploaded_file.getvalue() if uploaded_file is not None else None
uploaded_filename = uploaded_file.name if uploaded_file is not None else None

data_source_text = "data 폴더 기본 file"
use_fallback = False

if uploaded_bytes is not None:
    try:
        import io
        preview_df = pd.read_csv(io.BytesIO(uploaded_bytes), nrows=0)
        missing_cols = [c for c in NEEDED["app_events"] if c not in preview_df.columns]
        if missing_cols:
            st.error(f"업로드된 파일에 필요한 컬럼이 없습니다. 빠진 컬럼: {', '.join(missing_cols)}. data 폴더 기본 파일을 사용합니다.")
            use_fallback = True
            uploaded_bytes = None
            data_source_text = "data 폴더 기본 파일"
        else:
            data_source_text = f"올린 파일 {uploaded_filename}"
    except Exception as e:
        st.error(f"파일을 읽는 중 오류가 발생했습니다: {e}. data 폴더 기본 파일을 사용합니다.")
        use_fallback = True
        uploaded_bytes = None
        data_source_text = "data 폴더 기본 파일"
else:
    data_source_text = "data 폴더 기본 파일"

# 파일 업로드/삭제 상태 변경 감지를 위한 세션 상태 관리
if "prev_uploaded_bytes" not in st.session_state:
    st.session_state.prev_uploaded_bytes = uploaded_bytes

if "reset_key" not in st.session_state:
    st.session_state.reset_key = 0

if st.session_state.prev_uploaded_bytes != uploaded_bytes:
    st.session_state.reset_key += 1
    st.session_state.prev_uploaded_bytes = uploaded_bytes


@st.cache_resource
def get_db(file_bytes):  # 필요한 컬럼만 메모리 DB에 올리고, 정제 테이블 events를 만듦 (처음 한 번만)
    con = sqlite3.connect(":memory:", check_same_thread=False)
    if file_bytes is not None:
        import io
        df_up = pd.read_csv(io.BytesIO(file_bytes), usecols=NEEDED["app_events"])
        df_up.to_sql("app_events", con, index=False)
    else:
        for name, cols in NEEDED.items():
            pd.read_csv(f"{DATA_DIR}/{name}.csv", usecols=cols).to_sql(
                name, con, index=False
            )
    # 5주차 제외 규칙: QA 계정 제외 · 중복 이벤트 제거
    con.execute("""
        CREATE TABLE events AS
        SELECT event_id, event_datetime, customer_id, session_id, event_name, platform, traffic_source
          FROM (SELECT *,
                       ROW_NUMBER() OVER (PARTITION BY session_id, event_datetime, event_name
                                          ORDER BY event_id) AS dup
                  FROM app_events
                 WHERE customer_id NOT LIKE 'QA%')
         WHERE dup = 1
    """)
    # 세션별로 묶는 집계가 빨라지도록 session_id에 인덱스를 붙임
    con.execute("CREATE INDEX idx_events_session ON events(session_id)")
    return con


# ------------------------------------------------------------
# 1. 데이터 준비 — 정제된 events 테이블을 SQL로 집계
# ------------------------------------------------------------
@st.cache_data
def query(sql, file_bytes):  # SQL을 실행해 표로 돌려줌
    return pd.read_sql(sql, get_db(file_bytes))


# 주 × 플랫폼 × 유입 경로별 세션 퍼널
SQL_WEEKLY_FUNNEL = """
WITH s AS (                                 -- 세션의 첫 이벤트로 플랫폼 · 유입 경로 · 주 정하기
    SELECT session_id, platform, traffic_source,
           DATE(MIN(event_datetime), 'weekday 0', '-6 days') AS 주시작일
      FROM events
     GROUP BY session_id
)
SELECT s.주시작일,
       s.platform    AS 플랫폼,
       s.traffic_source AS 유입경로,
       COUNT(DISTINCT e.session_id)                                                                                                                                                           AS 세션수,
       COUNT(DISTINCT CASE WHEN e.event_name = 'view_restaurant' THEN e.session_id END)   AS 조회세션,
       COUNT(DISTINCT CASE WHEN e.event_name = 'add_to_cart'    THEN e.session_id END)   AS 장바구니세션,
       COUNT(DISTINCT CASE WHEN e.event_name = 'begin_checkout'  THEN e.session_id END)   AS 결제시작세션,
       COUNT(DISTINCT CASE WHEN e.event_name = 'order_complete'  THEN e.session_id END)   AS 주문세션,
       SUM(CASE WHEN e.event_name = 'payment_fail' THEN 1 ELSE 0 END)                     AS 결제실패
  FROM events e
  JOIN s ON e.session_id = s.session_id
 WHERE s.주시작일 < '2026-06-29'          -- 이틀치뿐인 마지막 주 제외
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3
"""


@st.cache_data
def load_data(file_bytes):
    data = query(SQL_WEEKLY_FUNNEL, file_bytes)
    data["주시작일"] = pd.to_datetime(data["주시작일"]).dt.date
    return data


df = load_data(uploaded_bytes)  # 주 × 플랫폼 × 유입 경로별 세션 퍼널

# ------------------------------------------------------------
# 사이드바 필터 및 초기화 관리
# ------------------------------------------------------------
if st.sidebar.button("필터 초기화"):
    st.session_state.reset_key += 1
    st.rerun()

rk = st.session_state.reset_key
st.sidebar.header("필터")

weeks = sorted(df["주시작일"].unique()) if not df.empty else []
if weeks:
    selected_period = st.sidebar.select_slider(
        "기간", options=weeks, value=(weeks[0], weeks[-1]), key=f"period_{rk}"
    )
else:
    selected_period = (None, None)

platforms = sorted(df["플랫폼"].unique()) if not df.empty else []
selected_platforms = st.sidebar.multiselect(
    "플랫폼", options=platforms, default=platforms, key=f"platforms_{rk}"
)

traffic_sources = sorted(df["유입경로"].unique()) if not df.empty else []
selected_traffic_sources = st.sidebar.multiselect(
    "유입 경로", options=traffic_sources, default=traffic_sources, key=f"traffic_{rk}"
)

# 평소 범위 기준 기간 설정 (기본값: 처음 26주) -> 고급 설정 expander 안으로 이동
default_baseline_end_idx = min(25, len(weeks) - 1) if weeks else 0
with st.sidebar.expander("고급 설정"):
    baseline_period = st.select_slider(
        "평소 범위 기준 기간",
        options=weeks if weeks else [None],
        value=(weeks[0], weeks[default_baseline_end_idx]) if weeks else (None, None),
        help="문제가 없던 기간을 고르세요. 이 기간의 주별 값 범위를 '평소 범위'로 씁니다.",
        key=f"baseline_{rk}"
    )
    exclude_low_sessions = st.checkbox(
        "세션 500개 미만 집단 제외 (집단 비교 · 드릴다운)",
        key=f"exclude_low_{rk}"
    )

# ------------------------------------------------------------
# 보고 있는 조건 표시 (제목 아래, 탭 위)
# ------------------------------------------------------------
# 기간 텍스트 결정
if len(weeks) > 0 and selected_period == (weeks[0], weeks[-1]):
    period_text = "전체"
elif not selected_period or selected_period[0] is None:
    period_text = "선택 없음"
else:
    period_text = f"{selected_period[0]} ~ {selected_period[1]}"

# 플랫폼 텍스트 결정
if len(selected_platforms) == len(platforms):
    platform_text = "전체"
elif not selected_platforms:
    platform_text = "선택 없음"
else:
    platform_text = ", ".join(selected_platforms)

# 유입 경로 텍스트 결정
if len(selected_traffic_sources) == len(traffic_sources):
    traffic_text = "전체"
elif not selected_traffic_sources:
    traffic_text = "선택 없음"
else:
    traffic_text = ", ".join(selected_traffic_sources)

st.caption(f"보고 있는 조건: 데이터 ({data_source_text}) · 기간 ({period_text}) · 플랫폼 ({platform_text}) · 유입 경로 ({traffic_text})")

# 집단 조건(플랫폼·유입경로)만 적용한 데이터 g
g = df[
    (df["플랫폼"].isin(selected_platforms))
    & (df["유입경로"].isin(selected_traffic_sources))
] if not df.empty else pd.DataFrame(columns=df.columns)

# 기존 f는 g에 기간 조건을 더해 만듦
f = g[
    (g["주시작일"] >= selected_period[0])
    & (g["주시작일"] <= selected_period[1])
] if not g.empty and selected_period[0] is not None else pd.DataFrame(columns=df.columns)

# ------------------------------------------------------------
# 사이드바 다운로드 버튼 추가 (f가 정의된 이후에 배치)
# ------------------------------------------------------------
st.sidebar.html("<div style='height: 30px;'></div>")
st.sidebar.subheader("데이터 내려받기")
dl_col1, dl_col2 = st.sidebar.columns(2)

with dl_col1:
    st.download_button(
        label="전체 다운로드",
        data=df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
        file_name="전체_데이터.csv",
        mime="text/csv",
        use_container_width=True
    )

with dl_col2:
    st.download_button(
        label="부분 다운로드",
        data=f.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
        file_name="필터적용_데이터.csv",
        mime="text/csv",
        use_container_width=True
    )

# ------------------------------------------------------------
# 탭 구성 (개요, 탐색, 액션, 데이터 업로드)
# ------------------------------------------------------------
tab_overview, tab_explore, tab_action, tab_upload = st.tabs(["📊 개요", "🔍 탐색", "✅ 액션", "📤 데이터 업로드"])

with tab_upload:
    st.caption("이 탭에서 볼 수 있는 것: 분석용 app_events.csv 파일 업로드")
    st.file_uploader("app_events.csv 올리기", type="csv", key="upload_file")
    st.info("파일을 업로드하거나 변경하면 대시보드 데이터가 자동으로 갱신됩니다. (서버나 저장소에 저장되지 않습니다.)")

if f.empty:
    st.warning("선택한 조건에 해당하는 데이터가 없습니다. 필터를 바꿔 주세요.")
    st.stop()

with tab_overview:
    st.caption("이 탭에서 볼 수 있는 것: 주요 지표(KPI) 카드, 규칙 기반 상태 문장, 전체 AI 해설, 주별 결제 전환율 및 세션 퍼널 시각화")

    # ------------------------------------------------------------
    # 2. KPI 카드
    # ------------------------------------------------------------
    def rate(a, b):
        return a / b * 100 if b else 0


    t = f[["세션수", "결제시작세션", "주문세션"]].sum()
    c1, c2, c3 = st.columns(3)
    c1.metric("세션 수", f"{t['세션수']:,}개", border=True, help="선택한 조건의 전체 세션의 숫자")
    c2.metric("주문 전환율 (방문 → 주문)", f"{rate(t['주문세션'], t['세션수']):.1f}%", border=True, help="주문 세션 ÷ 전체 세션")
    c3.metric("결제 전환율 (결제 시작 → 주문)", f"{rate(t['주문세션'], t['결제시작세션']):.1f}%", border=True, help="주문 세션 ÷ 결제 시작 세션")

    # ------------------------------------------------------------
    # 규칙 기반 설명 및 AI 해설 통합 영역 (container 적용)
    # ------------------------------------------------------------
    with st.container(border=True):
        st.markdown("**📏 지금 숫자 읽기**")
        
        selected_weeks_count = len([w for w in weeks if selected_period[0] <= w <= selected_period[1]])

        if selected_weeks_count < 4:
            st.markdown("- 비교하려면 선택 기간을 4주 이상 골라 주세요")
        else:
            f_sorted = f.sort_values("주시작일")
            f_last4 = f_sorted.tail(4)
            t_last4 = f_last4[["세션수", "결제시작세션", "주문세션"]].sum()
            
            current_order_rate = rate(t_last4["주문세션"], t_last4["세션수"])
            current_payment_rate = rate(t_last4["주문세션"], t_last4["결제시작세션"])
            
            last4_start_date = f_last4["주시작일"].iloc[0]
            
            g_baseline = g[
                (g["주시작일"] >= baseline_period[0])
                & (g["주시작일"] <= baseline_period[1])
            ].sort_values("주시작일")
            
            valid_order_weeks = g_baseline[g_baseline["세션수"] > 0]
            order_rates_base = (valid_order_weeks["주문세션"] / valid_order_weeks["세션수"] * 100) if not valid_order_weeks.empty else pd.Series(dtype=float)
            
            valid_payment_weeks = g_baseline[g_baseline["결제시작세션"] > 0]
            payment_rates_base = (valid_payment_weeks["주문세션"] / valid_payment_weeks["결제시작세션"] * 100) if not valid_payment_weeks.empty else pd.Series(dtype=float)
            
            if len(order_rates_base) < 4 or len(payment_rates_base) < 4:
                st.markdown("- 기준 기간을 이 집단의 데이터가 있는 시기로 옮겨 주세요")
            else:
                ord_min, ord_max = order_rates_base.min(), order_rates_base.max()
                pay_min, pay_max = payment_rates_base.min(), payment_rates_base.max()
                
                if current_order_rate < ord_min:
                    diff_ord = ord_min - current_order_rate
                    ord_msg = f"{last4_start_date} 주부터 4주간 주문 전환율은 이번 값 {current_order_rate:.1f}%로, 평소 범위({ord_min:.1f}% ~ {ord_max:.1f}%)보다 {diff_ord:.1f}%p 낮습니다."
                elif current_order_rate > ord_max:
                    diff_ord = current_order_rate - ord_max
                    ord_msg = f"{last4_start_date} 주부터 4주간 주문 전환율은 이번 값 {current_order_rate:.1f}%로, 평소 범위({ord_min:.1f}% ~ {ord_max:.1f}%)보다 {diff_ord:.1f}%p 높습니다."
                else:
                    ord_msg = f"{last4_start_date} 주부터 4주간 주문 전환율은 이번 값 {current_order_rate:.1f}%로, 평소 범위({ord_min:.1f}% ~ {ord_max:.1f}%) 안입니다."
                    
                if current_payment_rate < pay_min:
                    diff_pay = pay_min - current_payment_rate
                    pay_msg = f"{last4_start_date} 주부터 4주간 결제 전환율은 이번 값 {current_payment_rate:.1f}%로, 평소 범위({pay_min:.1f}% ~ {pay_max:.1f}%)보다 {diff_pay:.1f}%p 낮습니다."
                elif current_payment_rate > pay_max:
                    diff_pay = current_payment_rate - pay_max
                    pay_msg = f"{last4_start_date} 주부터 4주간 결제 전환율은 이번 값 {current_payment_rate:.1f}%로, 평소 범위({pay_min:.1f}% ~ {pay_max:.1f}%)보다 {diff_pay:.1f}%p 높습니다."
                else:
                    pay_msg = f"{last4_start_date} 주부터 4주간 결제 전환율은 이번 값 {current_payment_rate:.1f}%로, 평소 범위({pay_min:.1f}% ~ {pay_max:.1f}%) 안입니다."
                    
                st.markdown(f"- {ord_msg}")
                st.markdown(f"- {pay_msg}")

        st.caption(f"📏 자동 계산 · 평소 범위 기준 기간 ({baseline_period[0]}) ~ ({baseline_period[1]}) (주별 값)")

        AI_NOTES = {
            "전체": "전체 세션수는 28,840개이며 주문 전환율은 14.5%, 결제 전환율은 80.5%입니다. 단계별 퍼널 중 조회 → 장바구니 전환율이 41.4%로 가장 낮게 나타났습니다. 탐색 단계에서 유저 이탈이 가장 크게 발생하는 것으로 보입니다.",
            "Android": "Android 세션수는 16,053개로 전체의 약 55.7%를 차지하며 주문 전환율은 14.3%, 결제 전환율은 76.8%입니다. iOS(85.5%) 대비 결제 전환율이 낮으며, 특히 2026-04-13 주부터 2026-05-25 주까지 결제실패가 807건 집중 발생하며 결제 전환율이 크게 떨어졌습니다. 해당 기간 동안 Android 결제 시스템상의 오류가 발생했던 것으로 보입니다.",
            "iOS": "iOS 세션수는 12,787개이며 주문 전환율은 14.8%, 결제 전환율은 85.5%입니다. Android(76.8%) 대비 높은 결제 전환율을 보였으나, 가장 낮은 단계 전환율은 조회 → 장바구니(40.8%)로 나타났습니다. 결제 단계의 안정성은 높으나 식당 조회 후 장바구니 담기 유도를 위한 화면 개선이 필요한 것으로 보입니다.",
            "organic": "organic 유입 세션수는 22,489개로 전체의 약 78.0%를 차지하며 주문 전환율은 16.0%, 결제 전환율은 80.4%입니다. 가장 낮은 단계 전환율은 조회 → 장바구니(41.4%)로 전체 평균 수준을 보였습니다. 주요 유입 경로로서 안정적인 퍼널 흐름을 유지하고 있는 것으로 보입니다.",
            "paid_ad": "paid_ad 세션수는 1,595개이며 주문 전환율은 26.3%, 결제 전환율은 90.3%입니다. 전체 주문 전환율(14.5%) 대비 현저히 높은 성과를 보였으며 가장 낮은 단계인 조회 → 장바구니 전환율도 53.7%로 높았습니다. 구매 의도가 높은 유저층이 유입된 영향으로 보입니다.",
            "push": "push 유입은 2026-02-02 주에 처음 나타났으며 총 세션수는 4,635개, 주문 전환율은 3.2%입니다. 다른 집단과 달리 방문 → 조회 전환율이 28.4%로 가장 낮아 전체 평균 대비 큰 이탈을 보였습니다. 푸시 메시지와 앱 랜딩 페이지 간 연결성 및 타깃 적합성 확인이 필요합니다.",
            "referral": "referral 유입의 세션수는 121개로 표본 크기가 매우 작습니다. 주문 전환율과 결제 전환율이 모두 0.0%이며, 가장 낮은 단계 전환율은 결제시작 → 주문(0.0%)입니다. 세션 수가 500개 미만으로 적어 유효한 수치 분석을 위해 데이터 추가 수집이 필요합니다."
        }
        AI_SOURCE = "출처: 사용자 행동 탐색 대시보드 분석 노트"

        with st.expander("🤖 AI 해설 · 전체", expanded=False):
            st.write(AI_NOTES["전체"])
        st.caption(AI_SOURCE)

    st.divider()

    # ------------------------------------------------------------
    # 3. 차트 (개요 탭에 배치)
    # ------------------------------------------------------------
    left, right = st.columns(2, border=True)

    # 3-1. 주별 결제 전환율 (플랫폼별)
    wp = (
        f.groupby(["주시작일", "플랫폼"])[["결제시작세션", "주문세션"]]
        .sum()
        .reset_index()
    )
    wp["결제전환율"] = (wp["주문세션"] / wp["결제시작세션"] * 100).round(1)
    fig1 = px.line(
        wp,
        x="주시작일",
        y="결제전환율",
        color="플랫폼",
        markers=True,
        title="주별 결제 전환율 (플랫폼별)",
        labels={"주시작일": "주시작일", "결제전환율": "결제 전환율 (%)", "플랫폼": "플랫폼"}
    )
    fig1.update_layout(yaxis_title="결제 전환율 (%)", xaxis_title="")
    left.plotly_chart(fig1, width="stretch")

    # 3-2. 세션 퍼널
    steps = {
        "1.방문": "세션수",
        "2.식당조회": "조회세션",
        "3.장바구니": "장바구니세션",
        "4.결제시작": "결제시작세션",
        "5.주문완료": "주문세션",
    }
    fun = pd.DataFrame(
        {"단계": list(steps), "세션수": [f[c].sum() for c in steps.values()]}
    )
    fig2 = px.funnel(
        fun, 
        x="세션수", 
        y="단계", 
        title="세션 퍼널",
        labels={"세션수": "세션 수(개)", "단계": "퍼널 단계"}
    )
    right.plotly_chart(fig2, width="stretch")

    # 3-3. 유입 경로별 주문 전환율
    ws = f.groupby("유입경로")[["세션수", "주문세션"]].sum().reset_index()
    ws["주문전환율"] = (ws["주문세션"] / ws["세션수"] * 100).round(1)
    ws = ws.sort_values("주문전환율")
    fig3 = px.bar(
        ws,
        x="주문전환율",
        y="유입경로",
        orientation="h",
        text_auto=".1f",
        hover_data={"세션수": True},
        title="유입 경로별 주문 전환율",
        labels={"주문전환율": "주문 전환율 (%)", "유입경로": "유입 경로", "세션수": "세션 수(개)"}
    )
    fig3.update_xaxes(
        range=[0, ws["주문전환율"].max() * 1.2]
        if not ws.empty and ws["주문전환율"].max() > 0
        else [0, 100]
    )
    fig3.update_layout(xaxis_title="주문 전환율 (%)", yaxis_title="")
    fig3.update_traces(marker_color=[ACCENT if s == "push" else GRAY for s in ws["유입경로"]])  
    fig3.update_xaxes(visible=False)
    st.plotly_chart(fig3, width="stretch")

with tab_explore:
    st.caption("이 탭에서 볼 수 있는 것: 플랫폼 및 유입 경로별 집단 비교, 누적/단계별 전환율 퍼널, 그리고 드릴다운 분석 및 집단별 AI 해설")

    # ------------------------------------------------------------
    # 4. 집단 비교 영역
    # ------------------------------------------------------------
    st.header("집단 비교")
    compare_by = st.radio("비교 기준", ["플랫폼", "유입경로"], horizontal=True)
    col_name = "플랫폼" if compare_by == "플랫폼" else "유입경로"

    funnel_cols = ["세션수", "조회세션", "장바구니세션", "결제시작세션", "주문세션"]
    grouped = f.groupby(col_name)[funnel_cols].sum().reset_index()

    # 세션 500개 미만 집단 제외 처리
    if exclude_low_sessions:
        low_groups = grouped[grouped["세션수"] < 500][col_name].tolist()
        if low_groups:
            st.caption(f"제외한 집단: {', '.join(low_groups)} (세션 500개 미만)")
        grouped = grouped[grouped["세션수"] >= 500]
    else:
        if (grouped["세션수"] < 500).any():
            st.caption("⚠️ 세션 수가 500개 미만인 집단이 있어 값이 크게 흔들릴 수 있습니다.")

    cum_list = []
    for _, row in grouped.iterrows():
        base = row["세션수"]
        for step_name, col in {
            "1.방문": "세션수",
            "2.식당조회": "조회세션",
            "3.장바구니": "장바구니세션",
            "4.결제시작": "결제시작세션",
            "5.주문완료": "주문세션",
        }.items():
            val = row[col]
            pct = (val / base * 100) if base > 0 else 0
            cum_list.append(
                {
                    col_name: row[col_name],
                    "단계": step_name,
                    "누적전환율": round(pct, 1),
                    "세션수": val,
                }
            )
    df_cum = pd.DataFrame(cum_list)

    step_list = []
    for _, row in grouped.iterrows():
        s_vis = row["세션수"]
        s_view = row["조회세션"]
        s_cart = row["장바구니세션"]
        s_chk = row["결제시작세션"]
        s_ord = row["주문세션"]

        transitions = [
            ("방문 → 조회", s_view, s_vis),
            ("조회 → 장바구니", s_cart, s_view),
            ("장바구니 → 결제시작", s_chk, s_cart),
            ("결제시작 → 주문", s_ord, s_chk),
        ]

        for step_name, curr, prev in transitions:
            rate_val = (curr / prev * 100) if prev > 0 else 0
            step_list.append(
                {
                    col_name: row[col_name],
                    "단계전환": step_name,
                    "단계전환율": round(rate_val, 1),
                    "기준세션": prev,
                }
            )
    df_step = pd.DataFrame(step_list)

    left_comp, right_comp = st.columns(2)

    fig_cum = px.funnel(
        df_cum,
        x="누적전환율",
        y="단계",
        color=col_name,
        title="누적 전환율 퍼널 (첫 단계 대비 %)",
        hover_data={"세션수": True},
        labels={"누적전환율": "누적 전환율 (%)", "단계": "퍼널 단계", col_name: col_name, "세션수": "세션 수(개)"}
    )
    fig_cum.update_layout(xaxis_title="누적 전환율 (%)", yaxis_title="")
    left_comp.plotly_chart(fig_cum, width="stretch")

    fig_step = px.bar(
        df_step,
        x="단계전환",
        y="단계전환율",
        color=col_name,
        barmode="group",
        text="단계전환율",
        hover_data={"기준세션": True},
        title="단계 전환율 (직전 단계 대비 %)",
        labels={"단계전환율": "단계 전환율 (%)", "단계전환": "퍼널 단계 전환", col_name: col_name, "기준세션": "직전 단계 세션 수(개)"}
    )
    fig_step.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
    fig_step.update_layout(yaxis_title="단계 전환율 (%)", xaxis_title="")
    right_comp.plotly_chart(fig_step, width="stretch")

    st.divider()

    # ------------------------------------------------------------
    # 5. 드릴다운 영역: 언제부터, 무엇 때문일까
    # ------------------------------------------------------------
    st.header("드릴다운: 언제부터, 무엇 때문일까")

    # 체크박스 조건 적용하여 드릴다운 선택지 구성
    if exclude_low_sessions:
        valid_grouped_drill = f.groupby(col_name)["세션수"].sum().reset_index()
        available_groups = sorted(valid_grouped_drill[valid_grouped_drill["세션수"] >= 500][col_name].dropna().unique().tolist())
    else:
        available_groups = sorted(f[col_name].dropna().unique().tolist())

    step_mapping = {
        "방문 → 식당조회": ("세션수", "조회세션"),
        "식당조회 → 장바구니": ("조회세션", "장바구니세션"),
        "장바구니 → 결제시작": ("장바구니세션", "결제시작세션"),
        "결제시작 → 주문": ("결제시작세션", "주문세션"),
    }

    col_dd1, col_dd2 = st.columns(2)
    with col_dd1:
        selected_group = st.selectbox("자세히 볼 집단", options=available_groups)
    with col_dd2:
        selected_step_name = st.selectbox("단계", options=list(step_mapping.keys()))

    if selected_group and selected_step_name:
        other_axis = "유입경로" if col_name == "플랫폼" else "플랫폼"
        prev_col, curr_col = step_mapping[selected_step_name]

        df_group = f[f[col_name] == selected_group]
        df_drill1 = (
            df_group.groupby(["주시작일", other_axis], as_index=False)[
                [prev_col, curr_col]
            ]
            .sum()
            .sort_values("주시작일")
        )


        def calc_conv(row):
            p_val = row[prev_col]
            c_val = row[curr_col]
            if p_val == 0 or pd.isna(p_val):
                return None
            return (c_val / p_val) * 100


        df_drill1["전환율"] = df_drill1.apply(calc_conv, axis=1)

        fig_drill1 = px.line(
            df_drill1,
            x="주시작일",
            y="전환율",
            color=other_axis,
            markers=True,
            title=f"① {selected_group}의 주별 {selected_step_name} 전환율 — {other_axis}별로 쪼개 보기",
            labels={"전환율": "전환율 (%)", "주시작일": "주시작일", other_axis: other_axis},
        )
        st.plotly_chart(fig_drill1, width="stretch")
        st.caption("모든 갈래가 같은 시점에 함께 움직였다면, 그 축은 원인이 아닙니다.")

        f_copy = f.copy()
        f_copy["집단분류"] = f_copy[col_name].apply(
            lambda x: selected_group if x == selected_group else "그 외"
        )

        df_drill2 = (
            f_copy.groupby(["주시작일", "집단분류"], as_index=False)["결제실패"]
            .sum()
            .sort_values("주시작일")
        )

        color_map = {selected_group: "#b30000", "그 외": "#b0b0b0"}

        fig_drill2 = px.bar(
            df_drill2,
            x="주시작일",
            y="결제실패",
            color="집단분류",
            barmode="group",
            color_discrete_map=color_map,
            title=f"주별 결제실패 건수 비교 ({selected_group} vs 그 외)",
            labels={"결제실패": "결제실패 건수(건)", "주시작일": "주시작일", "집단분류": "집단 분류"},
        )
        st.plotly_chart(fig_drill2, width="stretch")
        st.caption(
            "문제가 생긴 시점과 함께 움직였다면 원인 후보입니다. 함께 움직였다고 원인이 확정되지는 않습니다."
        )

        # AI 해설 ②: 선택된 집단별 해설 추가
        with st.expander(f"🤖 AI 해설 · {selected_group}", expanded=False):
            note_text = AI_NOTES.get(selected_group, "이 집단의 해설은 아직 없습니다.")
            st.write(note_text)
        st.caption(AI_SOURCE)


with tab_action:
    st.caption("이 탭에서 볼 수 있는 것: 인사이트 기반 실행 계획 및 액션 아이템 제안")
    st.caption("측정 KPI의 현재 값은 선택한 기간의 마지막 4주로 계산합니다")

    # 액션 탭 내 KPI 계산을 위한 헬퍼 함수
    def calc_rate_action(a, b):
        return a / b * 100 if b else 0

    # 사이드바 기간(selected_period)만 적용하고 플랫폼/유입경로 필터는 무시한 데이터베이스
    f_action_base = df[
        (df["주시작일"] >= selected_period[0])
        & (df["주시작일"] <= selected_period[1])
    ] if not df.empty and selected_period[0] is not None else pd.DataFrame(columns=df.columns)

    # 기간 내 주 목록 정렬 및 마지막 4주 필터링
    action_weeks = sorted(f_action_base["주시작일"].unique()) if not f_action_base.empty else []
    if len(action_weeks) >= 4:
        last4_weeks = action_weeks[-4:]
        f_last4_action = f_action_base[f_action_base["주시작일"].isin(last4_weeks)]
    else:
        f_last4_action = f_action_base

    # 카드 1 KPI 계산: 플랫폼 == "Android"인 데이터의 결제 전환율 (주문세션 ÷ 결제시작세션)
    f_card1 = f_last4_action[f_last4_action["플랫폼"] == "Android"] if not f_last4_action.empty else pd.DataFrame()
    if not f_card1.empty:
        t_card1 = f_card1[["결제시작세션", "주문세션"]].sum()
        val_card1 = calc_rate_action(t_card1["주문세션"], t_card1["결제시작세션"])
        metric_str_card1 = f"{val_card1:.1f}%"
    else:
        metric_str_card1 = "데이터 없음"

    # 카드 2 KPI 계산: 유입경로 == "push"인 데이터의 방문 → 식당 조회 전환율 (조회세션 ÷ 세션수)
    f_card2 = f_last4_action[f_last4_action["유입경로"] == "push"] if not f_last4_action.empty else pd.DataFrame()
    if not f_card2.empty:
        t_card2 = f_card2[["세션수", "조회세션"]].sum()
        val_card2 = calc_rate_action(t_card2["조회세션"], t_card2["세션수"])
        metric_str_card2 = f"{val_card2:.1f}%"
    else:
        metric_str_card2 = "데이터 없음"

    col_act1, col_act2 = st.columns(2)

    with col_act1:
        with st.container(border=True):
            st.markdown("- **문제**: 결제 시작에서 주문 전환율이 2026-04-13 주부터 하락해 5월 3주차 최저 41.4%, 6월 1주차부터 회복")
            st.markdown("- **근거**: 같은 기간 iOS는 평소 범위 안 · Android 결제 실패가 주 4건 이하 → 최대 153건")
            st.markdown("- **성격**: 특정 시점에 발생한 문제")
            st.markdown("- **액션**: 추가 분석: 앱 버전별로 결제 실패 확인 → 모니터링: 결제 전환율이 평소 범위를 벗어나는지 매주 점검")
            st.markdown("- **우선순위**: 영향 큼 · 실행 쉬움 → 바로 실행")
            st.markdown("- **측정 KPI**: Android 결제 전환율 (최근 4주)")
            st.markdown("- **대시보드에서**: 개요 탭 규칙 문장(플랫폼 Android) · 탐색 탭 드릴다운")
            st.metric("Android 결제 전환율 (최근 4주)", metric_str_card1)
            st.caption("대시보드에서: 개요 탭 규칙 문장(플랫폼 Android) · 탐색 탭 드릴다운")

    with col_act2:
        with st.container(border=True):
            st.markdown("- **문제**: 방문 → 식당 조회 전환율 28.4% (다른 경로 57-69%), 주문 전환율 3.2%")
            st.markdown("- **근거**: 2026년 2월부터 월별 세션의 30-36%가 push · 기간 내내 25-35%")
            st.markdown("- **성격**: 전체 구간에 세그먼트별 반응 차이")
            st.markdown("- **액션**: 실험: 일부 사용자에게만 푸시 대상과 문구를 바꿔 방문 → 식당 조회 전환율 비교")
            st.markdown("- **우선순위**: 효과 불확실 · 실행 쉬움 → 실험부터")
            st.markdown("- **측정 KPI**: push 방문 → 식당 조회 전환율(최근 4주)")
            st.markdown("- **대시보드에서**: 탐색 탭 비교 기준 '유입경로' · 드릴다운 push")
            st.metric("push 방문 → 식당 조회 전환율 (최근 4주)", metric_str_card2)
            st.caption("대시보드에서: 탐색 탭 비교 기준 '유입경로' · 드릴다운 push")