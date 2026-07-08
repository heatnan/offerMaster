# Offer Master · AI 面试系统 / AI 陪面 / AI 模拟面试

> 开源的 **AI 面试模拟系统**，用 AI 面试官陪你练面试。基于 LangGraph 多智能体 + DeepSeek 大模型 + 本地 Whisper 语音识别，纯语音对话，会追问、会评分、会写面评报告。

**关键词**：AI 面试 · AI 陪面 · AI 模拟面试 · 智能面试官 · 面试模拟器 · 大模型面试 · AI Interviewer · Mock Interview · Interview Simulator · LLM Agent

---

## 亮点

- 🎤 **纯语音交互**：像和真人对话一样，AI 面试官会听你说话、会追问、会评分
- 🧠 **面试官在真的思考**：每次追问都会引用你回答里的原话（"你刚才提到的双 Agent 架构..."），不是套路化"能详细讲讲吗"
- 🎯 **简历+JD 定制出题**：上传简历和目标岗位，AI 结合你的技术栈针对性出题，不是题库随机
- 👔 **三档面试官人格**：一面同级工程师（peer 随和）/ 二面资深工程师（high peer 严谨）/ 三面直属经理（manager 沉稳），说话风格明显不同
- 🎙️ **智能衔接**：AI 会先说"嗯，我理解了..."再问下一题，不再是"沉默 20 秒然后突兀开口"
- 📊 **面评报告**：面完立即生成 Markdown + PDF，标注技术亮点、待改进点、学习建议
- 💰 **零成本运行**：本地 Whisper STT + 免费 Edge TTS + DeepSeek 便宜模型，一场面试 LLM 成本 ≈ ¥0.3
- 🐳 **Docker 一键起**：`docker compose up -d`，三分钟跑起来

## 演示截图

<!-- TODO: 放一张主界面截图 + 一张报告截图 -->

## 技术栈

| 模块 | 选型 |
|---|---|
| 前端 | Next.js 14 + TypeScript + Tailwind |
| 后端 | FastAPI + SQLAlchemy + LangGraph |
| 数据库 | MySQL 8 |
| LLM | OpenAI 兼容协议（默认 DeepSeek，可切 Kimi / Qwen / GPT） |
| STT | faster-whisper（默认，本地）· 可选火山引擎豆包流式 ASR 2.0（中文准确率更高） |
| TTS | edge-tts（默认，免费）· 可选豆包 Seed TTS 2.0（更自然、有情感） |
| 部署 | docker-compose |

## 快速开始

```bash
git clone https://github.com/heatnan/offerMaster.git
cd offerMaster
cp .env.example .env
# 编辑 .env，填入 OPENAI_API_KEY（DeepSeek Key 就行，¥1 能面几十场）

docker compose up -d --build
```

访问：
- 前端：http://localhost:3000
- 后端 API 文档：http://localhost:8000/docs

首次启动 backend 会下载 Whisper 模型（约 1.5GB），耐心等一下。模型缓存到 `whisper_cache` volume，后续启动秒开。

## 面试流程

1. **上传简历**（PDF/DOCX）+ **JD 文本**
2. **选择面试轮次**（1~3 轮）：一面 peer → 二面 high peer → 三面直属经理
3. **每轮 5~8 道题**，AI 根据你的简历和 JD 现场出题
4. **push-to-talk 语音作答**，AI 会等你说完再回应
5. **智能追问**：AI 判断你的知识边界，深入的答案会推进下一题，泛泛的答案会被继续追问
6. **每题维度打分**（技术/表达/深度），每轮 ≥70 分晋级下一轮
7. **面完自动生成面评报告**（Markdown + PDF），可下载

## 谁适合用

- 🎓 **准备找工作/换工作**的开发者，想练面试但没有真人愿意陪面
- 💼 **HR / 招聘方**，想在正式面试前先让 AI 过一轮
- 🎥 **面试培训机构**，作为学员的日常自练工具
- 🔧 **AI Agent 学习者**，作为多智能体 + 语音交互的开源参考

## 目录结构

```
offerMaster/
├── docker-compose.yml
├── .env.example
├── frontend/            Next.js 前端
│   ├── app/             页面（面试 / 报告）
│   ├── components/      UI 组件（面试官头像等）
│   └── lib/api.ts       后端 API 封装
└── backend/             FastAPI 后端
    └── app/
        ├── api/         REST endpoints
        ├── agent/       LangGraph Agent + prompts
        ├── services/    LLM / STT / TTS provider
        └── models.py    SQLAlchemy ORM
```

## 配置项

见 `.env.example`，支持切换：
- **LLM provider**（DeepSeek / Kimi / Qwen / GPT / 任何 OpenAI 兼容 API）
- **STT provider**（本地 Whisper / 火山引擎豆包流式 ASR / OpenAI API 兼容）
- **TTS provider**（Edge TTS / 豆包 Seed TTS 2.0 / OpenAI API 兼容）
- 每轮题量、追问激进度、通过阈值等

### 可选：切换到火山引擎流式 ASR（中文更准 + 实时字幕）

默认用本地 Whisper，零门槛。Whisper 在连续口语和专有名词（技术术语、人名）上准确率有限，且要录完整段才转写。切换到火山引擎豆包流式语音识别 2.0 后，中文识别更准，而且**边说边出字**（前端把 16kHz PCM 通过 WebSocket 实时推给后端转写）。

**开通**

1. 打开控制台：https://console.volcengine.com/speech/service/10007
2. 找到「豆包流式语音识别模型 2.0」，点击开通（试用 20 小时）
3. 在「API Key 管理」创建一个 API Key（新版控制台鉴权用 API Key）

**配置 `.env`**

```bash
STT_PROVIDER=volcengine_asr
VOLCENGINE_ASR_API_KEY=你的_API_Key
```

> 注：识别准确率仍受口音、网络、专有名词影响，识别错误可能拉低面试评分——回答中可在下方文本框手动订正后再提交。

**成本**：按识别小时数计费，试用 20 小时，资源包 900 元起。

改完后重建后端：

```bash
docker compose up -d --build backend
```

### 可选：切换到豆包 TTS（更像真人的中文情感语音）

默认用免费的 Edge TTS，零门槛。如果觉得 Edge 声音太"播音腔"、想要更自然有情感的中文面试官声音，可以切到火山引擎豆包语音合成大模型 2.0：

**开通**

1. 打开火山引擎语音技术控制台：https://console.volcengine.com/speech/service/10007
2. 找到「豆包语音合成模型 2.0」，点击开通（新用户有 2 万字符免费额度，约够 4-6 场完整面试）
3. 进入应用详情，复制 **APP ID** 和 **Access Token**

**配置 `.env`**

```bash
TTS_PROVIDER=doubao
DOUBAO_APP_ID=<你的 APP ID>
DOUBAO_ACCESS_TOKEN=<你的 Access Token>
DOUBAO_CLUSTER=volcano_tts
DOUBAO_VOICE_TYPE=zh_male_m191_uranus_bigtts   # 云舟（沉稳男声，默认推荐）
```

其他可选音色（2.0 通用场景）：
- `zh_male_m191_uranus_bigtts` — 云舟（沉稳男声）
- `zh_female_xiaohe_uranus_bigtts` — 小何（自然女声）
- `zh_male_taocheng_uranus_bigtts` — 小天（年轻男声）

**成本**：约 ¥15 / 百万字符，一场完整面试 ≈ ¥0.05。

改完后必须重建后端才会生效（restart 不会加载新的环境变量）：

```bash
docker compose up -d --build backend
```

## Roadmap

- [x] 多轮语音面试 + 智能追问
- [x] 三档面试官人格差异化
- [x] Answer 音频回放
- [x] 面评报告 PDF 导出
- [x] 流式 ASR（火山引擎豆包 2.0），录音时实时出字幕
- [x] 情感 TTS（豆包 Seed TTS 2.0 可选 provider）
- [x] 精准 STT（火山引擎豆包 ASR 2.0 可选 provider）
- [ ] 流式 TTS（豆包 V3 WebSocket），降低首字延迟
- [ ] 表情驱动 Avatar（SadTalker / LivePortrait）
- [ ] Prompt 版本化管理
- [ ] 面试历史管理 + 多用户

## 相关关键词（帮助搜索引擎收录）

AI 面试系统 | AI 陪面工具 | AI 模拟面试 | AI 面试官 | 智能面试 | 大模型面试 | LangGraph 面试 Agent | 语音面试 | 模拟面试软件 | 面试练习工具 | Mock Interview Bot | AI Interview Simulator | LLM Interviewer | Voice Interview Agent | Interview Preparation

## License

MIT

## 反馈 / 贡献

Issues 和 PR 都欢迎。如果这个项目对你有帮助，点个 ⭐ 支持一下。
