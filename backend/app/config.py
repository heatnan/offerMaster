from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # LLM
    OPENAI_BASE_URL: str = "https://api.deepseek.com/v1"
    OPENAI_API_KEY: str = ""
    LLM_MODEL: str = "deepseek-chat"

    # STT
    STT_PROVIDER: str = "local_whisper"  # local_whisper | openai_compat
    WHISPER_MODEL: str = "medium"
    WHISPER_DEVICE: str = "cpu"
    WHISPER_COMPUTE_TYPE: str = "int8"

    # TTS
    TTS_PROVIDER: str = "edge"  # edge | openai_compat
    TTS_VOICE: str = "zh-CN-YunjianNeural"

    # DB
    MYSQL_HOST: str = "mysql"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "offer"
    MYSQL_PASSWORD: str = "offer_pass"
    MYSQL_DATABASE: str = "offer_master"

    # Interview policy
    QUESTIONS_PER_ROUND_MIN: int = 5
    QUESTIONS_PER_ROUND_MAX: int = 8
    MAX_FOLLOWUPS: int = 2
    FOLLOWUP_STRATEGY: str = "balanced"  # aggressive | balanced | lenient
    ROUND_PASS_THRESHOLD: int = 70

    # Storage
    STORAGE_DIR: str = "/data"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}?charset=utf8mb4"
        )


settings = Settings()
