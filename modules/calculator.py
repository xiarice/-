"""植物生态效益计算引擎（广东地区，华南亚热带-热带气候）。

统一口径
--------
所有"每株"指标均为**单株日总量**，输入直接取自 ``data/plants.csv``：

============================  ==================  ==========================
CSV 字段                       单位                含义
============================  ==================  ==========================
``carbon_per_plant``          kg C/(株·d)         单株日固碳量
``transpiration_per_plant``   kg/(株·d)           单株日蒸腾量
``water_storage_per_plant``   kg/株               冠层最大截留贮水量
``dust_retention_daily``      kg/(株·d)           单株日滞尘量
``LAI``                       m²/m²               叶面积指数
``canopy_area``               m²/株               单株冠层投影面积
``height``                    m                   株高
============================  ==================  ==========================

本模块不重新推导这些量，只在"单株 → 群落"和"物理量 → 等效环境效益"两个方向
上做换算，避免与 CSV 的口径重复相乘。

换算公式一览
------------
::

    叶面积(m²)       = LAI × canopy_area
    释氧量(kg)       = 固碳量(kg C) × 2.67
    CO2 当量(kg)     = 固碳量(kg C) × 44/12
    吸热量(kJ)       = 蒸腾量(kg) × 2430            # 30℃ 汽化潜热 2.43 MJ/kg
    降温空气量(m³)   = 日吸热量 / (1.165 × 1.005)    # 空气密度 × 定压比热
    降温能力指数     = 单位面积潜热通量在库内的分位排名（0~100）
    截留水量(kg)     = 冠层截留贮水量 × 株数 × 截留率
    滞尘量(kg)       = 单株日滞尘量 × 株数 × 天数
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Iterable, Mapping

import pandas as pd

__all__ = [
    # 常量
    "LATENT_HEAT_KJ_PER_KG",
    "LATENT_HEAT_GENERIC_KJ_PER_KG",
    "AIR_DENSITY_KG_PER_M3",
    "AIR_SPECIFIC_HEAT_KJ_PER_KG_C",
    "AIR_HEAT_CAPACITY_KJ_PER_M3_C",
    "OXYGEN_RATIO",
    "CO2_TO_C_RATIO",
    "RAIN_INTENSITIES",
    "DEFAULT_RAIN_INTENSITY",
    "L06_INTERCEPTION_MODEL",
    "L06_RATE_BOUNDS",
    "REGION_FACTOR_DEFAULT",
    "NUMERIC_COLUMNS",
    "TOTAL_KEYS",
    "RainIntensity",
    # 地区修正
    "set_region_factor",
    "get_region_factor",
    # 数据读取
    "load_plants",
    "get_plant",
    "leaf_area",
    # 逐项计算
    "calc_oxygen",
    "calc_co2_equivalent",
    "calc_carbon_and_oxygen",
    "calc_transpiration_cooling",
    "calc_rainwater",
    "calc_dust",
    "resolve_interception_rate",
    "l06_interception_rate",
    # 组合与汇总
    "calculate_plant",
    "summarize_community",
    "community_totals",
]

# --------------------------------------------------------------------------
# 物理常数
# --------------------------------------------------------------------------

#: 水的汽化潜热，kJ/kg。
#:
#: 取值依据 **黎国健等(2008)《华南12种垂直绿化植物的生态效应》表3**：
#: 该文在华南夏季条件下实测的「蒸腾潜热 L」为 2401.84 ~ 2414.23 J/g，
#: 均值 2404 J/g，即 **2.404 MJ/kg**。
#: 比通用值 2450（约 20℃）低约 2%，更贴合华南夏季实际。
LATENT_HEAT_KJ_PER_KG = 2404.0

#: 通用汽化潜热参考值 kJ/kg（约 20℃），仅作对比，不参与计算
LATENT_HEAT_GENERIC_KJ_PER_KG = 2450.0

#: 空气密度 kg/m³（30℃、标准大气压）
AIR_DENSITY_KG_PER_M3 = 1.165

#: 空气定压比热 kJ/(kg·℃)
AIR_SPECIFIC_HEAT_KJ_PER_KG_C = 1.005

#: 空气体积热容 kJ/(m³·℃)
AIR_HEAT_CAPACITY_KJ_PER_M3_C = (
    AIR_DENSITY_KG_PER_M3 * AIR_SPECIFIC_HEAT_KJ_PER_KG_C
)

#: 光合作用释氧系数：每固定 1 kg 碳释放 2.67 kg 氧
OXYGEN_RATIO = 2.67

#: CO₂/C 分子量比
CO2_TO_C_RATIO = 44.0 / 12.0

# --------------------------------------------------------------------------
# 地区修正系数
# --------------------------------------------------------------------------

#: 地区修正系数默认值。1.0 即本模型的标定基准 —— **广东地区
#: （华南亚热带-热带气候）**。CSV 的 `region_factor` 列同步为 1.0。
REGION_FACTOR_DEFAULT = 1.0
_region_factor = REGION_FACTOR_DEFAULT


def set_region_factor(factor: float) -> float:
    """设置地区修正系数，返回设置后的值。

    本模型的全部参数（汽化潜热、截留率、实测通量）都以**广东地区**为基准
    标定。若要用于其他地区，用本函数整体缩放：

    * ``> 1.0``：更湿热、蒸腾更旺盛、降雨更频繁的地区
    * ``< 1.0``：更干冷、蒸腾较弱、降雨较少的地区
    * ``= 1.0``：广东地区（默认，无需调整）

    影响范围 —— 只作用于**受当地气候直接驱动的通量**：

    * 受影响：蒸腾降温、雨水截留
    * **不受影响**：固碳、释氧、滞尘（由植物生理与叶面特性决定，
      已由文献实测值直接标定，不随地区缩放）

    :param factor: 修正系数，须为正数
    :raises ValueError: 系数非正
    """
    global _region_factor
    f = float(factor)
    if f <= 0:
        raise ValueError(f"region_factor 必须为正数，收到 {f}")
    _region_factor = f
    return _region_factor


def get_region_factor() -> float:
    """读取当前地区修正系数（默认 1.0 = 广东地区）。"""
    return _region_factor


# --------------------------------------------------------------------------
# 广东地区降雨强度参数
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RainIntensity:
    """单次降雨的雨强档位与截留率。

    截留率区间取自华南地区实测结果：小雨(0~10mm) 约 50%~80%，
    中雨(10~25mm) 约 30%~50%，大雨(>25mm) 约 15%~25%。
    广东年均降雨量约 1600~2000mm 且降雨频繁，冠层截留以中雨为主，
    故默认档位为中雨、截留率 0.40。
    """

    key: str
    label: str
    depth_min_mm: float
    depth_max_mm: float | None
    rate_min: float
    rate_max: float
    rate_default: float


#: 雨强档位表。界面可用它生成下拉框，并把 rate_min/rate_max 作为滑块范围、
#: rate_default 作为初值。
#:
#: 截留率已用 **赵峰等《华南地区不同绿地植物配置方式对其冠层雨水截流能力的影响》**
#: 实测数据校准。该文在深圳光明新区新城公园实测 8 种配置，给出对数回归模型
#: ``I(%) = a·ln(P) + b``（P 为降水量 mm，R² 均 > 0.84），整体截留率区间
#: **25.88% ~ 80.05%**。各档上界取自该模型在档位代表雨量下的计算值，
#: 下界参考表现最弱的处理7（小叶榕）。
RAIN_INTENSITIES: dict[str, RainIntensity] = {
    "light": RainIntensity(
        key="light", label="小雨（0~10 mm）",
        depth_min_mm=0.0, depth_max_mm=10.0,
        rate_min=0.45, rate_max=0.80, rate_default=0.65,
    ),
    "moderate": RainIntensity(
        key="moderate", label="中雨（10~25 mm）",
        depth_min_mm=10.0, depth_max_mm=25.0,
        rate_min=0.30, rate_max=0.50, rate_default=0.40,
    ),
    "heavy": RainIntensity(
        key="heavy", label="大雨（>25 mm）",
        depth_min_mm=25.0, depth_max_mm=None,
        rate_min=0.20, rate_max=0.35, rate_default=0.30,
    ),
}

#: 默认雨强档位
DEFAULT_RAIN_INTENSITY = "moderate"

#: L06 实测对数模型系数 ``(a, b, 配置说明)``：``I(%) = a·ln(P) + b``。
#: 取表现最强与最弱的两种配置作为包络。
L06_INTERCEPTION_MODEL: dict[str, tuple[float, float, str]] = {
    "best": (-5.378, 87.474, "凤凰木+肾蕨（乔灌草复层）"),
    "worst": (-5.378, 48.458, "小叶榕（单层乔木）"),
}

#: L06 实测的整体截留率区间，作为模型输出的夹逼边界
L06_RATE_BOUNDS = (0.2588, 0.8005)


def l06_interception_rate(precipitation_mm: float, envelope: str = "best") -> float:
    """用 L06 实测对数模型计算给定降水量的冠层截留率。

    ``I(%) = a·ln(P) + b``，结果夹逼到文献实测区间 25.88% ~ 80.05%。

    该模型基于深圳 8 种植物配置的降水收集法实测，R² 均高于 0.84，
    比固定档位更精细 —— 若已知实际降水量，应优先用本函数。

    :param precipitation_mm: 单次降水量 (mm)，须为正数
    :param envelope: ``"best"``（乔灌草复层）或 ``"worst"``（单层乔木）
    :raises KeyError: envelope 取值非法
    :raises ValueError: 降水量非正
    """
    if envelope not in L06_INTERCEPTION_MODEL:
        raise KeyError(
            f"envelope 需为 {list(L06_INTERCEPTION_MODEL)} 之一，收到 {envelope!r}"
        )
    p = float(precipitation_mm)
    if p <= 0:
        raise ValueError(f"降水量必须为正数，收到 {p}")

    a, b, _ = L06_INTERCEPTION_MODEL[envelope]
    rate_pct = a * math.log(p) + b
    lo, hi = L06_RATE_BOUNDS
    return min(max(rate_pct / 100.0, lo), hi)


def resolve_interception_rate(
    intensity: str = DEFAULT_RAIN_INTENSITY,
    override: float | None = None,
    precipitation_mm: float | None = None,
    envelope: str = "best",
) -> float:
    """给出截留率。优先顺序：手动覆盖 > 实测降水模型 > 雨强档位默认值。

    :param intensity: ``RAIN_INTENSITIES`` 的键（light / moderate / heavy）
    :param override: 界面手动调整的截留率；None 表示继续向下取
    :param precipitation_mm: 已知单次降水量(mm)时用 L06 实测对数模型计算，
        比档位默认值更精确
    :param envelope: L06 模型包络，``best``（复层）或 ``worst``（单层）
    :raises KeyError: 雨强档位或 envelope 非法
    :raises ValueError: 截留率不在 [0, 1] 或降水量非正
    """
    if override is not None:
        rate = float(override)
        if not 0.0 <= rate <= 1.0:
            raise ValueError(f"截留率必须在 [0, 1] 之间，收到 {rate}")
        return rate

    if precipitation_mm is not None:
        return l06_interception_rate(precipitation_mm, envelope)

    if intensity not in RAIN_INTENSITIES:
        raise KeyError(
            f"未知雨强档位 {intensity!r}，可选：{list(RAIN_INTENSITIES)}"
        )
    return RAIN_INTENSITIES[intensity].rate_default


# --------------------------------------------------------------------------
# 数据读取
# --------------------------------------------------------------------------

#: CSV 中的数值型字段
NUMERIC_COLUMNS = [
    "carbon_per_plant",
    "transpiration_per_plant",
    "water_storage_per_plant",
    "LAI",
    "canopy_area",
    "height",
    "dust_per_leaf_area",
    "dust_retention_daily",
]

DEFAULT_CSV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "plants.csv",
)


def load_plants(csv_path: str | None = None) -> pd.DataFrame:
    """读取植物参数表，数值列统一转为 float。"""
    path = csv_path or DEFAULT_CSV
    df = pd.read_csv(path, encoding="utf-8-sig")
    for col in NUMERIC_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def get_plant(name_cn: str, csv_path: str | None = None) -> pd.Series:
    """按中文名取单株参数。

    :raises KeyError: 植物库中没有该名称
    """
    df = load_plants(csv_path)
    hit = df[df["name_cn"] == name_cn]
    if hit.empty:
        raise KeyError(f"植物库中没有 {name_cn!r}")
    return hit.iloc[0]


def leaf_area(plant: Mapping) -> float:
    """叶面积(m²) = LAI × 冠层投影面积。"""
    return float(plant["LAI"]) * float(plant["canopy_area"])


# --------------------------------------------------------------------------
# 逐项计算
# --------------------------------------------------------------------------


def calc_oxygen(carbon_kg: float) -> float:
    """释氧量(kg) = 固碳量(kg C) × 2.67。"""
    return carbon_kg * OXYGEN_RATIO


def calc_co2_equivalent(carbon_kg: float) -> float:
    """CO₂ 当量(kg) = 固碳量(kg C) × 44/12。"""
    return carbon_kg * CO2_TO_C_RATIO


def calc_carbon_and_oxygen(
    plant: Mapping, quantity: int = 1, days: int = 1
) -> dict[str, float]:
    """固碳量与释氧量。

    固碳量(kg C) = ``carbon_per_plant`` × 株数 × 天数
    释氧量(kg)   = 固碳量 × 2.67

    :param quantity: 株数
    :param days: 天数
    """
    if quantity < 0 or days < 0:
        raise ValueError("株数与天数不能为负")
    carbon = float(plant["carbon_per_plant"]) * quantity * days
    return {
        "carbon_kg": carbon,
        "oxygen_kg": calc_oxygen(carbon),
        "co2_equivalent_kg": calc_co2_equivalent(carbon),
    }


def calc_transpiration_cooling(
    plant: Mapping, quantity: int = 1, days: int = 1
) -> dict[str, float]:
    """蒸腾降温。

    蒸腾消耗的潜热：``吸热量(kJ) = 蒸腾量(kg) × LATENT_HEAT_KJ_PER_KG``。

    汽化潜热取 **2404 kJ/kg（2.404 MJ/kg）**，依据黎国健等(2008)在华南夏季
    实测的蒸腾潜热 2401.84~2414.23 J/g 均值，而非通用的 2450。

    结果会乘以 **地区修正系数** ``region_factor``（默认 1.0 = 广东地区），
    见 :func:`set_region_factor`。

    **口径警告**：瞬时的"等效降温幅度"依赖空气交换率，本模块不假设通风条件，
    因此不输出 ℃/d 这类绝对值——那会得到数十摄氏度的荒谬结果。改为输出两个
    可直接比较的量：

    * ``cooled_air_volume_m3``：日吸热量能把多少 m³ 空气降温 1℃，体现绝对规模
    * ``cooling_index``：冠层单位面积日潜热通量在植物库内的分位排名(0~100)，
      消除体量差异，适合不同树种横向比较

    ``heat_absorbed_kj`` 与 ``transpiration_kg`` 仍按 ``days`` 累计。
    """
    if quantity < 0 or days < 0:
        raise ValueError("株数与天数不能为负")

    rf = get_region_factor()
    plants_transpiration = float(plant["transpiration_per_plant"]) * quantity * rf
    transpiration = plants_transpiration * days
    heat_kj = transpiration * LATENT_HEAT_KJ_PER_KG

    # 日尺度潜热通量，避免把多日累计热量除以单日热容
    daily_heat_kj = plants_transpiration * LATENT_HEAT_KJ_PER_KG
    cooled_air = daily_heat_kj / AIR_HEAT_CAPACITY_KJ_PER_M3_C

    ground_area = float(plant["canopy_area"]) * quantity
    latent_flux = daily_heat_kj / ground_area if ground_area > 0 else math.nan

    return {
        "transpiration_kg": transpiration,
        "heat_absorbed_kj": heat_kj,
        "cooled_air_volume_m3": cooled_air,
        "latent_heat_flux_kj_per_m2_d": latent_flux,
        "cooling_index": _cooling_index(latent_flux),
        "region_factor": rf,
    }


#: 降温能力指数用的通量分布缓存，惰性计算
_FLUX_CACHE: list[float] | None = None


def _flux_distribution(csv_path: str | None = None) -> list[float]:
    """库内全部植物单位面积日潜热通量的分布（升序）。"""
    global _FLUX_CACHE
    if _FLUX_CACHE is None:
        df = load_plants(csv_path)
        fluxes = []
        for _, row in df.iterrows():
            qty = float(row["transpiration_per_plant"])
            area = float(row["canopy_area"])
            if area > 0:
                fluxes.append(qty * LATENT_HEAT_KJ_PER_KG / area)
        _FLUX_CACHE = sorted(fluxes)
    return _FLUX_CACHE


def _cooling_index(latent_flux: float) -> float:
    """把冠层单位面积日潜热通量映射为 0~100 的降温能力指数。

    采用**库内分位排名**，而不是除以某个手设基准通量——这样指数天然落在
    0~100，且以后往植物库里加数据会自动重新标定，不必维护基准常数。
    指数 100 表示当前库内最高，0 表示最低。
    """
    if latent_flux is None or (
        isinstance(latent_flux, float) and math.isnan(latent_flux)
    ):
        return math.nan
    dist = _flux_distribution()
    if not dist:
        return math.nan
    if latent_flux <= dist[0]:
        return 0.0
    if latent_flux >= dist[-1]:
        return 100.0
    below = sum(1 for v in dist if v < latent_flux)
    equal = sum(1 for v in dist if v == latent_flux)
    # 中位排名法：同值取平均位次，保证结果在 (0, 100) 内
    return 100.0 * (below + equal / 2.0) / len(dist)



def calc_rainwater(
    plant: Mapping,
    quantity: int = 1,
    intensity: str = DEFAULT_RAIN_INTENSITY,
    interception_rate: float | None = None,
    precipitation_mm: float | None = None,
    envelope: str = "best",
) -> dict[str, float]:
    """冠层雨水截留量。

    广东年均降雨量 1600~2000mm 且降雨频繁，单次截留率随雨强显著变化。
    截留率有三种取法，优先级从高到低：

    1. ``interception_rate`` —— 界面手动指定（覆盖一切）
    2. ``precipitation_mm`` —— 用 L06（赵峰等）实测对数模型
       ``I(%) = a·ln(P) + b`` 计算，最精确
    3. ``intensity`` —— 雨强档位默认值（见 ``RAIN_INTENSITIES``，已按 L06 校准）

    截留水量(kg) = ``water_storage_per_plant`` × 株数 × 截留率 × ``region_factor``

    说明：广东雨强以中雨为主，用小雨档会高估、大雨档会低估全年截留量；
    若需全年累计，建议按各雨强的降雨场次占比加权后求和。

    :param intensity: 雨强档位键（light / moderate / heavy）
    :param interception_rate: 手动覆盖截留率；None 表示按下面两种方式取
    :param precipitation_mm: 已知单次降水量(mm)，启用 L06 实测模型
    :param envelope: L06 模型包络，``best``（乔灌草复层）或 ``worst``（单层乔木）
    """
    if quantity < 0:
        raise ValueError("株数不能为负")

    rate = resolve_interception_rate(
        intensity, interception_rate, precipitation_mm, envelope
    )
    rf = get_region_factor()
    capacity = float(plant["water_storage_per_plant"]) * quantity
    return {
        "rain_intensity": intensity,
        "interception_rate": rate,
        "intercepted_water_kg": capacity * rate * rf,
        "canopy_capacity_kg": capacity,
        "region_factor": rf,
    }


def calc_dust(plant: Mapping, quantity: int = 1, days: int = 1) -> dict[str, float]:
    """滞尘量。

    滞尘量(kg) = ``dust_retention_daily`` × 株数 × 天数

    若该列缺失，回退为 ``dust_per_leaf_area × 叶面积 / 1000``。
    注意：CSV 中的滞尘参数为文献量级估算，非本种实测值。
    """
    if quantity < 0 or days < 0:
        raise ValueError("株数与天数不能为负")

    per_plant = plant.get("dust_retention_daily")
    if per_plant is None or pd.isna(per_plant):
        rate = float(plant["dust_per_leaf_area"])
        per_plant = rate * leaf_area(plant) / 1000.0

    return {"dust_kg": float(per_plant) * quantity * days}


# --------------------------------------------------------------------------
# 组合与群落汇总
# --------------------------------------------------------------------------


def calculate_plant(
    plant: Mapping,
    quantity: int = 1,
    days: int = 1,
    intensity: str = DEFAULT_RAIN_INTENSITY,
    interception_rate: float | None = None,
    precipitation_mm: float | None = None,
    envelope: str = "best",
) -> dict[str, object]:
    """单种植物的一次性完整计算，返回全部指标。

    固碳释氧与滞尘**不随** ``region_factor`` 变化；蒸腾降温与雨水截留会。
    """
    result: dict[str, object] = {
        "name_cn": plant.get("name_cn", ""),
        "type": plant.get("type", ""),
        "quantity": quantity,
        "days": days,
    }
    result.update(calc_carbon_and_oxygen(plant, quantity, days))
    result.update(calc_transpiration_cooling(plant, quantity, days))
    result.update(
        calc_rainwater(
            plant, quantity, intensity, interception_rate, precipitation_mm, envelope
        )
    )
    result.update(calc_dust(plant, quantity, days))
    return result


def summarize_community(
    selected: Iterable[tuple[Mapping, int]],
    days: int = 1,
    intensity: str = DEFAULT_RAIN_INTENSITY,
    interception_rate: float | None = None,
    precipitation_mm: float | None = None,
    envelope: str = "best",
) -> pd.DataFrame:
    """多种植物组成的绿地群落汇总，逐种一行。

    :param selected: ``[(植物行, 株数), ...]``
    """
    rows = [
        calculate_plant(
            p, qty, days, intensity, interception_rate, precipitation_mm, envelope
        )
        for p, qty in selected
    ]
    return pd.DataFrame(rows)


#: 汇总求和的核心指标
TOTAL_KEYS = [
    "carbon_kg",
    "oxygen_kg",
    "co2_equivalent_kg",
    "transpiration_kg",
    "heat_absorbed_kj",
    "cooled_air_volume_m3",
    "intercepted_water_kg",
    "dust_kg",
]


def community_totals(df: pd.DataFrame) -> dict[str, float]:
    """把 ``summarize_community`` 的结果按列求和，供页面指标卡使用。"""
    missing = [k for k in TOTAL_KEYS if k not in df.columns]
    if missing:
        raise KeyError(f"汇总表缺少字段：{missing}")
    return {k: float(df[k].sum()) for k in TOTAL_KEYS}
