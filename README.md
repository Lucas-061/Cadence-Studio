# Cadence Studio / 步频工坊

Cadence Studio 是一个用于跑步训练的桌面音频工具。用户可以导入音乐文件，设置目标步频和训练时长，软件会尝试调整音乐 BPM、叠加节拍器音色与语音提醒，并导出适合跑步使用的训练音频文件。

## 当前最小可运行系统

主程序文件：

```text
cadence_studio.py
```

运行方式：

进入项目目录后运行：

```powershell
python cadence_studio.py
```

Windows 开发环境也可以直接双击项目根目录下的 `run.bat` 启动程序。`run.bat` 会自动进入当前项目目录，并执行：

```bat
python cadence_studio.py
```

如果程序启动失败，批处理窗口会停留，方便查看错误信息。

Python 依赖：

```powershell
pip install PyQt6 pydub librosa pyrubberband pyttsx3 numpy
```

外部依赖：

- FFmpeg：用于 pydub 读取和导出音频。
- Rubber Band：用于 pyrubberband 进行变速不变调。

## 目前已实现的功能

- PyQt6 桌面 GUI 主窗口。
- 支持拖拽或选择音频文件。
- 当前已确认支持导入 `.mp3` 音频；`.wav`、`.flac`、`.m4a`、`.ogg` 入口已保留，但尚未完成稳定性测试。
- 支持多个音频文件加入列表，并选择其中一个进行处理。
- 使用 librosa 自动检测 BPM。
- BPM 检测失败时，允许用户手动输入原始 BPM。
- 状态栏显示当前文件名和原始 BPM。
- 固定步频模式，默认目标 BPM 为 170。
- 渐变步频模式，支持起始 BPM、结束 BPM、渐变时长。
- 目标时长设置，默认 30 分钟。
- 音频过长时支持“截取开头”和“智能截取高潮段”。
- 音频不足时支持“循环填充”和“保持原长”。
- 节拍器音量滑条。
- 节拍音色选择弹窗。
- 当前包含 3 种节拍音色：
  - 经典滴答
  - 强风吹拂节拍
  - 电子计时器
- 节拍试听支持热切换：试听一个音色时，点击另一个音色会立即停止当前播放并播放新音色。
- 节拍器叠加到导出音乐中。
- 节拍器每一拍使用独立声响，不再使用四拍强弱循环。
- 语音提醒开关。
- 自定义提醒文本列表。
- 每条提醒支持设置触发时间点。
- 支持提醒间隔设置。
- 使用 pyttsx3 离线合成语音提醒。
- 支持语音试听。
- 支持选择男声/女声。
- 导出界面支持选择输出格式：MP3、WAV、FLAC、M4A、OGG。
- 支持选择 192k / 320k 比特率。
- 支持选择输出目录。
- 支持转换进度条和实时状态文字。
- 转换完成后弹窗提示，并提供打开输出文件夹按钮。
- 启动和转换前检测 FFmpeg。
- 变速比例超过 ±30% 时给出警告，但允许继续。
- 转换过程放在后台线程中执行，避免主界面长时间卡死。
- 已加入项目内节拍资源：

```text
assets/metronome_beat_170bpm.wav
```

## 已知待测试 / 未通过完整验证的功能

以下功能已有代码实现，但尚未作为稳定功能确认，需要后续逐项测试：

- 端到端转换流程：导入真实音频后完整完成 BPM 检测、变速、节拍叠加、语音叠加、音频导出。
- 非 MP3 输入格式：`.wav`、`.flac`、`.m4a`、`.ogg` 的导入、BPM 检测和转换稳定性。
- 非 MP3 输出格式：WAV、FLAC、M4A、OGG 的导出和播放兼容性。
- pyrubberband 变速不变调在不同系统环境下的稳定性。
- Rubber Band 命令行工具缺失、路径异常、版本不兼容时的错误提示。
- FFmpeg / ffprobe 在不同安装方式下的检测准确性。
- librosa BPM 检测对长音频、弱节奏音频、复杂编曲音频的准确性。
- 渐变步频模式的音质表现和节拍同步效果。
- 智能截取高潮段算法目前基于 RMS 音量峰值，效果需要用更多歌曲验证。
- 语音提醒混音时的音量、清晰度和与音乐冲突情况。
- pyttsx3 男/女声选择依赖系统已安装语音包，不保证每台电脑都能准确匹配。
- QSoundEffect 预览播放在不同 Windows 音频设备下的兼容性。
- PyInstaller 打包后的资源路径、音频依赖和运行稳定性。

## 当前不稳定或需要谨慎处理的点

- `pyrubberband` 依赖系统中的 Rubber Band 命令行工具，单独 `pip install pyrubberband` 不够。
- pydub 依赖 FFmpeg，缺少 FFmpeg 时部分音频无法读取或导出。
- 真实音乐变速比例过大时，音质可能明显下降。
- 渐变步频会分段变速，极端参数下可能产生听感不连续。
- 当前“强风吹拂节拍”来自本地素材提取的短 WAV 资源，后续移动或打包项目时必须保留 `assets` 目录。

## 后续开发原则

此项目后续修改必须遵守以下原则：

- 始终以“最小可运行系统”为基础进行修改。
- 每次只针对一个确定的小功能进行优化或添加。
- 不随意重构现有基础框架。
- 不改变当前主程序入口、主窗口结构和核心处理流程，除非明确需要。
- 修改前先确认当前功能是否还能运行。
- 修改后至少做语法检查。
- 涉及音频转换、预览播放、导出流程的改动，需要单独记录测试结果。
- 不删除已有功能，除非明确说明该功能要废弃。
- 不引入新的大型框架或复杂依赖，优先沿用当前 PyQt6、pydub、librosa、pyrubberband、pyttsx3 技术栈。

## 建议测试清单

每次修改代码后，至少检查：

```powershell
python -c "from pathlib import Path; compile(Path('cadence_studio.py').read_text(encoding='utf-8'), 'cadence_studio.py', 'exec'); print('syntax ok')"
```

功能测试建议：

- 启动程序是否成功。
- 拖入或选择音频文件是否成功。
- BPM 检测是否有结果。
- 节拍预览弹窗是否能打开。
- 三种节拍音色是否都能试听。
- 连续点击不同试听按钮是否能热切换。
- 固定步频转换是否能导出目标格式。
- 渐变步频转换是否能导出目标格式。
- 启用语音提醒后是否能导出目标格式。
- 输出文件是否能正常播放。

## PyInstaller 打包参考

当前仍以命令行运行 `python cadence_studio.py` 作为最小可运行方式。后续可以考虑将工具打包成 `.exe`，让用户通过双击启动，而不需要输入命令行。

计划方向：

- 使用 PyInstaller 生成 Windows `.exe`。
- 使用 `--windowed` 参数隐藏命令行窗口。
- 保留 `assets` 目录中的节拍音色资源。
- 后续可新增 `build_exe.bat`，让开发者双击脚本完成打包。
- 打包完成后，可为 `dist\Cadence Studio\Cadence Studio.exe` 创建桌面快捷方式。
- 代码当前暂不为该功能做改动，先保留为后续打包优化事项。

```powershell
pyinstaller --noconfirm --windowed --name "Cadence Studio" ^
  --add-data "assets/metronome_beat_170bpm.wav;assets" ^
  --hidden-import=librosa --hidden-import=librosa.core --hidden-import=soundfile ^
  --hidden-import=pyrubberband --hidden-import=pyttsx3.drivers ^
  --hidden-import=pyttsx3.drivers.sapi5 --hidden-import=pydub ^
  cadence_studio.py
```

如需图标：

```powershell
--icon icon.ico
```
