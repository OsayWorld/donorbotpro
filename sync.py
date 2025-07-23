# sync.py (final production version)
import sqlite3
import logging
import time
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Union, Tuple
from contextlib import contextmanager
from config import Config

logger = logging.getLogger(__name__)
cfg = Config()

class DBConnection:
    """Production-grade SQLite connection pool with proper initialization"""
    
    _instance = None
    _lock = threading.Lock()
    _connections = {}
    _max_connections = 5
    _monitor_thread = None
    _monitor_active = True

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._initialize_pool()
                cls._start_monitor()
        return cls._instance

    @classmethod
    def _initialize_pool(cls):
        """Initialize connection pool with schema creation"""
        db_config = cfg.get_database_config()
        
        # First connection ensures database exists
        conn = sqlite3.connect(db_config['database_path'])
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.close()
        
        # Now create the connection pool
        for i in range(cls._max_connections):
            cls._create_connection(i, db_config)

    @classmethod
    def _create_connection(cls, conn_id, db_config):
        """Create a new database connection with schema verification"""
        try:
            conn = sqlite3.connect(
                db_config['database_path'],
                timeout=db_config.get('timeout', 30),
                isolation_level='IMMEDIATE',
                detect_types=sqlite3.PARSE_DECLTYPES,
                check_same_thread=False
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.row_factory = sqlite3.Row
            
            cls._connections[conn_id] = {
                'conn': conn,
                'in_use': False,
                'last_used': datetime.now(),
                'created_at': datetime.now(),
                'usage_count': 0,
                'thread_id': None
            }
        except Exception as e:
            logger.error(f"Connection {conn_id} initialization failed: {str(e)}")
            raise

    @classmethod
    def _start_monitor(cls):
        """Start background connection health monitor"""
        def monitor():
            while cls._monitor_active:
                time.sleep(60)  # Check every minute
                with cls._lock:
                    for conn_id, conn_data in cls._connections.items():
                        if not conn_data['in_use']:
                            try:
                                conn_data['conn'].execute("SELECT 1").fetchone()
                            except sqlite3.Error:
                                logger.warning(f"Recreating unhealthy connection {conn_id}")
                                try:
                                    conn_data['conn'].close()
                                except Exception:
                                    pass
                                cls._create_connection(conn_id, cfg.get_database_config())

        cls._monitor_thread = threading.Thread(target=monitor, daemon=True)
        cls._monitor_thread.start()

    @contextmanager
    def get_connection(self):
        """Get a managed database connection with schema verification"""
        conn_id = None
        try:
            with self._lock:
                for i, conn_data in self._connections.items():
                    if not conn_data['in_use']:
                        conn_id = i
                        conn_data['in_use'] = True
                        conn_data['last_used'] = datetime.now()
                        conn_data['usage_count'] += 1
                        conn_data['thread_id'] = threading.get_ident()
                        conn = conn_data['conn']
                        break

                if conn_id is None:
                    raise RuntimeError("No available database connections")

            # Verify schema exists
            try:
                conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
            except sqlite3.Error as e:
                logger.error(f"Schema verification failed: {str(e)}")
                raise RuntimeError("Database schema not initialized")

            yield conn
        except Exception as e:
            logger.error(f"Connection acquisition failed: {str(e)}")
            raise
        finally:
            if conn_id is not None:
                with self._lock:
                    self._connections[conn_id]['in_use'] = False
                    self._connections[conn_id]['thread_id'] = None

    def close_all(self):
        """Cleanup all connections"""
        self._monitor_active = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=1)
        with self._lock:
            for conn_data in self._connections.values():
                try:
                    conn_data['conn'].close()
                except Exception as e:
                    logger.error(f"Error closing connection: {str(e)}")
            self._connections.clear()

class DatabaseSync:
    """Complete database operations handler with guaranteed initialization"""
    
    _instance = None
    _lock = threading.Lock()
    _initialized = False

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialize_db()
        return cls._instance

    def _initialize_db(self):
        """Initialize database schema with retries"""
        retries = 3
        for attempt in range(retries):
            try:
                with self._transaction() as cursor:
                    # Check if schema exists
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
                    if not cursor.fetchone():
                        self._create_schema(cursor)
                        logger.info("Database schema created")
                    else:
                        logger.debug("Database schema already exists")
                self._initialized = True
                break
            except Exception as e:
                if attempt == retries - 1:
                    logger.error(f"Database initialization failed after {retries} attempts: {str(e)}")
                    raise
                time.sleep(1)

    def _create_schema(self, cursor):
        """Create all required tables"""
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT COLLATE NOCASE,
                first_name TEXT,
                last_name TEXT,
                phone TEXT UNIQUE COLLATE NOCASE,
                language_code TEXT,
                is_premium BOOLEAN DEFAULT 0,
                last_interaction DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT phone_format CHECK (
                    phone IS NULL OR 
                    phone GLOB '+*[0-9]*' OR
                    phone GLOB '07[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]' OR
                    phone GLOB '2547[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'
                )
            );

            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                internal_reference TEXT UNIQUE COLLATE NOCASE,
                gateway_reference TEXT,
                checkout_request_id TEXT,
                user_id INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
                amount REAL NOT NULL CHECK (amount > 0),
                currency TEXT DEFAULT 'KES' CHECK (length(currency) = 3),
                phone TEXT NOT NULL COLLATE NOCASE,
                status TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'failed', 'timeout')),
                gateway_name TEXT DEFAULT 'mpesa',
                error_message TEXT,
                metadata TEXT,
                verification_attempts INTEGER DEFAULT 0 CHECK (verification_attempts >= 0),
                expiry_time DATETIME,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME,
                processing_status TEXT DEFAULT 'pending' CHECK (processing_status IN ('pending', 'processing', 'completed', 'failed'))
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_id INTEGER REFERENCES payments(id) ON DELETE CASCADE,
                user_id INTEGER REFERENCES users(user_id) ON DELETE CASCADE,
                message_id INTEGER,
                chat_id INTEGER,
                notification_type TEXT NOT NULL,
                content TEXT,
                status TEXT DEFAULT 'sent' CHECK (status IN ('sent', 'delivered', 'failed')),
                read_status BOOLEAN DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS channel_access (
                user_id INTEGER REFERENCES users(user_id) ON DELETE CASCADE,
                channel_id TEXT NOT NULL,
                channel_name TEXT,
                access_granted DATETIME DEFAULT CURRENT_TIMESTAMP,
                access_expires DATETIME,
                payment_reference TEXT REFERENCES payments(internal_reference) ON DELETE SET NULL,
                PRIMARY KEY (user_id, channel_id)
            ) WITHOUT ROWID;

            CREATE TABLE IF NOT EXISTS phone_mappings (
                phone TEXT PRIMARY KEY COLLATE NOCASE,
                user_id INTEGER REFERENCES users(user_id) ON DELETE CASCADE,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                expiry DATETIME NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            
                             
            CREATE TABLE IF NOT EXISTS verification_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_ref TEXT NOT NULL REFERENCES payments(internal_reference),
    attempt_number INTEGER NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    status_code INTEGER,
    response_text TEXT,
    was_successful BOOLEAN DEFAULT 0,
    CONSTRAINT fk_payment FOREIGN KEY (payment_ref) 
        REFERENCES payments(internal_reference) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_verification_attempts_payment ON verification_attempts(payment_ref);                

            CREATE INDEX IF NOT EXISTS idx_payments_ref_status ON payments(internal_reference, status);
            CREATE INDEX IF NOT EXISTS idx_payments_phone ON payments(phone);
            CREATE INDEX IF NOT EXISTS idx_payments_processing ON payments(processing_status);
            CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone) WHERE phone IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_phone_mappings_expiry ON phone_mappings(expiry);
            CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id);
            CREATE INDEX IF NOT EXISTS idx_channel_access_expires ON channel_access(access_expires) WHERE access_expires IS NOT NULL;

            PRAGMA user_version = 1;
        """)

    @contextmanager
    def _transaction(self, isolation_level=None):
        """Managed transaction context with schema verification"""
        conn = None
        cursor = None
        try:
            with DBConnection().get_connection() as conn:
                if isolation_level:
                    conn.isolation_level = isolation_level
                cursor = conn.cursor()
                yield cursor
                conn.commit()
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            logger.error(f"Transaction failed: {str(e)}")
            raise
        finally:
            if cursor:
                try:
                    cursor.close()
                except Exception:
                    pass

    # User Operations
    def get_user(self, user_id: int) -> Optional[Dict]:
        """Get user by ID with schema verification"""
        try:
            with self._transaction() as cursor:
                cursor.execute(
                    "SELECT * FROM users WHERE user_id = ? LIMIT 1",
                    (user_id,)
                )
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to get user {user_id}: {str(e)}")
            return None

    def update_user(self, user_data: Dict) -> bool:
        """Upsert user record with phone validation"""
        try:
            phone = user_data.get('phone')
            if phone and not self._validate_phone(phone):
                raise ValueError(f"Invalid phone format: {phone}")

            with self._transaction() as cursor:
                cursor.execute("""
                    INSERT INTO users (
                        user_id, username, first_name, last_name, phone,
                        language_code, is_premium, last_interaction
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        username = COALESCE(excluded.username, username),
                        first_name = COALESCE(excluded.first_name, first_name),
                        last_name = COALESCE(excluded.last_name, last_name),
                        phone = COALESCE(excluded.phone, phone),
                        language_code = COALESCE(excluded.language_code, language_code),
                        is_premium = COALESCE(excluded.is_premium, is_premium),
                        last_interaction = excluded.last_interaction,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    user_data['user_id'],
                    user_data.get('username'),
                    user_data.get('first_name'),
                    user_data.get('last_name'),
                    phone,
                    user_data.get('language_code'),
                    user_data.get('is_premium', False),
                    user_data.get('last_interaction', datetime.now())
                ))
                return True
        except Exception as e:
            logger.error(f"Failed to update user: {str(e)}")
            return False

    def _validate_phone(self, phone: str) -> bool:
        """Validate phone number format"""
        if not phone:
            return False
        return (phone.startswith('+') and phone[1:].isdigit()) or \
               (phone.startswith('07') and len(phone) == 10 and phone[2:].isdigit()) or \
               (phone.startswith('2547') and len(phone) == 12 and phone[4:].isdigit())

    # Payment Operations
    def create_payment(self, payment_data: Dict) -> Optional[int]:
        """Create new payment record with validation"""
        try:
            with self._transaction() as cursor:
                cursor.execute("""
                    INSERT INTO payments (
                        internal_reference, gateway_reference, checkout_request_id,
                        user_id, amount, currency, phone, status, expiry_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    payment_data['internal_reference'],
                    payment_data.get('gateway_reference'),
                    payment_data.get('checkout_request_id'),
                    payment_data.get('user_id'),
                    payment_data['amount'],
                    payment_data.get('currency', 'KES'),
                    payment_data['phone'],
                    payment_data.get('status', 'pending'),
                    payment_data.get('expiry_time')
                ))
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"Failed to create payment: {str(e)}")
            return None

    def update_payment(self, reference: str, updates: Dict) -> bool:
        """Update payment record with validation"""
        try:
            fields = []
            params = []
            
            for field, value in updates.items():
                if field == 'status' and value not in ('pending', 'completed', 'failed', 'timeout'):
                    raise ValueError(f"Invalid status: {value}")
                fields.append(f"{field} = ?")
                params.append(value)
            
            params.append(reference)
            
            query = f"""
                UPDATE payments 
                SET {', '.join(fields)}, updated_at = CURRENT_TIMESTAMP
                WHERE internal_reference = ?
            """
            
            with self._transaction() as cursor:
                cursor.execute(query, params)
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Failed to update payment {reference}: {str(e)}")
            return False

    # Compatibility Methods
    def update_user_profile(self, user_data: Dict) -> bool:
        """Alias for update_user to maintain compatibility"""
        return self.update_user(user_data)

    def log_payment(self, payment_data: Dict) -> Optional[int]:
        """Compatibility alias for create_payment"""
        try:
            return self.create_payment({
                'internal_reference': payment_data['internal_ref'],
                'gateway_reference': payment_data.get('gateway_ref'),
                'checkout_request_id': payment_data.get('checkout_id'),
                'user_id': payment_data.get('user_id'),
                'amount': payment_data['amount'],
                'currency': payment_data.get('currency', 'KES'),
                'phone': payment_data['phone'],
                'status': payment_data.get('status', 'pending'),
                'expiry_time': payment_data.get('expiry_time')
            })
        except Exception as e:
            logger.error(f"Failed to log payment: {str(e)}")
            return None

    def update_payment_status(self, reference: str, status: str, **kwargs) -> bool:
        """Compatibility alias for update_payment"""
        updates = {'status': status}
        if 'error_message' in kwargs:
            updates['error_message'] = kwargs['error_message']
        return self.update_payment(reference, updates)

    def store_phone_mapping(self, phone: str, user_id: int,
                          chat_id: int, message_id: int, expiry: datetime) -> bool:
        """Store phone mapping for callback handling"""
        try:
            with self._transaction() as cursor:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO phone_mappings (
                        phone, user_id, chat_id, message_id, expiry
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (phone, user_id, chat_id, message_id, expiry)
                )
                return True
        except Exception as e:
            logger.error(f"Failed to store phone mapping: {str(e)}")
            return False

    def __del__(self):
        """Cleanup resources"""
        DBConnection().close_all()

# Singleton export
database = DatabaseSync()

def initialize_database():
    """Initialize the database (call this after all imports)"""
    database._initialize_db()