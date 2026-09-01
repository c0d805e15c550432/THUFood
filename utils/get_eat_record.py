from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import base64
import requests
import json
from datetime import date, datetime

from utils.app_paths import records_dir


class RecordQueryError(ValueError):
    """Safe error text for the query UI, without server payloads or cookies."""


def decrypt_aes_ecb(encrypted_data: str) -> str:
    
    key = encrypted_data[:16].encode('utf-8')
    encrypted_data = encrypted_data[16:]
    encrypted_data_bytes = base64.b64decode(encrypted_data)
    
    cipher = AES.new(key, AES.MODE_ECB)
    
    decrypted_data = unpad(cipher.decrypt(encrypted_data_bytes), AES.block_size)

    return decrypted_data.decode('utf-8')

def _format_query_date(value):
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return datetime.strptime(str(value), "%Y-%m-%d").strftime("%Y-%m-%d")

def get_record(servicehall, idserial, starttime, endtime):
    starttime = _format_query_date(starttime)
    endtime = _format_query_date(endtime)
    if starttime > endtime:
        raise ValueError("starttime must not be later than endtime")

    # 获取数据
    url = "https://card.tsinghua.edu.cn/business/querySelfTradeList"
    params = {
        "pageNumber": 0,
        "pageSize": 5000,
        "starttime": starttime,
        "endtime": endtime,
        "idserial": idserial,
        "tradetype": -1,
    }
    cookie = {"servicehall": servicehall}
    try:
        response = requests.post(
            url, params=params, cookies=cookie, timeout=30,
            allow_redirects=False, verify=True,
        )
    except requests.RequestException:
        raise RecordQueryError("无法连接校园卡查询接口，请检查校园网或 VPN 后重试。") from None
    if response.status_code in (301, 302, 303, 307, 308, 401, 403):
        raise RecordQueryError("servicehall 未登录或已过期，请重新登录或重新获取 Cookie。")
    response.raise_for_status()

    try:
        payload = response.json()
    except ValueError:
        raise RecordQueryError("校园卡未返回查询数据，登录可能已过期，请重新获取 servicehall。") from None
    if not isinstance(payload, dict):
        raise RecordQueryError("校园卡查询接口返回了无法识别的数据。")
    encrypted_string = payload.get("data")
    if not isinstance(encrypted_string, str) or not encrypted_string:
        raise RecordQueryError("查询未成功，请确认学号属于当前登录账号，或重新获取 servicehall。")

    try:
        decrypted_string = decrypt_aes_ecb(encrypted_string)
        data = json.loads(decrypted_string)
    except (ValueError, TypeError):
        raise RecordQueryError("校园卡查询数据无法解析，请重新登录后重试。") from None

    # Persistent user data stays outside both the source tree and PyInstaller's
    # temporary extraction directory.
    try:
        destination = records_dir()
        destination.mkdir(parents=True, exist_ok=True)
        data_file = destination / f"eat_record_{datetime.now():%Y%m%d_%H%M%S}.json"
        with data_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except OSError:
        pass

    return data
