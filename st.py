import json
import os
import sys
from datetime import date
import streamlit as st
from dotenv import load_dotenv
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
from utils.get_eat_record import get_record
from utils.process_data import process_data
from utils.prompts import get_eat_habbit_prompt
from utils.ask_gpt import ask_gpt

st.set_page_config(
    page_title="你清食堂消费总结",
    page_icon="🍜",
    layout="wide"
)

# Load environment variables
load_dotenv()

# Get TEST_MODE from environment variables
TEST_MODE = os.getenv('TEST_MODE', 'false').lower() == 'true'

# 添加自定义 CSS 样式
def load_css():
    # Resolve path so it works when packaged with PyInstaller (sys._MEIPASS)
    possible_paths = [
        os.path.join(os.getcwd(), 'utils', 'styles.css'),
        os.path.join(os.path.dirname(__file__), 'utils', 'styles.css'),
    ]
    if getattr(sys, 'frozen', False):
        possible_paths.insert(0, os.path.join(sys._MEIPASS, 'utils', 'styles.css'))

    css_text = None
    for p in possible_paths:
        try:
            with open(p, 'r', encoding='utf-8') as f:
                css_text = f.read()
                break
        except UnicodeDecodeError:
            try:
                with open(p, 'r', encoding='gbk') as f:
                    css_text = f.read()
                    break
            except Exception:
                try:
                    with open(p, 'rb') as f:
                        css_text = f.read().decode('utf-8', errors='replace')
                        break
                except Exception:
                    continue
        except FileNotFoundError:
            continue

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

def main():
    load_css()
    st.title("🍜 你清食堂消费总结")
    
    # Sidebar for configuration
    with st.sidebar:
        llm_submitted = st.toggle("启用 AI 生成评论 🤖", value= False)
        if llm_submitted:
            st.header("⚙️ LLM 设置")
            base_url = st.text_input("Base URL", value=os.getenv("BASE_URL", "https://api.deepseek.com"))
            model = st.text_input("Model", value=os.getenv("MODEL", "deepseek-chat"))
            api_key = st.text_input("API Key", value=os.getenv("API_KEY", ""), type="password")
    
    # 更新欢迎页面文案
    st.markdown("""
    
    👋 这是一个专门为你清吃货们打造的美食档案！
    """)

    # 更新用户输入区域文案
    get_data_online = st.toggle("在线获取数据", value=True, help="如果关闭，则从本地的 json 文件读取数据")
    with st.form("user_input"):
        if get_data_online:
            st.subheader("🔑 请出示你的美食证件")
            default_year = 2024 if TEST_MODE else date.today().year
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
                    value=date.today() if not TEST_MODE else date(2024, 12, 31),
                    key='query_end_date',
                )
            idserial = st.text_input("学号")
            servicehall = st.text_input("Cookie中的servicehall", help="如何获取？参考 https://github.com/SphenHe/THU-202x-Food")
            submitted = st.form_submit_button("开启美食档案 🚀")
            if submitted:
                if not idserial or not servicehall:
                    st.error("⚠️ 请填写完整信息！")
                    return
                if query_start_date > query_end_date:
                    st.error("⚠️ 查询开始日期不能晚于结束日期！")
                    return
        else:
            # 设置文件夹路径（修改为你的实际路径）
            folder_path = "./eat_records"  # 替换为你的文件夹路径
            # 获取文件夹中的所有文件
            try:
                file_list = sorted([f for f in os.listdir(folder_path) 
                            if os.path.isfile(os.path.join(folder_path, f))], reverse=True)
            except FileNotFoundError:
                st.error("❌ 文件夹不存在，请检查路径")
                file_list = []
            # 创建下拉选择框
            if file_list:
                selected_file = st.selectbox(
                    "请选择文件：",
                    file_list,
                    index=0  # 默认选中第一个文件
                )
                submitted = st.form_submit_button(f"通过{selected_file}开启美食档案 🚀")
                
                # 这里可以添加文件处理逻辑
                # file_path = os.path.join(folder_path, selected_file)
                # ...
            else:
                st.warning("⚠️ 文件夹为空或路径错误")
                st.subheader("📂 从本地文件读取数据")
                st.markdown("请确保当前目录下有名为 `log.json` 的文件，且格式正确。")
                submitted = st.form_submit_button("从本地文件读取数据 📂")
                idserial = None
                servicehall = None
                

        if TEST_MODE:
            idserial = "2025012345"
            servicehall = "1234567890"
            submitted = 'report_data' not in st.session_state

        # After the form submission check
        if submitted:

            # First spinner for data fetching
            with st.spinner("正在获取数据，请稍候..."):
                try:
                    data = None
                    if get_data_online:
                        data = (
                            get_record(
                                servicehall,
                                idserial,
                                query_start_date,
                                query_end_date,
                            )
                            if not TEST_MODE
                            else json.load(open("log.json", "r", encoding='utf-8'))
                        )
                    else:
                        data = json.load(open(os.path.join(folder_path, selected_file), "r", encoding='utf-8'))
                    df_raw, df = process_data(data)
                    if df_raw.empty:
                        st.session_state.pop('report_data', None)
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
                    st.success("✅ 数据获取成功")
                except Exception as e:
                    st.error(f"❌ 数据获取失败，请检查学号和 Cookie 是否正确，并确认 Cookies 是在本电脑上获取的（而不是来自其他同学的设备）")
                    return

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
                        
                        earliest_prompt = get_eat_habbit_prompt(username, earliest)
                        latest_prompt = get_eat_habbit_prompt(username, latest)
                        most_expensive_prompt = get_eat_habbit_prompt(username, most_expensive)
                        
                        try:
                            earliest_comment = ask_gpt(earliest_prompt, model=model, api_key=api_key, base_url=base_url)
                            latest_comment = ask_gpt(latest_prompt, model=model, api_key=api_key, base_url=base_url)
                            most_expensive_comment = ask_gpt(most_expensive_prompt, model=model, api_key=api_key, base_url=base_url)
                        except Exception as e:
                            st.error(
                                "❌ 调用 AI 失败，请检查侧边栏的设置并重试，这可能是由于以下原因之一：\n\n"
                                "1. API Key 不正确或已过期（一般为 `sk-*****` 的形式）\n"
                                "2. Base URL 配置错误（一般为 `https://api.deepseek.com` 的形式）\n"
                                "3. 模型名称错误或不可用（一般为 `deepseek-chat` 的形式）\n\n"
                                f"错误信息: {str(e)}"
                            )
                            earliest_comment = "无法生成评论"
                            latest_comment = "无法生成评论"
                            most_expensive_comment = "无法生成评论"

                        col1, col2, col3 = st.columns(3)
                        
                        with col1:
                            st.markdown(
                                create_stat_card(
                                    "清晨觅食冠军", 
                                    earliest['txdate'].strftime('%H:%M'),
                                    earliest['meraddr'],
                                    earliest['txdate'].strftime('%Y-%m-%d'),
                                    earliest_comment,
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
                                    latest_comment,
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
                                    most_expensive_comment,
                                    "💫"
                                ),
                                unsafe_allow_html=True
                            )
                    

                    

                except Exception as e:
                    st.error(f"❌ 生成报告时出现错误: {str(e)}")
                    return

if __name__ == "__main__":
    main()
