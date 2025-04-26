import os
import io
import re
import logging
import json
from logging.handlers import RotatingFileHandler
from datetime import datetime
from flask import Flask, request, abort
from dotenv import load_dotenv
import psycopg2

# LINE Bot SDK
from linebot.v3.exceptions import InvalidSignatureError, LineBotApiError
from linebot.v3.messaging import MessagingApi
from linebot.v3.webhook import WebhookHandler
from linebot.v3.models import MessageEvent, TextMessage, ImageMessage, VideoMessage, PostbackEvent, TextSendMessage

# Google Drive API imports
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ===================== Logging Setup =====================
LOG_FILE = 'app.log'
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

rot_handler = RotatingFileHandler(LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8')
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
rot_handler.setFormatter(formatter)
logger.addHandler(rot_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# ===================== Load & Validate Environment Variables =====================
load_dotenv()

# LINE credentials
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
# Database credentials
DB_HOST = os.getenv("PGHOST")
DB_PORT = os.getenv("PGPORT")
DB_NAME = os.getenv("PGDATABASE")
DB_USER = os.getenv("PGUSER")
DB_PASSWORD = os.getenv("PGPASSWORD")
# Google Drive credentials
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
# Application Port
PORT = os.getenv("PORT")

if not LINE_CHANNEL_SECRET or not LINE_CHANNEL_ACCESS_TOKEN:
    raise Exception("Please set LINE_CHANNEL_SECRET and LINE_CHANNEL_ACCESS_TOKEN in your environment.")
if not DB_HOST or not DB_PORT or not DB_NAME or not DB_USER or not DB_PASSWORD:
    raise Exception("Please set DB_HOST, DB_PORT, DB_NAME, DB_USER, and DB_PASSWORD in your environment.")
if not GOOGLE_DRIVE_FOLDER_ID:
    raise Exception("Please set GOOGLE_DRIVE_FOLDER_ID in your environment.")
if not PORT:
    raise Exception("Please set PORT in your environment.")

# ===================== Initialize LINE Bot API =====================
line_bot_api = MessagingApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ===================== Local Backup Setup =====================
OUTPUT_DIR = "./output"
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

# Load user mapping from the JSON file
def load_user_mapping():
    user_mapping_json = os.getenv("USER_MAPPING_JSON")
    if not user_mapping_json:
        raise Exception("USER_MAPPING_JSON environment variable is not set.")
    return json.loads(user_mapping_json)

# Load the mapping at the start of the application
USER_MAPPING = load_user_mapping()

def get_display_name(user_id):
    """Get the display name for a given user_id from the mapping."""
    return USER_MAPPING.get(user_id, "Unknown")
    
def sanitize_filename(name):
    """Remove illegal characters from a filename."""
    return re.sub(r'[^A-Za-z0-9_\-]+', '', name)

def get_daily_folder(dt):
    """Return a local folder for the given day (YYYY-MM-DD); create if not exists."""
    folder = os.path.join(OUTPUT_DIR, dt.strftime("%Y-%m-%d"))
    if not os.path.exists(folder):
        os.makedirs(folder)
    return folder

def append_text_message(dt, display_name, text):
    """Append a text message to a local backup file."""
    folder = get_daily_folder(dt)
    file_path = os.path.join(folder, f"{dt.strftime('%Y-%m-%d')}_msg.txt")
    time_str = dt.strftime("%H:%M")
    line_str = f"{time_str} | {display_name} | {text}\n"
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(line_str)
    logger.info(f"Appended text message to {file_path}")

def save_to_local(file_stream, filename, folder):
    """Save a BytesIO stream to the specified local folder."""
    if not os.path.exists(folder):
        os.makedirs(folder)
    filepath = os.path.join(folder, filename)
    with open(filepath, "wb") as f:
        f.write(file_stream.getvalue())
    return filepath

# ===================== Database Functions =====================
def insert_text_message_to_db(dt, user_id, display_name, text):
    """
    Insert a text message into the 'messages' table.
    Expected columns: id, user_id, display_name, message_text, created_at.
    """
    try:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
        )
        cur = conn.cursor()
        insert_sql = """
            INSERT INTO messages (user_id, display_name, message_text, created_at)
            VALUES (%s, %s, %s, %s)
            RETURNING id;
        """
        cur.execute(insert_sql, (user_id, display_name, text, dt))
        new_id = cur.fetchone()[0]
        conn.commit()
        logger.info(f"Inserted text message into DB with id: {new_id}")
    except Exception as e:
        logger.error(f"Error inserting message into DB: {e}")
    finally:
        if 'cur' in locals():
            cur.close()
        if 'conn' in locals():
            conn.close()

# ===================== Google Drive Helper Functions =====================
def get_or_create_subfolder(drive_service, parent_id, folder_name):
    """
    Retrieve or create a subfolder with the given name under the specified parent folder.
    """
    query = (
        "mimeType = 'application/vnd.google-apps.folder' and "
        f"name = '{folder_name}' and '{parent_id}' in parents and trashed = false"
    )
    results = drive_service.files().list(q=query, spaces='drive', fields="files(id, name)").execute()
    items = results.get('files', [])
    if items:
        folder_id = items[0]['id']
        logger.info(f"Found existing subfolder '{folder_name}' with ID: {folder_id}")
        return folder_id
    else:
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_id]
        }
        folder = drive_service.files().create(body=file_metadata, fields='id').execute()
        folder_id = folder.get('id')
        logger.info(f"Created new subfolder '{folder_name}' with ID: {folder_id}")
        return folder_id

def upload_image_to_drive(file_stream, filename, day_folder):
    """
    Upload an image (from a BytesIO stream) to Google Drive under a daily subfolder.
    Duplicate checking is done based on the filename (which includes the LINE message ID).
    """
    SCOPES = ['https://www.googleapis.com/auth/drive.file']
    credentials = get_google_credentials()
    drive_service = build('drive', 'v3', credentials=credentials)
    
    # Get (or create) the daily subfolder (e.g., "2025-03-15")
    subfolder_id = get_or_create_subfolder(drive_service, GOOGLE_DRIVE_FOLDER_ID, day_folder)
    
    # Check if the file already exists in this subfolder
    query = f"name = '{filename}' and '{subfolder_id}' in parents and trashed = false"
    results = drive_service.files().list(q=query, spaces='drive', fields="files(id)").execute()
    items = results.get('files', [])
    if items:
        logger.info(f"File {filename} already exists in Drive. Skipping upload.")
        return items[0]['id']
    
    file_metadata = {'name': filename, 'parents': [subfolder_id]}
    file_stream.seek(0)
    media = MediaIoBaseUpload(file_stream, mimetype='image/jpeg', resumable=True)
    
    try:
        created_file = drive_service.files().create(
            body=file_metadata, media_body=media, fields='id'
        ).execute()
        file_id = created_file.get('id')
        logger.info(f"Uploaded image to Drive. File ID: {file_id}")
        return file_id
    except Exception as e:
        logger.error(f"Error uploading image to Drive: {e}")
        return None

def upload_video_to_drive(file_stream, filename, day_folder):
    """
    Upload a video (from a BytesIO stream) to Google Drive under a daily subfolder.
    Duplicate checking is done based on the filename (which includes the LINE message ID).
    """
    SCOPES = ['https://www.googleapis.com/auth/drive.file']
    credentials = get_google_credentials()
    drive_service = build('drive', 'v3', credentials=credentials)
    
    # Get (or create) the daily subfolder (e.g., "2025-03-15")
    subfolder_id = get_or_create_subfolder(drive_service, GOOGLE_DRIVE_FOLDER_ID, day_folder)
    
    # Check if the file already exists in this subfolder
    query = f"name = '{filename}' and '{subfolder_id}' in parents and trashed = false"
    results = drive_service.files().list(q=query, spaces='drive', fields="files(id)").execute()
    items = results.get('files', [])
    if items:
        logger.info(f"File {filename} already exists in Drive. Skipping upload.")
        return items[0]['id']
    
    file_metadata = {'name': filename, 'parents': [subfolder_id]}
    file_stream.seek(0)
    media = MediaIoBaseUpload(file_stream, mimetype='video/mp4', resumable=True)
    
    try:
        created_file = drive_service.files().create(
            body=file_metadata, media_body=media, fields='id'
        ).execute()
        file_id = created_file.get('id')
        logger.info(f"Uploaded video to Drive. File ID: {file_id}")
        return file_id
    except Exception as e:
        logger.error(f"Error uploading video to Drive: {e}")
        return None

# ===================== Global Duplicate Tracking =====================
# Use global sets to track processed message IDs for images and videos.
processed_image_ids = set()
processed_video_ids = set()
# Global dictionary for video sequencing if needed.
video_counters = {}

# ===================== Flask App & Webhook Handlers =====================
app = Flask(__name__)

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)
    logger.info(f"Received LINE request, body: {body}")
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("Signature validation failed")
        abort(400)
    return "OK", 200

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    text = event.message.text.strip()
    user_id = event.source.user_id
    dt = datetime.fromtimestamp(event.timestamp / 1000)
    logger.info(f"Received text message from user {user_id}: {text}")
    
    # try:
    #     profile = line_bot_api.get_profile(user_id)
    #     display_name = profile.display_name
    # except LineBotApiError as e:
    #     display_name = "Unknown"
    #     logger.error(f"Error fetching profile for user {user_id}: {e}")
    display_name = get_display_name(user_id)
    
    if text == "建立相簿":
        reply_text = ("請輸入相簿資料，格式：\n"
                      "建立相簿: YYYY-MM-DD, 相簿名稱\n"
                      "例如：建立相簿: 2023-03-12, 我的假期")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
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
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="日期格式不正確，請使用 YYYY-MM-DD 格式"))
                return
            full_album_name = f"{date_part}_{album_name}"
            reply_text = f"相簿已建立：{full_album_name}"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
            logger.info(f"User {user_id} created album: {full_album_name}")
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請使用正確格式，範例：建立相簿: 2023-03-12, 我的假期"))
        return
    
    append_text_message(dt, display_name, text)
    insert_text_message_to_db(dt, user_id, display_name, text)

@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    # Check duplicate using message ID
    if event.message.id in processed_image_ids:
        logger.info(f"Image messageId={event.message.id} already processed, skipping upload.")
        return
    processed_image_ids.add(event.message.id)
    
    message_content = line_bot_api.get_message_content(event.message.id)
    user_id = event.source.user_id
    dt = datetime.fromtimestamp(event.timestamp / 1000)
    
    try:
        profile = line_bot_api.get_profile(user_id)
        display_name = sanitize_filename(profile.display_name)
    except LineBotApiError as e:
        display_name = "Unknown"
        logger.error(f"Error fetching profile for user {user_id}: {e}")
    
    date_str = dt.strftime("%Y%m%d")
    time_str = dt.strftime("%H%M")
    # Filename includes message ID for uniqueness
    filename = f"{display_name}_{date_str}_{time_str}_{event.message.id}.jpg"
    file_stream = io.BytesIO(message_content.content)
    
    # Use daily subfolder (e.g., "2025-03-15")
    day_folder = dt.strftime("%Y-%m-%d")
    file_id = upload_image_to_drive(file_stream, filename, day_folder)
    if file_id:
        logger.info(f"Image uploaded to Drive with File ID: {file_id}")
    else:
        logger.error("Failed to upload image to Drive.")

@handler.add(MessageEvent, message=VideoMessage)
def handle_video_message(event):
    global video_counters
    user_id = event.source.user_id
    message_id = event.message.id
    if message_id in processed_video_ids:
        logger.info(f"Video messageId={message_id} already processed, skipping upload.")
        return
    processed_video_ids.add(message_id)
    
    message_content = line_bot_api.get_message_content(message_id)
    dt = datetime.fromtimestamp(event.timestamp / 1000)
    date_str = dt.strftime("%Y%m%d")
    time_str = dt.strftime("%H%M")
    key = (user_id, date_str, time_str)
    sequence = video_counters.get(key, 0) + 1
    video_counters[key] = sequence
    
    try:
        profile = line_bot_api.get_profile(user_id)
        display_name = sanitize_filename(profile.display_name)
    except LineBotApiError as e:
        display_name = "Unknown"
        logger.error(f"Error fetching profile for user {user_id}: {e}")
    
    # Construct filename using message ID for uniqueness
    filename = f"{display_name}_{date_str}_{time_str}_{event.message.id}.mp4"
    file_stream = io.BytesIO(message_content.content)
    # Use daily subfolder (same as for images)
    day_folder = dt.strftime("%Y-%m-%d")
    file_id = upload_video_to_drive(file_stream, filename, day_folder)
    if file_id:
        logger.info(f"Video uploaded to Drive with File ID: {file_id}")
    else:
        logger.error("Failed to upload video to Drive.")

@handler.add(PostbackEvent)
def handle_postback(event):
    data = event.postback.data
    params = dict(item.split("=") for item in data.split("&"))
    if params.get("action") == "create_album":
        album_date = params.get("album_date", datetime.now().strftime("%Y-%m-%d"))
        album_name = params.get("album_name", "default")
        full_album_name = f"{album_date}_{album_name}"
        logger.info(f"User {event.source.user_id} created album: {full_album_name}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"相簿已建立：{full_album_name}"))

def upload_video_to_drive(file_stream, filename, day_folder):
    """
    Upload a video (from a BytesIO stream) to Google Drive under a daily subfolder.
    Duplicate checking is done based on the filename.
    """
    SCOPES = ['https://www.googleapis.com/auth/drive.file']
    credentials = get_google_credentials()
    drive_service = build('drive', 'v3', credentials=credentials)
    
    # Get (or create) the daily subfolder
    subfolder_id = get_or_create_subfolder(drive_service, GOOGLE_DRIVE_FOLDER_ID, day_folder)
    
    # Check for duplicate file in the subfolder
    query = f"name = '{filename}' and '{subfolder_id}' in parents and trashed = false"
    results = drive_service.files().list(q=query, spaces='drive', fields="files(id)").execute()
    items = results.get('files', [])
    if items:
        logger.info(f"File {filename} already exists in Drive. Skipping upload.")
        return items[0]['id']
    
    file_metadata = {'name': filename, 'parents': [subfolder_id]}
    file_stream.seek(0)
    media = MediaIoBaseUpload(file_stream, mimetype='video/mp4', resumable=True)
    
    try:
        created_file = drive_service.files().create(
            body=file_metadata, media_body=media, fields='id'
        ).execute()
        file_id = created_file.get('id')
        logger.info(f"Uploaded video to Drive. File ID: {file_id}")
        return file_id
    except Exception as e:
        logger.error(f"Error uploading video to Drive: {e}")
        return None
    
def get_google_credentials():
    # Try to read from the environment variable first
    cred_json = os.getenv("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if cred_json:
        credentials_info = json.loads(cred_json)
        return service_account.Credentials.from_service_account_info(credentials_info)
    else:
        # Fallback to reading from a local file
        return service_account.Credentials.from_service_account_file("C:/MyProjects/line-messaging-bot/keys/linebot-google-storage-key.json")
    
def init_db():
    """檢查並建立資料表（若不存在的話）。"""
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        cur = conn.cursor()
        create_table_sql = """
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL,
                display_name VARCHAR(255),
                message_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """
        cur.execute(create_table_sql)
        conn.commit()
        logger.info("資料表 'messages' 已初始化（若不存在則已建立）。")
    except Exception as e:
        logger.error(f"初始化資料表時發生錯誤: {e}")
    finally:
        if 'cur' in locals():
            cur.close()
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    init_db()
    port = int(PORT)
    app.run(host="0.0.0.0", port=port)
