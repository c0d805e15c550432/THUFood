import json
import os
import sys
from datetime import date
from pathlib import Path
import streamlit as st
import matplotlib.pyplot as plt
import platform
import subprocess
from utils.bonus import get_shower_stats, get_card_stats

from utils.analyze_data import (
    analyze_patterns,
    get_costs,
    get_max_cost,
    get_monthly_expenditure,
    get_time_bounds,
    get_top_counters,
    get_top_locations,
)
from utils.get_eat_record import RecordQueryError, get_record
from utils.process_data import process_data
from utils.prompts import get_eat_habbit_prompt
from utils.ask_gpt import ask_gpt
from utils.app_paths import records_dir
from utils.legacy_migration import migrate_legacy_files
from utils.llm_profiles import (
    PRESETS,
    ProfileStoreError,
    delete_profile,
    load_profile_api_key,
    load_profile_state,
    save_profile,
    select_profile,
)
from utils.auth import (
    AuthenticationError,
    SecondFactorChallenge,
    SecondFactorVerification,
    complete_second_factor,
    request_second_factor_code,
    start_login,
)
from utils.trusted_store import load_trusted_device, save_trusted_device


SECOND_FACTOR_LABELS = {
    'enterprise_email': '企业邮箱验证码',
    'sms': '短信验证码',
    'totp': 'TOTP 动态验证码',
}

st.set_page_config(
    page_title="你清食堂消费总结",
    page_icon="🍜",
    layout="wide"
)

def resource_path(*parts):
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS).joinpath(*parts)
    return Path(__file__).resolve().parent.joinpath(*parts)


# 添加自定义 CSS 样式
def load_css():
    css_path = resource_path('utils', 'styles.css')
    try:
        css_text = css_path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        css_text = css_path.read_text(encoding='gbk', errors='replace')
    except OSError:
        css_text = None

    if css_text is None:
        st.warning('无法读取样式文件 `utils/styles.css`，将使用默认样式。')
        return

    st.markdown(f'<style>{css_text}</style>', unsafe_allow_html=True)

def create_stat_card(title, value, location, date, comment, emoji=""):
    return f"""
        <div class='stat-card'>
            <div class='stat-label'>{title} {emoji}</div>
            <div class='stat-value'>{value}</div>
            <div class='stat-label'>地点: {location}</div>
            <div class='stat-label'>{'时间' if ':' in date else '日期'}: {date}</div>
            <div class='stat-label'>{comment}</div>
        </div>
    """

def plot_merchant_spending(df_raw):
    # Group by merchant name and sum the transaction amounts
    merchant_spending = df_raw.groupby('mername')['txamt'].sum().sort_values(ascending=True)
    
    # Set Chinese font based on platform
    system = platform.system()
    if system == 'Darwin':  # macOS
        plt.rcParams['font.sans-serif'] = ['Arial Unicode MS']
    elif system == 'Linux':
        # Try to install Noto fonts if not present
        try:
            subprocess.run(['apt-get', 'update'], check=True)
            subprocess.run(['apt-get', 'install', '-y', 'fonts-noto-cjk'], check=True)
        except subprocess.CalledProcessError:
            st.warning("无法安装字体，可能需要管理员权限。图表中文显示可能不正常。")
        except FileNotFoundError:
            st.warning("未找到apt-get命令，请手动安装fonts-noto-cjk包。")
        
        plt.rcParams['font.sans-serif'] = ['Noto Sans CJK JP', 'Noto Sans CJK SC', 'Noto Sans CJK TC', 'DengXian', 'SimHei', 'SimSun', 'WenQuanYi Micro Hei', 'FangSong_GB2312', 'KaiTi_GB2312']
    else:  # Windows
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'DengXian', 'SimSun', 'SimHei', 'KaiTi', 'FangSong']
    plt.rcParams['axes.unicode_minus'] = False

    # Create high-resolution figure
    plt.figure(figsize=(12, len(merchant_spending) / 66 * 18), dpi=500)
    
    # Set higher quality settings
    plt.rcParams['figure.dpi'] = 500
    plt.rcParams['savefig.dpi'] = 500
    plt.rcParams['figure.figsize'] = [12, len(merchant_spending) / 66 * 18]
    plt.rcParams['figure.autolayout'] = True
    
    # Create horizontal bar plot
    plt.barh(range(len(merchant_spending)), merchant_spending)
    
    # Add value labels on the bars with adjusted font size for high DPI
    for i, value in enumerate(merchant_spending):
        plt.text(value + 0.01 * max(merchant_spending), i, 
                f'¥{value:.2f}', va='center', ha='left', fontsize=6)
    
    # Customize the plot with adjusted font sizes
    plt.yticks(range(len(merchant_spending)), merchant_spending.index, fontsize=8)
    plt.xlabel('消费金额（元）', fontsize=10)
    plt.title('各窗口消费总额', fontsize=12)
    plt.xlim(0, 1.2 * max(merchant_spending))
    
    # Adjust layout to prevent label cutoff
    plt.tight_layout()
    
    return plt.gcf()

def plot_monthly_expenditure(monthly_expenditure):
    """Create a compact monthly expenditure chart."""
    system = platform.system()
    if system == 'Darwin':
        plt.rcParams['font.sans-serif'] = ['Arial Unicode MS']
    elif system == 'Linux':
        plt.rcParams['font.sans-serif'] = ['Noto Sans CJK SC', 'Droid Sans Fallback', 'DejaVu Sans']
    else:
        plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'DengXian', 'SimSun', 'SimHei']
    plt.rcParams['axes.unicode_minus'] = False

    labels = [month.strftime('%Y-%m') for month in monthly_expenditure.index]
    values = monthly_expenditure.to_list()
    width = max(8, len(labels) * 0.75)
    fig, ax = plt.subplots(figsize=(width, 4.5), dpi=150)
    bars = ax.bar(labels, values, color='#4f86c6', width=0.65)

    ax.set_title('每月消费金额')
    ax.set_xlabel('月份')
    ax.set_ylabel('消费金额（元）')
    ax.grid(axis='y', alpha=0.2)
    ax.tick_params(axis='x', rotation=45)

    for bar, value in zip(bars, values):
        ax.annotate(
            f'¥{value:.2f}',
            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
            xytext=(0, 3),
            textcoords='offset points',
            ha='center',
            va='bottom',
            fontsize=8,
        )

    fig.tight_layout()
    return fig

def _clear_auth_inputs():
    """Clear one-time secret widgets and pending callback values."""
    for key in ('auth_password', 'auth_verification_code', 'manual_servicehall'):
        if key in st.session_state:
            st.session_state[key] = ''
    st.session_state.pop('_submitted_credentials', None)


def _reset_auth_flow():
    """Discard an unfinished login when the user switches mode or starts over."""
    _clear_auth_inputs()
    st.session_state.pop('_auth_challenge', None)
    st.session_state.pop('_auth_verification', None)
    st.session_state.pop('_auth_pending_username', None)
    st.session_state.pop('auth_second_factor_method', None)


def _take_auth_inputs():
    # Form callbacks run before the next script execution. Move submitted
    # secrets out of the widgets, then consume them exactly once in main().
    credentials = {
        'password': st.session_state.get('auth_password', ''),
        'verification_code': st.session_state.get('auth_verification_code', ''),
        'second_factor_method': st.session_state.get('auth_second_factor_method', ''),
        'servicehall': st.session_state.get('manual_servicehall', ''),
    }
    _clear_auth_inputs()
    st.session_state['_submitted_credentials'] = credentials


def _render_llm_settings():
    """Render saved profiles and return the active provider configuration."""
    clear_api_key = st.session_state.pop('_clear_llm_api_key', None)
    if clear_api_key:
        st.session_state.pop(clear_api_key, None)
    state = load_profile_state()
    profiles = {profile['id']: profile for profile in state['profiles']}
    options = ['__new__', *profiles]
    pending = st.session_state.pop('_pending_llm_profile_selection', None)
    selected_default = pending or state.get('selected') or '__new__'
    if selected_default not in options:
        selected_default = '__new__'
    if st.session_state.get('llm_profile_choice') not in options:
        st.session_state['llm_profile_choice'] = selected_default
    selected_id = st.selectbox(
        "已保存配置",
        options,
        format_func=lambda value: "新建配置" if value == '__new__' else profiles[value]['name'],
        key='llm_profile_choice',
    )
    selected = profiles.get(selected_id)
    if selected:
        select_profile(selected_id)
    provider_default = selected['provider'] if selected else 'DeepSeek'
    provider_key = f"llm_provider_{selected_id}"
    provider = st.selectbox(
        "服务商预设", list(PRESETS),
        index=list(PRESETS).index(provider_default), key=provider_key,
    )
    preset = PRESETS[provider]
    use_saved_values = selected is not None and provider == selected['provider']
    scope = f"{selected_id}_{provider}"
    name_scope = selected_id if selected else f"new_{provider}"
    base_default = selected['base_url'] if use_saved_values else preset['base_url']
    model_default = selected['model'] if use_saved_values else preset['model']
    profile_name = st.text_input(
        "配置名称",
        value=selected['name'] if selected else f"{provider} 配置",
        key=f"llm_profile_name_{name_scope}",
    )
    base_url = st.text_input(
        "Base URL",
        value=base_default,
        key=f"llm_base_url_{scope}",
    )
    model = st.text_input(
        "Model",
        value=model_default,
        key=f"llm_model_{scope}",
    )
    api_key_input = st.text_input(
        "API Key",
        value="",
        type="password",
        placeholder="已保存的密钥不会显示；留空则继续使用",
        key=f"llm_api_key_{scope}",
    )
    saved_api_key = load_profile_api_key(selected_id) if selected else ""
    active_api_key = api_key_input or saved_api_key
    save_col, delete_col = st.columns(2)
    if save_col.button("保存配置", key='save_llm_profile', use_container_width=True):
        try:
            saved = save_profile(
                selected_id if selected else None,
                profile_name, provider, base_url, model, api_key_input,
            )
        except ProfileStoreError as error:
            st.error(str(error))
        else:
            st.session_state['_clear_llm_api_key'] = f"llm_api_key_{scope}"
            st.session_state['_pending_llm_profile_selection'] = saved['id']
            st.rerun()
    if delete_col.button(
        "删除配置", key='delete_llm_profile', use_container_width=True,
        disabled=selected is None,
    ):
        try:
            delete_profile(selected_id)
        except ProfileStoreError as error:
            st.error(str(error))
        else:
            st.session_state['_pending_llm_profile_selection'] = '__new__'
            st.rerun()
    st.caption("配置名称、接口和模型保存在用户配置目录；API Key 保存在系统凭据库。")
    return {
        "protocol": preset['protocol'],
        "base_url": base_url.strip(),
        "model": model.strip(),
        "api_key": active_api_key,
    }


def main():
    if not st.session_state.get('_legacy_migration_checked'):
        st.session_state['_legacy_migration_checked'] = True
        if os.getenv('THUFOOD_SKIP_LEGACY_MIGRATION') != '1':
            migration = migrate_legacy_files()
            if migration.records_moved or migration.env_migrated:
                migrated = []
                if migration.records_moved:
                    migrated.append(f"{migration.records_moved} 份消费记录")
                if migration.env_migrated:
                    migrated.append("旧版 AI 配置")
                st.session_state['_migration_notice'] = (
                    "已迁移" + "和".join(migrated) + "到稳定用户目录。"
                )
            if migration.warnings:
                st.session_state['_migration_warning'] = " ".join(migration.warnings)
    pending_manual = st.session_state.pop('_pending_manual_credentials', None)
    if isinstance(pending_manual, dict):
        st.session_state.pop('_auth_challenge', None)
        st.session_state.pop('_auth_verification', None)
        st.session_state.pop('_auth_pending_username', None)
        st.session_state['auth_mode'] = '手动输入 servicehall'
        st.session_state['manual_idserial'] = pending_manual.get('idserial', '')
        st.session_state['manual_servicehall'] = pending_manual.get('servicehall', '')
    credentials = st.session_state.pop('_submitted_credentials', {})
    load_css()
    st.title("🍜 你清食堂消费总结")
    auth_notice = st.session_state.pop('_auth_notice', None)
    if auth_notice:
        st.success(auth_notice)
    auth_warning = st.session_state.pop('_auth_warning', None)
    if auth_warning:
        st.warning(auth_warning)
    migration_notice = st.session_state.pop('_migration_notice', None)
    if migration_notice:
        st.success(migration_notice)
    migration_warning = st.session_state.pop('_migration_warning', None)
    if migration_warning:
        st.warning(migration_warning)
    
    # Sidebar for configuration
    llm_settings = None
    with st.sidebar:
        llm_submitted = st.toggle("启用 AI 生成评论 🤖", value=False, key='llm_enabled')
        if llm_submitted:
            st.header("⚙️ LLM 设置")
            llm_settings = _render_llm_settings()
    
    # 更新欢迎页面文案
    st.markdown("""
    
    👋 这是一个专门为你清吃货们打造的美食档案！
    """)

    # 更新用户输入区域文案
    get_data_online = st.toggle(
        "在线获取数据", value=True, help="如果关闭，则从本地的 json 文件读取数据",
        on_change=_reset_auth_flow,
    )
    auth_mode = '账号密码登录'
    if get_data_online:
        # Outside the form so changing modes immediately updates its fields.
        auth_mode = st.radio(
            "认证方式", ['手动输入 servicehall', '账号密码登录'],
            horizontal=True, index=1, key='auth_mode', on_change=_reset_auth_flow,
        )
        st.caption("密码和验证码仅用于认证，提交后清空，不写入文件或发送给 AI。")
    auth_challenge = st.session_state.get('_auth_challenge')
    auth_verification = st.session_state.get('_auth_verification')
    if auth_mode != '账号密码登录':
        auth_challenge = None
        auth_verification = None
    with st.form("user_input"):
        if get_data_online:
            st.subheader("🔑 请出示你的美食证件")
            default_year = date.today().year
            period_col1, period_col2 = st.columns(2)
            with period_col1:
                query_start_date = st.date_input(
                    "查询开始日期",
                    value=date(default_year, 1, 1),
                    key='query_start_date',
                )
            with period_col2:
                query_end_date = st.date_input(
                    "查询结束日期",
                    value=date.today(),
                    key='query_end_date',
                )
            if auth_mode == '账号密码登录':
                if isinstance(auth_verification, SecondFactorVerification):
                    login_username = st.session_state.get('_auth_pending_username', '')
                    method_label = SECOND_FACTOR_LABELS[auth_verification.method]
                    st.info(f"已进入{method_label}流程，请输入六位验证码。")
                    st.text_input(
                        f"{method_label}", type='password', max_chars=6,
                        key='auth_verification_code',
                    )
                elif isinstance(auth_challenge, SecondFactorChallenge):
                    login_username = st.session_state.get('_auth_pending_username', '')
                    st.info("学校要求二次验证。请选择一种可用方式。")
                    st.radio(
                        "二次验证方式", list(auth_challenge.methods),
                        format_func=lambda method: SECOND_FACTOR_LABELS[method],
                        key='auth_second_factor_method',
                    )
                else:
                    login_username = st.text_input("统一身份认证账号", key='auth_username').strip()
                    st.text_input("统一身份认证密码", type='password', key='auth_password')
                idserial = ''  # Filled only from the identity login response.
            else:
                idserial = st.text_input("学号", key='manual_idserial').strip()
                st.text_input(
                    "Cookie中的servicehall", type='password', key='manual_servicehall',
                    help="在校园卡官网登录后复制 servicehall= 后的值，不包含 servicehall= 或其他 Cookie。",
                )
            servicehall = credentials.get('servicehall', '').strip()
            if isinstance(auth_verification, SecondFactorVerification):
                submit_label = "验证并开启美食档案 🚀"
            elif isinstance(auth_challenge, SecondFactorChallenge):
                submit_label = "获取或准备验证码"
            else:
                submit_label = "开启美食档案 🚀"
            submitted = st.form_submit_button(submit_label, on_click=_take_auth_inputs)
            if submitted:
                if auth_mode == '手动输入 servicehall' and (not idserial or not servicehall):
                    st.error("⚠️ 请填写学号和 servicehall！")
                    return
                if (
                    auth_mode == '账号密码登录'
                    and not isinstance(auth_challenge, SecondFactorChallenge)
                    and not isinstance(auth_verification, SecondFactorVerification)
                    and (not login_username or not credentials.get('password'))
                ):
                    st.error("⚠️ 请填写统一身份认证账号和密码！")
                    return
                if auth_mode == '账号密码登录' and isinstance(auth_verification, SecondFactorVerification) and (
                    len(credentials.get('verification_code', '')) != 6
                    or not credentials.get('verification_code', '').isdigit()
                ):
                    st.error("⚠️ 二次验证码必须是六位数字！")
                    return
                if query_start_date > query_end_date:
                    st.error("⚠️ 查询开始日期不能晚于结束日期！")
                    return
        else:
            folder_path = records_dir()
            file_list = sorted(
                (path.name for path in folder_path.glob("*.json") if path.is_file()),
                reverse=True,
            ) if folder_path.is_dir() else []
            st.caption(f"本地记录目录：{folder_path}")
            if file_list:
                selected_file = st.selectbox(
                    "请选择文件：",
                    file_list,
                    index=0  # 默认选中第一个文件
                )
                submitted = st.form_submit_button(f"通过{selected_file}开启美食档案 🚀")
            else:
                st.warning("⚠️ 稳定数据目录中暂无消费记录，请先在线查询一次。")
                submitted = st.form_submit_button("暂无可分析的本地记录", disabled=True)
                selected_file = None
                idserial = None
                servicehall = None
                

        # After the form submission check
        if submitted:
            st.session_state.pop('report_data', None)
            st.session_state.pop('ai_comments', None)

            # First spinner for data fetching
            with st.spinner("正在获取数据，请稍候..."):
                auto_manual_credentials = None
                try:
                    data = None
                    if get_data_online:
                        if auth_mode == '账号密码登录':
                            with st.spinner("正在完成学校认证并验证校园卡会话..."):
                                if isinstance(auth_verification, SecondFactorVerification):
                                    login = complete_second_factor(
                                        auth_verification,
                                        credentials.pop('verification_code', ''),
                                    )
                                    st.session_state.pop('_auth_verification', None)
                                    st.session_state.pop('_auth_challenge', None)
                                    st.session_state.pop('_auth_pending_username', None)
                                    if login.trusted_device is not None:
                                        trusted_devices = st.session_state.setdefault('_trusted_devices', {})
                                        trusted_devices[login_username] = login.trusted_device
                                        if not save_trusted_device(login_username, login.trusted_device):
                                            st.session_state['_auth_warning'] = (
                                                "可信设备已在本次运行中生效，但未能写入系统凭据库；程序重启后可能再次要求二次验证。"
                                            )
                                    if not login.trust_saved:
                                        st.session_state['_auth_warning'] = (
                                            "二次验证已成功，但学校没有返回可信设备令牌；下次登录可能仍需验证。"
                                        )
                                elif isinstance(auth_challenge, SecondFactorChallenge):
                                    verification = request_second_factor_code(
                                        auth_challenge,
                                        credentials.get('second_factor_method', ''),
                                    )
                                    st.session_state['_auth_verification'] = verification
                                    st.rerun()
                                else:
                                    trusted_devices = st.session_state.setdefault('_trusted_devices', {})
                                    trusted_device = trusted_devices.get(login_username)
                                    if trusted_device is None:
                                        trusted_device = load_trusted_device(login_username)
                                        if trusted_device is not None:
                                            trusted_devices[login_username] = trusted_device
                                    login = start_login(
                                        login_username,
                                        credentials.pop('password'),
                                        trusted_device=trusted_device,
                                    )
                                    if isinstance(login, SecondFactorChallenge):
                                        st.session_state['_auth_challenge'] = login
                                        st.session_state['_auth_pending_username'] = login_username
                                        st.rerun()
                            servicehall = login.servicehall
                            idserial = login.idserial
                            if not idserial:
                                raise AuthenticationError("登录响应未返回有效学号，请重新登录或切换手动输入 servicehall。")
                            auto_manual_credentials = {
                                'idserial': idserial,
                                'servicehall': servicehall,
                            }
                        data = get_record(
                            servicehall,
                            idserial,
                            query_start_date,
                            query_end_date,
                        )
                    else:
                        data = json.loads((folder_path / selected_file).read_text(encoding='utf-8'))
                    df_raw, df = process_data(data)
                    if df_raw.empty:
                        st.session_state.pop('report_data', None)
                        if auto_manual_credentials:
                            st.session_state['_pending_manual_credentials'] = auto_manual_credentials
                            st.session_state['_auth_notice'] = (
                                "账号认证成功，已切换到手动 servicehall 模式并填入有效学号与 Cookie；所选时间段没有消费记录。"
                            )
                            st.rerun()
                        st.warning("所选时间段内没有消费记录，请调整查询日期后重试。")
                        return
                    username = df['username'].iloc[0]
                    shower_stats = get_shower_stats(data)
                    card_stats = get_card_stats(data)
                    st.session_state['report_data'] = {
                        'df_raw': df_raw,
                        'df': df,
                        'username': username,
                        'shower_stats': shower_stats,
                        'card_stats': card_stats,
                        'query_start_date': (
                            query_start_date if get_data_online else df_raw['txdate'].min().date()
                        ),
                        'query_end_date': (
                            query_end_date if get_data_online else df_raw['txdate'].max().date()
                        ),
                    }
                    if auto_manual_credentials:
                        st.session_state['_pending_manual_credentials'] = auto_manual_credentials
                        st.session_state['_auth_notice'] = (
                            "账号认证和数据查询成功，已切换到手动 servicehall 模式并自动填入有效学号与 Cookie。"
                        )
                        st.rerun()
                    st.success("✅ 数据获取成功")
                except RecordQueryError as e:
                    if auto_manual_credentials:
                        st.session_state['_pending_manual_credentials'] = auto_manual_credentials
                        st.session_state['_auth_warning'] = (
                            f"账号认证已完成并已切换到手动模式，但本次消费记录查询失败：{e}"
                        )
                        st.rerun()
                    if get_data_online and auth_mode == '手动输入 servicehall':
                        st.error(
                            f"❌ servicehall 可能已失效或无法用于当前查询：{e}"
                            " 建议切换到账号密码登录，重新获取有效学号与 servicehall。"
                        )
                    else:
                        st.error(f"❌ {e}")
                    return
                except AuthenticationError as e:
                    st.error(f"❌ {e}")
                    return
                except Exception:
                    st.error("❌ 数据获取失败，请检查网络、查询学号和数据格式后重试。认证凭证不会被输出。")
                    return
                finally:
                    credentials.clear()

    report_data = st.session_state.get('report_data')
    if report_data:
        df_raw = report_data['df_raw']
        df = report_data['df']
        username = report_data['username']
        shower_stats = report_data['shower_stats']
        card_stats = report_data['card_stats']
        query_start_date = report_data['query_start_date']
        query_end_date = report_data['query_end_date']

        # Create expander after successful data fetch
        with st.expander(f"📊 {username}的美食探险日记", expanded=True):
            # Second spinner for report generation
            with st.spinner("正在生成报告，请稍候..."):
                try:
                    # Monthly expenditure with an interactive inquiry period.
                    st.subheader("📅 月度消费查询")
                    st.caption(
                        f"查询时间段：{query_start_date:%Y-%m-%d} 至 "
                        f"{query_end_date:%Y-%m-%d}（含首尾日期）"
                    )
                    monthly_expenditure = get_monthly_expenditure(
                        df_raw,
                        query_start_date,
                        query_end_date,
                    )
                    period_total = monthly_expenditure.sum()
                    metric_col1, metric_col2, metric_col3 = st.columns(3)
                    metric_col1.metric("查询期间总消费", f"¥{period_total:.2f}")
                    metric_col2.metric("月平均消费", f"¥{monthly_expenditure.mean():.2f}")
                    metric_col3.metric("统计月数", len(monthly_expenditure))

                    monthly_fig = plot_monthly_expenditure(monthly_expenditure)
                    st.pyplot(monthly_fig, width='stretch')
                    plt.close(monthly_fig)

                    monthly_table = monthly_expenditure.rename('消费金额（元）').reset_index()
                    monthly_table.columns = ['月份', '消费金额（元）']
                    monthly_table['月份'] = monthly_table['月份'].astype(str)
                    st.dataframe(
                        monthly_table,
                        hide_index=True,
                        width='stretch',
                        column_config={
                            '消费金额（元）': st.column_config.NumberColumn(format='¥%.2f')
                        },
                    )

                    # 1. 消费统计卡片
                    st.subheader("💰 资金报告")
                    col1, col2 = st.columns(2)
                    
                    avg_cost, total_cost = get_costs(df)
                    with col1:
                        cups = int(total_cost // 13)
                        st.markdown("""
                            <div class='stat-card card-blue'>
                                <div class='stat-label'>{query_start_date:%Y-%m-%d} 至 {query_end_date:%Y-%m-%d} 一共吃了</div>
                                <div class='stat-value'>¥{total_cost:.2f}</div>
                                <div class='stat-label'>相当于 {cups} 杯生椰拿铁 🥥</div>
                            </div>
                        """.format(total_cost=total_cost, cups=cups, query_start_date=query_start_date, query_end_date=query_end_date), unsafe_allow_html=True)
                    
                    with col2:
                        cups = float(round(avg_cost / 13, 1))
                        st.markdown("""
                            <div class='stat-card card-green'>
                                <div class='stat-label'>平均每顿饭钱</div>
                                <div class='stat-value'>¥{avg_cost:.2f}</div>
                                <div class='stat-label'>相当于 {cups} 杯生椰拿铁 🥥</div>
                            </div>
                        """.format(avg_cost=avg_cost, cups=cups, query_start_date=query_start_date, query_end_date=query_end_date), unsafe_allow_html=True)

                    # 2. 最常光顾食堂展示
                    st.subheader("🏆 你的主力探店地")
                    top_3_canteens = get_top_locations(df)
                    cols = st.columns(3)  # 创建3列
                    
                    for idx, ((location, visits), col) in enumerate(zip(top_3_canteens.items(), cols), 1):
                        color_class = f"card-{'purple' if idx == 1 else 'orange' if idx == 2 else 'red'}"
                        with col:
                            st.markdown(f"""
                                <div class='stat-card {color_class}'>
                                    <div class='stat-label'>第 {idx} 名</div>
                                    <div class='stat-value'>{location}</div>
                                    <div class='stat-label'>一共吃了 {visits} 顿</div>
                                </div>
                            """, unsafe_allow_html=True)
                    st.markdown("", unsafe_allow_html=True)

                    # 3. 最喜爱的窗口
                    st.subheader("🎯 你的心头好")
                    counter_visits = get_top_counters(df)
                    top_5_counters = counter_visits.head()
                    cols = st.columns(5)
                    
                    for idx, ((counter, visits), col) in enumerate(zip(top_5_counters.items(), cols), 1):
                        with col:
                            st.markdown(f"""
                                <div class='stat-card'>
                                    <div class='stat-label'>第 {idx} 名</div>
                                    <div class='stat-value'>{counter.replace('园_', '')}</div>
                                    <div class='stat-label'>吃了 {visits} 次</div>
                                </div>
                            """, unsafe_allow_html=True)
                    st.markdown("", unsafe_allow_html=True)

                    # 4.5 Bonus 区域：洗澡/补卡，保持与逆天卡片相似的风格
                    with st.expander("🎁 Bonus", expanded=False):
                        col1, col2 = st.columns(2)

                        with col1:
                            st.markdown(
                                """
                                <div class='stat-card'>
                                    <div class='stat-label'>洗澡大王 🛁</div>
                                    <div class='stat-value'>总金额: ¥{amount:.2f}</div>
                                    <div class='stat-label'>共计洗澡 {count} 次，平均每次 ¥{avg_amount:.2f}</div>
                                    <div class='stat-label'>按水价为 ¥0.04 /磅计算，折合共计用开水 {weight_lb:.2f} 磅，每次洗澡用开水 {avg_weight_lb:.2f} 磅</div>
                                </div>
                                """.format(
                                    count=shower_stats.get("count", 0),
                                    amount=shower_stats.get("amount", 0.0),
                                    avg_amount=shower_stats.get("avg_amount", 0.0),
                                    weight_lb=shower_stats.get("weight_lb", 0.0),
                                    avg_weight_lb=shower_stats.get("avg_weight_lb", 0.0),
                                ),
                                unsafe_allow_html=True,
                            )

                        with col2:
                            st.markdown(
                                """
                                <div class='stat-card'>
                                    <div class='stat-label'>补卡大王 💳</div>
                                    <div class='stat-value'>{count} 次</div>
                                    <div class='stat-label'>总金额: ¥{amount:.2f}</div>
                                    <div class='stat-label'>{message}</div>
                                </div>
                                """.format(
                                    count=card_stats.get("count", 0),
                                    amount=card_stats.get("amount", 0.0),
                                    message=card_stats.get("message", "校园卡补办消费"),
                                ),
                                unsafe_allow_html=True,
                            )

                    # Add this section where you want to display the plot
                    st.subheader("💰 细细细则")
                    fig = plot_merchant_spending(df_raw)
                    st.pyplot(fig)
                    plt.close()
                    st.markdown("", unsafe_allow_html=True)

                    # 4. 最逆天的记录
                    if llm_submitted:
                        st.subheader("🤡 最逆天的一餐")
                        earliest, latest = get_time_bounds(df)
                        most_expensive = get_max_cost(df)

                        st.caption("确认侧边栏中的服务商、模型和密钥后，点击按钮调用 AI 生成评论。")
                        generate_comments = st.button(
                            "开始生成", key="generate_ai_comments", type="primary",
                            use_container_width=True,
                        )
                        if generate_comments:
                            earliest_prompt = get_eat_habbit_prompt(username, earliest)
                            latest_prompt = get_eat_habbit_prompt(username, latest)
                            most_expensive_prompt = get_eat_habbit_prompt(username, most_expensive)
                            try:
                                with st.spinner("正在生成 AI 评论，请稍候..."):
                                    st.session_state['ai_comments'] = {
                                        'earliest': ask_gpt(earliest_prompt, **llm_settings),
                                        'latest': ask_gpt(latest_prompt, **llm_settings),
                                        'most_expensive': ask_gpt(most_expensive_prompt, **llm_settings),
                                    }
                            except Exception as e:
                                st.session_state.pop('ai_comments', None)
                                st.error(
                                    "❌ 调用 AI 失败，请检查侧边栏的设置并重试，这可能是由于以下原因之一：\n\n"
                                    "1. API Key 不正确或已过期（一般为 `sk-*****` 的形式）\n"
                                    "2. Base URL 配置错误\n"
                                    "3. 模型名称错误或不可用\n\n"
                                    f"错误信息: {str(e)}"
                                )

                        ai_comments = st.session_state.get('ai_comments')
                        if ai_comments:
                            col1, col2, col3 = st.columns(3)

                            with col1:
                                st.markdown(
                                    create_stat_card(
                                        "清晨觅食冠军",
                                        earliest['txdate'].strftime('%H:%M'),
                                        earliest['meraddr'],
                                        earliest['txdate'].strftime('%Y-%m-%d'),
                                        ai_comments['earliest'],
                                        "☀️"
                                    ),
                                    unsafe_allow_html=True
                                )

                            with col2:
                                st.markdown(
                                    create_stat_card(
                                        "夜宵王者",
                                        latest['txdate'].strftime('%H:%M'),
                                        latest['meraddr'],
                                        latest['txdate'].strftime('%Y-%m-%d'),
                                        ai_comments['latest'],
                                        "🌙"
                                    ),
                                    unsafe_allow_html=True
                                )

                            with col3:
                                st.markdown(
                                    create_stat_card(
                                        "土豪餐王",
                                        f"¥{most_expensive['txamt']:.2f}",
                                        most_expensive['meraddr'],
                                        most_expensive['txdate'].strftime('%Y-%m-%d %H:%M'),
                                        ai_comments['most_expensive'],
                                        "💫"
                                    ),
                                    unsafe_allow_html=True
                                )
                    

                    

                except Exception as e:
                    st.error(f"❌ 生成报告时出现错误: {str(e)}")
                    return

if __name__ == "__main__":
    main()
