import os
import re
import requests

"""
we need to set up 
    GOOGLE_DRIVE_API_KEY=xyz   
in .env files

## Google Drive API Key Creation Guide

## 1. Create or Select a Project

* Open the Google Cloud Console.
* Sign in with your Google account.
* Click the Project Dropdown at the top left.
* Click New Project (or choose an existing one).
* Enter a recognizable Project Name.
* Click Create.

## 2. Enable the Google Drive API

* Open the left-hand Navigation Menu.
* Hover over APIs & Services.
* Select Library.
* Search for "Google Drive API".
* Click the Google Drive API result.
* Click the blue Enable button.

## 3. Generate Your API Key

* Open the left-hand Navigation Menu.
* Choose APIs & Services.
* Click Credentials.
* Click Create credentials at the top.
* Select API key from the dropdown.
* Copy and safely store the generated key.

## 4. Restrict Your API Key

* Click the Edit Icon next to your key.
* Scroll down to API restrictions.
* Select Restrict key.
* Choose Google Drive API from the dropdown.
* Go to Application restrictions.
* Set restrictions based on your server or website.
* Click Save.

------------------------------
"""

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

    def get_all_files_recursive(self, folder_id, path_prefix=""):
        """
        Recursively fetches all files inside a folder (and its subfolders).
        """
        all_files = []
        try:
            files = self.list_files(folder_id)
            for item in files:
                current_path = f"{path_prefix}{item['name']}"
                if item['mimeType'] == 'application/vnd.google-apps.folder':
                    all_files.extend(self.get_all_files_recursive(item['id'], path_prefix=f"{current_path}/"))
                else:
                    item['display_name'] = current_path
                    all_files.append(item)
        except Exception as e:
            print(f"Error fetching contents for folder ID {folder_id}: {e}")
        return all_files

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


def google_drive(save_directory: str = "google_download"):
    """
    Interactively prompts for Google Drive URLs, lets the user select and download files.
    Returns a list of local paths to the downloaded files.
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
            for f in files:
                f['display_name'] = f['name']
            all_files.extend(files)
        except ValueError as e:
            print(f"Skipping invalid URL '{url}': {e}")
        except Exception as e:
            print(f"Could not fetch files from '{url}': {e}")

    if not all_files:
        print("No files found in the provided locations.")
        return []

    print("\n--- Available Files & Folders ---")
    for i, item in enumerate(all_files):
        file_type = get_file_type(item['mimeType'])
        icon = "📁" if file_type == 'Folder' else "📄"
        print(f"{i + 1}: {icon} {item.get('display_name', item['name'])} ({file_type})")
    print("---------------------------------\n")

    selected_indices = []
    while True:
        choice_str = input("Enter the numbers of the items to process (e.g., 1 3 5), or 'a' for all: ")
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
            print("Invalid input. Please enter numbers separated by spaces.")

    expanded_files = []
    needs_refinement = False
    for i in selected_indices:
        item = all_files[i]
        if get_file_type(item['mimeType']) == 'Folder':
            needs_refinement = True
            sub_files = scraper.get_all_files_recursive(item['id'], path_prefix=f"{item['name']}/")
            expanded_files.extend(sub_files)
        else:
            expanded_files.append(item)

    if not expanded_files:
        print("No files found in the selection.")
        return []

    final_list_for_selection = expanded_files
    if needs_refinement:
        print("\n--- Final File Selection ---")
        tree = {}
        for item in expanded_files:
            parts = item.get('display_name', item['name']).split('/')
            current_level = tree
            for part in parts[:-1]:
                if part not in current_level:
                    current_level[part] = {}
                current_level = current_level[part]
            file_name = parts[-1]
            if file_name not in current_level or 'id' not in current_level[file_name]:
                current_level[file_name] = item
            else:
                current_level[f"{file_name}_{item['id']}"] = item

        ordered_files = []
        def print_tree(node, prefix=""):
            keys = list(node.keys())
            def sort_key(k):
                child = node[k]
                is_folder = isinstance(child, dict) and 'id' not in child
                return (not is_folder, k.lower())
            keys.sort(key=sort_key)
            for i, key in enumerate(keys):
                is_last = (i == len(keys) - 1)
                connector = "└── " if is_last else "├── "
                child = node[key]
                if isinstance(child, dict) and 'id' not in child:
                    print(f"{prefix}{connector}📁 {key}")
                    extension_prefix = "    " if is_last else "│   "
                    print_tree(child, prefix + extension_prefix)
                else:
                    ordered_files.append(child)
                    idx = len(ordered_files)
                    file_type = get_file_type(child['mimeType'])
                    print(f"{prefix}{connector}{idx}: 📄 {key} ({file_type})")
        print_tree(tree)
        print("----------------------------\n")
        final_list_for_selection = ordered_files

    downloaded_file_paths = []
    
    choice_str = input("Enter file numbers to download (e.g., 1 3 5), or 'a' for all: ")

    files_to_download_now = []
    try:
        if choice_str.lower() == 'a':
            files_to_download_now = final_list_for_selection
        else:
            cleaned_str = choice_str.replace(',', ' ')
            final_indices = [int(i) - 1 for i in cleaned_str.split()]
            if all(0 <= i < len(final_list_for_selection) for i in final_indices):
                files_to_download_now = [final_list_for_selection[i] for i in final_indices]
            else:
                print("Invalid number. Proceeding with an empty selection.")
    except ValueError:
        print("Invalid input. Proceeding with an empty selection.")

    if not files_to_download_now:
        print("No files selected for download.")
        return []

    print(f"\n--- Downloading {len(files_to_download_now)} file(s) ---")
    for file_info in files_to_download_now:
        print(f"Downloading '{file_info.get('display_name', file_info['name'])}'...")
        try:
            local_path = scraper.download_file(
                file_info['id'], 
                file_info['name'], 
                file_info['mimeType'], 
                save_directory=save_directory
            )
            if local_path not in downloaded_file_paths:
                downloaded_file_paths.append(local_path)
        except Exception as e:
            print(f"Failed to download {file_info['name']}. Error: {e}")
    print("---------------------------------")
    print("\nDownloads complete. Returning to orchestrator.")

    return downloaded_file_paths

# ==========================================
# Example Usage
# ==========================================
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    google_drive()
