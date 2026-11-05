from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from core.DatabaseConnect import get_db

app = FastAPI(root_path="/fastapi")

@app.get("/")
def root():
    return {"status": 200, "message": "Fast API 도메인 아키텍쳐 준비 완료"}

# DB 연결 확인용 엔드포인트
@app.get("/health/db")
def health_check_db(db: Session = Depends(get_db)):
    try:
        # 커넥션 풀을 통한 테스트 쿼리 실행
        db.execute(text("SELECT 1"))
        return {
            "status": "connected",
            "database": "MariaDB (capstone_db)",
            "result": "success"
        }
    except Exception as e:
        return {
            "status": "disconnected",
            "error": str(e)
        }