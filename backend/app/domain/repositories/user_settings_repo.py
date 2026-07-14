"""
User Settings Repository
Menyimpan preferensi user: routing settings dan prayer alarm settings
"""

from typing import Dict, Any, Optional
import datetime as dt
from app.infrastructure.database.arangodb_client import get_db


def save_user_settings(user_id: str, settings_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Simpan atau update user settings ke ArangoDB.
    
    Args:
        user_id: Unique identifier (bisa device_id atau username)
        settings_data: Dictionary berisi search_settings dan prayer_settings
        
    Returns:
        Saved settings document
    """
    db = get_db()
    col = db.collection('user_settings')
    
    # Cek apakah user sudah punya settings
    cursor = col.find({'user_id': user_id}, limit=1)
    existing = list(cursor)
    
    previous = existing[0] if existing else {}
    search_settings = dict(previous.get('search_settings') or {})
    prayer_settings = dict(previous.get('prayer_settings') or {})
    if 'search_settings' in settings_data:
        search_settings.update(settings_data['search_settings'] or {})
    if 'prayer_settings' in settings_data:
        prayer_settings.update(settings_data['prayer_settings'] or {})

    document = {
        'user_id': user_id,
        'search_settings': search_settings,
        'prayer_settings': prayer_settings,
        'updated_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'client_updated_at': settings_data.get('client_updated_at'),
        'schema_version': 1,
    }
    
    if existing:
        # Update existing
        doc_key = existing[0]['_key']
        col.update({'_key': doc_key, **document}, merge=True)
        result = col.get(doc_key)
    else:
        # Insert new
        result = col.insert(document, return_new=True)
        result = result.get('new', result)
    
    return result


def get_user_settings(user_id: str) -> Optional[Dict[str, Any]]:
    """
    Ambil user settings dari ArangoDB.
    
    Args:
        user_id: Unique identifier
        
    Returns:
        Settings document atau None jika tidak ditemukan
    """
    db = get_db()
    col = db.collection('user_settings')
    
    cursor = col.find({'user_id': user_id}, limit=1)
    results = list(cursor)
    
    if results:
        return results[0]
    return None


def delete_user_settings(user_id: str) -> bool:
    """
    Hapus user settings (untuk reset atau logout).
    
    Args:
        user_id: Unique identifier
        
    Returns:
        True jika berhasil dihapus
    """
    db = get_db()
    col = db.collection('user_settings')
    
    cursor = col.find({'user_id': user_id}, limit=1)
    results = list(cursor)
    
    if results:
        col.delete(results[0]['_key'])
        return True
    return False
