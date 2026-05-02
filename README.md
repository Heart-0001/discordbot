# 🎵 Discord Music Bot

個人 Discord 音樂 Bot，使用 YouTube Music API 搜尋，支援 Autoplay 自動推薦、隨機播放清單、插播等功能。

---

## 功能特色

- 🎵 **YouTube Music 優先搜尋** — 使用 ytmusicapi 在 YTMusic 歌曲庫搜尋，自動評分挑選最官方的版本（官方 Audio / 官方 MV 優先，過濾翻唱、字幕版、卡拉OK）
- 🔀 **Autoplay 自動推薦** — 歌曲播完後根據 YouTube Music Radio 自動接下一首，背景預載零間隔
- 📋 **播放清單支援** — 貼上 YouTube 播放清單連結，全部歌曲加入 queue（無數量上限）
- 🔀 **隨機播放清單** — 播放清單自動打亂順序
- ⏩ **插播下一首** — 插入歌曲到 queue 第一位

---

## 安裝與設定

### 1. 環境需求

- Python 3.10+
- FFmpeg（需加入 PATH 或修改 `cogs/music.py` 內的 `FFMPEG_PATH`）

### 2. 安裝依賴套件

```bash
pip install -r requirements.txt
pip install ytmusicapi
```

### 3. 設定 Bot Token

在專案根目錄建立 `.env` 檔案：

```env
DISCORD_TOKEN=你的_Bot_Token
```

> Bot Token 從 [Discord Developer Portal](https://discord.com/developers/applications) 取得。
> 需要開啟 **Message Content Intent** 與 **Voice States Intent**。

### 4. 設定 FFmpeg 路徑

開啟 `cogs/music.py`，找到第 15 行：

```python
FFMPEG_PATH = r'C:\你的\FFmpeg\路徑\ffmpeg.exe'
```

改成你實際的 FFmpeg 執行檔路徑。

### 5. 啟動 Bot

```bash
python bot.py
```

---

## 指令一覽

### 🎵 播放相關

| 指令 | 說明 |
|------|------|
| `/play <歌名或連結>` | 搜尋歌曲或貼上 YouTube / YouTube Music 連結播放。支援播放清單連結。 |
| `/randomlist <播放清單連結或歌名>` | 和 `/play` 相同，但會先隨機打亂所有歌曲順序再加入 queue。 |
| `/nextplay <歌名或連結>` | 搜尋或貼連結，將歌曲**插入到 queue 第一位**，目前這首播完立刻接它。 |
| `/pause` | 暫停播放。 |
| `/resume` | 繼續播放。 |
| `/skip` | 跳過目前正在播放的歌曲。 |
| `/stop` | 停止播放並清空 queue，但 Bot 留在語音頻道。 |
| `/disconnect` | 停止播放、清空 queue 並讓 Bot 離開語音頻道。 |

### 📋 隊列管理

| 指令 | 說明 |
|------|------|
| `/queue` | 查看目前播放中的歌曲與 queue 清單。若 Autoplay 開啟，也會顯示預載的下一首推薦。 |
| `/nowplaying` | 查看目前播放歌曲的詳細資訊。 |
| `/remove <位置>` | 從 queue 移除指定位置的歌曲（從 1 開始計算）。 |
| `/remove <開始> <結尾>` | 從 queue 移除一個範圍的歌曲，例如 `/remove 2 5` 移除第 2 到第 5 首。 |

### 🔀 Autoplay

| 指令 | 說明 |
|------|------|
| `/autoplay` | 開啟或關閉 Autoplay。開啟後 queue 播完會自動根據 YouTube Music Radio 推薦下一首。 |
| `/skipautoplay` | 不喜歡 Autoplay 預載的下一首？用這個指令換一首新的推薦（不會跳掉目前的歌）。 |

### 🔊 其他

| 指令 | 說明 |
|------|------|
| `/volume <0-100>` | 調整播放音量，範圍 0 到 100。 |

---

## 搜尋邏輯說明

```
輸入文字（歌名）
  └─ YouTube Music 搜尋（取前 5 個候選）
       └─ 評分挑最佳：
            + 標題與關鍵字重疊率
            + 頻道名含 official / vevo
            + 標題含「official」或「audio」
            - 標題含 cover / remix / lyrics / live / karaoke 等
       └─ 選出最高分 → 播放
       （若 YouTube Music 完全失敗 → 退回 YouTube 搜尋）

輸入連結（YouTube / YouTube Music）
  └─ 直接抓取該影片 / 播放清單

Autoplay 推薦
  └─ ytmusicapi.get_watch_playlist(radio=True)
       └─ 過濾已播過的歌（依標題比對）→ 預載串流 URL → 零間隔接播
```

---

## 檔案結構

```
discordbot/
├── bot.py              # Bot 主程式，負責啟動與 slash command 同步
├── cogs/
│   └── music.py        # 所有音樂功能邏輯
├── requirements.txt    # Python 依賴套件清單
└── .env                # Bot Token（不要上傳到 GitHub）
```

---

## 注意事項

- `.env` 內的 Bot Token 請勿上傳至 GitHub，確認 `.gitignore` 有排除 `.env`。
- FFmpeg 需要另外安裝，Windows 可從 [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) 下載。
- YouTube 串流 URL 有時效性，長時間暫停後可能需要重新播放。
- ytmusicapi 不需要帳號登入即可使用搜尋與 Radio 推薦功能。
