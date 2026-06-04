"""
步频工坊 / Cadence Studio

依赖安装：
    pip install PyQt6 pydub librosa pyrubberband pyttsx3 numpy

运行前还需要安装 FFmpeg，并确保 ffmpeg / ffprobe 已加入 PATH：
    Windows: https://ffmpeg.org/download.html 或 https://www.gyan.dev/ffmpeg/builds/
    macOS:   brew install ffmpeg
    Linux:   sudo apt install ffmpeg

pyrubberband 还依赖 Rubber Band 命令行工具：
    Windows: https://breakfastquay.com/rubberband/
    macOS:   brew install rubberband
    Linux:   sudo apt install rubberband-cli

PyInstaller 打包示例：
    pyinstaller --noconfirm --windowed --name "Cadence Studio" ^
      --add-data "assets/metronome_beat_170bpm.wav;assets" ^
      --hidden-import=librosa --hidden-import=librosa.core --hidden-import=soundfile ^
      --hidden-import=pyrubberband --hidden-import=pyttsx3.drivers ^
      --hidden-import=pyttsx3.drivers.sapi5 --hidden-import=pydub ^
      cadence_studio.py

如需图标，可加入：
    --icon icon.ico
"""

from __future__ import annotations

import math
import os
import shutil
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np
import pyttsx3
import pyrubberband as pyrb
from pydub import AudioSegment
from pydub.generators import Sine, WhiteNoise
from PyQt6.QtCore import QThread, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtMultimedia import QSoundEffect
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QRadioButton,
    QSlider,
    QSpinBox,
    QDoubleSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


SUPPORTED_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}
OUTPUT_FORMATS = {
    "MP3": {"extension": "mp3", "format": "mp3", "bitrate": True},
    "WAV": {"extension": "wav", "format": "wav", "bitrate": False},
    "FLAC": {"extension": "flac", "format": "flac", "bitrate": False},
    "M4A": {"extension": "m4a", "format": "ipod", "bitrate": True, "codec": "aac"},
    "OGG": {"extension": "ogg", "format": "ogg", "bitrate": True, "codec": "libvorbis"},
}
APP_TITLE = "步频工坊 Cadence Studio"
METRONOME_PRESETS = {
    "classic": ("经典滴答", "现有正弦滴答音色，每一拍独立响一次"),
    "footstep": ("强风吹拂节拍", "从 170bpm 强风吹拂素材中提取的节拍切片"),
    "timer": ("电子计时器", "明亮的电子提示音，适合在音乐里穿透"),
}


def app_resource_path(relative_path: str) -> Path:
    """获取开发环境或 PyInstaller 打包环境中的资源路径。"""
    base_dir = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_dir / relative_path


@dataclass
class AudioFile:
    path: Path
    bpm: float | None = None


@dataclass
class Reminder:
    minute: float
    text: str


@dataclass
class ConvertSettings:
    source_path: Path
    original_bpm: float
    fixed_mode: bool
    fixed_bpm: int
    start_bpm: int
    end_bpm: int
    gradient_minutes: float
    target_minutes: float
    over_mode: str
    under_mode: str
    metronome_volume: int
    metronome_sound: str
    enable_voice: bool
    reminder_interval: float
    reminders: list[Reminder]
    voice_gender: str
    output_format: str
    bitrate: str
    output_dir: Path


def has_ffmpeg() -> bool:
    """检测 FFmpeg 是否可用。pydub 需要 ffmpeg 和 ffprobe 才能读写常见音频格式。"""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def detect_bpm(path: Path) -> float:
    """使用 librosa 检测 BPM。只负责节奏检测，不做音频处理。"""
    y, sr = librosa.load(str(path), sr=None, mono=True, duration=180)
    tempo = librosa.feature.rhythm.tempo(y=y, sr=sr, aggregate=None)
    if tempo is None or len(tempo) == 0:
        raise ValueError("无法检测 BPM")
    bpm = float(np.median(tempo))
    if not math.isfinite(bpm) or bpm <= 0:
        raise ValueError("检测到的 BPM 无效")
    return round(bpm, 2)


def audiosegment_to_np(audio: AudioSegment) -> tuple[np.ndarray, int]:
    """将 pydub.AudioSegment 转成 pyrubberband/soundfile 兼容的 float32 数组。

    soundfile.write 期望二维音频为 frames x channels。之前如果转成
    channels x frames，会被当成“超多声道”临时 WAV，导致 Format not recognised。
    """
    audio = audio.set_sample_width(2)
    samples = np.array(audio.get_array_of_samples()).astype(np.float32)
    channels = audio.channels
    if channels > 1:
        samples = samples.reshape((-1, channels))
    samples /= float(1 << 15)
    return samples, audio.frame_rate


def np_to_audiosegment(data: np.ndarray, sample_rate: int, channels: int) -> AudioSegment:
    """将 pyrubberband 输出的 float numpy 数组转回 AudioSegment。"""
    data = np.clip(data, -1.0, 1.0)
    if data.ndim == 1 and channels > 1:
        channels = 1
    if data.ndim == 2:
        channels = data.shape[1]
        data = data.reshape(-1)
    pcm = (data * (1 << 15)).astype(np.int16).tobytes()
    return AudioSegment(
        data=pcm,
        sample_width=2,
        frame_rate=sample_rate,
        channels=channels,
    )


def time_stretch_audio(audio: AudioSegment, rate: float) -> AudioSegment:
    """使用 Rubber Band 做变速不变调。rate > 1 表示加快，rate < 1 表示放慢。"""
    if abs(rate - 1.0) < 0.005:
        return audio
    channels = audio.channels
    samples, sr = audiosegment_to_np(audio)
    stretched = pyrb.time_stretch(samples, sr, rate)
    return np_to_audiosegment(stretched, sr, channels)


def loop_to_length(audio: AudioSegment, target_ms: int) -> AudioSegment:
    """循环填充到目标时长。"""
    if len(audio) >= target_ms:
        return audio[:target_ms]
    loops = math.ceil(target_ms / max(1, len(audio)))
    return (audio * loops)[:target_ms]


def trim_loudest_section(audio: AudioSegment, target_ms: int) -> AudioSegment:
    """按窗口 RMS 峰值截取能量较高的一段，作为简单的“高潮段”判断。"""
    if len(audio) <= target_ms:
        return audio
    step_ms = 5000
    best_start = 0
    best_rms = -1
    last_start = max(0, len(audio) - target_ms)
    for start in range(0, last_start + 1, step_ms):
        rms = audio[start : start + target_ms].rms
        if rms > best_rms:
            best_rms = rms
            best_start = start
    return audio[best_start : best_start + target_ms]


def make_tick(sound_id: str, volume_db: float = -3.0) -> AudioSegment:
    """按预设生成单个节拍声。每一拍独立响一次，不再做四拍强弱分组。"""
    if sound_id == "footstep":
        asset_path = app_resource_path("assets/metronome_beat_170bpm.wav")
        if asset_path.exists():
            return (
                AudioSegment.from_file(asset_path)
                .set_channels(1)
                .apply_gain(volume_db)
                .fade_in(2)
                .fade_out(45)
            )
        noise = WhiteNoise().to_audio_segment(duration=55, volume=volume_db - 2).low_pass_filter(2200)
        thump = Sine(120).to_audio_segment(duration=45, volume=volume_db - 5)
        return noise.overlay(thump).fade_in(2).fade_out(28)
    if sound_id == "timer":
        ping = Sine(1250).to_audio_segment(duration=42, volume=volume_db)
        edge = Sine(2500).to_audio_segment(duration=18, volume=volume_db - 5)
        return ping.overlay(edge).fade_in(1).fade_out(18)
    return Sine(600).to_audio_segment(duration=70, volume=volume_db).fade_in(3).fade_out(25)


def generate_metronome_fixed(
    duration_ms: int, bpm: float, volume_percent: int, sound_id: str = "classic"
) -> AudioSegment:
    """生成固定 BPM 节拍器。"""
    bed = AudioSegment.silent(duration=duration_ms)
    if volume_percent <= 0 or bpm <= 0:
        return bed
    gain = -36 + (volume_percent / 100) * 30
    tick = make_tick(sound_id, volume_db=gain)
    interval_ms = 60000.0 / bpm
    pos = 0.0
    while pos < duration_ms:
        bed = bed.overlay(tick, position=int(pos))
        pos += interval_ms
    return bed


def bpm_at_time(start_bpm: float, end_bpm: float, total_ms: int, pos_ms: int) -> float:
    """按时间线性插值获取当前位置 BPM。"""
    if total_ms <= 0:
        return end_bpm
    ratio = min(1.0, max(0.0, pos_ms / total_ms))
    return start_bpm + (end_bpm - start_bpm) * ratio


def generate_metronome_gradient(
    duration_ms: int,
    start_bpm: float,
    end_bpm: float,
    volume_percent: int,
    sound_id: str = "classic",
    gradient_ms: int | None = None,
) -> AudioSegment:
    """生成跟随渐变 BPM 的节拍器。每个节拍位置使用当下线性插值得到的 BPM。"""
    bed = AudioSegment.silent(duration=duration_ms)
    if volume_percent <= 0:
        return bed
    gain = -36 + (volume_percent / 100) * 30
    tick = make_tick(sound_id, volume_db=gain)
    pos = 0.0
    timeline_ms = gradient_ms or duration_ms
    while pos < duration_ms:
        current = bpm_at_time(start_bpm, end_bpm, timeline_ms, int(pos))
        bed = bed.overlay(tick, position=int(pos))
        pos += 60000.0 / max(1.0, current)
    return bed


def synthesize_tts(text: str, gender: str, temp_dir: Path) -> AudioSegment:
    """使用 pyttsx3 离线合成语音，并返回 AudioSegment。"""
    engine = pyttsx3.init()
    voices = engine.getProperty("voices") or []
    want_male = gender == "男"
    selected_id = None
    for voice in voices:
        name = f"{getattr(voice, 'name', '')} {getattr(voice, 'id', '')}".lower()
        if want_male and ("male" in name or "huihui" in name or "kangkang" in name):
            selected_id = voice.id
            break
        if not want_male and ("female" in name or "zira" in name or "huihui" in name):
            selected_id = voice.id
            break
    if selected_id:
        engine.setProperty("voice", selected_id)
    engine.setProperty("rate", 175)
    out_path = temp_dir / f"tts_{abs(hash(text))}.wav"
    engine.save_to_file(text, str(out_path))
    engine.runAndWait()
    return AudioSegment.from_file(out_path)


class DropListWidget(QListWidget):
    files_dropped = pyqtSignal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setMinimumHeight(95)
        self.setStyleSheet(
            "QListWidget { border: 2px dashed #8aa1b4; border-radius: 8px; padding: 12px; }"
        )

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = []
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() in SUPPORTED_EXTS:
                paths.append(path)
        if paths:
            self.files_dropped.emit(paths)


class BPMDetectWorker(QThread):
    done = pyqtSignal(str, float)
    failed = pyqtSignal(str, str)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def run(self) -> None:
        try:
            self.done.emit(str(self.path), detect_bpm(self.path))
        except Exception as exc:
            self.failed.emit(str(self.path), str(exc))


class ConvertWorker(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)
    warn = pyqtSignal(str)

    def __init__(self, settings: ConvertSettings) -> None:
        super().__init__()
        self.settings = settings

    def run(self) -> None:
        try:
            out_path = self.convert()
            self.finished_ok.emit(str(out_path))
        except Exception:
            self.failed.emit(traceback.format_exc())

    def convert(self) -> Path:
        """按要求串行执行：加载、变速、时长处理、节拍器、语音、导出。"""
        s = self.settings
        target_ms = int(s.target_minutes * 60 * 1000)
        self.progress.emit(5, "检测BPM / 加载音频")
        audio = AudioSegment.from_file(s.source_path)
        audio = audio.set_channels(2).set_frame_rate(44100)

        if s.fixed_mode:
            self.progress.emit(22, "变速处理")
            rate = s.fixed_bpm / s.original_bpm
            self.check_rate(rate)
            processed = time_stretch_audio(audio, rate)
            processed = self.fit_duration(processed, target_ms)
            target_label = f"{s.fixed_bpm}"
        else:
            self.progress.emit(18, "变速处理 / 渐变步频分段")
            processed = self.process_gradient(audio, target_ms)
            target_label = f"{s.start_bpm}-{s.end_bpm}"

        self.progress.emit(55, "叠加节拍器")
        if s.fixed_mode:
            metro = generate_metronome_fixed(
                len(processed), s.fixed_bpm, s.metronome_volume, s.metronome_sound
            )
        else:
            metro = generate_metronome_gradient(
                len(processed),
                s.start_bpm,
                s.end_bpm,
                s.metronome_volume,
                s.metronome_sound,
                int(s.gradient_minutes * 60 * 1000),
            )
        processed = processed.overlay(metro)

        if s.enable_voice:
            self.progress.emit(72, "合成语音")
            processed = self.overlay_reminders(processed)

        self.progress.emit(90, "导出中")
        format_info = OUTPUT_FORMATS[s.output_format]
        extension = format_info["extension"]
        name = f"{s.source_path.stem}_{target_label}bpm_{int(s.target_minutes)}min.{extension}"
        safe_name = "".join(c for c in name if c not in r'\/:*?"<>|')
        out_path = s.output_dir / safe_name
        export_kwargs = {"format": format_info["format"]}
        if format_info.get("bitrate"):
            export_kwargs["bitrate"] = s.bitrate
        if "codec" in format_info:
            export_kwargs["codec"] = format_info["codec"]
        processed.export(out_path, **export_kwargs)
        self.progress.emit(100, "转换完成")
        return out_path

    def check_rate(self, rate: float) -> None:
        """变速比例超过 ±30% 时警告，但不中断。"""
        if rate < 0.7 or rate > 1.3:
            self.warn.emit(f"当前变速比例为 {rate:.2f}x，超过 ±30%，音质可能明显变化。")

    def fit_duration(self, audio: AudioSegment, target_ms: int) -> AudioSegment:
        """根据用户设置处理过长或过短的音频。"""
        s = self.settings
        if len(audio) > target_ms:
            if s.over_mode == "智能截取高潮段":
                return trim_loudest_section(audio, target_ms)
            return audio[:target_ms]
        if len(audio) < target_ms and s.under_mode == "循环填充":
            return loop_to_length(audio, target_ms)
        return audio

    def process_gradient(self, audio: AudioSegment, target_ms: int) -> AudioSegment:
        """渐变模式：每 5 秒一段，每段 BPM 线性插值后独立变速。"""
        s = self.settings
        source = self.fit_duration(audio, target_ms)
        if len(source) < target_ms and s.under_mode == "保持原长":
            target_ms = len(source)
        chunk_ms = 5000
        gradient_ms = int(s.gradient_minutes * 60 * 1000)
        chunks = []
        for start in range(0, target_ms, chunk_ms):
            end = min(target_ms, start + chunk_ms)
            chunk = source[start:end]
            current_bpm = bpm_at_time(s.start_bpm, s.end_bpm, gradient_ms, start)
            rate = current_bpm / s.original_bpm
            self.check_rate(rate)
            chunks.append(time_stretch_audio(chunk, rate))
            done = 18 + int(32 * (end / max(1, target_ms)))
            self.progress.emit(done, f"变速处理 / {current_bpm:.0f} BPM")
        combined = sum(chunks, AudioSegment.silent(duration=0))
        return self.fit_duration(combined, target_ms)

    def overlay_reminders(self, audio: AudioSegment) -> AudioSegment:
        """把用户设置的语音提醒混合到指定分钟位置。"""
        s = self.settings
        reminders = list(s.reminders)
        if s.reminder_interval > 0:
            minute = s.reminder_interval
            while minute < len(audio) / 60000:
                if not any(abs(r.minute - minute) < 0.01 for r in reminders):
                    reminders.append(Reminder(minute, "保持步频"))
                minute += s.reminder_interval
        if not reminders:
            return audio

        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = Path(tmp)
            for index, reminder in enumerate(reminders, start=1):
                if not reminder.text.strip():
                    continue
                segment = synthesize_tts(reminder.text.strip(), s.voice_gender, temp_dir)
                pos = int(reminder.minute * 60 * 1000)
                if 0 <= pos < len(audio):
                    audio = audio.overlay(segment + 3, position=pos)
                self.progress.emit(72 + int(12 * index / max(1, len(reminders))), "合成语音")
        return audio


class MetronomePickerDialog(QDialog):
    def __init__(
        self,
        parent: "CadenceStudio",
        current_sound: str,
        bpm: float,
        volume_percent: int,
    ) -> None:
        super().__init__(parent)
        self.parent_window = parent
        self.selected_sound = current_sound
        self.bpm = bpm
        self.volume_percent = volume_percent
        self.radio_group = QButtonGroup(self)

        self.setWindowTitle("选择节拍音色")
        self.resize(430, 260)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("选择一种节拍声。每个拍子都会使用同一个独立声响，不再组成四拍强弱单元。"))

        for sound_id, (name, desc) in METRONOME_PRESETS.items():
            row = QHBoxLayout()
            radio = QRadioButton(name)
            radio.setChecked(sound_id == current_sound)
            radio.toggled.connect(lambda checked, sid=sound_id: self.set_selected(sid) if checked else None)
            self.radio_group.addButton(radio)

            text = QLabel(desc)
            text.setWordWrap(True)
            preview = QPushButton("试听")
            preview.clicked.connect(lambda _, sid=sound_id: self.preview_sound(sid))

            row.addWidget(radio)
            row.addWidget(text, 1)
            row.addWidget(preview)
            layout.addLayout(row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addStretch(1)
        layout.addWidget(buttons)

    def set_selected(self, sound_id: str) -> None:
        self.selected_sound = sound_id

    def preview_sound(self, sound_id: str) -> None:
        audio = generate_metronome_fixed(6000, self.bpm, self.volume_percent, sound_id)
        self.parent_window.play_preview_audio(audio, "节拍预览失败")

    def done(self, result: int) -> None:
        self.parent_window.stop_preview_audio()
        super().done(result)


class CadenceStudio(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.files: dict[str, AudioFile] = {}
        self.detect_workers: list[BPMDetectWorker] = []
        self.convert_worker: ConvertWorker | None = None
        self.last_output: Path | None = None
        self.metronome_sound = "classic"
        self.preview_effect: QSoundEffect | None = None
        self.preview_temp_path: Path | None = None

        self.setWindowTitle(APP_TITLE)
        self.resize(600, 500)
        self.build_ui()
        self.check_ffmpeg_on_start()

    def build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 10, 12, 10)
        self.setCentralWidget(central)

        self.drop_list = DropListWidget()
        self.drop_list.addItem("拖拽音频到这里，支持 mp3 / wav / flac / m4a / ogg")
        self.drop_list.files_dropped.connect(self.add_files)
        self.drop_list.currentRowChanged.connect(self.update_status)
        root.addWidget(self.drop_list)

        pick_row = QHBoxLayout()
        add_button = QPushButton("选择文件")
        add_button.clicked.connect(self.pick_files)
        remove_button = QPushButton("移除选中")
        remove_button.clicked.connect(self.remove_selected)
        pick_row.addWidget(add_button)
        pick_row.addWidget(remove_button)
        pick_row.addStretch(1)
        root.addLayout(pick_row)

        tabs = QTabWidget()
        tabs.addTab(self.make_cadence_tab(), "步频")
        tabs.addTab(self.make_duration_tab(), "时长")
        tabs.addTab(self.make_voice_tab(), "语音提醒")
        tabs.addTab(self.make_export_tab(), "导出")
        root.addWidget(tabs, 1)

        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.status_text = QLabel("等待文件")
        progress_row.addWidget(self.progress_bar, 1)
        progress_row.addWidget(self.status_text)
        root.addLayout(progress_row)

        action_row = QHBoxLayout()
        self.convert_button = QPushButton("开始转换")
        self.convert_button.clicked.connect(self.start_convert)
        self.open_output_button = QPushButton("打开输出文件夹")
        self.open_output_button.clicked.connect(self.open_output_folder)
        self.open_output_button.setEnabled(False)
        action_row.addStretch(1)
        action_row.addWidget(self.convert_button)
        action_row.addWidget(self.open_output_button)
        root.addLayout(action_row)

        self.statusBar().showMessage("未加载文件")

        about = QAction("关于", self)
        about.triggered.connect(self.show_about)
        self.menuBar().addAction(about)

    def make_cadence_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        self.fixed_radio = QRadioButton("固定步频模式")
        self.gradient_radio = QRadioButton("渐变步频模式")
        self.fixed_radio.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.fixed_radio)
        group.addButton(self.gradient_radio)

        fixed_box = QGroupBox("固定步频")
        fixed_form = QFormLayout(fixed_box)
        self.fixed_bpm = QSpinBox()
        self.fixed_bpm.setRange(120, 200)
        self.fixed_bpm.setValue(170)
        fixed_form.addRow(self.fixed_radio, self.fixed_bpm)

        gradient_box = QGroupBox("渐变步频")
        gradient_form = QFormLayout(gradient_box)
        self.start_bpm = QSpinBox()
        self.start_bpm.setRange(80, 240)
        self.start_bpm.setValue(160)
        self.end_bpm = QSpinBox()
        self.end_bpm.setRange(80, 240)
        self.end_bpm.setValue(180)
        self.gradient_minutes = QDoubleSpinBox()
        self.gradient_minutes.setRange(1, 300)
        self.gradient_minutes.setValue(30)
        self.gradient_minutes.setSuffix(" 分钟")
        gradient_form.addRow(self.gradient_radio)
        gradient_form.addRow("起始 BPM", self.start_bpm)
        gradient_form.addRow("结束 BPM", self.end_bpm)
        gradient_form.addRow("渐变时长", self.gradient_minutes)

        preview_row = QHBoxLayout()
        preview_button = QPushButton("节拍预览")
        preview_button.clicked.connect(self.preview_metronome)
        self.metro_sound_label = QLabel(METRONOME_PRESETS[self.metronome_sound][0])
        self.metro_volume = QSlider(Qt.Orientation.Horizontal)
        self.metro_volume.setRange(0, 100)
        self.metro_volume.setValue(35)
        preview_row.addWidget(preview_button)
        preview_row.addWidget(QLabel("当前音色"))
        preview_row.addWidget(self.metro_sound_label)
        preview_row.addWidget(QLabel("节拍器音量"))
        preview_row.addWidget(self.metro_volume, 1)

        layout.addWidget(fixed_box)
        layout.addWidget(gradient_box)
        layout.addLayout(preview_row)
        layout.addStretch(1)
        return tab

    def make_duration_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        self.target_minutes = QDoubleSpinBox()
        self.target_minutes.setRange(1, 300)
        self.target_minutes.setValue(30)
        self.target_minutes.setSuffix(" 分钟")
        self.over_mode = QComboBox()
        self.over_mode.addItems(["截取开头", "智能截取高潮段"])
        self.under_mode = QComboBox()
        self.under_mode.addItems(["循环填充", "保持原长"])
        form.addRow("目标时长", self.target_minutes)
        form.addRow("超出时", self.over_mode)
        form.addRow("不足时", self.under_mode)
        return tab

    def make_voice_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        top = QHBoxLayout()
        self.enable_voice = QCheckBox("启用语音提醒")
        self.reminder_interval = QDoubleSpinBox()
        self.reminder_interval.setRange(0, 300)
        self.reminder_interval.setValue(5)
        self.reminder_interval.setSuffix(" 分钟")
        self.voice_gender = QComboBox()
        self.voice_gender.addItems(["女", "男"])
        top.addWidget(self.enable_voice)
        top.addWidget(QLabel("提醒间隔"))
        top.addWidget(self.reminder_interval)
        top.addWidget(QLabel("语音"))
        top.addWidget(self.voice_gender)
        layout.addLayout(top)

        self.reminder_table = QTableWidget(0, 2)
        self.reminder_table.setHorizontalHeaderLabels(["触发时间（分钟）", "提醒文本"])
        self.reminder_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.reminder_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.reminder_table)

        row = QHBoxLayout()
        add_button = QPushButton("添加提醒")
        add_button.clicked.connect(lambda: self.add_reminder_row(5, "保持步频"))
        del_button = QPushButton("删除提醒")
        del_button.clicked.connect(self.delete_reminder_row)
        preview_button = QPushButton("语音试听")
        preview_button.clicked.connect(self.preview_voice)
        row.addWidget(add_button)
        row.addWidget(del_button)
        row.addWidget(preview_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.add_reminder_row(5, "保持步频")
        self.add_reminder_row(15, "还剩一半")
        self.add_reminder_row(25, "冲刺了")
        return tab

    def make_export_tab(self) -> QWidget:
        tab = QWidget()
        form = QFormLayout(tab)
        self.output_format = QComboBox()
        self.output_format.addItems(OUTPUT_FORMATS.keys())
        self.output_format.currentTextChanged.connect(self.update_output_format_ui)
        self.bitrate = QComboBox()
        self.bitrate.addItems(["192k", "320k"])
        self.output_dir = QLineEdit(str(Path.cwd()))
        browse_button = QPushButton("选择输出目录")
        browse_button.clicked.connect(self.pick_output_dir)
        row_widget = QWidget()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.output_dir, 1)
        row.addWidget(browse_button)
        row_widget.setLayout(row)
        self.template_label = QLabel()
        form.addRow("输出格式", self.output_format)
        form.addRow("比特率", self.bitrate)
        form.addRow("输出目录", row_widget)
        form.addRow("文件名模板", self.template_label)
        self.update_output_format_ui()
        return tab

    def check_ffmpeg_on_start(self) -> None:
        if not has_ffmpeg():
            QMessageBox.warning(
                self,
                "缺少 FFmpeg",
                "未检测到 FFmpeg / ffprobe。请安装 FFmpeg 并加入 PATH 后再处理音频。\n\n"
                "下载地址：https://ffmpeg.org",
            )

    def add_files(self, paths: list[Path]) -> None:
        if self.drop_list.count() == 1 and self.drop_list.item(0).text().startswith("拖拽"):
            self.drop_list.clear()
        for path in paths:
            key = str(path)
            if key in self.files:
                continue
            self.files[key] = AudioFile(path=path)
            item = QListWidgetItem(f"{path.name}  |  BPM: 检测中")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.drop_list.addItem(item)
            self.start_bpm_detection(path)
        if self.drop_list.currentRow() < 0:
            self.drop_list.setCurrentRow(0)

    def start_bpm_detection(self, path: Path) -> None:
        worker = BPMDetectWorker(path)
        worker.done.connect(self.on_bpm_done)
        worker.failed.connect(self.on_bpm_failed)
        worker.finished.connect(lambda: self.detect_workers.remove(worker) if worker in self.detect_workers else None)
        self.detect_workers.append(worker)
        worker.start()

    def on_bpm_done(self, path: str, bpm: float) -> None:
        self.files[path].bpm = bpm
        self.refresh_file_item(path)
        self.update_status()

    def on_bpm_failed(self, path: str, error: str) -> None:
        value, ok = QInputDialog.getDouble(
            self,
            "BPM 检测失败",
            f"{Path(path).name}\n自动检测失败：{error}\n请手动输入原始 BPM：",
            160,
            40,
            260,
            2,
        )
        if ok:
            self.files[path].bpm = value
        self.refresh_file_item(path)
        self.update_status()

    def refresh_file_item(self, path: str) -> None:
        for row in range(self.drop_list.count()):
            item = self.drop_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == path:
                bpm = self.files[path].bpm
                item.setText(f"{Path(path).name}  |  BPM: {bpm:.2f}" if bpm else f"{Path(path).name}  |  BPM: 未设置")
                break

    def pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择音频文件",
            str(Path.cwd()),
            "Audio Files (*.mp3 *.wav *.flac *.m4a *.ogg)",
        )
        self.add_files([Path(f) for f in files])

    def remove_selected(self) -> None:
        row = self.drop_list.currentRow()
        if row < 0:
            return
        item = self.drop_list.takeItem(row)
        key = item.data(Qt.ItemDataRole.UserRole)
        if key in self.files:
            del self.files[key]
        if self.drop_list.count() == 0:
            self.drop_list.addItem("拖拽音频到这里，支持 mp3 / wav / flac / m4a / ogg")
        self.update_status()

    def update_status(self) -> None:
        current = self.current_audio()
        if not current:
            self.statusBar().showMessage("未加载文件")
            return
        bpm_text = f"{current.bpm:.2f}" if current.bpm else "未设置"
        self.statusBar().showMessage(f"当前文件：{current.path.name}    原始 BPM：{bpm_text}")

    def current_audio(self) -> AudioFile | None:
        item = self.drop_list.currentItem()
        if not item:
            return None
        key = item.data(Qt.ItemDataRole.UserRole)
        return self.files.get(key)

    def add_reminder_row(self, minute: float, text: str) -> None:
        row = self.reminder_table.rowCount()
        self.reminder_table.insertRow(row)
        self.reminder_table.setItem(row, 0, QTableWidgetItem(str(minute)))
        self.reminder_table.setItem(row, 1, QTableWidgetItem(text))

    def delete_reminder_row(self) -> None:
        row = self.reminder_table.currentRow()
        if row >= 0:
            self.reminder_table.removeRow(row)

    def read_reminders(self) -> list[Reminder]:
        reminders = []
        for row in range(self.reminder_table.rowCount()):
            minute_item = self.reminder_table.item(row, 0)
            text_item = self.reminder_table.item(row, 1)
            if not minute_item or not text_item:
                continue
            try:
                minute = float(minute_item.text())
            except ValueError:
                continue
            text = text_item.text().strip()
            if text:
                reminders.append(Reminder(minute, text))
        return reminders

    def play_preview_audio(self, audio: AudioSegment, error_title: str) -> None:
        try:
            self.stop_preview_audio()
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                temp_path = Path(tmp.name)
            audio.export(temp_path, format="wav")

            effect = QSoundEffect(self)
            effect.setSource(QUrl.fromLocalFile(str(temp_path)))
            effect.setLoopCount(1)
            effect.setVolume(1.0)
            effect.play()
            self.preview_effect = effect
            self.preview_temp_path = temp_path
        except Exception as exc:
            QMessageBox.critical(self, error_title, str(exc))

    def stop_preview_audio(self) -> None:
        if self.preview_effect is not None:
            self.preview_effect.stop()
            self.preview_effect.deleteLater()
            self.preview_effect = None
        if self.preview_temp_path is not None:
            try:
                self.preview_temp_path.unlink(missing_ok=True)
            except Exception:
                pass
            self.preview_temp_path = None

    def preview_metronome(self) -> None:
        bpm = self.fixed_bpm.value() if self.fixed_radio.isChecked() else self.start_bpm.value()
        dialog = MetronomePickerDialog(self, self.metronome_sound, bpm, self.metro_volume.value())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.metronome_sound = dialog.selected_sound
            self.metro_sound_label.setText(METRONOME_PRESETS[self.metronome_sound][0])

    def preview_voice(self) -> None:
        text = "保持步频"
        row = self.reminder_table.currentRow()
        if row >= 0 and self.reminder_table.item(row, 1):
            text = self.reminder_table.item(row, 1).text().strip() or text
        try:
            with tempfile.TemporaryDirectory() as tmp:
                audio = synthesize_tts(text, self.voice_gender.currentText(), Path(tmp))
                self.play_preview_audio(audio, "语音试听失败")
        except Exception as exc:
            QMessageBox.critical(self, "语音试听失败", str(exc))

    def pick_output_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录", self.output_dir.text())
        if directory:
            self.output_dir.setText(directory)

    def update_output_format_ui(self) -> None:
        output_format = self.output_format.currentText() if hasattr(self, "output_format") else "MP3"
        format_info = OUTPUT_FORMATS.get(output_format, OUTPUT_FORMATS["MP3"])
        extension = format_info["extension"]
        self.template_label.setText(f"{{原曲名}}_{{步频}}bpm_{{时长}}min.{extension}")
        self.bitrate.setEnabled(bool(format_info.get("bitrate")))

    def collect_settings(self) -> ConvertSettings | None:
        audio = self.current_audio()
        if not audio:
            QMessageBox.warning(self, "未选择文件", "请先拖入或选择一个音频文件。")
            return None
        if not audio.bpm:
            value, ok = QInputDialog.getDouble(self, "输入原始 BPM", "请输入原始 BPM：", 160, 40, 260, 2)
            if not ok:
                return None
            audio.bpm = value
            self.refresh_file_item(str(audio.path))
        output_dir = Path(self.output_dir.text()).expanduser()
        if not output_dir.exists():
            QMessageBox.warning(self, "输出目录不存在", "请选择有效的输出目录。")
            return None
        if not has_ffmpeg():
            QMessageBox.warning(
                self,
                "缺少 FFmpeg",
                "未检测到 FFmpeg / ffprobe，无法可靠读取或导出音频。\n下载地址：https://ffmpeg.org",
            )
            return None
        return ConvertSettings(
            source_path=audio.path,
            original_bpm=float(audio.bpm),
            fixed_mode=self.fixed_radio.isChecked(),
            fixed_bpm=self.fixed_bpm.value(),
            start_bpm=self.start_bpm.value(),
            end_bpm=self.end_bpm.value(),
            gradient_minutes=self.gradient_minutes.value(),
            target_minutes=self.target_minutes.value(),
            over_mode=self.over_mode.currentText(),
            under_mode=self.under_mode.currentText(),
            metronome_volume=self.metro_volume.value(),
            metronome_sound=self.metronome_sound,
            enable_voice=self.enable_voice.isChecked(),
            reminder_interval=self.reminder_interval.value(),
            reminders=self.read_reminders(),
            voice_gender=self.voice_gender.currentText(),
            output_format=self.output_format.currentText(),
            bitrate=self.bitrate.currentText(),
            output_dir=output_dir,
        )

    def start_convert(self) -> None:
        settings = self.collect_settings()
        if not settings:
            return
        target_bpm = settings.fixed_bpm if settings.fixed_mode else settings.end_bpm
        rate = target_bpm / settings.original_bpm
        if rate < 0.7 or rate > 1.3:
            ret = QMessageBox.warning(
                self,
                "变速比例较大",
                f"目标与原始 BPM 的比例为 {rate:.2f}x，超过 ±30%。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
        self.convert_button.setEnabled(False)
        self.open_output_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self.status_text.setText("准备转换")
        self.convert_worker = ConvertWorker(settings)
        self.convert_worker.progress.connect(self.on_convert_progress)
        self.convert_worker.warn.connect(lambda msg: QMessageBox.warning(self, "转换警告", msg))
        self.convert_worker.finished_ok.connect(self.on_convert_done)
        self.convert_worker.failed.connect(self.on_convert_failed)
        self.convert_worker.start()

    def on_convert_progress(self, value: int, text: str) -> None:
        self.progress_bar.setValue(value)
        self.status_text.setText(text)

    def on_convert_done(self, path: str) -> None:
        self.convert_button.setEnabled(True)
        self.open_output_button.setEnabled(True)
        self.last_output = Path(path)
        QMessageBox.information(self, "转换完成", f"已导出：\n{path}")

    def on_convert_failed(self, error: str) -> None:
        self.convert_button.setEnabled(True)
        self.status_text.setText("转换失败")
        friendly = error
        if "Format not recognised" in error:
            friendly = (
                "Rubber Band 临时音频写入失败。已修复多声道数组格式问题，"
                "请重新运行程序后再试。\n\n详细信息：\n" + error
            )
        elif "rubberband" in error.lower() and ("not found" in error.lower() or "No such file" in error):
            friendly = (
                "未找到 rubberband 命令行工具。请安装 Rubber Band 并把 rubberband.exe 加入 PATH。\n\n"
                "详细信息：\n" + error
            )
        QMessageBox.critical(self, "转换错误", friendly)

    def open_output_folder(self) -> None:
        target = self.last_output.parent if self.last_output else Path(self.output_dir.text())
        if sys.platform.startswith("win"):
            os.startfile(str(target))
        elif sys.platform == "darwin":
            os.system(f'open "{target}"')
        else:
            os.system(f'xdg-open "{target}"')

    def show_about(self) -> None:
        QMessageBox.information(
            self,
            "关于",
            "步频工坊 Cadence Studio\n\n"
            "用于跑步训练音乐的 BPM 调整、节拍器叠加与离线语音提醒生成。",
        )

    def closeEvent(self, event) -> None:
        self.stop_preview_audio()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    window = CadenceStudio()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
