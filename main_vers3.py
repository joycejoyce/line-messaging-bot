import os
import io
import uuid
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

def save_to_local(file_stream, filename, folder=OUTPUT_DIR):
    """
    將 BytesIO 物件存檔到指定資料夾
    """
    if not os.path.exists(folder):
        os.makedirs(folder)
    filepath = os.path.join(folder, filename)
    with open(filepath, "wb") as f:
        f.write(file_stream.getvalue())
    return filepath

# 用來記錄每個使用者的相簿名稱 (用戶ID => "日期_相簿名稱")
user_albums = {}

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
    timestamp = event.timestamp

    # 如果收到「建立相簿」則提醒使用者輸入完整格式
    if text == "建立相簿":
        reply_text = "請輸入相簿資料，格式：\n建立相簿: YYYY-MM-DD, 相簿名稱\n例如：建立相簿: 2023-03-12, 我的假期"
        line_bot_api.reply_message(event.reply_token, TextMessage(text=reply_text))
        return

    # 如果文字以「建立相簿:」開頭，則解析日期與相簿名稱
    if text.startswith("建立相簿:"):
        details = text[len("建立相簿:"):].strip()
        if "," in details:
            date_part, album_name = details.split(",", 1)
            date_part = date_part.strip()
            album_name = album_name.strip()
            # 驗證日期格式 (YYYY-MM-DD)
            try:
                datetime.strptime(date_part, "%Y-%m-%d")
            except ValueError:
                line_bot_api.reply_message(event.reply_token, TextMessage(text="日期格式不正確，請使用 YYYY-MM-DD 格式"))
                return
            full_album_name = f"{date_part}_{album_name}"
            user_albums[user_id] = full_album_name
            reply_text = f"相簿已建立：{full_album_name}"
            line_bot_api.reply_message(event.reply_token, TextMessage(text=reply_text))
            print(f"使用者 {user_id} 建立了相簿: {full_album_name}")
        else:
            line_bot_api.reply_message(event.reply_token, TextMessage(text="請使用正確格式，範例：建立相簿: 2023-03-12, 我的假期"))
        return

    # 一般文字訊息存檔 (非指令)
    filename = f"text_{user_id}_{timestamp}_{uuid.uuid4().hex}.txt"
    file_stream = io.BytesIO(text.encode("utf-8"))
    saved_path = save_to_local(file_stream, filename)
    print(f"已儲存文字訊息： {saved_path}")

# 若你仍希望提供按鈕互動，以下函式可以發送 Template Message
def send_create_album_template(reply_token):
    """
    傳送 Template Message，讓使用者點選按鈕建立相簿
    此方式僅能傳送固定參數，如需自訂日期與相簿名稱，建議使用文字格式
    """
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

# 處理 Postback 事件 (若使用者點選按鈕建立相簿)
@handler.add(PostbackEvent)
def handle_postback(event):
    data = event.postback.data  # 格式: "action=create_album&album_date=YYYY-MM-DD&album_name=我的相簿"
    params = dict(item.split("=") for item in data.split("&"))
    if params.get("action") == "create_album":
        album_date = params.get("album_date", datetime.now().strftime("%Y-%m-%d"))
        album_name = params.get("album_name", "default")
        full_album_name = f"{album_date}_{album_name}"
        user_id = event.source.user_id
        user_albums[user_id] = full_album_name
        print(f"使用者 {user_id} 建立了相簿: {full_album_name}")
        line_bot_api.reply_message(
            event.reply_token,
            TextMessage(text=f"相簿已建立：{full_album_name}")
        )

# 處理圖片訊息：根據使用者的相簿名稱存放到對應資料夾
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    message_content = line_bot_api.get_message_content(event.message.id)
    user_id = event.source.user_id
    timestamp = event.timestamp

    # 根據 user_albums 決定存放的資料夾，預設為 "default"
    album_name = user_albums.get(user_id, "default")
    album_folder = os.path.join(OUTPUT_DIR, album_name)
    
    filename = f"image_{user_id}_{timestamp}_{uuid.uuid4().hex}.jpg"
    file_stream = io.BytesIO(message_content.content)
    saved_path = save_to_local(file_stream, filename, folder=album_folder)
    print(f"已儲存圖片訊息至相簿 {album_name}： {saved_path}")

# 處理影片訊息 (存檔邏輯同圖片)
@handler.add(MessageEvent, message=VideoMessage)
def handle_video_message(event):
    message_content = line_bot_api.get_message_content(event.message.id)
    user_id = event.source.user_id
    timestamp = event.timestamp

    album_name = user_albums.get(user_id, "default")
    album_folder = os.path.join(OUTPUT_DIR, album_name)
    
    filename = f"video_{user_id}_{timestamp}_{uuid.uuid4().hex}.mp4"
    file_stream = io.BytesIO(message_content.content)
    saved_path = save_to_local(file_stream, filename, folder=album_folder)
    print(f"已儲存影片訊息至相簿 {album_name}： {saved_path}")

if __name__ == "__main__":
    # 測試時建議使用 ngrok 將本地端口暴露給外網
    app.run(port=5000)
