from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# mysql+pymysql://user:password@host/database
DB_URL = os.getenv("DB_URL", "mysql+pymysql://capstone@localhost/capstone_db")

engine = create_engine(DB_URL, pool_recycle=3600)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
