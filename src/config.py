from src.settings import settings as config

class Config:
    @classmethod
    def validate(cls):
        # Validation is now handled at import time by pydantic-settings in src/settings.py
        pass
