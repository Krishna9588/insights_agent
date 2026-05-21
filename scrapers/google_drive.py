import os
import re
import requests


class GoogleDriveScraper:
    def __init__(self, api_key=None):
        """
        Initializes the scraper. Uses the API key from your .env file.
        """
        self.api_key = api_key or os.getenv("GOOGLE_DRIVE_API_KEY")
        if not self.api_key:
            raise ValueError("Missing GOOGLE_DRIVE_API_KEY in environment variables.")

        self.base_url = "https://www.googleapis.com/drive/v3/files"

    def extract_id(self, url):
        """Extracts the folder or file ID from a standard public Google Drive URL."""
        match = re.search(r'folders/([a-zA-Z0-9_-]+)', url)
        if match: return match.group(1)

        match = re.search(r'id=([a-zA-Z0-9_-]+)', url)
        if match: return match.group(1)

        raise ValueError("Invalid Google Drive URL. Please ensure it is a valid folder link.")

    def list_files(self, folder_id):
        """
        Fetches a list of files inside a public folder.
        """
        params = {
            'q': f"'{folder_id}' in parents and trashed=false",
            'fields': "files(id, name, mimeType)",
            'key': self.api_key
        }

        response = requests.get(self.base_url, params=params)

        if response.status_code != 200:
            raise Exception(
                f"Failed to fetch folder. Is the link set to 'Anyone with the link can view'? Error: {response.text}")

        return response.json().get('files', [])

    def download_file(self, file_id, file_name, mime_type, save_directory="google_download"):
        """
        Downloads a file. Automatically converts Google Docs to plain text.
        """
        os.makedirs(save_directory, exist_ok=True)

        if 'application/vnd.google-apps' in mime_type:
            export_mime = 'text/plain' if 'document' in mime_type else 'text/csv'
            params = {'alt': 'media', 'mimeType': export_mime, 'key': self.api_key}
            url = f"{self.base_url}/{file_id}/export"
            if 'document' in mime_type:
                file_name = f"{file_name}.txt"
        else:
            params = {'alt': 'media', 'key': self.api_key}
            url = f"{self.base_url}/{file_id}"

        response = requests.get(url, params=params, stream=True)

        if response.status_code != 200:
            raise Exception(f"Failed to download file {file_name}. Error: {response.text}")

        file_path = os.path.join(save_directory, file_name)
        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        print(f"File downloaded successfully to: {file_path}")
        return file_path


def get_file_type(mime_type):
    if mime_type == 'application/vnd.google-apps.folder':
        return 'Folder'
    elif 'image' in mime_type:
        return 'Image'
    elif 'pdf' in mime_type:
        return 'PDF'
    elif 'document' in mime_type:
        return 'Document'
    else:
        return 'File'


def google_drive():
    """
    Interactively prompts the user for Google Drive URLs, lets them select files,
    and returns the selected file details for further processing.
    """
    scraper = GoogleDriveScraper()

    urls = []
    print("Enter Google Drive folder URLs (one per line, press Enter on an empty line to finish):")
    while True:
        url = input()
        if not url:
            break
        urls.append(url)

    if not urls:
        print("No URLs provided.")
        return []

    all_files = []
    for url in urls:
        try:
            folder_id = scraper.extract_id(url)
            files = scraper.list_files(folder_id)
            all_files.extend(files)
        except ValueError as e:
            print(f"Skipping invalid URL '{url}': {e}")
        except Exception as e:
            print(f"Could not fetch files from '{url}': {e}")

    if not all_files:
        print("No files found in the provided locations.")
        return []

    print("\n--- Available Files ---")
    for i, item in enumerate(all_files):
        file_type = get_file_type(item['mimeType'])
        print(f"{i + 1}: {item['name']} ({file_type})")
    print("-------------------------\n")

    selected_files = []
    while True:
        choice_str = input("Enter the numbers of the files to process (e.g., 1, 3, 5), or 'a' for all: ")
        if choice_str.lower() == 'a':
            selected_indices = range(len(all_files))
            break
        try:
            cleaned_str = choice_str.replace(',', ' ')
            selected_indices = [int(i) - 1 for i in cleaned_str.split()]
            if all(0 <= i < len(all_files) for i in selected_indices):
                break
            else:
                print("Invalid number. Please select valid items from the list.")
        except ValueError:
            print("Invalid input. Please enter numbers separated by spaces or commas.")

    for i in selected_indices:
        if 'folder' not in get_file_type(all_files[i]['mimeType']).lower():
            selected_files.append(all_files[i])
        else:
            print(f"Skipping folder: {all_files[i]['name']}")

    print(f"\nSelected {len(selected_files)} file(s) for processing.")
    return selected_files


# ==========================================
# Example Usage
# ==========================================
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    files_to_process = google_drive()

    if files_to_process:
        print("\n--- Processing selected files ---")
        scraper = GoogleDriveScraper()
        for file_info in files_to_process:
            print(f"Downloading '{file_info['name']}'...")
            try:
                scraper.download_file(file_info['id'], file_info['name'], file_info['mimeType'])
            except Exception as e:
                print(f"Failed to download {file_info['name']}. Error: {e}")
        print("---------------------------------")
