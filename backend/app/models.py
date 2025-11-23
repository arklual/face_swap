from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, String, DateTime, func, JSON
Base = declarative_base()

class Job(Base):
    __tablename__ = "jobs"
    job_id = Column(String, primary_key=True, index=True)
    user_id = Column(String, nullable=True)
    status = Column(String, default="pending")
    child_photo_uri = Column(String, nullable=True)
    caption_uri = Column(String, nullable=True)
    common_prompt = Column(String, nullable=True)
    analysis_json = Column(JSON, nullable=True)
    result_uri = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())