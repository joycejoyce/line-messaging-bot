import os
import io
import uuid
import re
from datetime import datetime
from flask import Flask, request, abort
from dotenv import load_dotenv
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, ImageMessage, VideoMessage,
    TemplateSendMessage, ButtonsTemplate, PostbackAction, PostbackEvent
)

# 載入專案根目錄下的 .env 檔
load_dotenv()

# 從環境變數讀取 LINE 的密鑰資訊
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")

if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
    raise Exception("請確認 .env 中已設定 LINE_CHANNEL_SECRET 與 LINE_CHANNEL_ACCESS_TOKEN")

# 初始化 LINE Bot API 與 WebhookHandler
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 定義儲存檔案的基底目錄
OUTPUT_DIR = "./output"
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

# 全域字典，記錄每個用戶在同一分鐘內的影像與影片流水號
image_counters = {}
video_counters = {}

def sanitize_filename(name):
    """移除檔名中不合法的字元"""
    return re.sub(r'[^A-Za-z0-9_\-]+', '', name)

def get_daily_folder(dt):
    """取得當日的資料夾路徑，若不存在則建立"""
    folder = os.path.join(OUTPUT_DIR, dt.strftime("%Y-%m-%d"))
    if not os.path.exists(folder):
        os.makedirs(folder)
    return folder

def append_text_message(dt, display_name, text):
    """將文字訊息以特定格式附加到當日的 messages.txt 檔案中"""
    folder = get_daily_folder(dt)
    file_path = os.path.join(folder, f"{dt.strftime('%Y-%m-%d')}_msg.txt")
    time_str = dt.strftime("%H:%M")
    line_str = f"{time_str} | {display_name} | {text}\n"
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(line_str)
    print(f"已追加文字訊息至 {file_path}")

def save_to_local(file_stream, filename, folder):
    """
    將 BytesIO 物件存檔到指定資料夾
    """
    if not os.path.exists(folder):
        os.makedirs(folder)
    filepath = os.path.join(folder, filename)
    with open(filepath, "wb") as f:
        f.write(file_stream.getvalue())
    return filepath

# 建立 Flask Webhook 伺服器
app = Flask(__name__)

@app.route("/callback", methods=["POST"])
def callback():
    # 取得 HTTP Header 中的 X-Line-Signature
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    print("收到 LINE 請求，body:", body)
    try:
        # 驗證並處理 LINE Webhook 事件
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("簽名驗證失敗")
        abort(400)
    return "OK", 200

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    text = event.message.text.strip()
    user_id = event.source.user_id
    # 以 event.timestamp (毫秒) 轉換為 datetime 物件
    dt = datetime.fromtimestamp(event.timestamp / 1000)

    # 取得用戶名稱 (必須為好友)
    try:
        profile = line_bot_api.get_profile(user_id)
        display_name = profile.display_name
    except Exception as e:
        display_name = "Unknown"
        print(f"Error fetching profile for user {user_id}: {e}")

    # 指令：建立相簿 (原本功能保留，可選)
    if text == "建立相簿":
        reply_text = "請輸入相簿資料，格式：\n建立相簿: YYYY-MM-DD, 相簿名稱\n例如：建立相簿: 2023-03-12, 我的假期"
        line_bot_api.reply_message(event.reply_token, TextMessage(text=reply_text))
        return

    if text.startswith("建立相簿:"):
        details = text[len("建立相簿:"):].strip()
        if "," in details:
            date_part, album_name = details.split(",", 1)
            date_part = date_part.strip()
            album_name = album_name.strip()
            try:
                datetime.strptime(date_part, "%Y-%m-%d")
            except ValueError:
                line_bot_api.reply_message(event.reply_token, TextMessage(text="日期格式不正確，請使用 YYYY-MM-DD 格式"))
                return
            full_album_name = f"{date_part}_{album_name}"
            # 這邊保留原本的相簿名稱記錄方式（可進一步擴充）
            # user_albums[user_id] = full_album_name
            reply_text = f"相簿已建立：{full_album_name}"
            line_bot_api.reply_message(event.reply_token, TextMessage(text=reply_text))
            print(f"使用者 {user_id} 建立了相簿: {full_album_name}")
        else:
            line_bot_api.reply_message(event.reply_token, TextMessage(text="請使用正確格式，範例：建立相簿: 2023-03-12, 我的假期"))
        return

    # 文字訊息處理：將訊息追加到當日的 messages.txt 檔中
    append_text_message(dt, display_name, text)

@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    message_content = line_bot_api.get_message_content(event.message.id)
    user_id = event.source.user_id
    dt = datetime.fromtimestamp(event.timestamp / 1000)

    # 取得每日資料夾
    folder = get_daily_folder(dt)
    date_str = dt.strftime("%Y%m%d")   # 例如：20250308
    time_str = dt.strftime("%H%M")       # 例如：2001

    # 建立流水號 key
    key = (user_id, date_str, time_str)
    sequence = image_counters.get(key, 0) + 1
    image_counters[key] = sequence

    try:
        profile = line_bot_api.get_profile(user_id)
        display_name = sanitize_filename(profile.display_name)
    except Exception as e:
        display_name = "Unknown"

    filename = f"{display_name}_{date_str}_{time_str}_{sequence:02d}.jpg"
    file_stream = io.BytesIO(message_content.content)
    saved_path = save_to_local(file_stream, filename, folder)
    print(f"已儲存圖片訊息： {saved_path}")

@handler.add(MessageEvent, message=VideoMessage)
def handle_video_message(event):
    message_content = line_bot_api.get_message_content(event.message.id)
    user_id = event.source.user_id
    dt = datetime.fromtimestamp(event.timestamp / 1000)

    folder = get_daily_folder(dt)
    date_str = dt.strftime("%Y%m%d")
    time_str = dt.strftime("%H%M")

    key = (user_id, date_str, time_str)
    sequence = video_counters.get(key, 0) + 1
    video_counters[key] = sequence

    try:
        profile = line_bot_api.get_profile(user_id)
        display_name = sanitize_filename(profile.display_name)
    except Exception as e:
        display_name = "Unknown"

    filename = f"{display_name}_{date_str}_{time_str}_{sequence:02d}.mp4"
    file_stream = io.BytesIO(message_content.content)
    saved_path = save_to_local(file_stream, filename, folder)
    print(f"已儲存影片訊息： {saved_path}")

# 若你仍希望提供按鈕互動，以下函式可以發送 Template Message
def send_create_album_template(reply_token):
    template_message = TemplateSendMessage(
        alt_text="建立相簿",
        template=ButtonsTemplate(
            title="建立相簿",
            text="請點選下方按鈕建立相簿 (預設為今日日期與「我的相簿」)",
            actions=[
                PostbackAction(
                    label="建立相簿",
                    data=f"action=create_album&album_date={datetime.now().strftime('%Y-%m-%d')}&album_name=我的相簿"
                )
            ]
        )
    )
    line_bot_api.reply_message(reply_token, template_message)

@handler.add(PostbackEvent)
def handle_postback(event):
    data = event.postback.data
    params = dict(item.split("=") for item in data.split("&"))
    if params.get("action") == "create_album":
        album_date = params.get("album_date", datetime.now().strftime("%Y-%m-%d"))
        album_name = params.get("album_name", "default")
        full_album_name = f"{album_date}_{album_name}"
        # 此處可記錄相簿名稱等資料
        print(f"使用者 {event.source.user_id} 建立了相簿: {full_album_name}")
        line_bot_api.reply_message(
            event.reply_token,
            TextMessage(text=f"相簿已建立：{full_album_name}")
        )

if __name__ == "__main__":
    app.run(port=5000)
