# Offer Master

AI 模拟面试系统。上传简历 + JD，选择面试轮次（一面 peer / 二面 high peer / 三面直属经理），AI 面试官以 Agent 模式与你语音对话、动态追问、逐题打分，最终产出带 PDF 的面试报告。

## 技术栈

- 前端：Next.js 14 + TypeScript + Tailwind + shadcn/ui
- 后端：FastAPI + SQLAlchemy + LangGraph
- 数据库：MySQL 8
- LLM：OpenAI 兼容协议（默认 DeepSeek）
- STT：faster-whisper（本地）
- TTS：edge-tts（免费，微软音色）
- 部署：docker-compose 一键起

## 快速开始

```bash
cp .env.example .env
# 编辑 .env 填入 OPENAI_API_KEY（DeepSeek 或其他兼容供应商）

docker compose up -d --build
```

- 前端：http://localhost:3000
- 后端 API：http://localhost:8000/docs
- MySQL：本地 3307 端口（容器内 3306）

首次启动 backend 会下载 whisper medium 模型（~1.5GB），耐心等待。模型会缓存到 `whisper_cache` volume，后续启动秒开。

## 面试流程

1. 上传简历 (PDF/DOCX) + JD 描述
2. 选择面试轮次（1~3 轮）：peer → high peer → 直属经理
3. 每轮 Agent 生成 5~8 个主题问题，按 push-to-talk 语音作答
4. Agent 判断是否追问以探知识边界
5. 每题维度打分，每轮 ≥70 分晋级下一轮
6. 结束生成 Markdown + PDF 面评报告

## 目录结构

```
offerMaster/
├── docker-compose.yml
├── .env.example
├── frontend/            Next.js
└── backend/             FastAPI + LangGraph
```

## 配置项说明

见 `.env.example` 顶部注释，可切换 LLM/STT/TTS provider，调整每轮题量、追问激进度、通过阈值等。
