"""AI 分析报告生成（DeepSeek API + 规则降级）。

对外主入口 :func:`generate_report`：按传入的三项计算结果与植物清单，
生成 200~300 字中文分析。优先调用 DeepSeek；任何一步失败都自动降级到
**基于规则的分析文本**（体现广东地区特点），保证界面不因网络问题崩溃。

设计要点
--------
* API Key 只从环境变量 ``DEEPSEEK_API_KEY`` 读取，不落盘、不进代码
* 结构化文本由 :func:`build_calculation_text` 生成，格式固定便于模型理解
* 降级文本由 :func:`build_fallback_report` 生成，含华南植物特性判断
* 所有异常收敛为"降级 + 说明"，绝不向上抛

环境变量配置（PowerShell）::

    $env:DEEPSEEK_API_KEY = "sk-xxxx"          # 仅当前窗口有效
    [Environment]::SetEnvironmentVariable(     # 永久（需重开终端）
        "DEEPSEEK_API_KEY", "sk-xxxx", "User")

环境变量配置（CMD）::

    set DEEPSEEK_API_KEY=sk-xxxx

环境变量配置（macOS / Linux）::

    export DEEPSEEK_API_KEY=sk-xxxx
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping, Sequence

# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

#: API Key 所在的环境变量名
API_KEY_ENV = "DEEPSEEK_API_KEY"

#: DeepSeek 对话补全接口
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

#: 模型名
DEEPSEEK_MODEL = "deepseek-chat"

#: 请求超时（秒）。超时即降级，不让界面长时间卡住。
REQUEST_TIMEOUT = 30

#: 生成参数
TEMPERATURE = 0.7
MAX_TOKENS = 700

# --------------------------------------------------------------------------
# 换算参照常数（把结果转成通俗类比）
# --------------------------------------------------------------------------

#: 一辆普通家用小汽车的年碳排放量，kg CO₂/年（参照美国 EPA 口径约 4.6 t/年）
CAR_CO2_KG_PER_YEAR = 4600.0

#: 一个成年人日均呼吸耗氧量，kg O₂/(人·d)
ADULT_OXYGEN_KG_PER_DAY = 0.75

#: 常见矿泉水瓶容积，L
BOTTLE_LITERS = 0.55

#: CO₂ 与碳的分子量比
CO2_TO_C_RATIO = 44.0 / 12.0

# --------------------------------------------------------------------------
# 华南植物特性参照表（用于降级文本的判断与举例）
# --------------------------------------------------------------------------

#: 广东常见**乡土树种**（本地原生或长期归化，生态适应性强）
NATIVE_SPECIES: frozenset[str] = frozenset({
    "榕树", "细叶榕", "香樟", "阴香", "木荷", "秋枫", "海南红豆",
    "麻楝", "扁桃", "黄槿", "白兰", "龙眼", "鸭脚木", "福建茶",
    "龙船花", "沿阶草", "海芋",
})

#: 常绿树种（华南亚热带-热带的建群主体）
EVERGREEN_SPECIES: frozenset[str] = frozenset({
    "榕树", "细叶榕", "香樟", "阴香", "木荷", "秋枫", "桃花心木",
    "海南红豆", "麻楝", "黄槿", "白兰", "芒果", "龙眼", "大红花",
    "假连翘", "黄叶榕", "红桑", "灰莉", "黄金榕", "福建茶",
    "龙船花", "鸭脚木", "麦冬", "沿阶草", "海芋", "春羽",
})

#: 落叶树种
DECIDUOUS_SPECIES: frozenset[str] = frozenset({
    "红花羊蹄甲", "扁桃",
})

#: 大叶、冠层浓密的树种（华南遮荫与截留的主力）
BROAD_CANOPY_SPECIES: frozenset[str] = frozenset({
    "榕树", "细叶榕", "木荷", "秋枫", "黄叶榕", "黄金榕", "芒果", "海芋",
})

#: 常见灌木（用于判断是否有灌木层）
SHRUB_SPECIES: frozenset[str] = frozenset({
    "大红花", "假连翘", "马缨丹", "黄叶榕", "红桑", "灰莉",
    "黄金榕", "福建茶", "龙船花", "鸭脚木",
})


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------


def _fmt(value: Any, default: float = 0.0) -> float:
    """安全取数值。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def plant_names(plant_list: Sequence[Any]) -> list[str]:
    """从植物清单提取中文名并去重（保序）。

    兼容：字符串、``{"name_cn": ...}`` 字典、``{"name": ...}`` 字典、
    pandas Series、``(植物, 株数)`` 元组。
    """
    names: list[str] = []

    def _from_any(obj: Any) -> str | None:
        if isinstance(obj, str):
            return obj
        if isinstance(obj, Mapping):
            nm = obj.get("name_cn") or obj.get("name")
            return str(nm) if nm else None
        getter = getattr(obj, "get", None)
        if callable(getter):
            nm = getter("name_cn") or getter("name")
            return str(nm) if nm else None
        return None

    for item in plant_list or []:
        if isinstance(item, (tuple, list)) and item:
            nm = _from_any(item[0])
        else:
            nm = _from_any(item)
        if nm:
            names.append(nm)

    seen: set[str] = set()
    uniq: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


# --------------------------------------------------------------------------
# 结构化文本
# --------------------------------------------------------------------------


def build_calculation_text(
    carbon_result: Mapping[str, Any],
    cooling_result: Mapping[str, Any],
    rainwater_result: Mapping[str, Any],
    plant_list: Sequence[Any],
) -> str:
    """把计算结果整理成结构化文本，供提示词与降级分析共用。"""
    c = _fmt(carbon_result.get("carbon_kg"))
    o2 = _fmt(carbon_result.get("oxygen_kg"))
    co2 = _fmt(carbon_result.get("co2_equivalent_kg"))
    transp = _fmt(cooling_result.get("transpiration_kg"))
    heat_kj = _fmt(cooling_result.get("heat_absorbed_kj"))
    air = _fmt(cooling_result.get("cooled_air_volume_m3"))
    water = _fmt(rainwater_result.get("intercepted_water_kg"))
    rate = _fmt(rainwater_result.get("interception_rate"))
    names = plant_names(plant_list)

    lines = ["【种植方案】"]
    lines.append(
        f"- 植物种类：{len(names)} 种（{'、'.join(names)}）"
        if names else "- 植物种类：未提供"
    )
    lines += [
        "",
        "【碳固定与释氧】",
        f"- 固碳量：{c:,.2f} kg C",
        f"- 释氧量：{o2:,.2f} kg",
        f"- CO2 当量：{co2:,.2f} kg",
        "",
        "【蒸腾降温】",
        f"- 蒸腾量：{transp:,.2f} kg",
        f"- 吸热量：{heat_kj:,.0f} kJ（约 {heat_kj / 1000:,.1f} MJ）",
        f"- 可降温空气量：{air:,.0f} m³",
        "",
        "【雨水截留】",
        f"- 单次降雨截留量：{water:,.2f} kg（约 {water:,.1f} L）",
    ]
    lines.append(
        f"- 采用的截留率：{rate * 100:.0f}%" if rate else "- 截留率：未提供"
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 提示词
# --------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "你是一个华南地区风景园林生态分析助手，熟悉广东常见园林植物的生态特性。\n"
    "根据以下种植方案的生态效益计算结果，生成一段简短的分析报告，包含：\n"
    "1. 方案整体表现评价\n"
    "2. 主要贡献植物识别（需结合华南植物特性，如榕树、大红花等）\n"
    "3. 结构优化建议（如常绿/落叶比例、乔灌草搭配、雨热同季特点的应对）\n"
    "4. 一句话总结\n"
    "不要罗列数据，要给出设计层面的解读。"
)

#: 附加写作约束，提高输出可直接使用的概率
STYLE_CONSTRAINT = (
    "要求：全文 200~300 字，分 3~4 段，纯中文，不使用 Markdown 标题符号，"
    "不使用项目符号列表，直接给成段的分析文字。"
)


def build_prompt(
    carbon_result: Mapping[str, Any],
    cooling_result: Mapping[str, Any],
    rainwater_result: Mapping[str, Any],
    plant_list: Sequence[Any],
) -> str:
    """拼接完整的用户提示词。"""
    calc_text = build_calculation_text(
        carbon_result, cooling_result, rainwater_result, plant_list
    )
    return f"{SYSTEM_PROMPT}\n\n{STYLE_CONSTRAINT}\n\n{calc_text}"


# --------------------------------------------------------------------------
# 降级分析（规则驱动，体现广东特点）
# --------------------------------------------------------------------------


def _pick(available: set[str], candidates: Sequence[str], n: int = 1) -> list[str]:
    """从可用植物名中按候选顺序挑选 n 个，用于点名举例。"""
    return [c for c in candidates if c in available][:n]


def build_fallback_report(
    carbon_result: Mapping[str, Any],
    cooling_result: Mapping[str, Any],
    rainwater_result: Mapping[str, Any],
    plant_list: Sequence[Any],
    reason: str | None = None,
) -> str:
    """基于规则的分析文本，作为 API 不可用时的降级输出。

    体现广东地区特点：乡土树种、常绿比例、乔灌草复层、雨热同季。
    """
    names = plant_names(plant_list)
    if not names:
        return "尚未添加植物，暂无分析内容。请先在方案中加入乔木、灌木与地被植物。"

    available = set(names)
    c = _fmt(carbon_result.get("carbon_kg"))
    co2 = _fmt(carbon_result.get("co2_equivalent_kg"))
    heat_mj = _fmt(cooling_result.get("heat_absorbed_kj")) / 1000.0
    water_l = _fmt(rainwater_result.get("intercepted_water_kg"))

    n_total = len(names)
    native = [n for n in names if n in NATIVE_SPECIES]
    evergreen = [n for n in names if n in EVERGREEN_SPECIES]
    deciduous = [n for n in names if n in DECIDUOUS_SPECIES]
    shrubs = [n for n in names if n in SHRUB_SPECIES]

    # ---- 段 1：整体表现 ----
    car_equiv = co2 / CAR_CO2_KG_PER_YEAR if co2 else 0.0
    p1 = (
        f"本方案共 {n_total} 种植物，核算期固碳 {c:,.0f} kg C"
        f"（折合 CO₂ {co2:,.0f} kg，约 {car_equiv:,.1f} 辆小汽车的年排放量），"
        f"蒸腾吸热 {heat_mj:,.0f} MJ，单次降雨截留 {water_l:,.0f} L。"
    )
    if c <= 0 and heat_mj <= 0:
        p1 += "各项效益偏低，建议补充冠层体量较大的乔木。"
    else:
        p1 += "方案已具备一定的碳汇与雨洪调蓄能力。"

    # ---- 段 2：主要贡献植物（点名 + 华南特性）----
    star_c = _pick(available, ["榕树", "细叶榕", "木荷", "秋枫", "香樟"])
    star_shade = _pick(available, ["细叶榕", "榕树", "木荷", "黄叶榕"])
    star_shrub = _pick(available, ["大红花", "灰莉", "假连翘", "马缨丹", "福建茶"], 2)

    if star_c:
        star = star_c[0]
        if star in ("榕树", "细叶榕"):
            detail = "桑科榕属常绿乔木，冠层浓密、叶面积指数高，是华南城市绿地固碳与遮荫的主力"
        elif star == "木荷":
            detail = "华南山地与城市绿地常见的常绿阔叶树种，蒸腾调温能力突出"
        elif star == "秋枫":
            detail = "冠层体量大、叶面积高，是华南乡土乔木中固碳潜力较强的一种"
        else:
            detail = "华南常见常绿阔叶乔木，冠层稳定、固碳表现较好"
        p2 = f"{star}等乡土树种固碳表现突出，为{detail}。"
    else:
        p2 = "方案缺少高大常绿乔木，固碳主要来自中低层植物，总量相对有限。"

    if star_shade and star_shade[0] != (star_c[0] if star_c else ""):
        p2 += f"{star_shade[0]}冠幅大，对树下降温增湿贡献明显。"
    if star_shrub:
        p2 += f"灌木层以{'、'.join(star_shrub)}为主，补充了近地面的截留与滞尘。"

    # ---- 段 3：结构优化建议 ----
    advice: list[str] = []

    if deciduous and not evergreen:
        advice.append("以落叶树种为主，冬季冠层稀疏、固碳存在断档，建议增配常绿树种")
    elif evergreen and not deciduous:
        advice.append("全为常绿树种，可点缀少量落叶种类（如红花羊蹄甲）增加季相变化")
    else:
        advice.append("常绿与落叶搭配均衡，季相与功能互补性较好")

    if not shrubs:
        advice.append("缺灌木层，建议在乔木下补植耐荫灌木")
    if n_total < 4:
        advice.append("种类偏少，建议补齐乔灌草三层结构")
    elif not deciduous:
        advice.append("宜维持乔灌草复层格局，使截留与滞尘效率高于单层配置")

    advice.append("雨热同季、暴雨集中，宜以大叶浓密冠层形成雨季高效截留，并选耐涝耐热的乡土种类降低养护风险")

    p3 = "结构建议：" + "；".join(advice) + "。"

    # ---- 段 4：一句话总结 ----
    if native and len(native) >= max(1, n_total // 2):
        p4 = f"总体而言，方案以{native[0]}等乡土树种为主体，契合华南气候，建议继续优化层次结构。"
    else:
        p4 = "总体而言，方案已产生可观生态效益，但乡土树种占比偏低，建议提高本地种类比重以增强长期稳定性。"

    body = "\n\n".join([p1, p2, p3, p4])
    if reason:
        body = f"（以下为离线分析·{reason}）\n\n{body}"
    return body


# --------------------------------------------------------------------------
# API 调用
# --------------------------------------------------------------------------


def _call_deepseek(prompt: str, api_key: str) -> str:
    """调用 DeepSeek 对话补全接口，返回纯文本。

    :raises RuntimeError: 任何请求或解析失败（由调用方转降级）
    """
    try:
        import requests
    except ImportError as e:      # pragma: no cover
        raise RuntimeError(f"缺少 requests 库：{e}") from e

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            DEEPSEEK_API_URL,
            headers=headers,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as e:        # noqa: BLE001
        raise RuntimeError(f"网络请求失败：{type(e).__name__}: {e}") from e

    if resp.status_code != 200:
        snippet = (resp.text or "")[:200]
        raise RuntimeError(f"HTTP {resp.status_code}：{snippet}")

    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:        # noqa: BLE001
        raise RuntimeError(f"响应解析失败：{type(e).__name__}: {e}") from e

    content = (content or "").strip()
    if not content:
        raise RuntimeError("接口返回空内容")
    return content


# --------------------------------------------------------------------------
# 对外入口
# --------------------------------------------------------------------------


def generate_report(
    carbon_result: Mapping[str, Any],
    cooling_result: Mapping[str, Any],
    rainwater_result: Mapping[str, Any],
    plant_list: Sequence[Any],
    api_key: str | None = None,
    use_api: bool = True,
    verbose: bool = False,
) -> str:
    """生成 200~300 字中文分析报告。

    优先调用 DeepSeek；**任何失败都降级**到基于规则的分析文本，
    必要时在文首标注降级原因，保证界面不会因网络或 Key 问题报错。

    :param carbon_result: 固碳结果 dict（``carbon_kg``/``oxygen_kg``/``co2_equivalent_kg``）
    :param cooling_result: 降温结果 dict（``transpiration_kg``/``heat_absorbed_kj``/``cooled_air_volume_m3``）
    :param rainwater_result: 截留结果 dict（``intercepted_water_kg``/``interception_rate``）
    :param plant_list: 植物清单；支持名称字符串 / dict / pandas Series / (植物, 株数) 元组
    :param api_key: 覆盖环境变量中的 Key（一般不传）
    :param use_api: 置 False 强制走降级方案（离线演示、测试用）
    :param verbose: 置 True 时在降级文本中附失败原因，便于排查
    :return: 报告文本；失败时返回降级文本，不抛异常
    """
    if not use_api:
        return build_fallback_report(
            carbon_result, cooling_result, rainwater_result, plant_list,
            reason="已手动关闭在线生成" if verbose else None,
        )

    key = (api_key or os.environ.get(API_KEY_ENV, "")).strip()
    if not key:
        return build_fallback_report(
            carbon_result, cooling_result, rainwater_result, plant_list,
            reason=f"未设置环境变量 {API_KEY_ENV}" if verbose else None,
        )

    prompt = build_prompt(
        carbon_result, cooling_result, rainwater_result, plant_list
    )
    try:
        return _call_deepseek(prompt, key)
    except Exception as e:        # noqa: BLE001 —— 任何失败都必须降级
        return build_fallback_report(
            carbon_result, cooling_result, rainwater_result, plant_list,
            reason=f"API 调用失败（{e}）" if verbose else None,
        )


# --------------------------------------------------------------------------
# 兼容旧接口（app.py 用的 generate_ai_report）
# --------------------------------------------------------------------------


def generate_ai_report(
    summary: Mapping[str, Any],
    detail_rows: Sequence[Mapping[str, Any]],
    use_api: bool = False,
    api_key: str | None = None,
) -> str:
    """兼容旧调用：从汇总 + 逐种明细还原三项结果后转调 :func:`generate_report`。

    :param summary: ``calculator.community_totals()`` 的结果
    :param detail_rows: ``calculator.summarize_community()`` 转成的记录列表
    """
    carbon_result = {
        "carbon_kg": summary.get("carbon_kg", 0.0),
        "oxygen_kg": summary.get("oxygen_kg", 0.0),
        "co2_equivalent_kg": summary.get("co2_equivalent_kg", 0.0),
    }
    cooling_result = {
        "transpiration_kg": summary.get("transpiration_kg", 0.0),
        "heat_absorbed_kj": summary.get("heat_absorbed_kj", 0.0),
        "cooled_air_volume_m3": summary.get("cooled_air_volume_m3", 0.0),
    }
    rate = detail_rows[0].get("interception_rate") if detail_rows else None
    rainwater_result = {
        "intercepted_water_kg": summary.get("intercepted_water_kg", 0.0),
        "interception_rate": rate,
    }
    return generate_report(
        carbon_result, cooling_result, rainwater_result,
        list(detail_rows), api_key=api_key, use_api=use_api,
    )
