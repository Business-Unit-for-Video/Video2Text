# Video2Text

Video2Text 用 GitHub Actions 自动转写和整理视频字幕，当前采用**兼容式统一**：保留原有工作流、输出目录和续跑状态，同时新增统一入口和跨目录统计/切片能力。

## 当前工作流

| 工作流 | 文件 | 用途 |
|---|---|---|
| 统一入口 | `.github/workflows/transcribe_unified.yml` | 手动选择任务类型，然后分发到现有转写工作流；不迁移历史目录。 |
| 张雪峰空间转写 | `.github/workflows/transcribe.yml` | 继续原有 `transcripts/` + `state/` 流程。 |
| B 站合集转写 | `.github/workflows/transcribe_collection.yml` | 输出到 `destinations/<destination>/`，状态在 `state_collections/<destination>/`。 |
| 单视频转写 | `.github/workflows/transcribe_single_video.yml` | 输出到 `single_videos/<destination>/`，状态在 `state_single_video/<destination>/`。 |
| YouTube 频道转写 | `.github/workflows/transcribe_youtube_channel.yml` | 输出到 `youtube_channels/<destination>/`，状态在 `state_youtube/<destination>/`。 |
| 构建检索切片 | `.github/workflows/build-transcript-chunks.yml` | 扫描带时间戳字幕，产出 `chunks.jsonl` artifact。 |
| 统计字幕时长 | `.github/workflows/stat_transcripts_duration.yml` | 扫描带时间戳字幕，产出时长/字符统计 artifact。 |

## 统一入口使用方式

在 GitHub Actions 里运行 **transcribe-unified**，按任务类型填写参数：

| source_type | 分发目标 | 必填参数 |
|---|---|---|
| `zhangxuefeng_space` | `transcribe.yml` | 可不填 `source_url` / `destination`。 |
| `bilibili_collection` | `transcribe_collection.yml` | `source_url`、`destination`、可选 `whisper_model`。 |
| `single_video` | `transcribe_single_video.yml` | `source_url`、`destination`、`platform`、`use_cookies`、可选 `whisper_model`。 |
| `youtube_channel` | `transcribe_youtube_channel.yml` | `source_url`、`destination`、`include_members`、可选 `whisper_model`。 |

统一入口会把新任务写到 `unified_outputs/<source_type>/<destination>/`，旧 workflow 直接运行仍保留原输出目录。

## 输出目录约定

统计和切片工作流会扫描以下带时间戳文本：

```text
transcripts/*.txt
destinations/*/with_timestamps/*.txt
single_videos/*/with_timestamps/*.txt
youtube_channels/*/with_timestamps/*.txt
unified_outputs/*/*/with_timestamps/*.txt
```

说明：

- `plain/*.txt` 不纳入统计和切片，避免和 `with_timestamps/*.txt` 重复计数。
- 支持两种时间戳格式：
  - `[HH:MM:SS - HH:MM:SS] 文本`
  - `[MM:SS.mmm --> MM:SS.mmm] 文本`
- 报告里的 `file_name` 使用相对路径，避免不同目录下同名文件冲突。

## 手动刷新统计/切片

如果转写工作流产生了新字幕，但辅助统计没有自动刷新，可以手动运行：

1. **build-transcript-chunks**：生成 `transcript-chunks` artifact，包含：
   - `output/chunks.jsonl`
   - `output/chunk_build_stats.json`
   - `output/summary.md`
2. **stat-transcripts-duration**：生成 `transcript-stats-report` artifact，包含：
   - `state/transcript_stats_report.json`
   - `state/transcript_stats_files.csv`

这两个辅助工作流只读仓库内容并上传 artifact，不触发实际转写。

## 兼容原则

- 不删除旧 workflow。
- 旧 workflow 直跑保持原输出目录。
- 统一入口的新任务写入 `unified_outputs/...`。
- 历史 state/续跑文件不迁移。
