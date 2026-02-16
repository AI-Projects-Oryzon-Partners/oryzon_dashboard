import os
import pickle
import mimetypes
import json
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request

# Full access scope for uploading files
SCOPES = ['https://www.googleapis.com/auth/drive']

# Mapping file to store local path -> Google Drive file ID
DRIVE_MAPPING_FILE = 'drive_file_mapping.json'

# Local folder to upload
RAG_DATA_FOLDER = 'RAG DATA'

# Optional: Set a parent folder ID in Google Drive (leave None to upload to root)
GOOGLE_DRIVE_PARENT_FOLDER_ID = None


def authenticate():
    """Authenticate with Google Drive API and return service object."""
    creds = None
    token_file = 'token_upload.pickle'
    
    if os.path.exists(token_file):
        with open(token_file, 'rb') as token:
            creds = pickle.load(token)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=8050)
        with open(token_file, 'wb') as token:
            pickle.dump(creds, token)
    
    service = build('drive', 'v3', credentials=creds)
    return service


def create_folder(service, folder_name, parent_id=None):
    """Create a folder in Google Drive and return its ID."""
    file_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    if parent_id:
        file_metadata['parents'] = [parent_id]
    
    try:
        folder = service.files().create(
            body=file_metadata,
            fields='id, name'
        ).execute()
        print(f"📁 Created folder: {folder_name} (ID: {folder.get('id')})")
        return folder.get('id')
    except HttpError as error:
        print(f"❌ Error creating folder {folder_name}: {error}")
        return None


def upload_file(service, file_path, parent_id=None, file_mapping=None):
    """Upload a single file to Google Drive."""
    file_name = os.path.basename(file_path)
    
    # Detect MIME type
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type is None:
        mime_type = 'application/octet-stream'
    
    file_metadata = {'name': file_name}
    if parent_id:
        file_metadata['parents'] = [parent_id]
    
    try:
        media = MediaFileUpload(
            file_path,
            mimetype=mime_type,
            resumable=True
        )
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, name'
        ).execute()
        print(f"  ✅ Uploaded: {file_name}")
        
        file_id = file.get('id')
        
        # Store mapping if provided
        if file_mapping is not None and file_id:
            # Create relative path from RAG DATA folder
            rel_path = os.path.relpath(file_path, RAG_DATA_FOLDER)
            file_mapping[rel_path] = {
                'file_id': file_id,
                'drive_link': f'https://drive.google.com/file/d/{file_id}/view'
            }
        
        return file_id
    except HttpError as error:
        print(f"  ❌ Error uploading {file_name}: {error}")
        return None


def upload_folder_recursive(service, local_folder_path, parent_id=None, file_mapping=None):
    """Recursively upload a folder and its contents to Google Drive."""
    folder_name = os.path.basename(local_folder_path)
    
    # Create the folder in Google Drive
    folder_id = create_folder(service, folder_name, parent_id)
    if not folder_id:
        return None
    
    # Track upload statistics
    stats = {'folders': 1, 'files': 0, 'errors': 0}
    
    # Iterate through folder contents
    try:
        items = os.listdir(local_folder_path)
    except PermissionError as e:
        print(f"  ⚠️ Permission denied: {local_folder_path}")
        stats['errors'] += 1
        return stats
    
    for item in items:
        item_path = os.path.join(local_folder_path, item)
        
        if os.path.isdir(item_path):
            # Recursively upload subfolder
            sub_stats = upload_folder_recursive(service, item_path, folder_id, file_mapping)
            if sub_stats:
                stats['folders'] += sub_stats['folders']
                stats['files'] += sub_stats['files']
                stats['errors'] += sub_stats['errors']
        else:
            # Upload file
            file_id = upload_file(service, item_path, folder_id, file_mapping)
            if file_id:
                stats['files'] += 1
            else:
                stats['errors'] += 1
    
    return stats


def find_existing_folder(service, folder_name, parent_id=None):
    """Check if a folder with the given name already exists."""
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    
    try:
        results = service.files().list(
            q=query,
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        files = results.get('files', [])
        if files:
            return files[0].get('id')
    except HttpError as error:
        print(f"Error checking for existing folder: {error}")
    
    return None


def push_rag_data_to_drive():
    """Main function to push RAG DATA folder to Google Drive."""
    print("=" * 60)
    print("🚀 RAG DATA to Google Drive Uploader")
    print("=" * 60)
    
    # Check if local folder exists
    if not os.path.exists(RAG_DATA_FOLDER):
        print(f"❌ Error: Folder '{RAG_DATA_FOLDER}' not found!")
        print(f"   Current directory: {os.getcwd()}")
        return
    
    # Authenticate
    print("\n🔐 Authenticating with Google Drive...")
    try:
        service = authenticate()
        print("✅ Authentication successful!")
    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        return
    
    # Check for existing folder
    print(f"\n🔍 Checking for existing '{RAG_DATA_FOLDER}' folder...")
    existing_id = find_existing_folder(service, RAG_DATA_FOLDER, GOOGLE_DRIVE_PARENT_FOLDER_ID)
    
    if existing_id:
        print(f"⚠️  Found existing folder with ID: {existing_id}")
        response = input("   Do you want to create a new folder anyway? (y/n): ").strip().lower()
        if response != 'y':
            print("   Upload cancelled.")
            return
    
    # Start upload
    print(f"\n📤 Starting upload of '{RAG_DATA_FOLDER}'...")
    print("-" * 60)
    
    # Initialize file mapping dictionary
    file_mapping = {}
    
    stats = upload_folder_recursive(
        service, 
        RAG_DATA_FOLDER, 
        GOOGLE_DRIVE_PARENT_FOLDER_ID,
        file_mapping
    )
    
    # Save the file mapping to JSON
    if file_mapping:
        with open(DRIVE_MAPPING_FILE, 'w', encoding='utf-8') as f:
            json.dump(file_mapping, f, indent=2, ensure_ascii=False)
        print(f"\n💾 Saved file mapping to '{DRIVE_MAPPING_FILE}'")
    
    # Print summary
    print("-" * 60)
    print("\n📊 Upload Summary:")
    print(f"   📁 Folders created: {stats['folders']}")
    print(f"   📄 Files uploaded:  {stats['files']}")
    print(f"   ❌ Errors:          {stats['errors']}")
    print(f"   🔗 File mappings:   {len(file_mapping)}")
    print("\n✨ Upload complete!")
    print("=" * 60)


if __name__ == '__main__':
    push_rag_data_to_drive()
