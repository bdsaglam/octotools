from functools import cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    default_llm: str
    default_vlm: str
    default_scoring_llm: str

    cache_enabled: bool = False

    class Config:
        env_file = ".env"


@cache
def get_settings():
    from dotenv import load_dotenv

    load_dotenv()
    return Settings()
