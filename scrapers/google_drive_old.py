import os
import io
import re
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload


class GoogleDriveScraper:
    def __init__(self, credentials_path="google_credentials.json"):
        """
        Initializes the Google Drive API client using a Service Account.
        """
        self.scopes = ['https://www.googleapis.com/auth/drive.readonly']
        self.credentials_path = credentials_path
        self.service = self._authenticate()

    def _authenticate(self):
        """Authenticates and returns the Drive API service."""
        if not os.path.exists(self.credentials_path):
            raise FileNotFoundError(f"Missing Service Account file: {self.credentials_path}")

        creds = service_account.Credentials.from_service_account_file(
            self.credentials_path, scopes=self.scopes
        )
        return build('drive', 'v3', credentials=creds)

    def extract_folder_id(self, url):
        """
        Extracts the folder ID from a standard Google Drive URL.
        """
        match = re.search(r'folders/([a-zA-Z0-9_-]+)', url)
        if match:
            return match.group(1)

        # Handle alternate ID format
        match = re.search(r'id=([a-zA-Z0-9_-]+)', url)
        if match:
            return match.group(1)

        raise ValueError("Invalid Google Drive Folder URL")

    def list_files(self, folder_url):
        """
        Scans a Drive folder and returns a list of available files.
        This is used by the frontend to show the user what they can select.
        """
        folder_id = self.extract_folder_id(folder_url)
        query = f"'{folder_id}' in parents and trashed=false"

        results = self.service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType)",
            pageSize=100
        ).execute()

        files = results.get('files', [])
        return files

    def download_file(self, file_id, file_name, save_directory="temp_downloads"):
        """
        Downloads a specific file by its ID.
        Auto-exports Google Docs to plain text.
        """
        if not os.path.exists(save_directory):
            os.makedirs(save_directory)

        file_metadata = self.service.files().get(fileId=file_id).execute()
        mime_type = file_metadata.get('mimeType')

        # If it's a native Google Doc, export it as text
        if 'application/vnd.google-apps.document' in mime_type:
            request = self.service.files().export_media(
                fileId=file_id,
                mimeType='text/plain'
            )
            file_name = f"{file_name}.txt"
        else:
            # For PDFs, audio, or standard files, download normally
            request = self.service.files().get_media(fileId=file_id)

        file_path = os.path.join(save_directory, file_name)

        fh = io.FileIO(file_path, 'wb')
        downloader = MediaIoBaseDownload(fh, request)

        done = False
        while not done:
            status, done = downloader.next_chunk()
            # In a production async environment, you could log status.progress() here

        return file_path


# ==========================================
# Example Usage (For testing locally)
# ==========================================
if __name__ == "__main__":
    # 1. Initialize the scraper
    # scraper = GoogleDriveScraper()

    # 2. Provide a folder URL from your frontend config
    # test_url = "https://drive.google.com/drive/folders/YOUR_FOLDER_ID_HERE"

    # 3. List the files
    # print("Scanning folder...")
    # files = scraper.list_files(test_url)
    # for f in files:
    #     print(f"- {f['name']} (ID: {f['id']})")
    pass