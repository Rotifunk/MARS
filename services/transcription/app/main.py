from fastapi import FastAPI, UploadFile, File, HTTPException
from pymongo import MongoClient
from datetime import datetime
import os
import uuid
import logging
from .models import Transcript
from .tasks import transcribe_audio

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 환경 변수
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
MONGODB_DB = os.getenv("MONGODB_DB", "transcription_db")
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/app/uploads")

app = FastAPI(title="Audio Transcription Service")

# MongoDB 연결
try:
    client = MongoClient(MONGODB_URI)
    db = client[MONGODB_DB]
    logger.info("MongoDB 연결 성공")
except Exception as e:
    logger.error(f"MongoDB 연결 실패: {str(e)}")
    raise

# 업로드 디렉토리 생성
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.get("/health")
async def health_check():
    try:
        client.admin.command('ping')
        return {
            "status": "healthy",
            "database": "connected",
            "upload_dir": os.path.exists(UPLOAD_DIR)
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    try:
        # 파일 형식 검사
        logger.info(f"수신된 파일: {file.filename}, 타입: {file.content_type}")
        if not file.filename.endswith('.wav'):
            raise HTTPException(status_code=400, detail="WAV 파일만 지원됩니다")
        
        # 고유한 파일명 생성
        job_id = str(uuid.uuid4())
        file_path = os.path.join(UPLOAD_DIR, f"{job_id}_{file.filename}")
        
        # 파일 저장
        try:
            with open(file_path, "wb") as f:
                content = await file.read()
                f.write(content)
            logger.info(f"파일 저장됨: {file_path}")
        except Exception as e:
            logger.error(f"파일 저장 실패: {str(e)}")
            raise HTTPException(status_code=500, detail="파일 저장 실패")
            
        # Transcription 작업 시작
        current_time = datetime.utcnow()
        
        transcript = {
            "job_id": job_id,
            "status": "pending",
            "input_file": file_path,
            "created_at": current_time,
            "updated_at": current_time
        }
        
        # MongoDB에 저장
        try:
            db.transcripts.insert_one(transcript)
            logger.info(f"MongoDB에 작업 저장됨: {job_id}")
        except Exception as e:
            logger.error(f"MongoDB 저장 실패: {str(e)}")
            raise HTTPException(status_code=500, detail="데이터베이스 저장 실패")
        
        # Celery 작업 시작
        try:
            transcribe_audio.delay(
                job_id=job_id,
                input_path=file_path,
                db_connection_string=MONGODB_URI
            )
            logger.info(f"Celery 작업 시작됨: {job_id}")
        except Exception as e:
            logger.error(f"Celery 작업 시작 실패: {str(e)}")
            raise HTTPException(status_code=500, detail="작업 시작 실패")
        
        return {"job_id": job_id, "status": "pending"}
            
    except Exception as e:
        logger.error(f"Transcribe 엔드포인트 오류: {str(e)}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/transcript/{job_id}")
async def get_transcript(job_id: str):
    try:
        logger.info(f"작업 상태 조회 시작: {job_id}")
        
        # MongoDB 연결 상태 확인
        try:
            # client.admin.command('ping') 대신 client.admin.command('ping') 사용
            client.admin.command('ping')
        except Exception as db_error:
            logger.error(f"MongoDB 연결 오류: {str(db_error)}")
            raise HTTPException(status_code=500, detail="데이터베이스 연결 오류")
            
        # 작업 조회
        transcript = db.transcripts.find_one({"job_id": job_id})
        logger.info(f"조회된 작업: {transcript}")
        
        if not transcript:
            logger.error(f"작업을 찾을 수 없음: {job_id}")
            raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다")
            
        response = {
            "job_id": job_id,
            "status": transcript["status"],
            "text": transcript.get("text"),
            "error": transcript.get("error")
        }
        logger.info(f"응답 데이터: {response}")
        return response
        
    except Exception as e:
        logger.error(f"작업 상태 조회 중 오류 발생: {str(e)}, 타입: {type(e)}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e))