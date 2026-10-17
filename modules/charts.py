"""Plotly 图表生成 —— 单一绿色系（Botanical / Organic Serif 风格）。

配色理念
--------
全部颜色落在**同一绿色系**内，与 `.streamlit/config.toml` 的主题同源，
不出现红、橙、蓝。分三层：

* :data:`TYPE_PALETTE` —— 按生活型的语义色。乔木用深林绿（体量最大、色最沉），
  灌木用苔藓绿，地被用鼠尾草绿，形成"从林冠到地被"的明度梯度。
* :data:`ACCENT_PALETTE` —— 同色系的强调与中性色（基线系列、次级填充、
  图内强调文字），替代早期的陶土/赭黄等偏红的大地色。
* :data:`COLORWAY` —— 由深到浅的七档绿，供环形图等多色场景循环取用。

图表背景统一**透明**，让页面的纸张颗粒纹理透进来。

图表清单
--------
==============================  ==========================================
:func:`make_radar_chart`        三维效益得分雷达图（固碳/降温/截留）
:func:`make_stacked_bar`        各植物固碳贡献堆叠柱状图（按生活型着色）
:func:`make_pie_chart`          各植物贡献占比环形图
:func:`make_baseline_values`    由当前方案推算纯草坪基线
:func:`make_comparison_bar`     当前方案 vs 纯草坪基线对比（双面板）
==============================  ==========================================
"""

from __future__ import annotations

from typing import Mapping, Sequence

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ==========================================================================
# 配色 —— 单一绿色系（与 .streamlit/config.toml 完全一致）
# ==========================================================================
# 设计约束（来自需求方）：
#   1) 全部颜色必须落在同一色系内，不出现红、橙、蓝；
#   2) 与页面主题（纸底 #F9F8F4 / 深林绿 #2D3A31 / 鼠尾草 #8C9A84）同源；
#   3) 图表背景透明，让页面的纸张纹理透进来。
# 原先的「大地色系」含陶土/赭黄（偏红），已整体替换为绿系不同明度。

#: 生活型语义色：从林冠到地被的同一绿系三级明度
TYPE_PALETTE: dict[str, str] = {
    "乔木": "#2D3A31",   # 深林绿（最沉，体量最大）
    "灌木": "#4A5F50",   # 苔藓绿（中）
    "地被": "#6E7F68",   # 鼠尾草绿（最浅）
}

#: 强调与中性色（同一色系的不同角色，替代原先的陶土/赭黄）
ACCENT_PALETTE: dict[str, str] = {
    "baseline": "#8C9A84",   # 基线/对照系列（鼠尾草绿，与深绿主系列可区分）
    "muted": "#A8B49E",      # 次级填充
    "pale": "#CFD6C6",       # 极浅填充 / 标注底色
    "text": "#4A5F50",       # 图内强调文字（对纸底 5.00:1，达 AA）
    "neutral": "#B5B0A4",    # 中性灰褐，仅作填充与虚线，不承载文字
}

#: 页面与文字（与 config.toml 对齐）
BG_COLOR = "#F9F8F4"           # 暖米色纸底（仅用于饼图描边等需要"切纸"效果处）
GRID_COLOR = "#E8E4DA"         # 暖灰网格线
FONT_COLOR = "#2D3A31"         # 深林绿正文（对纸底 11.22:1 AAA）

#: 通用离散色序：同一绿系由深到浅（与 theme.chartCategoricalColors 一致）
COLORWAY = [
    "#2D3A31", "#3F513F", "#4A5F50", "#5F7059",
    "#6E7F68", "#8C9A84", "#A8B49E",
]

#: 正文字体族（与 theme.font 一致，回退到系统中文字体）
FONT_FAMILY = "Noto Sans SC, Microsoft YaHei, PingFang SC, SimHei, sans-serif"

#: 标题字体族（衬线，与 theme.headingFont 一致）
TITLE_FONT_FAMILY = "Noto Serif SC, Songti SC, SimSun, serif"

#: 图内标注底色（半透明，压住网格又不遮纹理）
ANNOT_BG = "rgba(207,214,198,0.55)"

#: 三维雷达图的维度定义：(键, 显示名, 单位)
RADAR_AXES: list[tuple[str, str, str]] = [
    ("carbon", "固碳", "kg C"),
    ("cooling", "降温", "MJ"),
    ("rainwater", "雨水截留", "L"),
]

#: 满量程参考基准：各项得分达到 100 分所需的量。
#:
#: **单位必须与传入值一致**，否则会有维度顶到 100 分而失去分辨率。
#: 默认按"规模化方案的全年累计"标定（截留为单次降雨量）。
RADAR_BENCHMARKS: dict[str, float] = {
    "carbon": 12000.0,     # kg C / 年
    "cooling": 6000000.0,  # MJ / 年
    "rainwater": 20000.0,  # L / 单次降雨
}

#: 对比图指标：(键, 显示名, 单位, 换算系数)
COMPARISON_METRICS: list[tuple[str, str, str, float]] = [
    ("carbon", "固碳", "kg C", 1.0),
    ("cooling", "降温吸热", "MJ", 1e-3),   # 内部按 kJ 存储，此处折 MJ
    ("rainwater", "雨水截留", "L", 1.0),
]

#: 草坪基线使用的参照草种（取自植物库，逐株推算）
BASELINE_SPECIES = "白三叶"

__all__ = [
    # 配色与常量
    "TYPE_PALETTE", "ACCENT_PALETTE", "COLORWAY", "BG_COLOR", "GRID_COLOR",
    "FONT_COLOR", "FONT_FAMILY", "TITLE_FONT_FAMILY", "ANNOT_BG",
    "RADAR_AXES", "RADAR_BENCHMARKS",
    "COMPARISON_METRICS", "BASELINE_SPECIES",
    # 图表函数
    "make_radar_chart", "make_stacked_bar", "make_pie_chart",
    "make_baseline_values", "make_comparison_bar",
]


def _base_layout(
    fig: go.Figure,
    title: str,
    height: int = 440,
    bottom: int = 110,
) -> go.Figure:
    """统一套用「Botanical / Organic Serif」版式。

    关键点
    ------
    1. ``template="none"`` + 透明背景 —— 不能用 ``plotly_white``，
       它会强制把画布底色刷成白色，页面的纸张纹理就透不进来了。
    2. 透明后网格线必须自己给（否则看不见），见下方 update_xaxes/yaxes。
    3. **图例锚定到画布容器而非绘图区**（``yref="container"``）。
       用 paper 坐标（如 ``y=-0.20``）时，图例位置会随绘图区高度浮动，
       在底部有倾斜刻度标签或轴标题的图表里就会与之重叠。
       改成容器坐标后图例固定在画布底边，配合 ``bottom`` 留白即不可能重叠。
    4. 标题用衬线体，与页面 headingFont 呼应；正文用无衬线体。

    :param bottom: 底部留白（px）。底部有倾斜 X 轴标签时需给足，
        建议 120~140；没有 X 轴标签的图（雷达/环形）可给小值。
    """
    fig.update_layout(
        title=dict(
            text=title,
            font=dict(size=17, color=FONT_COLOR, family=TITLE_FONT_FAMILY),
            x=0.01,
        ),
        template="none",                      # 不用 plotly_white，否则强制白底
        colorway=COLORWAY,
        paper_bgcolor="rgba(0,0,0,0)",        # 画布透明 → 透出纸纹
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT_FAMILY, size=13, color=FONT_COLOR),
        margin=dict(l=56, r=32, t=64, b=bottom),
        height=height,
        hoverlabel=dict(
            bgcolor="#FFFDF8",                # 纸白
            bordercolor=GRID_COLOR,
            font=dict(family=FONT_FAMILY, size=13, color=FONT_COLOR),
        ),
        legend=dict(
            orientation="h",
            yref="container", yanchor="bottom", y=0.01,   # 固定贴画布底边
            xref="container", xanchor="left", x=0.0,
            bgcolor="rgba(0,0,0,0)", font=dict(size=12),
        ),
    )
    fig.update_xaxes(gridcolor=GRID_COLOR, zeroline=False, linecolor=GRID_COLOR)
    fig.update_yaxes(gridcolor=GRID_COLOR, zeroline=False, linecolor=GRID_COLOR)
    return fig


def _empty(title: str, height: int = 380) -> go.Figure:
    """空数据占位图。"""
    fig = go.Figure()
    fig.add_annotation(
        text="暂无数据", x=0.5, y=0.5, showarrow=False,
        font=dict(size=18, color=ACCENT_PALETTE["text"]),
    )
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return _base_layout(fig, title, height)


def _norm_score(value: float, benchmark: float) -> float:
    """把绝对量按满量程基准折算为 0~100 分。"""
    if benchmark <= 0:
        return 0.0
    return max(0.0, min(100.0, value / benchmark * 100.0))


# ==========================================================================
# 函数 1：三维效益雷达图
# ==========================================================================


def make_radar_chart(
    carbon: float,
    cooling: float,
    rainwater: float,
    benchmarks: Mapping[str, float] | None = None,
    label: str = "当前方案",
    height: int = 460,
) -> go.Figure:
    """三维效益得分雷达图（固碳 / 降温 / 雨水截留）。

    各维度按 :data:`RADAR_BENCHMARKS` 的满量程基准折算为 0~100 分，
    因此得分是**相对规模化方案量级的绝对刻度**，不同方案之间可直接比较；
    悬停提示同时给出原始数值与单位。

    :param carbon: 固碳量 (kg C)
    :param cooling: 降温吸热量 (MJ)
    :param rainwater: 雨水截留量 (L)
    :param benchmarks: 覆盖默认满量程基准
    :param label: 图例中显示的名称
    """
    bench = dict(RADAR_BENCHMARKS)
    if benchmarks:
        bench.update(benchmarks)

    raw = {"carbon": carbon, "cooling": cooling, "rainwater": rainwater}
    scores = [_norm_score(raw[k], bench[k]) for k, _, _ in RADAR_AXES]

    labels = [lab for _, lab, _ in RADAR_AXES]
    closed_labels = labels + [labels[0]]
    hover = [
        f"<b>{lab}</b><br>得分 {s:.1f} / 100<br>实际 {raw[k]:,.2f} {unit}"
        for (k, lab, unit), s in zip(RADAR_AXES, scores)
    ]
    hover.append(hover[0])

    fig = go.Figure()
    fig.add_trace(
        go.Scatterpolar(
            r=scores + [scores[0]],
            theta=closed_labels,
            name=label,
            fill="toself",
            fillcolor="rgba(140,154,132,0.22)",     # 鼠尾草绿，半透明
            line=dict(color=TYPE_PALETTE["乔木"], width=2.5),
            marker=dict(size=8, color=ACCENT_PALETTE["baseline"]),
            hovertemplate="%{customdata}<extra></extra>",
            customdata=hover,
        )
    )
    # 满分轮廓作参照（虚线，不进图例）
    fig.add_trace(
        go.Scatterpolar(
            r=[100] * (len(labels) + 1),
            theta=closed_labels,
            mode="lines",
            line=dict(color=ACCENT_PALETTE["neutral"], width=1.2, dash="dot"),
            hoverinfo="skip",
            showlegend=False,
        )
    )

    fig.update_layout(
        polar=dict(
            bgcolor="rgba(0,0,0,0)",          # 透明 → 透出纸纹
            radialaxis=dict(
                visible=True, range=[0, 100], angle=90,
                tickvals=[20, 40, 60, 80, 100],
                tickfont=dict(size=10, color=FONT_COLOR),
                gridcolor=GRID_COLOR, linecolor=GRID_COLOR,
            ),
            angularaxis=dict(
                tickfont=dict(size=15, color=FONT_COLOR),
                gridcolor=GRID_COLOR, linecolor=GRID_COLOR,
            ),
        ),
        showlegend=True,
    )
    # 三维雷达图无 X 轴刻度标签，底部留白可收窄
    return _base_layout(fig, "三维效益得分（固碳 / 降温 / 雨水截留）", height, bottom=60)


# ==========================================================================
# 函数 2：固碳贡献堆叠柱状图
# ==========================================================================


def make_stacked_bar(
    details_df: pd.DataFrame,
    name_col: str = "name_cn",
    type_col: str = "type",
    value_col: str = "carbon_kg",
    unit: str = "kg C",
    height: int = 460,
) -> go.Figure:
    """各植物固碳贡献堆叠柱状图，按植物类型（乔木/灌木/地被）着色。

    X 轴为植物名；按生活型分组着色——同一生活型共享一种颜色，
    每根柱子悬停显示该植物自身的数值。

    :param details_df: 逐种明细表
    :param name_col: 植物名列
    :param type_col: 生活型列
    :param value_col: 贡献值列
    :param unit: 数值单位（用于悬停提示）
    """
    if details_df.empty or value_col not in details_df.columns:
        return _empty("各植物固碳贡献")

    df = details_df[[name_col, type_col, value_col]].copy()
    df = df[df[value_col] > 0].sort_values(value_col, ascending=False)
    if df.empty:
        return _empty("各植物固碳贡献")

    x = df[name_col].tolist()
    y = df[value_col].tolist()
    types = df[type_col].tolist()

    fig = go.Figure()
    for ptype in list(TYPE_PALETTE) + ["其他"]:
        idx = [
            i for i, t in enumerate(types)
            if (t == ptype) or (ptype == "其他" and t not in TYPE_PALETTE)
        ]
        if not idx:
            continue
        color = TYPE_PALETTE.get(ptype, ACCENT_PALETTE["neutral"])
        fig.add_trace(
            go.Bar(
                x=[x[i] for i in idx],
                y=[y[i] for i in idx],
                name=ptype,
                marker=dict(color=color, line=dict(color="white", width=1.2)),
                hovertemplate=(
                    "<b>%{x}</b><br>类型: " + ptype + "<br>"
                    "固碳贡献: %{y:,.3f} " + unit + "<extra></extra>"
                ),
            )
        )

    total = sum(y)
    # X 轴不设标题：植物名本身已说明含义，省下的纵向空间留给倾斜刻度标签，
    # 避免与底部图例、轴标题三者挤在同一带里重叠
    fig.update_layout(
        barmode="stack",
        xaxis=dict(tickangle=-35),
        yaxis=dict(title=f"固碳贡献（{unit}）"),
        bargap=0.35,
    )
    fig.add_annotation(
        text=f"合计 {total:,.2f} {unit}", xref="paper", yref="paper",
        x=0.99, y=0.97, showarrow=False, xanchor="right",
        font=dict(size=13, color=ACCENT_PALETTE["text"]),
        bgcolor=ANNOT_BG, borderpad=5,
    )
    # 柱状图：底部有 -35° 倾斜的植物名，留够 150px 给标签 + 图例
    return _base_layout(fig, "各植物固碳贡献（按生活型着色）", height, bottom=150)


# ==========================================================================
# 函数 3：贡献占比环形图
# ==========================================================================


def make_pie_chart(
    details_df: pd.DataFrame,
    name_col: str = "name_cn",
    value_col: str = "carbon_kg",
    metric_label: str = "固碳量",
    unit: str = "kg C",
    height: int = 440,
) -> go.Figure:
    """各植物贡献占比环形图。

    :param details_df: 逐种明细表
    :param value_col: 用于计算占比的指标列
    :param metric_label: 指标显示名（标题与悬停用）
    :param unit: 数值单位
    """
    if details_df.empty or value_col not in details_df.columns:
        return _empty(f"{metric_label}贡献占比")

    df = details_df[[name_col, value_col]].copy()
    df = df[df[value_col] > 0].sort_values(value_col, ascending=False)
    if df.empty:
        return _empty(f"{metric_label}贡献占比")

    total = float(df[value_col].sum())
    colors = [COLORWAY[i % len(COLORWAY)] for i in range(len(df))]

    fig = go.Figure(
        go.Pie(
            labels=df[name_col],
            values=df[value_col],
            hole=0.52,
            sort=False,
            direction="clockwise",
            textinfo="label+percent",
            textposition="auto",
            insidetextorientation="radial",
            textfont=dict(size=12, color="white"),
            marker=dict(colors=colors, line=dict(color=BG_COLOR, width=2.5)),
            hovertemplate=(
                "<b>%{label}</b><br>" + metric_label + ": %{value:,.3f} "
                + unit + "<br>占比 %{percent}<extra></extra>"
            ),
        )
    )
    fig.add_annotation(
        text=f"<span style='font-size:12px'>{metric_label}合计</span><br>"
             f"<b style='font-size:19px'>{total:,.2f}</b><br>"
             f"<span style='font-size:11px'>{unit}</span>",
        x=0.5, y=0.5, showarrow=False, font=dict(color=FONT_COLOR),
    )
    # 环形图无坐标轴与图例，底部留白可收到最小
    return _base_layout(fig, f"各植物{metric_label}贡献占比", height, bottom=40)


# ==========================================================================
# 函数 4：当前方案 vs 纯草坪基线
# ==========================================================================


def make_baseline_values(
    details_df: pd.DataFrame,
    species: str = BASELINE_SPECIES,
    name_col: str = "name_cn",
    quantity_col: str = "quantity",
    days: int | None = None,
    interception_rate: float | None = None,
) -> dict[str, float]:
    """把当前方案的全部种植面积换算成**纯草坪**，得到基线指标值。

    基线的物理含义：**同样面积如果全部种草坪**，各项效益会是多少。
    用植物库中 :data:`BASELINE_SPECIES`（白三叶）的单株参数逐株推算：

    * 总冠层投影面积 = Σ(株数 × 单株冠层面积)
    * 基线株数       = 总冠层投影面积 ÷ 草种单株冠层面积
    * 基线指标       = 草种单株指标 × 基线株数

    :param details_df: 当前方案逐种明细表（需含株数列）
    :param species: 作为基线的草种名
    :param days: 核算天数；None 时优先取明细表的 days 列，再退回 365
    :param interception_rate: 截留率；None 时沿用明细表首行的截留率
    :return: ``{"carbon"/"cooling"/"rainwater": float}``，cooling 单位为 MJ
    """
    from modules import calculator as calc

    plant = calc.get_plant(species)
    grass_area = float(plant["canopy_area"])
    if grass_area <= 0:
        return {"carbon": 0.0, "cooling": 0.0, "rainwater": 0.0}

    total_area = 0.0
    for _, row in details_df.iterrows():
        p = calc.get_plant(row[name_col])
        total_area += float(p["canopy_area"]) * float(row[quantity_col])
    if total_area <= 0:
        return {"carbon": 0.0, "cooling": 0.0, "rainwater": 0.0}

    if days is None:
        days = int(details_df["days"].iloc[0]) if "days" in details_df.columns else 365

    rate = interception_rate
    if rate is None and "interception_rate" in details_df.columns:
        rate = float(details_df["interception_rate"].iloc[0])

    grass_qty = total_area / grass_area
    # 时间口径必须与当前方案一致：
    # 固碳/吸热按 days 累计，雨水截留为单次降雨量。
    # 早期版本此处把吸热写成固定 1 天，导致"一年 vs 一天"相比、
    # 改善指数虚高到 10 万倍，已修正。
    carbon = calc.calc_carbon_and_oxygen(plant, 1, days)["carbon_kg"] * grass_qty
    heat_kj = (
        calc.calc_transpiration_cooling(plant, 1, days)["heat_absorbed_kj"] * grass_qty
    )
    water = calc.calc_rainwater(
        plant, 1, interception_rate=rate
    )["intercepted_water_kg"] * grass_qty

    return {
        "carbon": carbon,
        "cooling": heat_kj / 1000.0,   # kJ → MJ
        "rainwater": water,
    }


def make_comparison_bar(
    current_values: Mapping[str, float],
    baseline_values: Mapping[str, float],
    metrics: Sequence[tuple[str, str, str, float]] | None = None,
    current_label: str = "当前方案",
    baseline_label: str = "纯草坪基线",
    height: int = 470,
) -> go.Figure:
    """当前方案 vs 纯草坪基线对比柱状图（三个指标分组显示）。

    **量纲提示**：固碳(kg C)、降温(MJ)、截留(L)三者量级可差 4 个数量级，
    放在同一线性坐标里小指标会被压成一条线。故采用双面板：

    * 左面板：**相对改善指数**（基线 = 100），三指标可比，一眼看出倍数
    * 右面板：**绝对值**，Y 轴对数刻度，保留真实量级

    :param current_values: ``{"carbon"/"cooling"/"rainwater": float}``
    :param baseline_values: 同结构
    :param metrics: 覆盖默认指标定义 ``[(键, 显示名, 单位, 换算系数), ...]``
    """
    mets = list(metrics or COMPARISON_METRICS)
    if not current_values or not baseline_values:
        return _empty("当前方案 vs 纯草坪基线")

    labels, cur_vals, base_vals, ratios = [], [], [], []
    for key, label, unit, scale in mets:
        c = float(current_values.get(key, 0.0)) * scale
        b = float(baseline_values.get(key, 0.0)) * scale
        labels.append(f"{label}<br><span style='font-size:11px'>({unit})</span>")
        cur_vals.append(c)
        base_vals.append(b)
        ratios.append(100.0 if b <= 0 else c / b * 100.0)

    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=("相对改善指数（基线 = 100）", "绝对量（对数刻度）"),
        horizontal_spacing=0.14,
    )

    # ---- 左：改善指数 ----
    fig.add_trace(
        go.Bar(
            x=labels, y=ratios, name=current_label,
            marker=dict(color=TYPE_PALETTE["乔木"], line=dict(color="white", width=1.2)),
            text=[f"{r:,.0f}" for r in ratios], textposition="outside",
            hovertemplate="<b>%{x}</b><br>" + current_label
            + "指数: %{y:,.1f}<extra></extra>",
            legendgroup="current",
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Bar(
            x=labels, y=[100.0] * len(labels), name=baseline_label,
            marker=dict(color=ACCENT_PALETTE["baseline"],
                        line=dict(color="white", width=1.2)),
            hovertemplate="<b>%{x}</b><br>" + baseline_label
            + "指数: 100（基准）<extra></extra>",
            legendgroup="baseline",
        ),
        row=1, col=1,
    )

    # ---- 右：绝对值（对数轴，避免被压平）----
    fig.add_trace(
        go.Bar(
            x=labels, y=[max(v, 1e-6) for v in cur_vals], name=current_label,
            marker=dict(color=TYPE_PALETTE["乔木"], line=dict(color="white", width=1.2)),
            hovertemplate="<b>%{x}</b><br>" + current_label
            + ": %{y:,.3g}<extra></extra>",
            legendgroup="current", showlegend=False,
        ),
        row=1, col=2,
    )
    fig.add_trace(
        go.Bar(
            x=labels, y=[max(v, 1e-6) for v in base_vals], name=baseline_label,
            marker=dict(color=ACCENT_PALETTE["baseline"],
                        line=dict(color="white", width=1.2)),
            hovertemplate="<b>%{x}</b><br>" + baseline_label
            + ": %{y:,.3g}<extra></extra>",
            legendgroup="baseline", showlegend=False,
        ),
        row=1, col=2,
    )

    fig.update_yaxes(title="改善指数", row=1, col=1)
    fig.update_yaxes(title="绝对量", type="log", row=1, col=2)
    fig.update_xaxes(tickangle=-15, row=1, col=1)
    fig.update_xaxes(tickangle=-15, row=1, col=2)
    for ann in fig.layout.annotations:      # 子图标题样式
        ann.font.size = 13
        ann.font.color = FONT_COLOR

    # 对比图：X 轴是两行式指标标签（名称 + 单位），需较大底部留白
    return _base_layout(fig, f"{current_label} vs {baseline_label}", height, bottom=140)
