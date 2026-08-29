import typing as t

# Keywords to distinguish shower vs drinking-water transactions
_SHOWER_KEYWORDS = ("淋浴", "澡", "浴室", "洗澡")
_WATER_EXCLUDE_KEYWORDS = ("饮水", "直饮", "开水", "热水", "水房", "BOT")


def _iter_rows(data: t.Any) -> t.Iterable[t.Dict[str, t.Any]]:
    """Safely iterate transaction rows from the raw API payload."""
    if isinstance(data, dict):
        result = data.get("resultData", {}) or {}
        rows = result.get("rows", []) or []
        if isinstance(rows, list):
            return rows
    return []


def _is_shower_row(row: t.Dict[str, t.Any]) -> bool:
    """Identify shower transactions and filter out drinking-water ones."""
    summary = str(row.get("summary", ""))
    if summary != "水控POS消费流水":
        return False

    mername = str(row.get("mername", ""))
    # Filter out drinking/boiling water related records
    if any(keyword in mername for keyword in _WATER_EXCLUDE_KEYWORDS):
        return False
    # Keep shower-related records
    return any(keyword in mername for keyword in _SHOWER_KEYWORDS)


def _is_card_reissue_row(row: t.Dict[str, t.Any]) -> bool:
    """Identify card reissue transactions."""
    summary = str(row.get("summary", ""))
    mername = str(row.get("mername", ""))
    meraddr = str(row.get("meraddr", ""))
    
    # Match card reissue transactions
    return (summary == "自助补卡账户余额扣费" or 
            mername == "学生卡成本" or 
            meraddr == "学生卡成本")


def get_shower_stats(data: t.Any) -> t.Dict[str, t.Any]:
    """
    Compute shower count and total amount (in yuan) from raw payload.
    Excludes drinking-water transactions. Also returns water weight in pounds
    using the tariff of ¥0.04 per pound.
    """
    count = 0
    amount = 0.0
    rate_per_lb = 0.04  # yuan per pound of water

    for row in _iter_rows(data):
        if row.get("poscode") == "401036":
            continue
        if _is_shower_row(row):
            count += 1
            amount += float(row.get("txamt", 0)) * 0.01

    weight_lb = amount / rate_per_lb if rate_per_lb else 0.0
    avg_amount = amount / count if count > 0 else 0.0
    avg_weight_lb = weight_lb / count if count > 0 else 0.0

    return {
        "count": count,
        "amount": round(amount, 2),
        "weight_lb": round(weight_lb, 2),
        "avg_amount": round(avg_amount, 2),
        "avg_weight_lb": round(avg_weight_lb, 2),
        "available": True,
    }


def get_card_stats(data: t.Any) -> t.Dict[str, t.Any]:
    """
    Compute card reissue count and total amount (in yuan) from raw payload.
    Identifies transactions with summary "自助补卡账户余额扣费" or mername/meraddr "学生卡成本".
    """
    count = 0
    amount = 0.0

    for row in _iter_rows(data):
        if _is_card_reissue_row(row):
            count += 1
            amount += float(row.get("txamt", 0)) * 0.01

    # Generate fun messages based on card reissue count
    if count == 0:
        message = "真不错！一次都没丢过卡 🎉"
    elif count == 1:
        message = "还算小心，只丢了一次 😌"
    elif count == 2:
        message = "有点马虎了哦，丢了两次 😅"
    elif count == 3:
        message = "这...已经补了3次了 🤔"
    elif count >= 4:
        message = f"补卡达人！已经补了{count}次 😱"
    else:
        message = ""

    return {
        "count": count,
        "amount": round(amount, 2),
        "available": True,
        "message": message,
    }
