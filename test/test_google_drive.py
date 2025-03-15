import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from dotenv import load_dotenv

# Load environment variables from .env file if available
load_dotenv()

def upload_image_to_drive(image_path):
    # Define the required scope
    SCOPES = ['https://www.googleapis.com/auth/drive.file']
    
    # Retrieve the service account JSON file path from environment variables
    service_account_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not service_account_file or not os.path.exists(service_account_file):
        raise FileNotFoundError("Service account JSON file not found. Set GOOGLE_APPLICATION_CREDENTIALS env variable correctly.")

    # Create credentials using the service account
    credentials = service_account.Credentials.from_service_account_file(
        service_account_file,
        scopes=SCOPES
    )
    
    # Build the Google Drive API client
    drive_service = build('drive', 'v3', credentials=credentials)
    
    # Get the shared folder ID from environment variables
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    if not folder_id:
        raise Exception("GOOGLE_DRIVE_FOLDER_ID is not set. Please set it to your shared folder's ID.")

    # Prepare the file metadata; the 'parents' field places the file in the desired folder.
    file_metadata = {
        'name': os.path.basename(image_path),
        'parents': [folder_id]
    }
    
    # Prepare the media upload; adjust the mimetype if necessary.
    media = MediaFileUpload(image_path, resumable=True)
    
    # Upload the file to Google Drive
    created_file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id'
    ).execute()
    
    print("Uploaded file. File ID:", created_file.get('id'))

if __name__ == "__main__":
    # For example, upload an image called "test_image.png" in the current directory.
    upload_image_to_drive("Joyce_20250315_0910_01.jpg")
