# ================================================
# レトロ風ドット絵 動画変換ツール
#
# 【必須インストールコマンド】
# pip install --upgrade pillow numpy opencv-python tkinterdnd2 scikit-learn
#
# 【別途必要】
# ffmpeg をインストールして PATH に通してください。
# Windows なら winget install Gyan.FFmpeg など。
# ================================================

import os
import re
import math
import time
import shutil
import tempfile
import subprocess
import threading
import queue
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    DND_AVAILABLE = True
except ImportError:
    TkinterDnD = None
    DND_FILES = None
    DND_AVAILABLE = False

import cv2
import numpy as np
from PIL import Image, ImageTk, ImageEnhance, ImageFilter
from sklearn.cluster import MiniBatchKMeans


VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v")
PREVIEW_MAX_SIZE = (300, 300)


def even_int(value: float) -> int:
    """動画エンコードで安全な偶数サイズに丸める。"""
    n = int(round(value))
    if n < 2:
        return 2
    return n if n % 2 == 0 else n + 1


def parse_size_from_label(label: str):
    """コンボ表示文字列から 640x360 のようなサイズを抜き出す。"""
    match = re.search(r"(\d+)x(\d+)", label)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def format_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    minutes = int(seconds // 60)
    secs = seconds - minutes * 60
    return f"{minutes:02d}:{secs:05.2f}"


class RetroVideoConverter:
    def __init__(self, root):
        self.root = root
        self.root.title("レトロ風ドット絵 動画変換ツール")

        window_width = 1280
        window_height = 1000
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        x_coordinate = (screen_width // 2) - (window_width // 2)
        y_coordinate = (screen_height // 2) - (window_height // 2)
        root.geometry(f"{window_width}x{window_height}+{x_coordinate}+{y_coordinate}")

        self.video_path = None
        self.cap = None
        self.video_w = 0
        self.video_h = 0
        self.fps = 30.0
        self.frame_count = 0
        self.duration = 0.0
        self.current_frame_index = 0
        self.seek_after_id = None
        self.is_converting = False
        self.last_preview_settings_key = None
        self.input_img_tk = None
        self.output_img_tk = None
        self.ui_queue = queue.Queue()

        self.size_map = {}

        self.var_folder_overwrite = tk.BooleanVar(value=False)
        self.last_loaded_size_labels = []

        # 再エンコード時の画質/容量プリセット。video-cutter.py と同じ内容。
        # CRFを下げるほど高画質・大容量、上げるほど低容量・劣化増。
        self.reencode_presets = {
            "最高画質 CRF18 / slow": {"crf": "18", "preset": "slow", "audio_bitrate": "192k"},
            "おすすめ 高画質小さめ CRF20 / slow": {"crf": "20", "preset": "slow", "audio_bitrate": "160k"},
            "標準小さめ CRF23 / slow": {"crf": "23", "preset": "slow", "audio_bitrate": "128k"},
            "容量優先 CRF25 / veryslow": {"crf": "25", "preset": "veryslow", "audio_bitrate": "96k"},
            "かなり小さめ CRF28 / veryslow": {"crf": "28", "preset": "veryslow", "audio_bitrate": "96k"},
        }
        self.default_reencode_preset_name = "おすすめ 高画質小さめ CRF20 / slow"
        self.reencode_quality_var = tk.StringVar(value=self.default_reencode_preset_name)

        main_frame = tk.Frame(root)
        main_frame.pack(fill="both", expand=True, padx=15, pady=10)

        # ====================== 左エリア ======================
        left_frame = tk.Frame(main_frame, width=680)
        left_frame.pack(side="left", fill="both", expand=True, padx=(0, 20))

        tk.Label(
            left_frame,
            text="レトロ風ドット絵 動画変換ツール",
            font=("メイリオ", 16, "bold"),
            anchor="w"
        ).pack(fill="x", pady=8)

        self.btn_select = tk.Button(
            left_frame,
            text="1. 動画を選択",
            command=self.select_video,
            width=25,
            height=2
        )
        self.btn_select.pack(anchor="w", pady=8)

        self.lbl_input = tk.Label(left_frame, text="入力動画: 未選択", anchor="w")
        self.lbl_input.pack(fill="x", pady=(0, 4))

        self.lbl_video_info = tk.Label(left_frame, text="動画情報: -", anchor="w", fg="#555555")
        self.lbl_video_info.pack(fill="x", pady=(0, 10))

        preview_area = tk.Frame(left_frame)
        preview_area.pack(pady=10)

        input_frame = tk.LabelFrame(preview_area, text="入力動画フレーム", font=("メイリオ", 9))
        input_frame.pack(side="left", padx=15)
        self.input_preview_frame = tk.Frame(input_frame, width=320, height=320, bg="#e8e8e8", relief="sunken", bd=3)
        self.input_preview_frame.pack(padx=10, pady=10)
        self.input_preview_frame.pack_propagate(False)
        self.lbl_input_preview = tk.Label(self.input_preview_frame, bg="#e8e8e8")
        self.lbl_input_preview.place(relx=0.5, rely=0.5, anchor="center")

        output_frame = tk.LabelFrame(preview_area, text="出力プレビュー", font=("メイリオ", 9))
        output_frame.pack(side="left", padx=15)
        self.output_preview_frame = tk.Frame(output_frame, width=320, height=320, bg="#e8e8e8", relief="sunken", bd=3)
        self.output_preview_frame.pack(padx=10, pady=10)
        self.output_preview_frame.pack_propagate(False)
        self.lbl_output_preview = tk.Label(self.output_preview_frame, bg="#e8e8e8")
        self.lbl_output_preview.place(relx=0.5, rely=0.5, anchor="center")

        # ====================== シークバー ======================
        seek_frame = tk.LabelFrame(left_frame, text="プレビュー位置", font=("メイリオ", 9))
        seek_frame.pack(fill="x", pady=(10, 5), padx=10)

        self.seek_var = tk.IntVar(value=0)
        self.seek_scale = tk.Scale(
            seek_frame,
            from_=0,
            to=0,
            orient="horizontal",
            variable=self.seek_var,
            command=self.on_seek_changed,
            length=600,
            state="disabled"
        )
        self.seek_scale.pack(fill="x", padx=10, pady=(6, 0))

        seek_bottom = tk.Frame(seek_frame)
        seek_bottom.pack(fill="x", padx=10, pady=(0, 8))

        self.lbl_time = tk.Label(seek_bottom, text="00:00.00 / 00:00.00", anchor="w")
        self.lbl_time.pack(side="left")

        self.btn_refresh_preview = tk.Button(
            seek_bottom,
            text="現在フレームを再プレビュー",
            command=self.update_preview_from_seek,
            state="disabled"
        )
        self.btn_refresh_preview.pack(side="right")

        # ====================== ボタンエリア ======================
        button_frame = tk.Frame(left_frame)
        button_frame.pack(fill="x", pady=20)

        filename_frame = tk.Frame(button_frame)
        filename_frame.pack(anchor="w", pady=(0, 10))
        tk.Label(filename_frame, text="出力ファイル名:", font=("メイリオ", 10)).pack(side="left")
        self.var_output_name = tk.StringVar(value="")
        tk.Entry(filename_frame, textvariable=self.var_output_name, width=45).pack(side="left", padx=(5, 5))
        tk.Label(filename_frame, text="※空なら 元ファイル名_retro.mp4", fg="#777777", font=("メイリオ", 9)).pack(side="left")

        self.chk_folder_overwrite = tk.Checkbutton(
            button_frame,
            text="✅フォルダの場合は上書きモードで即時変換保存する",
            variable=self.var_folder_overwrite,
            font=("メイリオ", 10, "bold"),
            fg="red",
            anchor="w"
        )
        self.chk_folder_overwrite.pack(anchor="w", pady=(0, 8))

        self.btn_convert = tk.Button(
            button_frame,
            text="2. 動画をレトロ風に変換して保存！",
            command=self.convert_video,
            bg="#00FF00",
            fg="black",
            font=("メイリオ", 14, "bold"),
            width=30,
            height=4,
            state="disabled"
        )
        self.btn_convert.pack(side="left", padx=(0, 10))

        progress_frame = tk.Frame(left_frame)
        progress_frame.pack(fill="x", pady=(0, 5), padx=10)
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill="x")
        self.lbl_progress = tk.Label(progress_frame, text="待機中", anchor="w", fg="#555555")
        self.lbl_progress.pack(fill="x", pady=(4, 0))

        # ====================== 右エリア ======================
        right_frame = tk.Frame(main_frame, width=560)
        right_frame.pack(side="right", fill="both", expand=True)

        tk.Label(right_frame, text="設定オプション", font=("メイリオ", 12, "bold"), anchor="w").pack(fill="x", pady=(10, 15))

        tk.Label(right_frame, text="プリセット", font=("メイリオ", 10), anchor="w").pack(fill="x", pady=(0, 2))
        self.preset_var = tk.StringVar(value="")
        self.preset_combo = ttk.Combobox(right_frame, textvariable=self.preset_var, state="readonly", width=38)
        self.preset_combo["values"] = ("", "PC98", "PC88", "MSX", "MSX2", "MSX2 インターレース")
        self.preset_combo.pack(anchor="w", pady=2)
        self.preset_combo.bind("<<ComboboxSelected>>", self.apply_preset)

        tk.Label(right_frame, text="出力サイズ", font=("メイリオ", 10), anchor="w").pack(fill="x", pady=(8, 0))
        self.size_var = tk.StringVar(value="幅 320（縦横比維持）")
        self.size_combo = ttk.Combobox(right_frame, textvariable=self.size_var, state="readonly", width=38)
        self.size_combo.pack(anchor="w", pady=2)
        self.size_combo.bind("<<ComboboxSelected>>", self.on_size_selected)

        custom_frame = tk.Frame(right_frame)
        custom_frame.pack(anchor="w", pady=(2, 4))
        tk.Label(custom_frame, text="カスタム:", font=("メイリオ", 9)).pack(side="left")
        self.var_custom_w = tk.StringVar(value="")
        self.var_custom_h = tk.StringVar(value="")
        self.custom_w_entry = tk.Entry(custom_frame, textvariable=self.var_custom_w, width=7, justify="center", state="disabled")
        self.custom_w_entry.pack(side="left", padx=(5, 2))
        tk.Label(custom_frame, text="x", font=("メイリオ", 9)).pack(side="left")
        self.custom_h_entry = tk.Entry(custom_frame, textvariable=self.var_custom_h, width=7, justify="center", state="disabled")
        self.custom_h_entry.pack(side="left", padx=(2, 5))
        self.btn_custom_apply = tk.Button(custom_frame, text="適用", command=self.apply_custom_size, state="disabled")
        self.btn_custom_apply.pack(side="left")

        tk.Label(right_frame, text="エンコ品質", font=("メイリオ", 10), anchor="w").pack(fill="x", pady=(8, 0))
        self.quality_combo = ttk.Combobox(
            right_frame,
            textvariable=self.reencode_quality_var,
            state="readonly",
            width=38,
        )
        self.quality_combo["values"] = tuple(self.reencode_presets.keys())
        self.quality_combo.pack(anchor="w", pady=2)

        self.init_size_options()

        tk.Label(right_frame, text="色数", font=("メイリオ", 10), anchor="w").pack(fill="x", pady=(10, 0))
        self.colors_var = tk.StringVar(value="8")
        self.colors_combo = ttk.Combobox(right_frame, textvariable=self.colors_var, state="readonly", width=38)
        self.colors_combo["values"] = ("4", "8", "16", "32", "64", "128", "256", "512")
        self.colors_combo.pack(anchor="w", pady=2)
        self.colors_combo.bind("<<ComboboxSelected>>", self.on_setting_changed)

        tk.Label(right_frame, text="リサイズ方法", font=("メイリオ", 10), anchor="w").pack(fill="x", pady=(10, 0))
        self.resize_var = tk.StringVar(value="Bicubic (高品質・滑らか)")
        self.resize_combo = ttk.Combobox(right_frame, textvariable=self.resize_var, state="readonly", width=38)
        self.resize_combo["values"] = ("Nearest", "Bilinear", "Bicubic (高品質・滑らか)")
        self.resize_combo.pack(anchor="w", pady=2)
        self.resize_combo.bind("<<ComboboxSelected>>", self.on_setting_changed)

        self.var_pre_dot = tk.BooleanVar(value=True)
        self.chk_pre_dot = tk.Checkbutton(
            right_frame,
            text="✅ 事前ドット化（出力サイズへ先に縮小）",
            variable=self.var_pre_dot,
            font=("メイリオ", 10),
            anchor="w",
            command=self.on_setting_changed
        )
        self.chk_pre_dot.pack(fill="x", pady=(12, 4))

        self.var_outline = tk.BooleanVar(value=False)
        self.chk_outline = tk.Checkbutton(
            right_frame,
            text="✅ 輪郭線を黒に統一（レトロ風アウトライン強化）",
            variable=self.var_outline,
            font=("メイリオ", 10),
            anchor="w",
            command=self.on_setting_changed
        )
        self.chk_outline.pack(fill="x", pady=4)

        tk.Label(right_frame, text="ドット化強調（縮小→拡大）", font=("メイリオ", 10), anchor="w").pack(fill="x", pady=(12, 0))
        self.var_dot_boost = tk.StringVar(value="なし")
        self.dot_combo = ttk.Combobox(right_frame, textvariable=self.var_dot_boost, state="readonly", width=38)
        self.dot_combo["values"] = ("なし", "10%", "20%", "30%", "40%", "50%", "60%", "70%", "80%", "90%")
        self.dot_combo.pack(anchor="w", pady=2)
        self.dot_combo.bind("<<ComboboxSelected>>", self.on_setting_changed)
        tk.Label(right_frame, text="※小さい％ほど強いドット強調（10%が最強）", fg="red", font=("メイリオ", 9), anchor="w").pack(fill="x")

        self.var_anti_alias = tk.BooleanVar(value=True)
        self.chk_anti_alias = tk.Checkbutton(
            right_frame,
            text="✅ アンチエイリアス（輪郭部分のみ）",
            variable=self.var_anti_alias,
            font=("メイリオ", 10),
            anchor="w",
            command=self.on_setting_changed
        )
        self.chk_anti_alias.pack(fill="x", pady=6)

        self.var_chromatic = tk.BooleanVar(value=False)
        self.chk_chromatic = tk.Checkbutton(
            right_frame,
            text="✅ 色ずれ（色収差）効果を追加",
            variable=self.var_chromatic,
            font=("メイリオ", 10),
            anchor="w",
            command=self.on_setting_changed
        )
        self.chk_chromatic.pack(fill="x", pady=(4, 0))

        chromatic_frame = tk.Frame(right_frame)
        chromatic_frame.pack(fill="x", padx=25, pady=2)
        tk.Label(chromatic_frame, text="ずれの強度 (ピクセル):", font=("メイリオ", 9)).pack(side="left")
        self.var_chromatic_intensity = tk.StringVar(value="2")
        self.entry_chromatic = tk.Entry(chromatic_frame, textvariable=self.var_chromatic_intensity, width=5, justify="center")
        self.entry_chromatic.pack(side="left", padx=5)
        self.entry_chromatic.bind("<KeyRelease>", self.on_setting_changed)

        self.var_exp_skin_protect = tk.BooleanVar(value=False)
        self.chk_exp_skin_protect = tk.Checkbutton(
            right_frame,
            text="✅ 肌の黒ドット撲滅（事前バリア＋事後補正）",
            variable=self.var_exp_skin_protect,
            font=("メイリオ", 10),
            anchor="w",
            command=self.on_setting_changed
        )
        self.chk_exp_skin_protect.pack(fill="x", pady=(4, 0))

        param_frame = tk.Frame(right_frame)
        param_frame.pack(fill="x", padx=25, pady=2)
        tk.Label(param_frame, text="1:事前バリア [", font=("メイリオ", 9)).pack(side="left")
        self.var_pre_thresh = tk.StringVar(value="130")
        self.entry_pre_thresh = tk.Entry(param_frame, textvariable=self.var_pre_thresh, width=4, justify="center")
        self.entry_pre_thresh.pack(side="left")
        tk.Label(param_frame, text="]   2:事後補正 [", font=("メイリオ", 9)).pack(side="left")
        self.var_post_thresh = tk.StringVar(value="45")
        self.entry_post_thresh = tk.Entry(param_frame, textvariable=self.var_post_thresh, width=4, justify="center")
        self.entry_post_thresh.pack(side="left")
        tk.Label(param_frame, text="]", font=("メイリオ", 9)).pack(side="left")
        self.entry_pre_thresh.bind("<KeyRelease>", self.on_setting_changed)
        self.entry_post_thresh.bind("<KeyRelease>", self.on_setting_changed)

        notice = (
            "出力は mp4 固定。\n"
            "ffmpeg が使える場合、元動画の音声を自動結合します。\n"
            "現状維持は各動画のサイズを出力時に自動解決します。\n"
            "フォルダD&Dの上書きモードはサブフォルダも処理します。"
        )
        tk.Label(right_frame, text=notice, fg="#555555", justify="left", anchor="w").pack(fill="x", pady=(18, 0))

        # ====================== Drag & Drop 設定 ======================
        if DND_AVAILABLE:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self.drop_video)

        self.root.after(80, self.poll_ui_queue)

    # ====================== スレッド/UI連携 ======================
    def poll_ui_queue(self):
        try:
            while True:
                msg = self.ui_queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    percent, text = msg[1], msg[2]
                    if percent is not None:
                        self.progress_var.set(max(0.0, min(100.0, float(percent))))
                    if text is not None:
                        self.lbl_progress.config(text=text)
                elif kind == "done_single":
                    output_path, quality_name = msg[1], msg[2]
                    self.progress_var.set(100)
                    self.lbl_progress.config(text=f"完了: {output_path}")
                    self.set_converting_ui(False)
                    messagebox.showinfo("変換完了", f"動画変換が完了しました！\nエンコ品質: {quality_name}\n{output_path}")
                elif kind == "done_folder":
                    converted, failed, backup_root, quality_name = msg[1], msg[2], msg[3], msg[4]
                    self.progress_var.set(100)
                    self.set_converting_ui(False)
                    if failed:
                        fail_text = "\n".join(f"- {os.path.basename(path)}: {err[:120]}" for path, err in failed[:8])
                        more = "" if len(failed) <= 8 else f"\n...ほか {len(failed) - 8} 件"
                        self.lbl_progress.config(text=f"完了（一部失敗）: 成功 {converted} / 失敗 {len(failed)}")
                        messagebox.showwarning(
                            "一括変換完了（一部失敗）",
                            f"変換成功: {converted}件 / 失敗: {len(failed)}件\n"
                            f"エンコ品質: {quality_name}\n"
                            f"バックアップ: {backup_root}\n\n{fail_text}{more}"
                        )
                    else:
                        self.lbl_progress.config(text=f"完了: {converted}件 / バックアップ {backup_root}")
                        messagebox.showinfo(
                            "一括変換完了",
                            f"{converted}件の動画を上書き変換しました。\nエンコ品質: {quality_name}\nバックアップ: {backup_root}"
                        )
                elif kind == "error":
                    title, text = msg[1], msg[2]
                    self.lbl_progress.config(text="エラーで停止")
                    self.set_converting_ui(False)
                    messagebox.showerror(title, text)
        except queue.Empty:
            pass
        self.root.after(80, self.poll_ui_queue)

    def post_progress(self, percent=None, text=None):
        self.ui_queue.put(("progress", percent, text))

    def start_worker(self, target, *args):
        worker = threading.Thread(target=target, args=args, daemon=True)
        worker.start()
        return worker

    # ====================== ファイル読み込み ======================
    def drop_video(self, event):
        paths = self.root.tk.splitlist(event.data)
        if not paths:
            return

        folder_paths = [p for p in paths if os.path.isdir(p)]
        if folder_paths:
            if self.var_folder_overwrite.get():
                self.convert_folder_overwrite(folder_paths[0])
            else:
                messagebox.showwarning(
                    "フォルダがドロップされました",
                    "フォルダを一括変換する場合は、赤字の上書きモードにチェックを入れてからドロップしてね。"
                )
            return

        valid_files = [f for f in paths if os.path.isfile(f) and f.lower().endswith(VIDEO_EXTENSIONS)]
        if not valid_files:
            messagebox.showwarning("エラー", "有効な動画ファイルがありません")
            return
        self.load_video(valid_files[0])

    def select_video(self):
        path = filedialog.askopenfilename(
            title="変換したい動画を選んでね",
            filetypes=[("Video files", "*.mp4 *.mov *.avi *.mkv *.webm *.m4v"), ("All files", "*.*")]
        )
        if not path:
            return
        self.load_video(path)

    def load_video(self, path):
        if self.cap is not None:
            self.cap.release()

        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            messagebox.showerror("エラー", "動画を開けませんでした")
            return

        self.video_path = path
        self.cap = cap
        self.video_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.video_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        self.fps = fps if fps and fps > 0 else 30.0
        self.frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if self.frame_count <= 0:
            self.frame_count = 1
        self.duration = self.frame_count / self.fps
        self.current_frame_index = 0
        self.last_preview_settings_key = None

        self.lbl_input.config(text=f"入力動画: {os.path.basename(path)}")
        self.lbl_video_info.config(
            text=f"動画情報: {self.video_w}x{self.video_h} / {self.fps:.3f}fps / {self.frame_count}フレーム / {format_time(self.duration)}"
        )

        base, _ = os.path.splitext(os.path.basename(path))
        self.var_output_name.set(f"{base}_retro.mp4")

        self.generate_size_options()
        self.seek_scale.config(state="normal", from_=0, to=max(0, self.frame_count - 1))
        self.seek_var.set(0)
        self.btn_refresh_preview.config(state="normal")
        self.btn_convert.config(state="normal")
        self.progress_var.set(0)
        self.lbl_progress.config(text="待機中")
        self.update_preview_from_seek()

    # ====================== 出力サイズ候補 ======================
    def init_size_options(self):
        """動画未読み込みでも使える基本サイズ候補。フォルダ一括変換でもこの候補を使う。"""
        self.size_map = {
            "現状維持": None,
            "幅 256（縦横比維持）": ("width", 256),
            "幅 320（縦横比維持）": ("width", 320),
            "幅 480（縦横比維持）": ("width", 480),
            "幅 640（縦横比維持）": ("width", 640),
            "幅 960（縦横比維持）": ("width", 960),
            "幅 1280（縦横比維持）": ("width", 1280),
            "幅 1920（縦横比維持）": ("width", 1920),
        }
        self.last_loaded_size_labels = []
        labels = list(self.size_map.keys()) + ["カスタム..."]
        self.size_combo["values"] = tuple(labels)
        if self.size_var.get() not in labels:
            self.size_var.set("現状維持")
        self.on_size_selected()

    def generate_size_options(self):
        """単体動画読み込み時は、その動画の縦横比に合う固定サイズ候補を追加する。"""
        if self.video_w <= 0 or self.video_h <= 0:
            return

        # 以前の読み込み動画由来の候補だけを消し、基本候補とカスタム候補は残す。
        for label in getattr(self, "last_loaded_size_labels", []):
            self.size_map.pop(label, None)
        self.last_loaded_size_labels = []

        ratio = self.video_w / self.video_h
        canonical = [
            ("16:9", 16 / 9, [(256, 144), (320, 180), (480, 270), (640, 360), (960, 540), (1280, 720), (1920, 1080)]),
            ("4:3", 4 / 3, [(256, 192), (320, 240), (480, 360), (640, 480), (800, 600), (1024, 768), (1440, 1080)]),
            ("1:1", 1.0, [(128, 128), (256, 256), (320, 320), (480, 480), (640, 640), (1024, 1024)]),
            ("9:16", 9 / 16, [(144, 256), (180, 320), (270, 480), (360, 640), (540, 960), (720, 1280), (1080, 1920)]),
        ]

        aspect_name = "custom"
        sizes = None
        for name, target_ratio, candidates in canonical:
            if abs(ratio - target_ratio) / target_ratio <= 0.025:
                aspect_name = name
                sizes = candidates
                break

        added_labels = []
        if sizes is not None:
            for w, h in sizes:
                if aspect_name == "16:9":
                    label_name = self.size_label_for_width(w, {320: "レトロ小", 480: "レトロ中", 640: "標準", 1280: "高解像度", 1920: "フルHD"})
                elif aspect_name == "4:3":
                    label_name = self.size_label_for_width(w, {320: "レトロ小", 480: "レトロ中", 640: "標準", 1024: "高解像度"})
                elif aspect_name == "1:1":
                    label_name = self.size_label_for_width(w, {256: "レトロ小", 480: "レトロ中", 640: "標準", 1024: "高解像度"})
                else:
                    label_name = self.size_label_for_width(h, {320: "レトロ小", 480: "レトロ中", 640: "標準", 1280: "高解像度", 1920: "フルHD"})
                label = f"{label_name} - {even_int(w)}x{even_int(h)}"
                if label not in self.size_map:
                    self.size_map[label] = (even_int(w), even_int(h))
                    added_labels.append(label)
        else:
            for width in (256, 320, 480, 640, 960, 1280, 1920):
                height = even_int(width / ratio)
                w = even_int(width)
                label_name = self.size_label_for_width(width, {320: "レトロ小", 480: "レトロ中", 640: "標準", 1280: "高解像度", 1920: "大"})
                label = f"{label_name} - {w}x{height}"
                if label not in self.size_map:
                    self.size_map[label] = (w, height)
                    added_labels.append(label)

        self.last_loaded_size_labels = added_labels
        current = self.size_var.get()
        basic_labels = [
            "現状維持",
            "幅 256（縦横比維持）",
            "幅 320（縦横比維持）",
            "幅 480（縦横比維持）",
            "幅 640（縦横比維持）",
            "幅 960（縦横比維持）",
            "幅 1280（縦横比維持）",
            "幅 1920（縦横比維持）",
        ]
        custom_labels = [label for label in self.size_map.keys() if label.startswith("カスタム - ")]
        labels = basic_labels + added_labels + custom_labels + ["カスタム..."]
        self.size_combo["values"] = tuple(labels)
        if current not in labels:
            self.size_var.set("現状維持")
        self.on_size_selected()

    @staticmethod
    def size_label_for_width(value, named_map):
        if value in named_map:
            return named_map[value]
        if value <= 320:
            return "小"
        if value <= 640:
            return "中"
        if value <= 1280:
            return "大"
        return "超大"

    def on_size_selected(self, event=None):
        is_custom = self.size_var.get() == "カスタム..."
        state = "normal" if is_custom else "disabled"
        self.custom_w_entry.config(state=state)
        self.custom_h_entry.config(state=state)
        self.btn_custom_apply.config(state=state)
        if not is_custom:
            size = self.get_selected_size()
            if size:
                self.var_custom_w.set(str(size[0]))
                self.var_custom_h.set(str(size[1]))
            self.update_preview_from_seek_delayed()

    def apply_custom_size(self):
        try:
            w = even_int(float(self.var_custom_w.get()))
            h = even_int(float(self.var_custom_h.get()))
        except ValueError:
            messagebox.showwarning("エラー", "カスタムサイズは数値で入力してね")
            return
        if w <= 0 or h <= 0:
            messagebox.showwarning("エラー", "カスタムサイズが不正です")
            return
        label = f"カスタム - {w}x{h}"
        self.size_map[label] = (w, h)
        values = list(self.size_combo["values"])
        if label not in values:
            insert_pos = max(0, len(values) - 1)
            values.insert(insert_pos, label)
            self.size_combo["values"] = tuple(values)
        self.size_var.set(label)
        self.on_size_selected()

    def get_selected_size_for_dimensions(self, src_w, src_h):
        return self.resolve_size_for_dimensions(src_w, src_h, self.size_var.get(), self.size_map)

    def get_selected_size(self):
        return self.get_selected_size_for_dimensions(self.video_w, self.video_h)

    # ====================== プレビュー ======================
    def on_seek_changed(self, _value=None):
        if self.is_converting:
            return
        self.update_preview_from_seek_delayed()

    def on_setting_changed(self, event=None):
        if self.is_converting:
            return
        self.update_preview_from_seek_delayed()

    def update_preview_from_seek_delayed(self):
        if self.video_path is None:
            return
        if self.seek_after_id:
            self.root.after_cancel(self.seek_after_id)
        self.seek_after_id = self.root.after(180, self.update_preview_from_seek)

    def read_frame(self, frame_index):
        if self.cap is None:
            return None
        frame_index = max(0, min(int(frame_index), self.frame_count - 1))
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame_bgr = self.cap.read()
        if not ok or frame_bgr is None:
            return None
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        return Image.fromarray(frame_rgb).convert("RGB")

    def update_preview_from_seek(self):
        if self.video_path is None:
            return
        self.seek_after_id = None
        idx = int(self.seek_var.get())
        self.current_frame_index = idx
        seconds = idx / self.fps if self.fps else 0
        self.lbl_time.config(text=f"{format_time(seconds)} / {format_time(self.duration)}  |  frame {idx + 1}/{self.frame_count}")

        src = self.read_frame(idx)
        if src is None:
            return

        out = self.process_frame(src)
        self.show_preview_image(src, self.lbl_input_preview, "input")
        self.show_preview_image(out, self.lbl_output_preview, "output")

    def show_preview_image(self, img, label_widget, target):
        preview = img.convert("RGB").copy()
        preview.thumbnail(PREVIEW_MAX_SIZE)
        tk_img = ImageTk.PhotoImage(preview)
        label_widget.config(image=tk_img)
        if target == "input":
            self.input_img_tk = tk_img
        else:
            self.output_img_tk = tk_img

    # ====================== プリセット ======================
    def apply_preset(self, event=None):
        preset = self.preset_var.get()
        if not preset:
            self.size_combo.config(state="readonly" if self.video_path else "disabled")
            self.colors_combo.config(state="readonly")
            self.update_preview_from_seek_delayed()
            return

        preset_map = {
            "PC98": ((640, 400), "16"),
            "PC88": ((640, 200), "8"),
            "MSX": ((256, 192), "16"),
            "MSX2": ((512, 212), "16"),
            "MSX2 インターレース": ((512, 424), "16"),
        }
        if preset in preset_map:
            size, colors = preset_map[preset]
            w, h = size
            label = f"{preset} - {w}x{h}"
            self.size_map[label] = (w, h)
            values = list(self.size_combo["values"])
            if label not in values:
                insert_pos = max(0, len(values) - 1)
                values.insert(insert_pos, label)
                self.size_combo["values"] = tuple(values)
            self.size_var.set(label)
            self.colors_var.set(colors)

        self.size_combo.config(state="disabled")
        self.colors_combo.config(state="disabled")
        self.update_preview_from_seek_delayed()

    # ====================== フレーム変換 ======================
    def get_resample_filter(self):
        return self.get_resample_filter_for_value(self.resize_var.get())

    @staticmethod
    def get_resample_filter_for_value(resize_mode):
        if "Bilinear" in resize_mode:
            return Image.BILINEAR
        if "Nearest" in resize_mode:
            return Image.NEAREST
        return Image.BICUBIC

    def collect_processing_settings(self):
        """Tkinter変数をメインスレッドで読み取り、ワーカー用に固定する。"""
        try:
            colors = int(self.colors_var.get())
        except ValueError:
            colors = 8
        try:
            pre_thresh = float(self.var_pre_thresh.get())
        except ValueError:
            pre_thresh = 130.0
        try:
            post_thresh = float(self.var_post_thresh.get())
        except ValueError:
            post_thresh = 45.0
        try:
            chromatic_intensity = int(self.var_chromatic_intensity.get())
        except ValueError:
            chromatic_intensity = 2

        return {
            "size_label": self.size_var.get(),
            "size_map": dict(self.size_map),
            "resize_mode": self.resize_var.get(),
            "pre_dot": self.var_pre_dot.get(),
            "outline": self.var_outline.get(),
            "anti_alias": self.var_anti_alias.get(),
            "dot_boost": self.var_dot_boost.get(),
            "skin_protect": self.var_exp_skin_protect.get(),
            "colors": colors,
            "pre_thresh": pre_thresh,
            "post_thresh": post_thresh,
            "preset": self.preset_var.get(),
            "chromatic": self.var_chromatic.get(),
            "chromatic_intensity": chromatic_intensity,
        }

    @staticmethod
    def resolve_size_for_dimensions(src_w, src_h, label, size_map):
        spec = size_map.get(label)
        if spec is None and label == "現状維持":
            if src_w <= 0 or src_h <= 0:
                return None
            return even_int(src_w), even_int(src_h)
        if isinstance(spec, tuple) and len(spec) == 2 and spec[0] == "width":
            if src_w <= 0 or src_h <= 0:
                return None
            target_w = even_int(spec[1])
            target_h = even_int(target_w * src_h / src_w)
            return target_w, target_h
        if isinstance(spec, tuple) and len(spec) == 2:
            return even_int(spec[0]), even_int(spec[1])
        parsed = parse_size_from_label(label)
        if parsed:
            return even_int(parsed[0]), even_int(parsed[1])
        if src_w and src_h:
            return even_int(src_w), even_int(src_h)
        return None

    def process_frame(self, frame_img, target_size=None):
        settings = self.collect_processing_settings()
        return self.process_frame_with_settings(frame_img, settings, target_size=target_size)

    def process_frame_with_settings(self, frame_img, settings, target_size=None):
        img = frame_img.convert("RGB")
        if target_size is None:
            target_size = self.resolve_size_for_dimensions(
                img.size[0], img.size[1], settings.get("size_label", "現状維持"), settings.get("size_map", {})
            )
        if target_size is None:
            target_size = img.size
        target_w, target_h = target_size
        resample = self.get_resample_filter_for_value(settings.get("resize_mode", "Bicubic"))

        # 動画版では余白背景処理を削除。
        # 事前ドット化ON: 先に出力サイズへ縮小してから効果をかける。軽くてドット感が強い。
        # 事前ドット化OFF: 元解像度で効果をかけ、最後に出力サイズへ縮小する。少し滑らか寄り。
        if settings.get("pre_dot", True) and img.size != (target_w, target_h):
            img = img.resize((target_w, target_h), resample)

        if settings.get("outline", False):
            img = self.enhance_outline_weak(img)

        if settings.get("anti_alias", True) and settings.get("outline", False):
            img = self.apply_anti_alias_to_outline(img)

        dot_boost_value = settings.get("dot_boost", "なし")
        if dot_boost_value != "なし":
            img = self.dot_boost(img, dot_boost_value)

        if settings.get("skin_protect", False):
            edges = img.filter(ImageFilter.FIND_EDGES).convert("L")
            smoothed = img.filter(ImageFilter.SMOOTH_MORE).filter(ImageFilter.SMOOTH_MORE)
            edge_mask = edges.point(lambda x: 255 if x > 20 else 0, "1")
            img = Image.composite(img, smoothed, edge_mask)

        img = self.bayer_ordered_dither(
            img,
            int(settings.get("colors", 8)),
            settings.get("preset", ""),
            skin_protect=bool(settings.get("skin_protect", False)),
            pre_thresh=float(settings.get("pre_thresh", 130.0)),
            post_thresh=float(settings.get("post_thresh", 45.0)),
        ).convert("RGB")

        if settings.get("chromatic", False):
            intensity = int(settings.get("chromatic_intensity", 2))
            img = self.apply_chromatic_aberration(img, intensity).convert("RGB")

        if img.size != (target_w, target_h):
            img = img.resize((target_w, target_h), resample)

        return img

    def enhance_outline_weak(self, img):
        edges = img.filter(ImageFilter.FIND_EDGES).convert("L")
        thick = edges.filter(ImageFilter.MaxFilter(3))
        mask = thick.point(lambda x: 0 if x < 120 else 255, "1")

        mask_array = np.array(mask)
        border = 2
        mask_array[0:border, :] = 0
        mask_array[-border:, :] = 0
        mask_array[:, 0:border] = 0
        mask_array[:, -border:] = 0
        mask = Image.fromarray(mask_array)
        black = Image.new("RGB", img.size, (0, 0, 0))
        result = Image.composite(black, img.convert("RGB"), mask)
        return result.convert("RGB")

    def apply_chromatic_aberration(self, img, intensity):
        if intensity <= 0:
            return img

        arr = np.array(img)
        shifted = np.copy(arr)

        if intensity >= arr.shape[1]:
            return img

        shifted[:, :-intensity, 0] = arr[:, intensity:, 0]
        shifted[:, intensity:, 2] = arr[:, :-intensity, 2]

        return Image.fromarray(shifted, mode=img.mode)

    def bayer_ordered_dither(self, img, colors=16, preset="", skin_protect=False, pre_thresh=130.0, post_thresh=45.0):
        img_rgb = img.convert("RGB")
        enhancer = ImageEnhance.Contrast(img_rgb)
        img_enhanced = enhancer.enhance(1.2)

        arr = np.array(img_enhanced, dtype=np.float32)

        bayer_matrix = np.array([
            [0, 8, 2, 10],
            [12, 4, 14, 6],
            [3, 11, 1, 9],
            [15, 7, 13, 5]
        ], dtype=np.float32)

        if preset == "PC88" or colors <= 8:
            amplitude = 128.0
        else:
            amplitude = 255.0 / colors * 2.0

        bayer = (bayer_matrix / 16.0 - 0.5) * amplitude

        h, w = arr.shape[:2]
        bayer = np.tile(bayer, (h // 4 + 1, w // 4 + 1))[:h, :w]
        bayer = np.expand_dims(bayer, axis=2)

        luminance = np.dot(arr[..., :3], [0.299, 0.587, 0.114])

        if skin_protect:
            lum_mask = np.clip((170.0 - luminance) / 60.0, 0.0, 1.0)
            lum_mask = np.expand_dims(lum_mask, axis=2)
            bayer = bayer * lum_mask

        noisy = arr + bayer

        if skin_protect:
            bright_mask = np.expand_dims(luminance > pre_thresh, axis=2)
            noisy = np.where(bright_mask, np.clip(noisy, pre_thresh, 255), noisy)

        noisy = np.clip(noisy, 0, 255)
        noisy_img = Image.fromarray(noisy.astype(np.uint8))

        result_img = None

        if preset == "PC88" or colors == 8:
            pal_img_tmp = Image.new("P", (1, 1))
            pal_img_tmp.putpalette([
                0, 0, 0,
                255, 0, 0,
                0, 255, 0,
                255, 255, 0,
                0, 0, 255,
                255, 0, 255,
                0, 255, 255,
                255, 255, 255,
            ] + [0] * 248 * 3)
            result_img = noisy_img.quantize(palette=pal_img_tmp, dither=0).convert("RGB")
        else:
            if preset in ["MSX2", "MSX2 インターレース"] or colors == 512:
                noisy_arr = np.array(noisy_img, dtype=np.float32)
                quantized_512 = np.round(noisy_arr / 36.428) * 36.428
                noisy_img = Image.fromarray(np.clip(quantized_512, 0, 255).astype(np.uint8))

            if colors >= 512:
                result_img = noisy_img.convert("RGB")
            else:
                if colors > 256:
                    img_array = np.array(noisy_img.convert("RGB")).reshape((-1, 3))
                    try:
                        kmeans = MiniBatchKMeans(n_clusters=colors, random_state=0, n_init="auto")
                    except TypeError:
                        kmeans = MiniBatchKMeans(n_clusters=colors, random_state=0)
                    labels = kmeans.fit_predict(img_array)
                    palette = kmeans.cluster_centers_.astype("uint8")
                    quantized = palette[labels].reshape(noisy_img.size[1], noisy_img.size[0], 3)
                    result_img = Image.fromarray(quantized).convert("RGB")
                else:
                    result_img = noisy_img.quantize(colors=colors, method=2, kmeans=0).convert("RGB")

        if skin_protect:
            q_arr = np.array(result_img)
            orig_arr = np.array(img_rgb)

            orig_lum = np.dot(orig_arr, [0.299, 0.587, 0.114])
            q_lum = np.dot(q_arr, [0.299, 0.587, 0.114])

            bad_mask = (orig_lum > 120) & (q_lum < post_thresh)

            if np.any(bad_mask):
                unique_colors = np.unique(q_arr.reshape(-1, 3), axis=0)
                safe_colors = [c for c in unique_colors if np.dot(c, [0.299, 0.587, 0.114]) >= post_thresh]

                if len(safe_colors) == 0:
                    safe_colors = [np.array([255, 255, 255], dtype=np.uint8)]

                safe_colors = np.array(safe_colors)
                bad_orig_colors = orig_arr[bad_mask]

                diff = bad_orig_colors[:, np.newaxis, :] - safe_colors[np.newaxis, :, :]
                dist = np.sum(diff ** 2, axis=2)
                best_color_idx = np.argmin(dist, axis=1)

                q_arr[bad_mask] = safe_colors[best_color_idx]
                result_img = Image.fromarray(q_arr)

        return result_img.convert("RGBA")

    def apply_anti_alias_to_outline(self, img):
        edges = img.filter(ImageFilter.FIND_EDGES).convert("L")
        mask = edges.point(lambda x: 0 if x < 100 else 255, "1")
        smoothed = img.filter(ImageFilter.SMOOTH)
        result = Image.composite(smoothed, img, mask)
        return result.convert("RGB")

    def dot_boost(self, img, percent_str):
        percent = int(percent_str.rstrip("%")) / 100.0
        w, h = img.size
        small_w = max(1, int(w * percent))
        small_h = max(1, int(h * percent))

        small = img.resize((small_w, small_h), Image.BICUBIC)
        boosted = small.resize((w, h), Image.NEAREST)
        return boosted.convert("RGB")

    def get_selected_reencode_quality(self):
        quality_name = self.reencode_quality_var.get()
        quality = self.reencode_presets.get(quality_name)
        if quality is None:
            quality_name = self.default_reencode_preset_name
            quality = self.reencode_presets[quality_name]
            self.reencode_quality_var.set(quality_name)
        return quality_name, quality

    def get_ffmpeg_executable(self):
        # video-cutter.py と同じ方針。スクリプトと同じフォルダの ffmpeg.exe を優先し、なければPATHを使う。
        script_dir = os.path.dirname(os.path.abspath(__file__))
        local_ffmpeg = os.path.join(script_dir, "ffmpeg.exe")
        if os.path.exists(local_ffmpeg):
            return local_ffmpeg
        return shutil.which("ffmpeg")

    def run_subprocess_capture(self, command):
        return subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    # ====================== 動画変換 ======================
    def build_output_path_for_current_video(self):
        output_name = self.var_output_name.get().strip()
        if not output_name:
            base, _ = os.path.splitext(os.path.basename(self.video_path))
            output_name = f"{base}_retro.mp4"
        if not output_name.lower().endswith(".mp4"):
            output_name += ".mp4"
        return os.path.join(os.path.dirname(os.path.abspath(self.video_path)), output_name)

    def convert_video(self):
        if not self.video_path or self.is_converting:
            return

        ffmpeg_path = self.get_ffmpeg_executable()
        if not ffmpeg_path:
            messagebox.showerror(
                "ffmpeg が見つかりません",
                "ffmpeg が見つかりません。\nffmpeg.exe をツールと同じフォルダに置くか、PATHに通してね。"
            )
            return

        output_path = self.build_output_path_for_current_video()
        if os.path.abspath(output_path) == os.path.abspath(self.video_path):
            messagebox.showwarning("エラー", "入力動画と同じファイル名にはできません")
            return

        target_size = self.get_selected_size_for_dimensions(self.video_w, self.video_h)
        if target_size is None:
            messagebox.showwarning("エラー", "出力サイズが不正です")
            return

        quality_name, quality = self.get_selected_reencode_quality()
        settings = self.collect_processing_settings()
        self.progress_var.set(0)
        self.set_converting_ui(True, "変換準備中...")
        self.start_worker(
            self.convert_video_worker,
            self.video_path,
            output_path,
            ffmpeg_path,
            quality,
            quality_name,
            target_size,
            settings,
        )

    def convert_video_worker(self, input_path, output_path, ffmpeg_path, quality, quality_name, target_size, settings):
        try:
            self.convert_one_video(
                input_path=input_path,
                output_path=output_path,
                ffmpeg_path=ffmpeg_path,
                quality=quality,
                quality_name=quality_name,
                target_size=target_size,
                progress_prefix="変換中",
                settings=settings,
                progress_base=0.0,
                progress_span=98.0,
            )
            self.ui_queue.put(("done_single", output_path, quality_name))
        except Exception as e:
            self.ui_queue.put(("error", "エラー", f"動画変換中にエラーが発生しました:\n{str(e)}"))

    def set_converting_ui(self, converting, progress_text=None):
        self.is_converting = converting
        state = "disabled" if converting else "normal"
        self.btn_select.config(state=state)
        self.btn_convert.config(state="disabled" if converting or not self.video_path else "normal")
        self.btn_refresh_preview.config(state="disabled" if converting or not self.video_path else "normal")
        self.chk_folder_overwrite.config(state=state)
        self.size_combo.config(state="disabled" if converting or self.preset_var.get() else "readonly")
        self.colors_combo.config(state="disabled" if converting or self.preset_var.get() else "readonly")
        if progress_text is not None:
            self.lbl_progress.config(text=progress_text)
        self.root.update_idletasks()

    def collect_videos_recursive(self, folder_path):
        videos = []
        for root_dir, dirnames, filenames in os.walk(folder_path):
            dirnames[:] = [d for d in dirnames if not d.startswith("_backup.")]
            for name in filenames:
                path = os.path.join(root_dir, name)
                if path.lower().endswith(VIDEO_EXTENSIONS):
                    videos.append(path)
        videos.sort(key=lambda p: p.lower())
        return videos

    def convert_folder_overwrite(self, folder_path):
        if self.is_converting:
            return

        ffmpeg_path = self.get_ffmpeg_executable()
        if not ffmpeg_path:
            messagebox.showerror(
                "ffmpeg が見つかりません",
                "ffmpeg が見つかりません。\nffmpeg.exe をツールと同じフォルダに置くか、PATHに通してね。"
            )
            return

        videos = self.collect_videos_recursive(folder_path)
        if not videos:
            messagebox.showwarning("エラー", "フォルダ内に有効な動画ファイルがありません")
            return

        script_dir = os.path.dirname(os.path.abspath(__file__))
        backup_root = os.path.join(script_dir, "_backup." + datetime.now().strftime("%Y%m%d%H%M%S"))
        folder_name = os.path.basename(os.path.normpath(folder_path)) or "folder"
        quality_name, quality = self.get_selected_reencode_quality()
        settings = self.collect_processing_settings()

        self.progress_var.set(0)
        self.set_converting_ui(True, f"フォルダ一括変換準備中... {len(videos)}件")
        self.start_worker(
            self.convert_folder_overwrite_worker,
            folder_path,
            videos,
            ffmpeg_path,
            backup_root,
            folder_name,
            quality,
            quality_name,
            settings,
        )

    def convert_folder_overwrite_worker(self, folder_path, videos, ffmpeg_path, backup_root, folder_name, quality, quality_name, settings):
        converted = 0
        failed = []
        temp_dir = tempfile.mkdtemp(prefix="retro_overwrite_")

        try:
            for index, src_path in enumerate(videos, start=1):
                rel = os.path.relpath(src_path, folder_path)
                rel_for_backup = os.path.join(folder_name, rel)
                backup_path = os.path.join(backup_root, rel_for_backup)
                os.makedirs(os.path.dirname(backup_path), exist_ok=True)

                suffix = os.path.splitext(src_path)[1] or ".mp4"
                temp_output = os.path.join(temp_dir, f"converted_{index:05d}{suffix}")
                item_base = (index - 1) / max(1, len(videos)) * 100.0
                item_span = 100.0 / max(1, len(videos))

                try:
                    self.post_progress(item_base, f"バックアップ中 {index}/{len(videos)}: {os.path.basename(src_path)}")
                    shutil.copy2(src_path, backup_path)

                    w, h, _fps, _frames = self.get_video_basic_info(src_path)
                    target_size = self.resolve_size_for_dimensions(
                        w, h, settings.get("size_label", "現状維持"), settings.get("size_map", {})
                    )
                    if target_size is None:
                        raise RuntimeError("出力サイズが不正です")

                    self.convert_one_video(
                        input_path=src_path,
                        output_path=temp_output,
                        ffmpeg_path=ffmpeg_path,
                        quality=quality,
                        quality_name=quality_name,
                        target_size=target_size,
                        progress_prefix=f"上書き変換中 {index}/{len(videos)}: {os.path.basename(src_path)}",
                        settings=settings,
                        progress_base=item_base,
                        progress_span=item_span * 0.98,
                    )
                    os.replace(temp_output, src_path)
                    converted += 1
                    self.post_progress(item_base + item_span, f"完了 {index}/{len(videos)}: {os.path.basename(src_path)}")
                except Exception as e:
                    failed.append((src_path, str(e)))
                    try:
                        if os.path.exists(temp_output):
                            os.remove(temp_output)
                    except Exception:
                        pass
                    continue

            self.ui_queue.put(("done_folder", converted, failed, backup_root, quality_name))
        except Exception as e:
            self.ui_queue.put(("error", "エラー", f"フォルダ一括変換中にエラーが発生しました:\n{str(e)}"))
        finally:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

    def get_video_basic_info(self, path):
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise RuntimeError("動画を開けませんでした")
        try:
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = float(cap.get(cv2.CAP_PROP_FPS))
            frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if fps <= 0:
                fps = 30.0
            if frames <= 0:
                frames = 1
            return w, h, fps, frames
        finally:
            cap.release()

    def convert_one_video(self, input_path, output_path, ffmpeg_path, quality, quality_name, target_size, progress_prefix, settings, progress_base=0.0, progress_span=100.0):
        target_w, target_h = target_size
        tmp_dir = tempfile.mkdtemp(prefix="retro_video_")
        temp_video_path = os.path.join(tmp_dir, "video_only.mp4")

        cap = None
        writer = None
        try:
            cap = cv2.VideoCapture(input_path)
            if not cap.isOpened():
                raise RuntimeError("動画を開けませんでした")

            fps = float(cap.get(cv2.CAP_PROP_FPS))
            fps = fps if fps and fps > 0 else 30.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total <= 0:
                total = 1

            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(temp_video_path, fourcc, fps, (target_w, target_h))
            if not writer.isOpened():
                raise RuntimeError("一時動画の書き出しを開始できませんでした")

            start_time = time.time()
            frame_idx = 0
            last_ui_update = 0.0

            while True:
                ok, frame_bgr = cap.read()
                if not ok or frame_bgr is None:
                    break

                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(frame_rgb).convert("RGB")
                processed = self.process_frame_with_settings(pil_img, settings, target_size=target_size).convert("RGB")
                out_rgb = np.array(processed)
                out_bgr = cv2.cvtColor(out_rgb, cv2.COLOR_RGB2BGR)
                writer.write(out_bgr)

                frame_idx += 1
                now = time.time()
                if now - last_ui_update >= 0.12 or frame_idx == total:
                    percent = min(100.0, frame_idx / max(1, total) * 100.0)
                    elapsed = now - start_time
                    fps_proc = frame_idx / elapsed if elapsed > 0 else 0
                    overall_percent = progress_base + (percent / 100.0) * progress_span
                    self.post_progress(overall_percent, f"{progress_prefix}... {frame_idx}/{total} フレーム ({percent:.1f}%) / {fps_proc:.1f} fps")
                    last_ui_update = now

            writer.release()
            writer = None
            cap.release()
            cap = None

            self.post_progress(progress_base + progress_span, f"エンコード中... {quality_name}")

            ffmpeg_cmd = [
                ffmpeg_path,
                "-y",
                "-i", temp_video_path,
                "-i", input_path,
                "-map", "0:v:0",
                "-map", "1:a?",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-crf", quality["crf"],
                "-preset", quality["preset"],
                "-c:a", "aac",
                "-b:a", quality["audio_bitrate"],
                "-shortest",
                output_path,
            ]
            result = self.run_subprocess_capture(ffmpeg_cmd)
            if result.returncode != 0:
                fallback_cmd = [
                    ffmpeg_path,
                    "-y",
                    "-i", temp_video_path,
                    "-i", input_path,
                    "-map", "0:v:0",
                    "-map", "1:a?",
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-b:a", quality.get("audio_bitrate", "192k"),
                    "-shortest",
                    output_path,
                ]
                fallback = self.run_subprocess_capture(fallback_cmd)
                if fallback.returncode != 0:
                    raise RuntimeError("ffmpeg での結合に失敗しました:\n" + fallback.stderr[-1200:])
        finally:
            if writer is not None:
                writer.release()
            if cap is not None:
                cap.release()
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

    def __del__(self):
        try:
            if self.cap is not None:
                self.cap.release()
        except Exception:
            pass


if __name__ == "__main__":
    root = TkinterDnD.Tk() if DND_AVAILABLE else tk.Tk()
    app = RetroVideoConverter(root)
    root.mainloop()
