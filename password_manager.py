"""
password_manager.py — HF Hub Based Password Manager
=====================================================
- পাসওয়ার্ড এনক্রিপ্টেড অবস্থায় HF Hub-এ সেভ থাকে (Restart এও থাকে)
- Agent লোকাল ক্যাশে ডিক্রিপ্ট করে রাখে (use করার জন্য)
- ব্যবহারের পর আবার এনক্রিপ্ট করে লোকাল থেকে মুছে দেয় না (ক্যাশেই থাকে)
- নতুন সেভ/আপডেট হলে HF Hub-এ sync হয়
"""

import os
import json
import time
import base64
import logging
import threading
from io import BytesIO
from datetime import datetime
from typing import Dict, List, Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from crewai.tools import tool

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  HF HUB CONFIGURATION (Memory-এর মতো)
# ─────────────────────────────────────────────

HF_TOKEN = os.getenv("HF_TOKEN", "")
VAULT_MASTER_KEY = os.getenv("AGENT_VAULT_KEY", "")

_repo_id = None
_repo_ready = False
_lock = threading.Lock()

# লোকাল ক্যাশে (Agent runtime-এ ডিক্রিপ্টেড থাকে)
_local_cache: Dict[str, Dict] = {}  # {service: entry}
_cache_loaded = False


def _get_repo_id() -> str | None:
    """Memory-এর মতো একই repo ব্যবহার করে"""
    global _repo_id, _repo_ready
    
    if not HF_TOKEN:
        logger.warning("⚠️ HF_TOKEN not set — passwords will NOT persist across restarts!")
        return None
    
    if _repo_id is None:
        configured = os.getenv("MEMORY_REPO_ID", "").strip()
        if configured:
            _repo_id = configured
        else:
            try:
                from huggingface_hub import HfApi
                api = HfApi(token=HF_TOKEN)
                whoami = api.whoami()
                username = whoami["name"]
                _repo_id = f"{username}/agent-memory"
                logger.info(f"Using memory repo: {_repo_id}")
            except Exception as e:
                logger.error(f"Failed to get HF username: {e}")
                return None
    
    if not _repo_ready and _repo_id:
        try:
            from huggingface_hub import HfApi
            api = HfApi(token=HF_TOKEN)
            api.create_repo(
                repo_id=_repo_id,
                repo_type="dataset",
                private=True,
                exist_ok=True,
            )
            _repo_ready = True
            logger.info(f"✅ Password vault repo ready: {_repo_id}")
        except Exception as e:
            logger.error(f"Failed to create repo: {e}")
            return None
    
    return _repo_id


def _get_remote_path() -> str:
    """HF Hub-এ পাসওয়ার্ড ফাইলের পাথ"""
    return "memory/passwords.enc"  # memory ফোল্ডারের ভিতরেই রাখছে


# ─────────────────────────────────────────────
#  ENCRYPTION ENGINE
# ─────────────────────────────────────────────

class PasswordEncryption:
    """AES-256 encryption for passwords"""
    
    @staticmethod
    def _derive_key(password: str, salt: bytes = None) -> tuple:
        """Derive encryption key from master password"""
        if salt is None:
            salt = os.urandom(32)
        
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return key, salt
    
    @staticmethod
    def encrypt(data: str, master_key: str) -> str:
        """Encrypt data using master key"""
        key, salt = PasswordEncryption._derive_key(master_key)
        cipher = Fernet(key)
        encrypted = cipher.encrypt(data.encode())
        combined = base64.b64encode(salt + encrypted).decode()
        return combined
    
    @staticmethod
    def decrypt(encrypted_data: str, master_key: str) -> str:
        """Decrypt data using master key"""
        combined = base64.b64decode(encrypted_data)
        salt = combined[:32]
        encrypted = combined[32:]
        key, _ = PasswordEncryption._derive_key(master_key, salt)
        cipher = Fernet(key)
        decrypted = cipher.decrypt(encrypted)
        return decrypted.decode()


# ─────────────────────────────────────────────
#  CORE PASSWORD MANAGER (HF Hub Sync)
# ─────────────────────────────────────────────

class PasswordManager:
    def __init__(self):
        self.master_key = VAULT_MASTER_KEY
        self.passwords: Dict[str, Dict] = {}  # লোকাল ক্যাশে
        self._load_from_hub()  # Start-এ HF Hub থেকে লোড করে
    
    def _load_from_hub(self):
        """HF Hub থেকে এনক্রিপ্টেড ভল্ট লোড করে এবং ডিক্রিপ্ট করে"""
        global _cache_loaded
        
        if not HF_TOKEN or not self.master_key:
            logger.warning("⚠️ HF_TOKEN or AGENT_VAULT_KEY missing — using empty vault")
            self.passwords = {}
            return
        
        repo_id = _get_repo_id()
        if not repo_id:
            self.passwords = {}
            return
        
        try:
            from huggingface_hub import hf_hub_download
            
            # এনক্রিপ্টেড ফাইল ডাউনলোড করে
            path = hf_hub_download(
                repo_id=repo_id,
                repo_type="dataset",
                filename=_get_remote_path(),
                token=HF_TOKEN,
            )
            
            with open(path, "r", encoding="utf-8") as f:
                encrypted_data = f.read()
            
            # ডিক্রিপ্ট করে
            decrypted = PasswordEncryption.decrypt(encrypted_data, self.master_key)
            self.passwords = json.loads(decrypted)
            _cache_loaded = True
            logger.info(f"✅ Loaded {len(self.passwords.get('entries', {}))} passwords from HF Hub")
            
        except Exception as e:
            # ফাইল না থাকলে নতুন ভল্ট তৈরি করে
            logger.info("No existing vault found, creating new one...")
            self.passwords = {
                "version": "1.0",
                "created": time.time(),
                "last_sync": None,
                "entries": {}
            }
            self._sync_to_hub()  # নতুন ভল্ট আপলোড করে
    
    def _sync_to_hub(self):
        """লোকাল ক্যাশে এনক্রিপ্ট করে HF Hub-এ আপলোড করে"""
        if not HF_TOKEN or not self.master_key:
            logger.warning("Cannot sync: HF_TOKEN or AGENT_VAULT_KEY missing")
            return
        
        repo_id = _get_repo_id()
        if not repo_id:
            return
        
        def _upload():
            try:
                from huggingface_hub import HfApi
                api = HfApi(token=HF_TOKEN)
                
                # লোকাল ডাটা এনক্রিপ্ট করে
                data = json.dumps(self.passwords, ensure_ascii=False)
                encrypted = PasswordEncryption.encrypt(data, self.master_key)
                
                with _lock:
                    api.upload_file(
                        path_or_fileobj=BytesIO(encrypted.encode()),
                        path_in_repo=_get_remote_path(),
                        repo_id=repo_id,
                        repo_type="dataset",
                        token=HF_TOKEN,
                        commit_message=f"Update passwords vault at {time.time()}"
                    )
                
                self.passwords["last_sync"] = time.time()
                logger.debug("✅ Passwords synced to HF Hub")
            except Exception as e:
                logger.error(f"Failed to sync passwords to hub: {e}")
        
        # Non-blocking upload (মেমোরির মতো)
        threading.Thread(target=_upload, daemon=True).start()
    
    def _save_local_and_sync(self):
        """লোকাল সেভ করে এবং HF Hub-এ sync করে"""
        self._sync_to_hub()
    
    def add_password(self, service: str, username: str, password: str, 
                     category: str = "general", notes: str = "") -> Dict:
        """পাসওয়ার্ড যোগ/আপডেট করে"""
        entry = {
            "service": service,
            "username": username,
            "password": password,
            "category": category,
            "notes": notes,
            "created": time.time(),
            "updated": time.time(),
            "last_used": None,
            "use_count": 0
        }
        
        self.passwords["entries"][service] = entry
        self._save_local_and_sync()  # HF Hub-এ sync
        
        return {
            "success": True,
            "message": f"🔐 Password for '{service}' saved securely",
            "service": service,
            "username": username
        }
    
    def get_password(self, service: str) -> Optional[Dict]:
        """পাসওয়ার্ড রিট্রিভ করে (লোকাল ক্যাশে থেকে)"""
        entry = self.passwords["entries"].get(service)
        if not entry:
            return None
        
        # ব্যবহারের পরিসংখ্যান আপডেট
        entry["last_used"] = time.time()
        entry["use_count"] = entry.get("use_count", 0) + 1
        self._save_local_and_sync()  # sync usage stats
        
        return {
            "service": entry["service"],
            "username": entry["username"],
            "password": entry["password"],
            "category": entry.get("category", "general"),
            "notes": entry.get("notes", "")
        }
    
    def get_all_passwords(self, category: str = None) -> List[Dict]:
        """সব পাসওয়ার্ডের লিস্ট (পাসওয়ার্ড ছাড়া)"""
        entries = []
        for service, entry in self.passwords["entries"].items():
            if category and entry.get("category") != category:
                continue
            
            entries.append({
                "service": service,
                "username": entry["username"],
                "category": entry.get("category", "general"),
                "last_used": datetime.fromtimestamp(entry.get("last_used", 0)).strftime("%Y-%m-%d") if entry.get("last_used") else "Never",
                "use_count": entry.get("use_count", 0),
                "created": datetime.fromtimestamp(entry["created"]).strftime("%Y-%m-%d")
            })
        
        return sorted(entries, key=lambda x: x["last_used"] if x["last_used"] != "Never" else "", reverse=True)
    
    def delete_password(self, service: str) -> bool:
        """পাসওয়ার্ড ডিলিট করে"""
        if service in self.passwords["entries"]:
            del self.passwords["entries"][service]
            self._save_local_and_sync()
            return True
        return False
    
    def update_password(self, service: str, new_password: str = None, 
                       new_username: str = None, notes: str = None) -> bool:
        """পাসওয়ার্ড আপডেট করে"""
        if service not in self.passwords["entries"]:
            return False
        
        entry = self.passwords["entries"][service]
        if new_password:
            entry["password"] = new_password
        if new_username:
            entry["username"] = new_username
        if notes:
            entry["notes"] = notes
        entry["updated"] = time.time()
        
        self._save_local_and_sync()
        return True
    
    def search_passwords(self, query: str) -> List[Dict]:
        """পাসওয়ার্ড খোঁজে"""
        query_lower = query.lower()
        results = []
        
        for service, entry in self.passwords["entries"].items():
            if (query_lower in service.lower() or 
                query_lower in entry["username"].lower()):
                results.append({
                    "service": service,
                    "username": entry["username"],
                    "category": entry.get("category", "general")
                })
        
        return results
    
    def get_stats(self) -> Dict:
        """ভল্টের পরিসংখ্যান"""
        entries = self.passwords["entries"]
        total = len(entries)
        
        categories = {}
        for entry in entries.values():
            cat = entry.get("category", "general")
            categories[cat] = categories.get(cat, 0) + 1
        
        most_used = None
        if entries:
            most_used = max(entries.values(), key=lambda x: x.get("use_count", 0))
        
        return {
            "total_passwords": total,
            "categories": categories,
            "most_used_service": most_used["service"] if most_used else None,
            "vault_created": datetime.fromtimestamp(self.passwords["created"]).strftime("%Y-%m-%d"),
            "last_sync": datetime.fromtimestamp(self.passwords.get("last_sync", 0)).strftime("%Y-%m-%d %H:%M:%S") if self.passwords.get("last_sync") else "Never",
            "encryption_enabled": bool(self.master_key),
            "persistent_storage": bool(HF_TOKEN)
        }


# ─────────────────────────────────────────────
#  GLOBAL INSTANCE
# ─────────────────────────────────────────────

_password_manager = None

def get_manager() -> PasswordManager:
    global _password_manager
    if _password_manager is None:
        _password_manager = PasswordManager()
    return _password_manager


# ─────────────────────────────────────────────
#  CREWAI TOOLS (Memory-এর মতোই)
# ─────────────────────────────────────────────

@tool("password_save")
def password_save(service: str, username: str, password: str, category: str = "general") -> str:
    """🔐 Save a password to secure vault (syncs to HF Hub automatically)"""
    manager = get_manager()
    result = manager.add_password(service.lower(), username, password, category)
    
    if result["success"]:
        return f"✅ {result['message']}\n   Username: {username}\n   🔒 Encrypted & synced to permanent storage"
    return f"❌ Failed to save password"


@tool("password_get")
def password_get(service: str) -> str:
    """🔓 Retrieve a password from secure vault"""
    manager = get_manager()
    result = manager.get_password(service.lower())
    
    if result:
        stats = manager.get_stats()
        return f"""🔓 *Password Retrieved*

📌 *Service:* {result['service']}
👤 *Username:* {result['username']}
🔑 *Password:* `{result['password']}`
📂 *Category:* {result['category']}

💡 *Info:* Password is decrypted only while using. 
🔒 Stored encrypted in HF Hub (persists across restarts)"""
    
    return f"❌ No password found for '{service}'. Use 'password_save' to store it first."


@tool("password_list")
def password_list(category: str = "") -> str:
    """📋 List all saved passwords (shows only service names)"""
    manager = get_manager()
    passwords = manager.get_all_passwords(category if category else None)
    stats = manager.get_stats()
    
    if not passwords:
        return "📭 No passwords saved yet. Use 'password_save' to add some."
    
    result = f"🔐 *Password Manager Vault*\n"
    result += f"📊 *Total:* {len(passwords)} entries\n"
    result += f"💾 *Storage:* {'✅ Persistent (HF Hub)' if stats['persistent_storage'] else '⚠️ Local only'}\n"
    result += f"🔒 *Encryption:* {'✅ Enabled' if stats['encryption_enabled'] else '⚠️ Disabled'}\n\n"
    
    for p in passwords[:20]:
        result += f"📌 *{p['service']}*\n"
        result += f"   👤 {p['username']}\n"
        result += f"   📂 {p['category']} | Used {p['use_count']}x | Added: {p['created']}\n\n"
    
    if len(passwords) > 20:
        result += f"\n... and {len(passwords) - 20} more."
    
    return result


@tool("password_delete")
def password_delete(service: str) -> str:
    """🗑️ Delete a password from vault"""
    manager = get_manager()
    if manager.delete_password(service.lower()):
        return f"✅ Deleted password for '{service}' from vault (synced to HF Hub)"
    return f"❌ No password found for '{service}'"


@tool("password_update")
def password_update(service: str, new_password: str = None, new_username: str = None) -> str:
    """🔄 Update an existing password"""
    manager = get_manager()
    if manager.update_password(service.lower(), new_password, new_username):
        changes = []
        if new_password:
            changes.append("password")
        if new_username:
            changes.append("username")
        return f"✅ Updated {', '.join(changes)} for '{service}' (synced to HF Hub)"
    return f"❌ No password found for '{service}'"


@tool("password_search")
def password_search(query: str) -> str:
    """🔍 Search for passwords by service name or username"""
    manager = get_manager()
    results = manager.search_passwords(query)
    
    if not results:
        return f"🔍 No results found for '{query}'"
    
    result = f"🔍 *Search Results for '{query}':*\n\n"
    for r in results:
        result += f"📌 *{r['service']}* - {r['username']} ({r['category']})\n"
    
    return result


@tool("password_stats")
def password_stats() -> str:
    """📊 Show password manager statistics"""
    manager = get_manager()
    stats = manager.get_stats()
    
    return f"""📊 *Password Manager Statistics*

🔐 Total passwords: {stats['total_passwords']}
📁 Categories: {stats['categories']}
⭐ Most used: {stats['most_used_service'] or 'N/A'}
📅 Vault created: {stats['vault_created']}
🔄 Last sync to HF Hub: {stats['last_sync']}
💾 Persistent storage: {'✅ Enabled (survives restarts)' if stats['persistent_storage'] else '⚠️ Local only'}
🔒 Encryption: {'✅ AES-256 Encrypted' if stats['encryption_enabled'] else '⚠️ Disabled'}

*How it works:*
- Passwords are encrypted with AES-256
- Stored permanently in HF Hub dataset
- Decrypted only when needed
- Survives Space restarts!"""


@tool("password_autofill")
def password_autofill(service: str) -> str:
    """⚡ Get credentials ready for autofill"""
    manager = get_manager()
    result = manager.get_password(service.lower())
    
    if result:
        return f"Username: {result['username']}\nPassword: {result['password']}"
    return f"No credentials found for {service}"


@tool("password_sync_now")
def password_sync_now() -> str:
    """🔄 Manually sync passwords to HF Hub"""
    manager = get_manager()
    if HF_TOKEN and VAULT_MASTER_KEY:
        manager._sync_to_hub()
        return "✅ Manual sync triggered! Passwords backed up to HF Hub."
    return "❌ Cannot sync: HF_TOKEN or AGENT_VAULT_KEY missing"