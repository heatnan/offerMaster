from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # LLM
    OPENAI_BASE_URL: str = "https://api.deepseek.com/v1"
    OPENAI_API_KEY: str = ""
    LLM_MODEL: str = "deepseek-chat"

    # STT
    STT_PROVIDER: str = "local_whisper"  # local_whisper | volcengine_asr | openai_compat
    WHISPER_MODEL: str = "medium"
    WHISPER_DEVICE: str = "cpu"
    WHISPER_COMPUTE_TYPE: str = "int8"

    # TTS
    TTS_PROVIDER: str = "edge"  # edge | openai_compat | doubao
    TTS_VOICE: str = "zh-CN-YunjianNeural"

    # Doubao (Volcengine) TTS — only used when TTS_PROVIDER=doubao.
    # 火山引擎语音合成大模型 API：https://www.volcengine.com/docs/6561/1257544
    # 需要在火山引擎后台开通"语音合成大模型"服务后填以下四个字段。
    DOUBAO_APP_ID: str = ""
    DOUBAO_ACCESS_TOKEN: str = ""
    VOLCENGINE_ASR_API_KEY: str = ""  # 新版控制台 API Key，用于豆包流式 ASR 2.0
    DOUBAO_CLUSTER: str = "volcano_tts"  # 大模型音色集群 (bigtts 系列音色用这个)
    DOUBAO_VOICE_TYPE: str = "zh_male_m191_uranus_bigtts"  # 默认"云舟" (Seed TTS 2.0 沉稳男声)

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
