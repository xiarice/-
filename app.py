"""华南地区植物生态效益自动计算与图表生成器 —— Streamlit 主程序。

设计原则：**图表优先**。文字只保留标题、关键提示与单句类比，
数据一律用卡片、图表、表格呈现。

运行（需在 project/ 目录下执行）::

    streamlit run app.py

模块分工
--------
* ``modules/calculator.py`` 计算引擎（已用广东实测文献校准）
* ``modules/charts.py``     Plotly 图表
* ``modules/ai_report.py``  AI 报告（当前为模板化占位实现）
* ``data/plants.csv``       31 种华南植物参数（13 字段）

调试用假数据
------------
把 :data:`USE_MOCK_DATA` 改为 ``True`` 可跳过真实计算、用假数据验证布局，
确认布局无误后改回 ``False`` 即接入真实引擎。
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import streamlit as st

from modules import ai_report, calculator as calc, charts

#: 置为 True 时使用假数据搭建/检查布局，不调用真实计算引擎
USE_MOCK_DATA = False

# --------------------------------------------------------------------------
# 页面配置与全局样式
# --------------------------------------------------------------------------

st.set_page_config(
    page_title="华南地区植物生态效益计算器",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 2.2rem; padding-bottom: 1rem;}

      /* ---------- 大卡片：24px 圆角 + 极柔阴影 + hover 上浮 ---------- */
      .kpi-card {
          border-radius: 24px;              /* 与 theme.baseRadius 一致 */
          padding: 30px 26px;
          color: white;
          /* 用布局而非 height:100% 保证三张卡等高：
             父级没有确定高度时 height:100% 是空操作，卡片会随内容长短不齐 */
          display: flex;
          flex-direction: column;
          min-height: 220px;
          /* 双层阴影：大范围淡阴影营造"浮起"，小范围贴边阴影收拢边缘。
             用带绿调的 rgba 而非纯黑，避免在纸色背景上发灰发脏 */
          box-shadow:
              0 2px 10px rgba(45,58,49,.05),
              0 1px 2px  rgba(45,58,49,.04);
          transition: transform .18s ease, box-shadow .18s ease;
      }
      /* hover 时轻微上浮 + 阴影加深（纯 CSS，不触发 Streamlit 重跑） */
      .kpi-card:hover {
          transform: translateY(-3px);
          box-shadow:
              0 10px 26px rgba(45,58,49,.10),
              0 2px 6px   rgba(45,58,49,.06);
      }
      /* 尊重系统"减少动态效果"设置 */
      @media (prefers-reduced-motion: reduce) {
          .kpi-card {transition: none;}
          .kpi-card:hover {transform: none;}
      }

      .kpi-label {font-size: 1rem; opacity: .92; margin-bottom: 6px;}
      .kpi-value {font-size: 3.1rem; font-weight: 700; line-height: 1.05;}
      .kpi-unit  {font-size: 1.15rem; font-weight: 400; opacity: .9;}
      /* margin-top:auto 把说明文字压到卡片底部，三张卡的说明行对齐 */
      .kpi-note  {font-size: .95rem; margin-top: auto; padding-top: 14px; opacity: .95;}

      /* 三张卡片统一为同一绿色系的三个深度（白字对比度均 >=5.2:1） */
      .c-deep  {background: linear-gradient(135deg,#2D3A31,#3A4A3C);}
      .c-mid   {background: linear-gradient(135deg,#3F513F,#4A5F50);}
      .c-light {background: linear-gradient(135deg,#4F6B55,#5C7261);}

      /* 侧边栏说明小字：与主色同系，替代原来的红色 */
      .side-note {color:#4A5F50; font-size:.78rem; line-height:1.5;}
    </style>
    """,
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------
# 纸张颗粒纹理（Paper Grain）
# --------------------------------------------------------------------------
# 用内联 SVG 的 feTurbulence 生成噪点，直接作为 background-image 贴在容器上。
# 这样做的两个好处：
#   1) 不新增 DOM 节点 —— 不存在覆盖层遮挡点击的问题；
#   2) 不涉及 z-index —— 下拉菜单、toast 等弹层的层级完全不受影响。
# 注意：SVG 内的 url(#n) 必须把 # 转义成 %23，否则 CSS 会把它当成锚点。
_NOISE_SVG = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'>"
    "<filter id='n'>"
    "<feTurbulence type='fractalNoise' baseFrequency='0.75' "
    "numOctaves='3' stitchTiles='stitch'/>"
    "</filter>"
    "<rect width='160' height='160' filter='url(%23n)' opacity='0.035'/>"
    "</svg>"
)

st.markdown(
    f"""
    <style>
      /* 主区域与侧边栏各贴一层（两者底色不同，需分别绘制）。
         若主区域看不到纹理，说明被中间某层不透明容器盖住，
         届时把选择器扩展到 stMain / stMainBlockContainer 即可。 */
      [data-testid="stAppViewContainer"],
      [data-testid="stSidebar"] {{
          background-image: url("{_NOISE_SVG}");
          background-repeat: repeat;
      }}
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------
# 数据加载（缓存）
# --------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def load_plants() -> pd.DataFrame:
    """读取植物参数库。"""
    return calc.load_plants()


PLANTS = load_plants()
PLANT_BY_NAME = {r["name_cn"]: r for _, r in PLANTS.iterrows()}


# --------------------------------------------------------------------------
# 会话状态
# --------------------------------------------------------------------------

if "plan" not in st.session_state:
    st.session_state.plan = []          # [{"name_cn", "quantity", "unit"}, ...]
if "computed" not in st.session_state:
    st.session_state.computed = None    # 点击"开始计算"后写入


# --------------------------------------------------------------------------
# 侧边栏：输入区
# --------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### 种植方案输入")

    # ======================================================================
    # 分类联动选择：先选「植物类别」，再在该类别下选具体植物
    # ======================================================================

    # 若植物库为空（CSV 只有表头或无有效行），直接给出明确提示并停止渲染，
    # 避免在后续 selectbox 上抛难以理解的异常
    if PLANTS.empty:
        st.error("植物库 data/plants.csv 为空，无法选择植物。")
        st.caption("请检查该文件是否包含表头与至少一行数据。")
        st.stop()

    # 类别取值直接从 plants.csv 的 type 字段提取（去重保序），
    # 这样将来 CSV 新增类别（如"藤本"）无需改代码
    CATEGORY_ORDER = ["乔木", "灌木", "地被"]
    available_cats = list(dict.fromkeys(PLANTS["type"].astype(str)))
    plant_categories = [c for c in CATEGORY_ORDER if c in available_cats]
    plant_categories += [c for c in available_cats if c not in plant_categories]

    if not plant_categories:
        st.error("植物库中没有任何可用的 ‘type’ 类别，无法选择植物。")
        st.caption("请检查 data/plants.csv 的 type 字段是否填写。")
        st.stop()

    # 每个类别的默认植物与默认单位（用于切换类别时自动带出）
    PREFERRED_SPECIES = {"乔木": "细叶榕", "灌木": "大红花", "地被": "麦冬"}
    DEFAULT_UNIT = {"乔木": "株", "灌木": "株", "地被": "㎡"}

    # 初始化会话状态
    if "category" not in st.session_state:
        st.session_state.category = "乔木" if "乔木" in plant_categories else plant_categories[0]
    if "unit" not in st.session_state:
        st.session_state.unit = DEFAULT_UNIT.get(st.session_state.category, "株")
    if "species" not in st.session_state:
        st.session_state.species = PREFERRED_SPECIES.get(st.session_state.category)

    def _on_category_change() -> None:
        """切换类别时：同步带出该类别下的默认植物与默认单位。

        在该类别下没有默认植物时清空选择，交由 selectbox 取第一个选项。
        """
        cat = st.session_state["category"]
        st.session_state["unit"] = DEFAULT_UNIT.get(cat, "株")
        pref = PREFERRED_SPECIES.get(cat)
        st.session_state["species"] = pref if pref else None

    # 类别下拉框
    category = st.selectbox(
        "植物类别",
        plant_categories,
        key="category",
        on_change=_on_category_change,
        help="决定下方植物下拉框的可选范围",
    )

    # 按类别过滤植物（保持中文名排序，便于查找）
    filtered = PLANTS[PLANTS["type"].astype(str) == category]
    species_options = sorted(filtered["name_cn"].tolist())

    if not species_options:
        # 异常提示：该类别下没有数据
        st.warning(f"当前类别下暂无数据（类别：{category}）")
        st.caption("请检查 data/plants.csv 的 type 字段，或改选其他类别。")
        selected_name = None
        _p = None
    else:
        # 会话中的 species 若不属于当前类别（例如刚切换了类别），
        # 重置为该类别下的第一个植物，避免 selectbox 取值越界
        if st.session_state.get("species") not in species_options:
            pref = PREFERRED_SPECIES.get(category)
            st.session_state["species"] = pref if pref in species_options else species_options[0]

        selected_name = st.selectbox(
            "选择植物",
            species_options,
            key="species",
            help=f"当前类别「{category}」下的植物，共 {len(species_options)} 种",
        )
        _p = PLANT_BY_NAME[selected_name]
        st.caption(
            f"本类别 {len(species_options)} 种｜LAI {_p['LAI']}｜"
            f"冠层 {_p['canopy_area']} m²｜株高 {_p['height']} m"
        )

    # ---- 2. 数量输入框 + 单位选择 ----
    # 单位默认值随类别联动：乔木/灌木 → 株，地被 → ㎡（可在界面上改）
    c1, c2 = st.columns([2, 1])
    with c1:
        quantity_input = st.number_input(
            "数量", min_value=0.1, value=10.0, step=1.0, format="%.1f"
        )
    with c2:
        unit = st.selectbox("单位", ["株", "㎡"], key="unit")

    if _p is not None and unit == "㎡" and float(_p["canopy_area"]) > 0:
        est = quantity_input / float(_p["canopy_area"])
        st.caption(f"≈ {est:,.1f} 株（按冠层 {_p['canopy_area']} m²/株 折算）")

    # ---- 3. 添加到方案 ----
    if st.button(
        "添加到方案",
        width="stretch",
        type="primary",
        disabled=(selected_name is None),
    ):
        st.session_state.plan.append(
            {"name_cn": selected_name, "quantity": float(quantity_input), "unit": unit}
        )
        st.session_state.computed = None
        st.rerun()

    st.divider()

    # ---- 4. 已添加方案列表（带删除按钮）----
    st.markdown(f"**已选方案**（{len(st.session_state.plan)} 项）")
    if not st.session_state.plan:
        st.caption("尚未添加植物")
    else:
        for i, item in enumerate(st.session_state.plan):
            col_a, col_b = st.columns([5, 1])
            with col_a:
                st.markdown(f"**{item['name_cn']}**　{item['quantity']:g} {item['unit']}")
            with col_b:
                if st.button("✕", key=f"del_{i}", help=f"删除 {item['name_cn']}"):
                    st.session_state.plan.pop(i)
                    st.session_state.computed = None
                    st.rerun()

    st.divider()

    # ---- 计算参数 ----
    with st.expander("计算参数", expanded=False):
        intensity_keys = list(calc.RAIN_INTENSITIES)
        intensity = st.selectbox(
            "降雨强度档位", intensity_keys,
            index=intensity_keys.index(calc.DEFAULT_RAIN_INTENSITY),
            format_func=lambda k: calc.RAIN_INTENSITIES[k].label,
            help="截留率已用赵峰等华南绿地实测数据校准",
        )
        _ri = calc.RAIN_INTENSITIES[intensity]
        st.caption(
            f"该档默认截留率 {_ri.rate_default:.0%}"
            f"（区间 {_ri.rate_min:.0%}~{_ri.rate_max:.0%}）"
        )

        use_precip = st.checkbox(
            "输入实际降水量（启用实测模型）", value=False,
            help="按赵峰等实测对数模型 I(%) = a·ln(P) + b 计算，比档位更精确",
        )
        precipitation_mm = st.slider("单次降水量 (mm)", 1.0, 100.0, 15.0, 0.5) if use_precip else None

        st.markdown("---")
        region_factor = st.slider(
            "地区修正系数 region_factor", 0.5, 1.5, 1.0, 0.05,
            help="1.0 = 广东地区（基准）。其他地区可调整；只影响蒸腾降温与雨水截留，"
                 "不影响固碳释氧与滞尘。",
        )
        calc.set_region_factor(region_factor)

        days = st.number_input(
            "核算天数（天）", min_value=1, value=365, step=1,
            help="影响固碳/释氧/吸热/滞尘的累计量；雨水截留为单次降雨量",
        )

    # ---- 5. 开始计算 ----
    if st.button("开始计算", width="stretch", type="primary"):
        if not st.session_state.plan:
            st.warning("请先添加植物")
        else:
            st.session_state.computed = {
                "plan": list(st.session_state.plan),
                "intensity": intensity,
                "precipitation_mm": precipitation_mm,
                "days": int(days),
                "region_factor": region_factor,
            }
            st.rerun()

    # ---- 6. 清空方案（侧边栏底部）----
    if st.button("清空方案", width="stretch"):
        st.session_state.plan = []
        st.session_state.computed = None
        st.rerun()

    st.markdown("---")
    st.markdown(
        '<div class="side-note">数据已用广东地区实测文献校准<br>'
        "林欣2014 / 黎国健2008 / 赵峰 / 杨士弘1996 / 李寒娥2006 / "
        "代色平2013 / 李江波2024 / 朱炜晔2022</div>",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------
# 计算
# --------------------------------------------------------------------------

def build_selection(plan: list[dict]) -> list[tuple[pd.Series, int]]:
    """把方案列表转成 calculator 需要的 ``[(植物行, 株数), ...]``。"""
    selected = []
    for item in plan:
        plant = PLANT_BY_NAME[item["name_cn"]]
        if item["unit"] == "㎡":
            canopy = float(plant["canopy_area"]) or 1.0
            qty = max(1, int(round(item["quantity"] / canopy)))
        else:
            qty = max(1, int(round(item["quantity"])))
        selected.append((plant, qty))
    return selected


def build_mock_detail(plan: list[dict]) -> pd.DataFrame:
    """生成假数据，用于在不接入引擎时验证布局。"""
    rng = np.random.default_rng(42)
    rows = []
    for item in plan:
        plant = PLANT_BY_NAME[item["name_cn"]]
        rows.append(
            {
                "name_cn": item["name_cn"],
                "type": plant["type"],
                "quantity": item["quantity"],
                "days": 365,
                "carbon_kg": float(rng.uniform(5, 400)),
                "oxygen_kg": float(rng.uniform(10, 1000)),
                "co2_equivalent_kg": float(rng.uniform(20, 1500)),
                "transpiration_kg": float(rng.uniform(100, 60000)),
                "heat_absorbed_kj": float(rng.uniform(1e5, 1.5e8)),
                "cooled_air_volume_m3": float(rng.uniform(1e5, 1.3e8)),
                "cooling_index": float(rng.uniform(20, 100)),
                "interception_rate": float(rng.uniform(0.25, 0.8)),
                "intercepted_water_kg": float(rng.uniform(10, 1200)),
                "canopy_capacity_kg": float(rng.uniform(20, 3000)),
                "dust_kg": float(rng.uniform(1, 300)),
                "region_factor": 1.0,
            }
        )
    return pd.DataFrame(rows)


computed = st.session_state.computed
detail_df = pd.DataFrame()
summary: dict[str, float] = {}

if computed:
    calc.set_region_factor(computed["region_factor"])
    if USE_MOCK_DATA:
        detail_df = build_mock_detail(computed["plan"])
    else:
        detail_df = calc.summarize_community(
            build_selection(computed["plan"]),
            days=computed["days"],
            intensity=computed["intensity"],
            precipitation_mm=computed["precipitation_mm"],
        )
    summary = calc.community_totals(detail_df)

# 图表用的口径变量（避免在 Tab 内重复换算，也便于核对单位）
#   *_annual = 核算期累计；*_daily = 日均；water_per_event = 单次降雨截留
carbon_annual = summary.get("carbon_kg", 0.0)                     # kg C
cooling_mj_annual = summary.get("heat_absorbed_kj", 0.0) / 1000.0  # MJ
water_per_event = summary.get("intercepted_water_kg", 0.0)        # L
_days = computed["days"] if computed else 1
carbon_daily = carbon_annual / _days                              # kg C/天
cooling_daily = cooling_mj_annual / _days                         # MJ/天


# --------------------------------------------------------------------------
# 首部标题
# --------------------------------------------------------------------------

st.title("华南地区植物生态效益自动计算与图表生成器")
st.markdown(
    "**输入种植方案，自动计算固碳、降温、雨水截留效益**（数据基于广东地区文献校准）"
)
st.divider()


# --------------------------------------------------------------------------
# 主区域：4 个 Tab
# --------------------------------------------------------------------------

tab_dash, tab_chart, tab_detail, tab_ai = st.tabs(
    ["核心数据看板", "可视化图表", "种植方案明细", "AI设计辅助报告"]
)


# ========================== Tab1 核心数据看板 ==========================
with tab_dash:
    if not computed or detail_df.empty:
        st.info("请先添加植物，然后在左侧点击「开始计算」")
    else:
        days = computed["days"]
        total_carbon = summary["carbon_kg"]                     # kg C / 核算期
        # 吸热量按**日均**展示：summary 里是核算期累计 kJ，
        # 必须先除以天数再换算成 MJ，否则会把「一年」标成「一天」（差 365 倍）
        total_heat_mj_day = cooling_daily                       # MJ / 天
        total_water_l = summary["intercepted_water_kg"]         # kg ≈ L / 次

        # 通俗类比系数统一放在 ai_report.py，便于集中调整
        car_co2 = total_carbon * calc.CO2_TO_C_RATIO / ai_report.CAR_CO2_KG_PER_YEAR
        bottles = total_water_l / ai_report.BOTTLE_LITERS

        k1, k2, k3 = st.columns(3)
        with k1:
            st.markdown(
                f"""<div class="kpi-card c-deep">
                  <div class="kpi-label">总固碳量（{days} 天）</div>
                  <div class="kpi-value">{total_carbon:,.1f}
                    <span class="kpi-unit">kg C</span></div>
                  <div class="kpi-note">
                    折合 CO₂ {total_carbon * calc.CO2_TO_C_RATIO:,.0f} kg<br>
                    相当于 {car_co2:,.1f} 辆小汽车一年的碳排放</div>
                </div>""",
                unsafe_allow_html=True,
            )
        with k2:
            st.markdown(
                f"""<div class="kpi-card c-mid">
                  <div class="kpi-label">日蒸腾吸热量</div>
                  <div class="kpi-value">{total_heat_mj_day:,.1f}
                    <span class="kpi-unit">MJ/天</span></div>
                  <div class="kpi-note">
                    核算期累计 {cooling_mj_annual:,.0f} MJ<br>
                    按汽化潜热 2.404 MJ/kg 折算（黎国健等2008 实测）</div>
                </div>""",
                unsafe_allow_html=True,
            )
        with k3:
            rate_txt = f"{detail_df['interception_rate'].iloc[0]:.0%}"
            st.markdown(
                f"""<div class="kpi-card c-light">
                  <div class="kpi-label">总截留量（单次降雨）</div>
                  <div class="kpi-value">{total_water_l:,.1f}
                    <span class="kpi-unit">L</span></div>
                  <div class="kpi-note">
                    截留率 {rate_txt}｜{calc.RAIN_INTENSITIES[computed['intensity']].label}<br>
                    相当于 {bottles:,.0f} 瓶 550 mL 矿泉水</div>
                </div>""",
                unsafe_allow_html=True,
            )

        st.write("")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("释氧量", f"{summary['oxygen_kg']:,.1f} kg")
        m2.metric("CO₂ 当量", f"{summary['co2_equivalent_kg']:,.1f} kg")
        m3.metric("蒸腾量", f"{summary['transpiration_kg']:,.0f} kg")
        m4.metric("滞尘量", f"{summary['dust_kg']:,.1f} kg")

        st.caption(
            f"核算天数 {days} 天｜植物 {len(detail_df)} 种｜地区修正系数 "
            f"{computed['region_factor']:.2f}（1.0 = 广东地区）"
        )


# ========================== Tab2 可视化图表 ==========================
with tab_chart:
    if not computed or detail_df.empty:
        st.info("请先添加植物，然后在左侧点击「开始计算」")
    else:
        # ---- 第 1 排：三维效益雷达图（整幅，需要足够宽度才看得清三角）----
        st.plotly_chart(
            charts.make_radar_chart(
                carbon_annual, cooling_mj_annual, water_per_event,
                label="当前方案",
            ),
            width="stretch", config={"displayModeBar": False},
        )

        # ---- 第 2 排：固碳贡献堆叠柱状图 + 贡献占比环形图 ----
        left, right = st.columns(2)
        with left:
            st.plotly_chart(
                charts.make_stacked_bar(detail_df),
                width="stretch", config={"displayModeBar": False},
            )
        with right:
            PIE_META = {
                "carbon_kg": ("固碳量", "kg C"),
                "intercepted_water_kg": ("雨水截留量", "kg"),
                "heat_absorbed_kj": ("降温吸热量", "kJ"),
                "dust_kg": ("滞尘量", "kg"),
            }
            pie_metric = st.selectbox(
                "贡献指标", list(PIE_META), key="pie_metric",
                format_func=lambda k: PIE_META[k][0],
            )
            st.plotly_chart(
                charts.make_pie_chart(
                    detail_df,
                    value_col=pie_metric,
                    metric_label=PIE_META[pie_metric][0],
                    unit=PIE_META[pie_metric][1],
                ),
                width="stretch", config={"displayModeBar": False},
            )

        # ---- 第 3 排：当前方案 vs 纯草坪基线（双面板对比）----
        # 对比图：当前值取「日均 / 单次」口径，基线必须用同一口径，
        # 故显式传 days=1 强制日均。若省略 days，函数会从明细表的 days 列
        # 读到 365，返回全年累计，与日均相比会差 365 倍（方案反而显示"不如草坪"）。
        baseline_daily = charts.make_baseline_values(detail_df, days=1)
        st.plotly_chart(
            charts.make_comparison_bar(
                {"carbon": carbon_daily, "cooling": cooling_daily, "rainwater": water_per_event},
                baseline_daily,
                current_label="乔灌草方案",
                baseline_label="纯草坪基线",
            ),
            width="stretch", config={"displayModeBar": False},
        )
        st.caption(
            "对比口径：固碳与降温为**日均**，雨水截留为**单次降雨**；"
            f"基线按同样冠层面积全部种 {charts.BASELINE_SPECIES} 推算。"
        )


# ========================== Tab3 种植方案明细 ==========================
with tab_detail:
    if not computed or detail_df.empty:
        st.info("请先添加植物，然后在左侧点击「开始计算」")
    else:
        st.markdown("#### 各植物贡献明细")

        # ---- 明细表整理 ----
        # 两个显示层修正：
        # 1) 「方案输入」列：保留用户原始输入（如 10 ㎡），
        #    否则单位被折算成株数后，读者会误以为是 2 株。
        # 2) 「截留率」：interception_rate 存的是小数（0.40），
        #    必须 ×100 再配 % 格式，否则渲染成 0.4%。
        table_df = detail_df.copy()
        plan_items = computed["plan"] if computed else []
        if len(plan_items) == len(table_df):
            table_df["方案输入"] = [
                f"{it['quantity']:g} {it['unit']}" for it in plan_items
            ]
        else:  # 兜底：长度不一致时不显示该列，避免错位
            table_df["方案输入"] = "—"
        table_df["截留率%"] = table_df["interception_rate"] * 100.0

        SHOW_COLS = {
            "name_cn": "植物", "type": "类型",
            "方案输入": "方案输入", "quantity": "株数(株)",
            "carbon_kg": "固碳(kg C)", "oxygen_kg": "释氧(kg)",
            "co2_equivalent_kg": "CO₂当量(kg)", "transpiration_kg": "蒸腾(kg)",
            "heat_absorbed_kj": "吸热(kJ)", "intercepted_water_kg": "截留(kg)",
            "截留率%": "截留率", "dust_kg": "滞尘(kg)",
            "cooling_index": "降温指数",
        }
        table = table_df[list(SHOW_COLS)].rename(columns=SHOW_COLS)
        st.dataframe(
            table, width="stretch", hide_index=True,
            column_config={
                "截留率": st.column_config.NumberColumn(format="%.1f%%"),
                "降温指数": st.column_config.ProgressColumn(
                    min_value=0, max_value=100, format="%.1f"
                ),
            },
        )
        st.caption(
            "「方案输入」为你填写时的原始数值与单位；「株数」是换算后的实际株数"
            "（选 ㎡ 时按单株冠层面积折算，四舍五入）。"
            "截留率是本次计算的**场景级参数**，同一雨强档位下所有植物相同；"
            "降温指数为单株冠层单位面积日潜热通量在全部 31 种植物中的分位排名（0~100）。"
        )

        st.markdown("#### 合计")
        t1, t2, t3 = st.columns(3)
        t1.metric("总固碳量", f"{detail_df['carbon_kg'].sum():,.1f} kg C")
        t2.metric("总吸热量", f"{detail_df['heat_absorbed_kj'].sum() / 1000:,.1f} MJ")
        t3.metric("总截留量", f"{detail_df['intercepted_water_kg'].sum():,.1f} kg")

        with st.expander("数据来源（逐种，含文献引用与换算过程）"):
            for _, row in PLANTS.iterrows():
                if row["name_cn"] in set(detail_df["name_cn"]):
                    st.markdown(f"**{row['name_cn']}**　{row['data_source']}")


# ========================== Tab4 AI 设计辅助报告 ==========================
with tab_ai:
    if not computed or detail_df.empty:
        st.info("请先添加植物，然后在左侧点击「开始计算」")
    else:
        # 把三个计算结果组装成 generate_report 需要的结构
        carbon_result = {
            "carbon_kg": summary["carbon_kg"],
            "oxygen_kg": summary["oxygen_kg"],
            "co2_equivalent_kg": summary["co2_equivalent_kg"],
        }
        cooling_result = {
            "transpiration_kg": summary["transpiration_kg"],
            "heat_absorbed_kj": summary["heat_absorbed_kj"],
            "cooled_air_volume_m3": summary["cooled_air_volume_m3"],
        }
        rainwater_result = {
            "intercepted_water_kg": summary["intercepted_water_kg"],
            "interception_rate": float(detail_df["interception_rate"].iloc[0]),
        }
        # 植物清单：直接传逐种明细，ai_report 兼容该结构
        plant_list = detail_df.to_dict("records")

        # 环境变量是否已配置（决定开关的可用性）
        # 用 getattr 兜底：即使 ai_report 是旧版本/被覆盖而缺少该常量，
        # 界面也不会崩成 AttributeError，只是回退到默认变量名
        key_env = getattr(ai_report, "API_KEY_ENV", "DEEPSEEK_API_KEY")
        has_key = bool(os.environ.get(key_env, "").strip())
        if has_key:
            st.success(f"已检测到环境变量 {key_env}，可在线生成。")
        else:
            st.info(
                f"未设置环境变量 `{key_env}`，将使用离线分析。"
                "配置方法见下方说明。"
            )

        use_api = st.toggle(
            "使用 DeepSeek API 生成", value=has_key, disabled=not has_key,
            help="需先配置环境变量 DEEPSEEK_API_KEY",
        )
        show_reason = st.checkbox("显示降级原因（便于排查）", value=False)

        # ---- 生成报告 ----
        # 注意：Streamlit 每次交互都会重新执行整个脚本（含所有 Tab）。
        # 若在此直接调用 API，用户每拖一次滑块就会产生一次计费请求。
        # 因此在线模式改为「按钮触发 + 结果按场景签名缓存」：
        #   * 只有点击按钮才真正请求；
        #   * 相同场景（植物数/天数/雨强/地区系数/开关）后续重跑直接复用结果。
        report = None
        if use_api:
            sig = (
                len(plant_list),
                computed["days"],
                computed["intensity"],
                computed["region_factor"],
                show_reason,
            )
            if st.button("生成 AI 报告", type="primary"):
                with st.spinner("正在调用 DeepSeek…"):
                    st.session_state["ai_report_cache"] = (
                        sig,
                        ai_report.generate_report(
                            carbon_result, cooling_result, rainwater_result,
                            plant_list, use_api=True, verbose=show_reason,
                        ),
                    )
            cached = st.session_state.get("ai_report_cache")
            if cached and cached[0] == sig:
                report = cached[1]
            else:
                st.info(
                    "点击上方按钮生成在线报告。"
                    "（仅点击时调用 API，避免每次刷新重复计费）"
                )
        else:
            report = ai_report.generate_report(
                carbon_result, cooling_result, rainwater_result,
                plant_list, use_api=False, verbose=show_reason,
            )

        if report:
            st.markdown(report)

        with st.expander("查看发送给 AI 的提示词"):
            st.code(
                ai_report.build_prompt(
                    carbon_result, cooling_result, rainwater_result, plant_list
                ),
                language="text",
            )

        with st.expander("如何配置 DeepSeek API Key"):
            st.markdown(
                "**PowerShell（仅当前窗口有效）**\n"
                "```powershell\n"
                '$env:DEEPSEEK_API_KEY = "sk-你的key"\n'
                "```\n"
                "**PowerShell（永久，需重开终端）**\n"
                "```powershell\n"
                '[Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", '
                '"sk-你的key", "User")\n'
                "```\n"
                "**CMD**\n"
                "```bat\n"
                "set DEEPSEEK_API_KEY=sk-你的key\n"
                "```\n"
                "配置后重启 `streamlit run app.py` 生效。"
            )

