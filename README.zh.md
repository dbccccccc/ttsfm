# TTSFM - 文本转语音 API 客户端

> **Language / 语言**: [English](README.md) | [中文](README.zh.md)

[![Docker Pulls](https://img.shields.io/docker/pulls/dbcccc/ttsfm?style=flat-square&logo=docker)](https://hub.docker.com/r/dbcccc/ttsfm)
[![GitHub Stars](https://img.shields.io/github/stars/dbccccccc/ttsfm?style=social)](https://github.com/dbccccccc/ttsfm)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](https://opensource.org/licenses/MIT)
![ghcr pulls](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fghcr-badge.elias.eu.org%2Fapi%2Fdbccccccc%2Fttsfm%2Fttsfm&query=downloadCount&label=ghcr+pulls&logo=github)

## Star History

[![Star History Chart](https://star-history.dera.page/svg?repos=dbccccccc/ttsfm&type=Date)](https://star-history.dera.page/#dbccccccc/ttsfm&Date)

## 概述

TTSFM 是一个免费的、兼容 OpenAI 的文本转语音 API 服务，提供将文本转换为自然语音的完整解决方案，使用OpenAI的GPT-4o mini TTS。基于 openai.fm 后端构建，提供强大的 Python SDK、RESTful API 接口以及直观的网页 Playground，方便测试和集成。

**TTSFM 的功能：**
- 🎤 **多种语音选择**：11 种兼容 OpenAI 的语音（alloy、ash、ballad、coral、echo、fable、nova、onyx、sage、shimmer、verse）
- 🎵 **灵活的音频格式**：支持 6 种音频格式（MP3、WAV、OPUS、AAC、FLAC、PCM）
- ⚡ **语速控制**：0.25x 到 4.0x 的播放速度调节，适应不同使用场景
- 📝 **长文本支持**：自动文本分割和音频合并，支持任意长度内容
- 🔄 **实时流式传输**：WebSocket 支持流式音频生成
- 🐍 **Python SDK**：易用的同步和异步客户端
- 🌐 **网页 Playground**：交互式网页界面，方便测试和实验
- 🐳 **Docker 就绪**：预构建的 Docker 镜像，即刻部署
- 🔍 **智能检测**：自动功能检测和友好的错误提示
- 🤖 **OpenAI 兼容**：可直接替代 OpenAI 的 TTS API

**v3.5.0 版本的主要特性：**
- 🎯 镜像变体检测（完整版 vs 精简版 Docker 镜像）
- 🔍 运行时功能 API，检查特性可用性
- ⚡ 基于 ffmpeg 的语速调节
- 🎵 所有 6 种音频格式的真实格式转换
- 📊 增强的错误处理，提供清晰、可操作的错误信息
- 🐳 针对不同使用场景优化的双镜像版本

> **⚠️ 免责声明**：本项目仅用于**学习和研究目的**。这是对 openai.fm 服务的逆向工程实现，不应用于商业用途或生产环境。用户需自行确保遵守适用的法律法规和服务条款。

## 安装

### Python 包

```bash
pip install ttsfm        # 核心客户端
pip install ttsfm[web]   # 核心客户端 + Web/服务端依赖
```

### Docker 镜像

TTSFM 提供两种 Docker 镜像变体以满足不同需求：

#### 完整版（推荐）
```bash
docker run -p 8000:8000 dbcccc/ttsfm:latest
```

**包含 ffmpeg，支持高级功能：**
- ✅ 所有 6 种音频格式（MP3、WAV、OPUS、AAC、FLAC、PCM）
- ✅ 语速调节（0.25x - 4.0x）
- ✅ 使用 ffmpeg 进行格式转换
- ✅ 长文本 MP3 自动合并
- ✅ 长文本 WAV 自动合并

#### 精简版
```bash
docker run -p 8000:8000 dbcccc/ttsfm:slim
```

**不含 ffmpeg 的最小化镜像：**
- ✅ 基础 TTS 功能
- ✅ 2 种音频格式（仅 MP3、WAV）
- ✅ 长文本 WAV 自动合并
- ❌ 不支持语速调节
- ❌ 不支持格式转换
- ❌ 不支持 MP3 自动合并

容器默认开放网页 Playground（`http://localhost:8000`）以及兼容 OpenAI 的 `/v1/audio/speech` 接口。

**检查可用功能：**
```bash
curl http://localhost:8000/api/capabilities
```

## 快速开始

### Python 客户端

```python
from ttsfm import TTSClient, AudioFormat, Voice

client = TTSClient()

# 基础用法
response = client.generate_speech(
    text="来自 TTSFM 的问候！",
    voice=Voice.ALLOY,
    response_format=AudioFormat.MP3,
)
response.save_to_file("hello")  # -> hello.mp3

# 使用语速调节（需要 ffmpeg）
response = client.generate_speech(
    text="这段语音会更快！",
    voice=Voice.NOVA,
    response_format=AudioFormat.MP3,
    speed=1.5,  # 1.5 倍速（范围：0.25 - 4.0）
)
response.save_to_file("fast")  # -> fast.mp3
```

### 命令行

```bash
ttsfm "你好，世界" --voice nova --format mp3 --output hello.mp3
```

### REST API（兼容 OpenAI）

```bash
# 基础请求
curl -X POST http://localhost:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{
    "model": "tts-1",
    "input": "你好，世界",
    "voice": "alloy",
    "response_format": "mp3"
  }' --output speech.mp3

# 使用语速调节（需要完整版镜像）
curl -X POST http://localhost:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{
    "model": "tts-1",
    "input": "你好，世界",
    "voice": "alloy",
    "response_format": "mp3",
    "speed": 1.5
  }' --output speech_fast.mp3
```

**可用语音：** alloy、ash、ballad、coral、echo、fable、nova、onyx、sage、shimmer、verse
**可用格式：** mp3、wav（始终可用）+ opus、aac、flac、pcm（仅完整版镜像）
**语速范围：** 0.25 - 4.0（需要完整版镜像）

## 了解更多

- 在 [Web 文档](http://localhost:8000/docs)（或 `ttsfm-web/templates/docs.html`）查看完整接口说明与运行注意事项。
- 查看 [架构概览](docs/architecture.md) 了解组件间的关系。
- 欢迎参与贡献，流程说明请见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

TTSFM 采用 [MIT 许可证](LICENSE) 发布。

## LinuxDo

- [LinuxDo](https://linux.do)
